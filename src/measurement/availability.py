"""Persistent observer-availability accounting for measurement pilots.

The measurement worker cannot sample while Windows is suspended, powered off, rebooting, or while
the observer itself is stopped. A long interval between successful samples therefore proves only an
unobserved gap, not its exact cause. This module records those gaps without storing a detailed
activity timeline and turns them into average observed and unavailable hours per day.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import fmean

DEFAULT_EXPECTED_INTERVAL_SECONDS = 30.0


def _nonnegative_finite(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return max(0.0, number)


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return 0


@dataclass
class AvailabilityTracker:
    """Compact timing state persisted beside the rolling utilization buckets."""

    first_sample_at: float = 0.0
    last_sample_at: float = 0.0
    observed_seconds: float = 0.0
    unavailable_seconds: float = 0.0
    gap_count: int = 0
    sample_count: int = 0
    expected_interval_seconds: float = DEFAULT_EXPECTED_INTERVAL_SECONDS

    @classmethod
    def from_dict(cls, raw: object) -> AvailabilityTracker:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            first_sample_at=_nonnegative_finite(raw.get("first_sample_at")),
            last_sample_at=_nonnegative_finite(raw.get("last_sample_at")),
            observed_seconds=_nonnegative_finite(raw.get("observed_seconds")),
            unavailable_seconds=_nonnegative_finite(raw.get("unavailable_seconds")),
            gap_count=_nonnegative_int(raw.get("gap_count")),
            sample_count=_nonnegative_int(raw.get("sample_count")),
            expected_interval_seconds=max(
                0.001,
                _nonnegative_finite(
                    raw.get("expected_interval_seconds"),
                    DEFAULT_EXPECTED_INTERVAL_SECONDS,
                ),
            ),
        )

    @property
    def span_seconds(self) -> float:
        return self.observed_seconds + self.unavailable_seconds

    def record(
        self,
        sampled_at: float,
        expected_interval_seconds: float = DEFAULT_EXPECTED_INTERVAL_SECONDS,
    ) -> None:
        """Record one successful sample and classify a long elapsed interval as unavailable."""
        timestamp = _nonnegative_finite(sampled_at)
        if timestamp <= 0.0:
            return
        self.sample_count += 1
        expected = max(
            0.001,
            _nonnegative_finite(
                expected_interval_seconds,
                DEFAULT_EXPECTED_INTERVAL_SECONDS,
            ),
        )
        self.expected_interval_seconds = expected
        if self.last_sample_at <= 0.0:
            self.first_sample_at = timestamp
            self.last_sample_at = timestamp
            return

        elapsed = timestamp - self.last_sample_at
        self.last_sample_at = timestamp
        if elapsed <= 0.0:
            return

        gap_threshold = max(3.0 * expected, expected + 30.0)
        if elapsed > gap_threshold:
            observed = min(elapsed, expected)
            self.observed_seconds += observed
            self.unavailable_seconds += elapsed - observed
            self.gap_count += 1
        else:
            self.observed_seconds += elapsed

    def to_dict(self) -> dict:
        return {
            "first_sample_at": self.first_sample_at,
            "last_sample_at": self.last_sample_at,
            "observed_seconds": self.observed_seconds,
            "unavailable_seconds": self.unavailable_seconds,
            "gap_count": self.gap_count,
            "sample_count": self.sample_count,
            "expected_interval_seconds": self.expected_interval_seconds,
        }


def summarize_availability(raw: object, *, source: str = "profile") -> dict:
    """Normalize tracker state into a privacy-preserving average daily availability summary."""
    tracker = raw if isinstance(raw, AvailabilityTracker) else AvailabilityTracker.from_dict(raw)
    span = tracker.span_seconds
    if span <= 0.0:
        return {
            "source": source,
            "span_seconds": 0.0,
            "observed_seconds": 0.0,
            "unavailable_seconds": 0.0,
            "observed_hours_per_day": 0.0,
            "unavailable_hours_per_day": 0.0,
            "observed_pct": 0.0,
            "unavailable_pct": 0.0,
            "gap_count": tracker.gap_count,
            "sample_count": tracker.sample_count,
        }
    observed_fraction = tracker.observed_seconds / span
    unavailable_fraction = tracker.unavailable_seconds / span
    return {
        "source": source,
        "span_seconds": span,
        "observed_seconds": tracker.observed_seconds,
        "unavailable_seconds": tracker.unavailable_seconds,
        "observed_hours_per_day": 24.0 * observed_fraction,
        "unavailable_hours_per_day": 24.0 * unavailable_fraction,
        "observed_pct": 100.0 * observed_fraction,
        "unavailable_pct": 100.0 * unavailable_fraction,
        "gap_count": tracker.gap_count,
        "sample_count": tracker.sample_count,
    }


def aggregate_availability(summaries: Iterable[dict]) -> dict:
    """Average normalized availability across contributing devices."""
    valid = [summary for summary in summaries if summary.get("span_seconds", 0.0) > 0.0]
    if not valid:
        empty = summarize_availability(None, source="aggregate")
        empty["device_count"] = 0
        empty["total_gap_count"] = 0
        empty["total_sample_count"] = 0
        return empty
    return {
        "source": "aggregate",
        "device_count": len(valid),
        "span_seconds": fmean(s["span_seconds"] for s in valid),
        "observed_seconds": fmean(s["observed_seconds"] for s in valid),
        "unavailable_seconds": fmean(s["unavailable_seconds"] for s in valid),
        "observed_hours_per_day": fmean(s["observed_hours_per_day"] for s in valid),
        "unavailable_hours_per_day": fmean(s["unavailable_hours_per_day"] for s in valid),
        "observed_pct": fmean(s["observed_pct"] for s in valid),
        "unavailable_pct": fmean(s["unavailable_pct"] for s in valid),
        "gap_count": round(fmean(s["gap_count"] for s in valid), 1),
        "sample_count": round(fmean(s.get("sample_count", 0) for s in valid), 1),
        "total_gap_count": sum(int(s["gap_count"]) for s in valid),
        "total_sample_count": sum(int(s.get("sample_count", 0)) for s in valid),
    }


def availability_from_timestamps(
    timestamps: Iterable[float],
    *,
    expected_interval_seconds: float = DEFAULT_EXPECTED_INTERVAL_SECONDS,
) -> dict:
    """Infer availability from sample timestamps, sorting and ignoring invalid values."""
    tracker = tracker_from_timestamps(
        timestamps,
        expected_interval_seconds=expected_interval_seconds,
    )
    return summarize_availability(tracker, source="telemetry")


def tracker_from_timestamps(
    timestamps: Iterable[float],
    *,
    expected_interval_seconds: float = DEFAULT_EXPECTED_INTERVAL_SECONDS,
) -> AvailabilityTracker:
    """Build restartable tracker state from sample timestamps."""
    valid = sorted(
        timestamp
        for value in timestamps
        if (timestamp := _nonnegative_finite(value)) > 0.0
    )
    tracker = AvailabilityTracker()
    for timestamp in valid:
        tracker.record(timestamp, expected_interval_seconds)
    return tracker


def availability_tracker_from_telemetry(
    path: str | Path,
    *,
    expected_interval_seconds: float = DEFAULT_EXPECTED_INTERVAL_SECONDS,
) -> AvailabilityTracker:
    """Build restartable tracker state from a local measure-event JSONL file."""
    timestamps: list[float] = []
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                    if record.get("event") != "measure":
                        continue
                    text = str(record.get("ts", "")).replace("Z", "+00:00")
                    timestamps.append(datetime.fromisoformat(text).timestamp())
                except (ValueError, TypeError, AttributeError):
                    continue
    except (OSError, UnicodeDecodeError):
        return AvailabilityTracker()
    return tracker_from_timestamps(
        timestamps,
        expected_interval_seconds=expected_interval_seconds,
    )


def infer_telemetry_availability(
    path: str | Path,
    *,
    expected_interval_seconds: float = DEFAULT_EXPECTED_INTERVAL_SECONDS,
) -> dict:
    """Infer availability from local measure-event JSONL without exposing its timestamps."""
    tracker = availability_tracker_from_telemetry(
        path,
        expected_interval_seconds=expected_interval_seconds,
    )
    return summarize_availability(tracker, source="telemetry")
