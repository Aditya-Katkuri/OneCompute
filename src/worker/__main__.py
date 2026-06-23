"""Command-line entrypoint for the NightShift worker."""

from __future__ import annotations

import argparse
import logging
import time

from worker.agent import WorkerAgent
from worker.capability import detect_capability
from worker.idle import IdleGate


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a NightShift worker agent")
    parser.add_argument("--url", required=True, help="Orchestrator base URL")
    parser.add_argument("--once", action="store_true", help="Run at most one job then exit")
    parser.add_argument("--idle-threshold", type=float, default=60.0, help="Input idle seconds before work")
    parser.add_argument(
        "--gate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use the idle gate in the continuous loop",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    agent = WorkerAgent(args.url, detect_capability())
    try:
        if args.once:
            rr = agent.run_once()
            print(rr.model_dump() if rr else "no work")
            return

        gate = IdleGate(input_idle_threshold_s=args.idle_threshold)
        while True:
            if args.gate and not gate.should_run():
                print("skip: not idle")
                time.sleep(agent.poll_interval_s or 1.5)
                continue
            rr = agent.run_guarded(gate) if args.gate else agent.run_once()
            if rr is None:
                print("idle: no work")
            elif rr.status == "yielded":
                print(f"yielded job={rr.job_id} units={rr.units}")
            else:
                print(f"{rr.status} job={rr.job_id} units={rr.units}")
            time.sleep(agent.poll_interval_s or 1.5)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        agent.close()


if __name__ == "__main__":
    main()
