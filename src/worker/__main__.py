"""Command-line entrypoint for the OneCompute worker."""

from __future__ import annotations

import argparse
import logging
import math
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from isolation import active_boundary
from measurement.availability import availability_tracker_from_telemetry
from trust import build_client
from worker.agent import WorkerAgent
from worker.capability import detect_capability
from worker.governor import AdaptiveGovernor, system_gpu_load_pct, system_ram_load_pct
from worker.idle import IdleGate
from worker.measurement_identity import load_or_create_measurement_id
from worker.profile_lock import ProfileInUseError, ProfileLock
from worker.profiler import UsageProfiler, default_profile_path
from worker.telemetry import PilotTelemetry, default_telemetry_path

MIN_MEASURE_INTERVAL_SECONDS = 5.0
MAX_MEASURE_INTERVAL_SECONDS = 3600.0
PROFILE_SAVE_INTERVAL_SECONDS = 60.0
PROFILE_UPLOAD_INTERVAL_SECONDS = 300.0

try:  # psutil is a declared dependency; guard so the worker still runs without it
    import psutil
except Exception:  # pragma: no cover
    psutil = None


def _start_usage_heartbeat(agent: WorkerAgent, period_s: float = 1.0) -> threading.Event:
    """Stream this machine's live CPU/GPU/free-RAM to the orchestrator on a fixed cadence so the
    dashboard fleet view + per-device usage graphs stay current even between jobs.

    Pure telemetry: it never sends current_job_id, so it cannot perturb leasing/scheduling. Runs
    as a daemon thread; returns a stop Event the caller sets on shutdown. The cadence is floored
    at 0.25 s: psutil.cpu_percent measures the delta since the last call, so faster than that
    gives a noisy/meaningless CPU reading (and just hammers the orchestrator).
    """
    period_s = max(0.25, period_s)
    stop = threading.Event()

    def loop() -> None:
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)  # prime the rolling delta
            except Exception:
                pass
        while not stop.wait(period_s):
            cpu = 0.0
            if psutil is not None:
                try:
                    cpu = float(psutil.cpu_percent(interval=None))
                except Exception:
                    cpu = 0.0
            gpu = system_gpu_load_pct() if agent.capability.has_gpu else None
            try:
                agent.heartbeat(
                    cpu_pct=round(cpu, 1),
                    gpu_pct=(round(gpu, 1) if gpu is not None else None),
                )
            except Exception:
                pass

    threading.Thread(target=loop, name="onecompute-usage-heartbeat", daemon=True).start()
    return stop


def _live_cpu_pct(governor: AdaptiveGovernor) -> float | None:
    """System CPU% sampled over a short REAL window. In measure-only mode no job runs, so system
    CPU is the employee's demand -- exactly what ``AdaptiveGovernor.observe()`` folds in between
    jobs. We take a brief *blocking* sample rather than psutil's non-blocking delta so that even a
    single ``--once`` reading reflects actual load instead of ~0% measured over a zero-length
    interval (the non-blocking delta only becomes meaningful once time has elapsed since the prior
    call). Falls back to the governor's ctypes ``GetSystemTimes`` sampler when psutil is absent.
    """
    if psutil is not None:
        try:
            return float(psutil.cpu_percent(interval=0.1))
        except Exception:
            pass
    return governor.system_cpu_sample()


def _persist(profiler) -> bool:
    """Best-effort local save of the learned usage profile.

    Tolerates a stub profiler with no ``save()`` and never raises. A false return makes persistence
    failure visible to the measurement loop without taking down a week-long observer.
    """
    save = getattr(profiler, "save", None)
    if not callable(save):
        return True
    try:
        result = save()
    except (OSError, TypeError, ValueError) as exc:
        logging.error("usage profile save failed: %s", exc)
        return False
    return result is not False


def _measurement_pct(name: str, value: object) -> float:
    if value is None:
        raise RuntimeError(f"{name} utilization is unavailable")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{name} utilization is invalid") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"{name} utilization is not finite")
    return max(0.0, min(100.0, number))


def _yn(value: bool | None) -> str:
    if value is None:
        return "?"
    return "Y" if value else "N"


def _measurement_enrollment_ready(agent: WorkerAgent) -> bool:
    ensure = getattr(agent, "ensure_measurement_enrollment", None)
    if callable(ensure):
        return bool(ensure())
    return True


