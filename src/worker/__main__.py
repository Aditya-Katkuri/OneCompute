"""Command-line entrypoint for the NightShift worker."""

from __future__ import annotations

import argparse
import logging
import time

from worker.agent import WorkerAgent
from worker.capability import detect_capability


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a NightShift worker agent")
    parser.add_argument("--url", required=True, help="Orchestrator base URL")
    parser.add_argument("--once", action="store_true", help="Run at most one job then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    agent = WorkerAgent(args.url, detect_capability())
    try:
        if args.once:
            rr = agent.run_once()
            print(rr.model_dump() if rr else "no work")
            return

        while True:
            rr = agent.run_once()
            if rr is None:
                print("idle")
            else:
                print(f"{rr.status} job={rr.job_id} units={rr.units}")
            time.sleep(agent.poll_interval_s or 1.5)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        agent.close()


if __name__ == "__main__":
    main()
