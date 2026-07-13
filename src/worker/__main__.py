"""Command-line entrypoint for the OneCompute worker."""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time

from isolation import active_boundary
from trust import build_client
from worker.agent import WorkerAgent
from worker.capability import detect_capability
from worker.governor import AdaptiveGovernor, system_gpu_load_pct, system_ram_load_pct
from worker.idle import IdleGate
from worker.telemetry import PilotTelemetry

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


def _live_cpu_pct(governor: AdaptiveGovernor) -> float:
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
    return governor.user_cpu()


def _persist(profiler) -> None:
    """Best-effort local save of the learned usage profile.

    Tolerates a stub profiler with no ``save()`` and never raises, so a persistence hiccup can
    never take down a week-long observer.
    """
    save = getattr(profiler, "save", None)
    if callable(save):
        try:
            save()
        except Exception:
            pass


def run_measure_loop(
    agent: WorkerAgent,
    governor: AdaptiveGovernor,
    telem: PilotTelemetry,
    interval: float,
    once: bool = False,
) -> int:
    """Measurement-only loop for the low-risk pilot: fold this machine's live CPU/GPU/RAM into the
    on-device usage profile and (if telemetry is on) append a local ``measure`` event each tick.
    It also **saves the profile locally** and uploads the learned envelope to the orchestrator on
    the first sample and about every five minutes after (opt-in, best-effort via
    ``agent.report_profile``), so a reboot or power-loss costs at most one upload window of learning
    and a failed upload is swallowed so the pilot keeps working with no reachable orchestrator.

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
    interval = max(0.0, interval)
    # Save + upload the learned envelope on the first sample (so the device shows up in the central
    # measurement view fast) and roughly every five minutes after.
    post_every = max(1, round(300.0 / interval)) if interval > 0 else 1
    samples = 0
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
                cpu = _live_cpu_pct(governor)
                gpu = system_gpu_load_pct() if agent.capability.has_gpu else 0.0
                ram = system_ram_load_pct()
                governor.profiler.record(cpu, gpu, ram)  # learn the hour-of-week usage envelope
                telem.log("measure", cpu=round(cpu, 1), gpu=round(gpu, 1), ram=round(ram, 1))
                samples += 1
                if samples == 1 or samples % post_every == 0:
                    _persist(governor.profiler)              # local durability: reboot loses <= one window
                    agent.report_profile(governor.profiler)  # opt-in, best-effort, offline-safe
                print(f"measure: cpu={cpu:.1f}% gpu={gpu:.1f}% ram={ram:.1f}% (no jobs will run)")
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
        _persist(governor.profiler)
        agent.report_profile(governor.profiler)
    return samples


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a OneCompute worker agent")
    parser.add_argument("--url", required=True, help="Orchestrator base URL")
    parser.add_argument("--once", action="store_true", help="Run at most one job then exit")
    parser.add_argument("--idle-threshold", type=float, default=60.0, help="Input idle seconds before work")
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
        help="Measurement-only pilot: register + stream live CPU/GPU/RAM for the dashboard and "
             "learn the on-device usage profile, but NEVER pull or run a job. For a low-risk "
             "'measure first, run workloads later' voluntary pilot.",
    )
    parser.add_argument(
        "--measure-interval",
        type=float,
        default=30.0,
        help="Seconds between usage samples folded into the on-device profile in --measure-only "
             "mode (default 30.0; the live usage heartbeat still streams at --usage-interval)",
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
    try:
        client = build_client(
            args.url,
            ca_cert=args.tls_ca,
            client_cert=args.client_cert,
            client_key=args.client_key,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.url.startswith("http://") and (args.tls_ca or args.client_cert or args.client_key):
        logging.warning(
            "TLS material was provided but --url is http://; TLS options apply only to an https:// URL"
        )
    agent = WorkerAgent(
        args.url,
        detect_capability(),
        client=client,
        isolated=args.isolated,
        require_isolation=args.require_isolation,
        trusted_public_key_hex=args.trusted_key,
        client_cert_path=args.client_cert,
    )
    gate: IdleGate | AdaptiveGovernor | None = None
    usage_stop: threading.Event | None = None
    try:
        if args.measure_only:
            # Low-risk pilot: ONLY track CPU/GPU/RAM, never pull or run a job. Join the fleet so
            # the device appears in the dashboard, stream live usage for the fleet view, then loop
            # taking measurements. No admission/yield decision is ever made here: measurement
            # imposes no compute load, so it needs no AC power and never admits work.
            if not agent.registered:
                agent.register()
            if agent.registered and not agent.approved:
                # Block (heartbeating) until approved so the device shows up in the dashboard, but
                # with --once send a single heartbeat and exit rather than hang.
                if not agent.wait_for_approval(once=args.once):
                    return
            # Stream live usage so the dashboard shows this device + its graph, exactly as the
            # work loop does. Pure telemetry -- it never leases or runs anything.
            usage_stop = _start_usage_heartbeat(agent, args.usage_interval)
            # The governor is used ONLY to own + persist the on-device UsageProfiler; its
            # should_run()/active_now() admission+yield paths are intentionally never called.
            gate = AdaptiveGovernor(idle_gate=IdleGate(input_idle_threshold_s=args.idle_threshold))
            telem = PilotTelemetry(agent.capability.worker_id, enabled=args.telemetry)
            if args.telemetry:
                print(f"pilot telemetry -> {telem.path}")
            print("measure-only: tracking CPU/GPU/RAM, no jobs will run")
            run_measure_loop(agent, gate, telem, args.measure_interval, once=args.once)
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

        idle_gate = IdleGate(input_idle_threshold_s=args.idle_threshold)
        adaptive = args.governor == "adaptive"
        # The adaptive governor is a drop-in for IdleGate: same should_run()/active_now(), but
        # it admits work into the machine's learned spare headroom (runs during light use) and
        # yields on the employee's own attributed compute demand. See worker/governor.py.
        gate = AdaptiveGovernor(idle_gate=idle_gate) if adaptive else idle_gate
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
                time.sleep(agent.poll_interval_s or 1.5)
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
        if isinstance(gate, AdaptiveGovernor):
            gate.profiler.save()  # persist the learned envelope on the way out
        agent.close()


if __name__ == "__main__":
    main()
