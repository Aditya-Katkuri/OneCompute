"""Command-line entrypoint for the OneCompute worker."""

from __future__ import annotations

import argparse
import logging
import threading
import time

from isolation import active_boundary
from worker.agent import WorkerAgent
from worker.capability import detect_capability
from worker.governor import AdaptiveGovernor, system_gpu_load_pct
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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    agent = WorkerAgent(args.url, detect_capability(), isolated=args.isolated)
    gate: IdleGate | AdaptiveGovernor | None = None
    usage_stop: threading.Event | None = None
    try:
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
