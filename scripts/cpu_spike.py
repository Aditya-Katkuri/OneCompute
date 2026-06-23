"""Briefly peg all CPU cores to test the governor's yield during Phase 0.

Run this in a SEPARATE process while a harvested job is running: because it is not a child of the
worker, the governor attributes it to the **employee's own demand** (user_cpu spikes), so a correct
governor yields the harvested job within a second and requeues it.

Usage:  uv run python scripts/cpu_spike.py [seconds]    (default 15)
"""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time


def _burn(deadline: float) -> None:
    while time.time() < deadline:
        pass


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0
    cores = os.cpu_count() or 4
    deadline = time.time() + seconds
    procs = [mp.Process(target=_burn, args=(deadline,)) for _ in range(cores)]
    print(f"spiking {cores} cores for {seconds:.0f}s (simulates the employee getting busy)...")
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    print("spike done")


if __name__ == "__main__":
    mp.freeze_support()
    main()
