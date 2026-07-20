"""Tests for persistent observer availability and telemetry gap inference."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from measurement.availability import (
    AvailabilityTracker,
    aggregate_availability,
    availability_tracker_from_telemetry,
    infer_telemetry_availability,
    summarize_availability,
)


def test_tracker_separates_normal_sampling_from_a_long_gap() -> None:
    tracker = AvailabilityTracker()
    for timestamp in (1_000.0, 1_030.0, 1_060.0, 4_660.0):
        tracker.record(timestamp, expected_interval_seconds=30.0)

    assert tracker.observed_seconds == pytest.approx(90.0)
    assert tracker.unavailable_seconds == pytest.approx(3_570.0)
    assert tracker.gap_count == 1
    assert tracker.sample_count == 4
    summary = summarize_availability(tracker)
    assert summary["observed_hours_per_day"] + summary["unavailable_hours_per_day"] == pytest.approx(24.0)
    assert summary["unavailable_pct"] == pytest.approx(100.0 * 3_570.0 / 3_660.0)


def test_tracker_roundtrip_continues_across_a_restart() -> None:
    first = AvailabilityTracker()
    first.record(1_000.0, 30.0)
    first.record(1_030.0, 30.0)

    restored = AvailabilityTracker.from_dict(first.to_dict())
    restored.record(4_630.0, 30.0)

    assert restored.observed_seconds == pytest.approx(60.0)
    assert restored.unavailable_seconds == pytest.approx(3_570.0)
    assert restored.gap_count == 1
    assert restored.sample_count == 3


def test_telemetry_inference_ignores_bad_and_non_measure_rows(tmp_path) -> None:
    start = datetime(2026, 7, 15, 18, 0, tzinfo=UTC)
    rows = [
        {"ts": (start + timedelta(seconds=30)).isoformat(), "event": "measure"},
        {"ts": start.isoformat(), "event": "measure"},
        {"ts": (start + timedelta(hours=1)).isoformat(), "event": "measure"},
        {"ts": (start + timedelta(hours=2)).isoformat(), "event": "heartbeat"},
    ]
    path = tmp_path / "pilot-telemetry.jsonl"
    path.write_text(
        "\n".join([json.dumps(row) for row in rows] + ["{bad json"]) + "\n",
        encoding="utf-8",
    )

    summary = infer_telemetry_availability(path)

    assert summary["source"] == "telemetry"
    assert summary["gap_count"] == 1
    assert summary["span_seconds"] == pytest.approx(3_600.0)
    assert summary["observed_seconds"] == pytest.approx(60.0)
    assert summary["unavailable_seconds"] == pytest.approx(3_540.0)
    assert summary["sample_count"] == 3
    tracker = availability_tracker_from_telemetry(path)
    assert tracker.first_sample_at > 0.0
    assert tracker.last_sample_at > tracker.first_sample_at
    assert tracker.gap_count == 1


def test_availability_aggregate_is_per_average_device() -> None:
    first = summarize_availability(
        AvailabilityTracker(observed_seconds=8 * 3600, unavailable_seconds=16 * 3600, gap_count=2)
    )
    second = summarize_availability(
        AvailabilityTracker(observed_seconds=12 * 3600, unavailable_seconds=12 * 3600, gap_count=4)
    )

    aggregate = aggregate_availability([first, second])

    assert aggregate["device_count"] == 2
    assert aggregate["observed_hours_per_day"] == pytest.approx(10.0)
    assert aggregate["unavailable_hours_per_day"] == pytest.approx(14.0)
    assert aggregate["gap_count"] == pytest.approx(3.0)
    assert aggregate["total_gap_count"] == 6