def _wait_for_headroom(gate, adaptive: bool, base_interval: float, step: float = 0.5) -> None:
    """Sleep up to ``base_interval`` seconds while the machine is busy, but wake EARLY the instant
    spare headroom returns so a freed-up worker reroutes into work in ~``step`` seconds instead of a
    full poll tick. Uses the governor's non-recording ``available_now`` (a plain IdleGate's cheap
    ``should_run`` otherwise) so this fast re-checking never biases the learned usage profile."""
    step = min(step, base_interval)
    waited = 0.0
    while waited < base_interval:
        time.sleep(step)
        waited += step
        try:
            if adaptive and isinstance(gate, AdaptiveGovernor):
                if gate.available_now():
                    return
            elif not adaptive and gate.should_run():
                return
        except Exception:
            continue



def run_measure_loop(
    agent: WorkerAgent,
    governor: AdaptiveGovernor,
    telem: PilotTelemetry,
    interval: float,
    once: bool = False,
    upload: bool = True,
    device_class: str = "unknown",
) -> int:
    """Measurement-only loop for the low-risk pilot: fold this machine's live CPU/GPU/RAM into the
    on-device usage profile. It saves the profile locally and uploads one compact derived summary
    on the first sample and about every five minutes after. It never writes a timestamped sample
    timeline and never streams live utilization to the orchestrator.

    It **never** pulls or runs a job -- ``poll_once`` / ``run_once`` / ``run_guarded`` / ``run_job``
    are never called -- so an org can run a "measure first, run workloads later" pilot with zero
    chance of a workload ever landing on an employee's machine. Because pure measurement imposes no
    compute load, it needs no AC power and admits no work: the governor's admission/yield decision
    (``should_run`` / ``active_now``) is deliberately never consulted here; the governor is used
    only as the owner of the on-device ``UsageProfiler`` so the fold-in matches what
    ``AdaptiveGovernor.observe()`` records between jobs.

    Built to run unattended for a week: each iteration is wrapped so a transient sampling error
    (a psutil hiccup, a disk blip) is logged and skipped rather than killing the loop, and a wake
    from sleep/hibernate is detected and noted (the CPU reading is a fresh blocking sample, so it is
    still valid for the current hour-of-week bucket). Returns the number of samples taken. With
    ``once=True`` it takes exactly one sample then returns (keeps ``--once`` testable and
    non-hanging); otherwise it loops on ``interval`` until interrupted, terminating cleanly on
    Ctrl-C. The learned profile is saved and a final envelope uploaded on exit.
    """
    if not math.isfinite(interval):
        raise ValueError("measurement interval must be finite")
    interval = max(0.0, interval)
    if not once and interval < MIN_MEASURE_INTERVAL_SECONDS:
        raise ValueError(
            f"continuous measurement interval must be at least "
            f"{MIN_MEASURE_INTERVAL_SECONDS:g} seconds"
        )
    if not once and interval > MAX_MEASURE_INTERVAL_SECONDS:
        raise ValueError(
            f"continuous measurement interval must be at most "
            f"{MAX_MEASURE_INTERVAL_SECONDS:g} seconds"
        )
    # Save locally about every minute and upload about every five minutes. Both happen on the first
    # successful sample so status and the central fleet view become useful immediately.
    save_every = max(1, round(PROFILE_SAVE_INTERVAL_SECONDS / interval)) if interval > 0 else 1
    post_every = max(1, round(PROFILE_UPLOAD_INTERVAL_SECONDS / interval)) if interval > 0 else 1
    samples = 0
    last_upload_sample = 0
    last_wall = time.monotonic()
    try:
        while True:
            try:
                now = time.monotonic()
                gap = now - last_wall
                last_wall = now
                # A gap far larger than the interval means the machine was suspended (sleep or
                # hibernate). The CPU reading below is a fresh blocking sample, so it stays valid for
                # the current hour-of-week bucket; we just note the resume for the pilot log.
                if interval > 0 and samples > 0 and gap > max(3 * interval, interval + 30.0):
                    print(f"measure: resumed after {gap:.0f}s gap (sleep/hibernate); continuing")
                # No job runs in measure-only mode, so the employee's demand == the whole machine.
                cpu = _measurement_pct("CPU", _live_cpu_pct(governor))
                gpu = None
                if agent.capability.has_gpu:
                    gpu_sample = system_gpu_load_pct()
                    try:
                        gpu = _measurement_pct("GPU", gpu_sample)
                    except RuntimeError as exc:
                        logging.warning("%s; retaining the CPU/RAM sample", exc)
                ram = _measurement_pct("RAM", system_ram_load_pct())
                gate = getattr(governor, "gate", None)
                on_ac_reader = getattr(gate, "on_ac_state", None)
                idle_reader = getattr(gate, "user_idle_state", None)
                on_ac = (
                    on_ac_reader()
                    if callable(on_ac_reader)
                    else gate.on_ac() if gate is not None else None
                )
                idle = (
                    idle_reader()
                    if callable(idle_reader)
                    else gate.user_idle() if gate is not None else None
                )
                governor.profiler.record(cpu, gpu, ram, on_ac=on_ac, idle=idle)  # learn the envelope
                record_availability = getattr(governor.profiler, "record_availability", None)
                if callable(record_availability):
                    record_availability(time.time(), interval or 30.0)
                samples += 1
                save_due = samples == 1 or samples % save_every == 0
                persisted = _persist(governor.profiler) if save_due else False
                if upload and persisted:
                    if (
                        _measurement_enrollment_ready(agent)
                        and (
                            last_upload_sample == 0
                            or samples - last_upload_sample >= post_every
                        )
                        and agent.report_profile(
                            governor.profiler,
                            device_class=device_class,
                        )
                    ):
                        last_upload_sample = samples
                gpu_text = f"{gpu:.1f}%" if gpu is not None else "?"
                print(f"measure: cpu={cpu:.1f}% gpu={gpu_text} ram={ram:.1f}% "
                      f"ac={_yn(on_ac)} idle={_yn(idle)} (no jobs will run)")
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # a bad sample must never kill a week-long observer
                logging.warning("measure sample failed (%s); continuing", exc)
            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        # Land the final envelope (and a local save) so the profile and central rollup reflect the
        # full pilot window even on a clean stop.
        persisted = _persist(governor.profiler)
        if (
            upload
            and persisted
            and samples > 0
            and last_upload_sample != samples
            and _measurement_enrollment_ready(agent)
            and agent.report_profile(governor.profiler, device_class=device_class)
        ):
            last_upload_sample = samples
    return samples


