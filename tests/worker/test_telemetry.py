"""Tests for the local pilot telemetry logger (never raises, local JSONL)."""
from __future__ import annotations

import json

from worker.telemetry import PilotTelemetry


def test_appends_jsonl(tmp_path):
    path = tmp_path / "t.jsonl"
    t = PilotTelemetry("w1", path=path)
    t.log("tick", admitted=True, user_cpu=12.0)
    t.log("result", status="completed", units=5)
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["worker_id"] == "w1"
    assert rec["event"] == "tick"
    assert rec["admitted"] is True
    assert "ts" in rec


def test_disabled_is_noop(tmp_path):
    path = tmp_path / "t.jsonl"
    PilotTelemetry("w1", path=path, enabled=False).log("tick", x=1)
    assert not path.exists()


def test_never_raises_on_unwritable_path():
    # A bad drive/path must not raise: it degrades to a silent no-op.
    PilotTelemetry("w1", path="Z:/no/such/dir/t.jsonl").log("tick", x=1)
