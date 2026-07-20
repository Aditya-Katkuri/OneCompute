"""Tests for the measured business-case CLI and telemetry backfill."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "business_case_script",
    Path(__file__).resolve().parents[1] / "scripts" / "business_case.py",
)
business_case = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(business_case)


def _profile(path: Path) -> Path:
    bucket = {
        "n": 10,
        "cpu_mean": 20.0,
        "cpu_max": 30.0,
        "gpu_mean": 0.0,
        "gpu_max": 0.0,
        "ram_mean": 50.0,
        "ram_max": 60.0,
        "ac_mean": 50.0,
        "idle_mean": 40.0,
    }
    path.write_text(json.dumps({"buckets": [bucket]}), encoding="utf-8")
    return path


def _telemetry(path: Path) -> Path:
    start = datetime(2026, 7, 15, 18, 0, tzinfo=UTC)
    rows = [
        {"ts": start.isoformat(), "event": "measure"},
        {"ts": (start + timedelta(seconds=30)).isoformat(), "event": "measure"},
        {"ts": (start + timedelta(hours=8)).isoformat(), "event": "measure"},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_load_summary_auto_backfills_from_sibling_telemetry(tmp_path) -> None:
    profile = _profile(tmp_path / "usage_profile.json")
    _telemetry(tmp_path / "pilot-telemetry.jsonl")

    summary = business_case._load_summary(profile)

    assert summary is not None
    assert summary["availability"]["source"] == "telemetry"
    assert summary["availability"]["gap_count"] == 1
    assert summary["availability"]["unavailable_hours_per_day"] > 20.0


def test_cli_labels_wake_model_and_awake_only_comparison(tmp_path, capsys) -> None:
    profile = _profile(tmp_path / "usage_profile.json")
    _telemetry(tmp_path / "pilot-telemetry.jsonl")

    business_case.main(
        [
            str(profile),
            "--device-class",
            "laptop_assigned",
            "--count",
            "1",
        ]
    )
    wake_text = capsys.readouterr().out
    assert "wake-enabled mode" in wake_text
    assert "modeled potential, not currently executable capacity" in wake_text
    assert "wake " in wake_text
    assert "CURRENT WORKER awake-only value" in wake_text
    assert "WAKE-ENABLED modeled potential" in wake_text
    assert "timing confidence: preliminary" in wake_text
    assert "\u2014" not in wake_text

    business_case.main(
        [
            str(profile),
            "--device-class",
            "laptop_assigned",
            "--count",
            "1",
            "--awake-only",
        ]
    )
    awake_text = capsys.readouterr().out
    assert "awake-only mode" in awake_text
    assert "wake-enabled mode" not in awake_text
    assert "TOTAL currently executable awake-only value" in awake_text
    assert "\u2014" not in awake_text
