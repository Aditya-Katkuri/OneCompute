"""Lightweight, local-only pilot telemetry for the OneCompute worker.

Appends one JSON line per event to a local file (never uploaded), so a pilot lead can later
summarize a machine's behaviour: how often the governor admitted vs held off, the user demand it
saw, jobs completed/yielded, units, and durations. On-device only (idea.md §8); every call is
guarded so it can never break the worker.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


def default_telemetry_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "OneCompute" / "pilot-telemetry.jsonl"


class PilotTelemetry:
    """Append-only JSONL event log, local to the machine. Never raises."""

    def __init__(self, worker_id: str, path: str | os.PathLike[str] | None = None,
                 enabled: bool = True) -> None:
        self.worker_id = worker_id
        self.enabled = enabled
        self.path = Path(path) if path is not None else default_telemetry_path()
        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                self.enabled = False  # can't write here -> degrade to a no-op

    def log(self, event: str, **fields) -> None:
        if not self.enabled:
            return
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "worker_id": self.worker_id,
            "event": event,
            **fields,
        }
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        except Exception:
            pass
