"""Summarize a OneCompute worker's local pilot telemetry.

Usage:  uv run python scripts/pilot_report.py [path-to-pilot-telemetry.jsonl]

Reads the on-device telemetry written by the worker (default
``%LOCALAPPDATA%\\OneCompute\\pilot-telemetry.jsonl``) and prints a per-machine pilot summary:
ticks (admitted vs held off), the user demand the governor observed, and jobs completed/yielded.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _default_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "OneCompute" / "pilot-telemetry.jsonl"


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else _default_path()
    if not path.exists():
        print(f"no telemetry at {path}")
        return 0

    ticks = admitted = units = 0
    duration = 0.0
    results: dict[str, int] = {}
    user_cpus: list[float] = []
    worker_id = None

    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        worker_id = rec.get("worker_id", worker_id)
        event = rec.get("event")
        if event == "tick":
            ticks += 1
            if rec.get("admitted"):
                admitted += 1
            if isinstance(rec.get("user_cpu"), (int, float)):
                user_cpus.append(float(rec["user_cpu"]))
        elif event == "result":
            status = rec.get("status", "?")
            results[status] = results.get(status, 0) + 1
            units += int(rec.get("units", 0) or 0)
            duration += float(rec.get("duration_s", 0) or 0)

    pct = (100 * admitted // ticks) if ticks else 0
    print(f"== OneCompute pilot summary: {worker_id or 'unknown'} ==")
    print(f"  telemetry file : {path}")
    print(f"  ticks          : {ticks}  (admitted {admitted}, held {ticks - admitted}, {pct}% harvesting)")
    if user_cpus:
        avg = sum(user_cpus) / len(user_cpus)
        print(f"  user CPU seen  : min {min(user_cpus):.0f}%  avg {avg:.0f}%  max {max(user_cpus):.0f}%")
    print("  jobs           : " + (", ".join(f"{k}={v}" for k, v in sorted(results.items())) or "none"))
    print(f"  units total    : {units}   wall time in jobs: {duration:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
