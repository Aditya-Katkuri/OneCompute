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
    """Size-bounded JSONL event log for job-mode diagnostics. Never raises."""

    def __init__(
        self,
        worker_id: str,
        path: str | os.PathLike[str] | None = None,
        enabled: bool = True,
        *,
        max_bytes: int = 2 * 1024 * 1024,
        backups: int = 2,
    ) -> None:
        self.worker_id = worker_id
        self.enabled = enabled
        self.path = Path(path) if path is not None else default_telemetry_path()
        self.max_bytes = max(1024, int(max_bytes))
        self.backups = max(0, min(10, int(backups)))
        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.enabled = False  # can't write here -> degrade to a no-op

    def _rotate_if_needed(self) -> None:
        try:
            if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
                return
            if self.backups <= 0:
                self.path.unlink(missing_ok=True)
                return
            self.path.with_name(f"{self.path.name}.{self.backups}").unlink(missing_ok=True)
            for index in range(self.backups - 1, 0, -1):
                source = self.path.with_name(f"{self.path.name}.{index}")
                if source.exists():
                    source.replace(self.path.with_name(f"{self.path.name}.{index + 1}"))
            self.path.replace(self.path.with_name(f"{self.path.name}.1"))
        except OSError:
            return

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
            self._rotate_if_needed()
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError:
            pass