def _adaptive_governor(idle_threshold: float, profile_path: str | None = None) -> AdaptiveGovernor:
    """Build the demand-adaptive governor, optionally backing its usage profiler with a custom path.

    A per-device ``--profile`` lets a multi-person pilot collect distinctly-named profiles into one
    folder for a single aggregate readout. When ``profile_path`` is None the profiler uses its default
    location (``%LOCALAPPDATA%\\OneCompute\\usage_profile.json``).
    """
    profiler = UsageProfiler(path=profile_path) if profile_path else None
    return AdaptiveGovernor(
        profiler=profiler, idle_gate=IdleGate(input_idle_threshold_s=idle_threshold)
    )


def _is_loopback_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a OneCompute worker agent")
    parser.add_argument(
        "--url",
        default=None,
        help="Orchestrator base URL. Required for the work loop; OPTIONAL with --measure-only "
             "(omit for a fully local run that records CPU/GPU/RAM to the on-device profile only, "
             "with no registration, heartbeat, or upload).",
    )
    parser.add_argument("--once", action="store_true", help="Run at most one job then exit")
    parser.add_argument("--idle-threshold", type=float, default=60.0, help="Input idle seconds before work")
    parser.add_argument(
        "--profile",
        default=None,
        help="Path to the on-device usage profile JSON (default: "
             "%LOCALAPPDATA%\\OneCompute\\usage_profile.json). Give each device a distinct name to "
             "collect profiles from several people into one folder for a single aggregate readout.",
    )
    parser.add_argument(
        "--usage-interval",
        type=float,
        default=1.0,
        help="Seconds between live CPU/GPU/RAM usage heartbeats for the dashboard (default 1.0; "
             "floored at 0.25 so the CPU reading stays accurate)",
    )
    parser.add_argument(
        "--governor",
        choices=("adaptive", "idle"),
        default="adaptive",
        help="Admission policy: 'adaptive' headroom governor (default) or binary 'idle' gate",
    )
    parser.add_argument(
        "--gate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the idle gate in the continuous loop",
    )
    parser.add_argument(
        "--telemetry",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write local pilot telemetry (governor decisions + job results) to a JSONL file",
    )
    parser.add_argument(
        "--isolated",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run jobs sandboxed (Docker/Job-Object). Also lets the governor attribute the job's "
             "own CPU (a child process) so it never yields on its own load. Default on.",
    )
    parser.add_argument(
        "--measure-only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Measurement-only pilot: learn the on-device usage profile and periodically upload "
             "one compact summary, but NEVER pull jobs, stream live utilization, or write a raw "
             "sample timeline.",
    )
    parser.add_argument(
        "--measure-interval",
        type=float,
        default=30.0,
        help="Seconds between usage samples folded into the on-device profile in --measure-only "
             "mode (default 30.0)",
    )
    parser.add_argument(
        "--measurement-device-class",
        choices=("laptop", "desktop", "devbox", "xbox", "unknown"),
        default="unknown",
        help="Coarse device class included in the compact fleet summary (default unknown).",
    )
    parser.add_argument(
        "--measurement-id",
        default=None,
        help="Optional 8-64 character fleet alias. Otherwise a stable random observer ID is "
             "created locally and no hostname is used for measurement registration.",
    )
    parser.add_argument(
        "--require-isolation",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Fail CLOSED: refuse to run any job unless a real OS-enforced sandbox (Docker; MXC "
             "when available) is active. Blocks the unsandboxed subprocess fallback and host-side "
             "GPU/AI execution. Recommended for real pilots; off by default so the local demo runs.",
    )
    parser.add_argument(
        "--trusted-key",
        default=os.environ.get("ONECOMPUTE_TRUSTED_PUBKEY"),
        help="Hex Ed25519 public key the worker pins as the ONLY trusted job signer (defaults to "
             "$ONECOMPUTE_TRUSTED_PUBKEY). When set, unsigned or differently-signed manifests are "
             "refused, so a compromised orchestrator cannot inject a self-signed job.",
    )
    parser.add_argument(
        "--tls-ca",
        default=None,
        help="CA cert used to verify the orchestrator's TLS certificate (pin a private CA). "
             "Default: the system trust store. Applies only to an https:// --url.",
    )
    parser.add_argument(
        "--client-cert",
        default=None,
        help="Client certificate presented for mutual TLS (pair with --client-key).",
    )
    parser.add_argument(
        "--client-key",
        default=None,
        help="Client private key for mutual TLS (pair with --client-cert).",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    if not args.url and not args.measure_only:
        parser.error("--url is required (it is optional only with --measure-only, for a local run)")
    url_scheme = urlparse(args.url).scheme.lower() if args.url else ""
    if args.url and url_scheme not in {"http", "https"}:
        parser.error("--url must use http:// or https://")
    if args.measure_only and args.url and url_scheme == "http" and not _is_loopback_url(args.url):
        parser.error("remote measurement uploads require an https:// --url")
    if args.measure_only and not math.isfinite(args.measure_interval):
        parser.error("--measure-interval must be finite")
    if args.measure_only and not args.once and args.measure_interval < MIN_MEASURE_INTERVAL_SECONDS:
        parser.error(
            f"--measure-interval must be at least {MIN_MEASURE_INTERVAL_SECONDS:g} seconds "
            "for a continuous observer"
        )
    if args.measure_only and not args.once and args.measure_interval > MAX_MEASURE_INTERVAL_SECONDS:
        parser.error(
            f"--measure-interval must be at most {MAX_MEASURE_INTERVAL_SECONDS:g} seconds "
            "for a continuous observer"
        )
    if args.url and url_scheme != "https" and (
        args.tls_ca or args.client_cert or args.client_key
    ):
        parser.error("TLS material requires an https:// --url")
    client = None
    if args.url:
        try:
            client = build_client(
                args.url,
                ca_cert=args.tls_ca,
                client_cert=args.client_cert,
                client_key=args.client_key,
            )
        except ValueError as exc:
            parser.error(str(exc))
    measurement_id = None
    if args.measure_only:
        try:
            measurement_id = load_or_create_measurement_id(requested=args.measurement_id)
        except (OSError, ValueError) as exc:
            parser.error(f"could not establish measurement identity: {exc}")
    agent = WorkerAgent(
        args.url or "http://localhost",
        detect_capability(worker_id=measurement_id),
        client=client,
        isolated=args.isolated,
        require_isolation=args.require_isolation,
        trusted_public_key_hex=args.trusted_key,
        measurement_only=args.measure_only,
    )
    gate: IdleGate | AdaptiveGovernor | None = None
    profile_lock: ProfileLock | None = None
    usage_stop: threading.Event | None = None
    try:
        if args.measure_only:
            # Low-risk pilot: ONLY track CPU/GPU/RAM, never pull or run a job. With a --url it joins
            # the fleet so the device appears in the dashboard; WITHOUT a
            # --url it runs fully local (no network at all), recording only to the on-device profile.
            # No admission/yield decision is ever made here: measurement imposes no compute load, so
            # it needs no AC power and never admits work.
            local_only = not args.url
            # Lock before loading the profile or registering. A duplicate launcher must not recover,
            # write, or advertise the same observer profile.
            profile_path = Path(args.profile) if args.profile else default_profile_path()
            candidate_lock = ProfileLock(profile_path)
            try:
                candidate_lock.acquire()
            except (OSError, ProfileInUseError) as exc:
                parser.error(f"measurement profile is not writable: {exc}")
            profile_lock = candidate_lock
            try:
                gate = _adaptive_governor(args.idle_threshold, args.profile)
                gate.profiler.gpu_supported = bool(agent.capability.has_gpu)
                gate.profiler.assert_writable()
            except OSError as exc:
                profile_lock.release()
                profile_lock = None
                parser.error(f"measurement profile is not writable: {exc}")
            if not local_only:
                if not agent.registered:
                    agent.register()
                if agent.registered and not agent.approved:
                    code = agent.device_code or "pending"
                    print(
                        f"measure-only: fleet approval pending ({code}); "
                        "local collection will continue"
                    )
                elif not agent.registered:
                    logging.warning(
                        "measurement enrollment is unavailable; collecting locally and retrying"
                    )
            else:
                print("measure-only: LOCAL mode (no --url). Recording CPU/GPU/RAM to the on-device "
                      "profile only; no registration, heartbeat, or upload.")
            # The governor is used ONLY to own + persist the on-device UsageProfiler; its
            # should_run()/active_now() admission+yield paths are intentionally never called.
            telem = PilotTelemetry(agent.capability.worker_id, enabled=False)
            if (
                not args.profile
                and gate.profiler.availability.span_seconds <= 0.0
            ):
                historic = availability_tracker_from_telemetry(
                    default_telemetry_path(),
                    expected_interval_seconds=args.measure_interval,
                )
                if historic.span_seconds > 0.0:
                    gate.profiler.availability = historic
                    _persist(gate.profiler)
                    print(
                        "measure-only: restored availability timing from "
                        f"{historic.sample_count} local telemetry samples"
                    )
            print(
                "measure-only: tracking CPU/GPU/RAM locally, no jobs or sample timeline "
                f"(profile: {gate.profiler.path}; observer: {agent.capability.worker_id})"
            )
            run_measure_loop(
                agent,
                gate,
                telem,
                args.measure_interval,
                once=args.once,
                upload=not local_only,
                device_class=args.measurement_device_class,
            )
            return

        if args.once:
            rr = agent.run_once()
            print(rr.model_dump() if rr else "no work")
            return

        # Join the fleet before doing any work: register, then (if the fleet gates access)
        # show the device code and block on heartbeats until an admin approves in the dashboard.
        if not agent.registered:
            agent.register()
        if agent.registered and not agent.approved:
            agent.wait_for_approval()

        # Once we've joined, stream live usage so the dashboard can show this device + its graph.
        usage_stop = _start_usage_heartbeat(agent, args.usage_interval)

        adaptive = args.governor == "adaptive"
        # The adaptive governor is a drop-in for IdleGate: same should_run()/active_now(), but
        # it admits work into the machine's learned spare headroom (runs during light use) and
        # yields on the employee's own attributed compute demand. See worker/governor.py.
        gate = (
            _adaptive_governor(args.idle_threshold, args.profile)
            if adaptive
            else IdleGate(input_idle_threshold_s=args.idle_threshold)
        )
        telem = PilotTelemetry(agent.capability.worker_id, enabled=args.telemetry)
        if args.telemetry:
            print(f"pilot telemetry -> {telem.path}")
        while True:
            admitted = (not args.gate) or gate.should_run()
            if args.telemetry:
                snap = dict(gate.last_decision) if isinstance(gate, AdaptiveGovernor) else {}
                snap.setdefault("admitted", bool(admitted))
                telem.log("tick", boundary=active_boundary(), **snap)
            if not admitted:
                print("skip: outside headroom" if adaptive else "skip: not idle")
                # Proactive reroute-in: while the machine is busy, re-check availability at a fast,
                # non-recording cadence so we pull work within ~0.5s of it freeing up, not a full tick.
                _wait_for_headroom(gate, adaptive, agent.poll_interval_s or 1.5)
                continue
            rr = agent.run_guarded(gate) if args.gate else agent.run_once()
            if rr is None:
                print("idle: no work")
            else:
                if args.telemetry:
                    telem.log("result", status=rr.status, units=rr.units,
                              duration_s=round(rr.duration_s or 0.0, 3), job_id=rr.job_id)
                print(
                    f"yielded job={rr.job_id} units={rr.units}"
                    if rr.status == "yielded"
                    else f"{rr.status} job={rr.job_id} units={rr.units}"
                )
            time.sleep(agent.poll_interval_s or 1.5)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        if usage_stop is not None:
            usage_stop.set()  # stop streaming usage on the way out
        if isinstance(gate, AdaptiveGovernor) and (not args.measure_only or profile_lock is not None):
            gate.profiler.save()  # persist the learned envelope on the way out
        if profile_lock is not None:
            profile_lock.release()
        agent.close()


if __name__ == "__main__":
    main()
