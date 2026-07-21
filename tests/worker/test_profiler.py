"""Tests for the rolling usage profiler (the 'learn the envelope' half of the governor)."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from worker.profiler import BUCKETS, SystemCpuSampler, UsageProfiler, bucket_index


def test_bucket_index_properties():
    d = datetime(2026, 6, 22, 9, 0)
    assert bucket_index(d) == (d.weekday() * 24 + d.hour) % BUCKETS
    # Same hour-of-week 7 days later -> same bucket.
    assert bucket_index(d) == bucket_index(d + timedelta(days=7))
    # One hour later -> next bucket (mod 168).
    assert bucket_index(d + timedelta(hours=1)) == (bucket_index(d) + 1) % BUCKETS


def test_record_learns_envelope(tmp_path):
    p = UsageProfiler(path=tmp_path / "prof.json")
    when = datetime(2026, 6, 22, 9, 0)
    for _ in range(50):
        p.record(cpu=30.0, gpu=0.0, ram=40.0, when=when)
    p.record(cpu=95.0, gpu=10.0, ram=70.0, when=when)  # a peak
    stat = p.profile_now(when)
    assert 25.0 <= stat.cpu_mean <= 40.0  # mean tracks the ~30% typical
    assert stat.cpu_max >= 90.0           # peak captured
    assert stat.n == 51


def test_persistence_roundtrip(tmp_path):
    path = tmp_path / "prof.json"
    when = datetime(2026, 6, 22, 9, 0)
    p1 = UsageProfiler(path=path)
    for _ in range(10):
        p1.record(50.0, 5.0, 60.0, when=when)
    p1.gpu_supported = True
    assert p1.save() is True
    p2 = UsageProfiler(path=path)  # reloads from disk
    assert p2.profile_now(when).n == 10
    assert abs(p2.profile_now(when).cpu_mean - p1.profile_now(when).cpu_mean) < 1e-6
    assert p2.gpu_supported is True


def test_availability_persistence_roundtrip_tracks_restart_gap(tmp_path):
    path = tmp_path / "prof.json"
    p1 = UsageProfiler(path=path)
    p1.record_availability(1_000.0, 30.0)
    p1.record_availability(1_030.0, 30.0)
    assert p1.save() is True

    p2 = UsageProfiler(path=path)
    p2.record_availability(4_630.0, 30.0)

    assert p2.availability.observed_seconds == 60.0
    assert p2.availability.unavailable_seconds == 3_570.0
    assert p2.availability.gap_count == 1


def test_record_folds_ac_and_idle_indicators(tmp_path):
    # on_ac/idle are 0/1 indicators folded as percentages, so their bucket means become the % of
    # time on AC and the % of time idle (the harvestable-window signals).
    p = UsageProfiler(path=tmp_path / "p.json")
    when = datetime(2026, 6, 22, 9, 0)
    for _ in range(20):
        p.record(10.0, 0.0, 40.0, when=when, on_ac=True, idle=True)
    b = p.profile_now(when)
    assert b.ac_mean == 100.0
    assert b.idle_mean == 100.0


def test_record_without_ac_idle_leaves_them_at_default(tmp_path):
    p = UsageProfiler(path=tmp_path / "p.json")
    when = datetime(2026, 6, 22, 9, 0)
    p.record(10.0, 0.0, 40.0, when=when)  # no on_ac/idle passed
    b = p.profile_now(when)
    assert b.ac_mean == 0.0 and b.idle_mean == 0.0
    assert b.ac_n == 0 and b.idle_n == 0


def test_unknown_power_and_idle_samples_do_not_bias_later_valid_values(tmp_path):
    profiler = UsageProfiler(path=tmp_path / "p.json")
    when = datetime(2026, 6, 22, 9, 0)
    for _ in range(20):
        profiler.record(10.0, 0.0, 40.0, when=when, on_ac=None, idle=None)

    profiler.record(10.0, 0.0, 40.0, when=when, on_ac=True, idle=False)
    bucket = profiler.profile_now(when)

    assert bucket.n == 21
    assert bucket.ac_n == 1
    assert bucket.idle_n == 1
    assert bucket.ac_mean == 100.0
    assert bucket.idle_mean == 0.0


def test_load_tolerates_old_profile_missing_new_fields(tmp_path):
    # A pre-upgrade profile has no ac_mean/idle_mean (and may carry an unknown legacy key): loading
    # must default the new fields and drop the unknown key rather than raise.
    import json

    path = tmp_path / "old.json"
    path.write_text(
        json.dumps({"buckets": [{"n": 3, "cpu_mean": 20.0, "legacy_field": 1}]}),
        encoding="utf-8",
    )
    p = UsageProfiler(path=path)
    b = p.buckets[0]
    assert b.n == 3 and b.cpu_mean == 20.0
    assert b.ac_mean == 0.0 and b.idle_mean == 0.0
    assert b.ac_n == 0 and b.idle_n == 0
    assert p.availability.span_seconds == 0.0


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path, caplog):
    # A hard crash mid-save must never leave a half-written profile: save writes a temp file then
    # atomically swaps it in, so the final file is always complete and no .tmp lingers.
    import json

    path = tmp_path / "prof.json"
    when = datetime(2026, 6, 22, 9, 0)
    p = UsageProfiler(path=path)
    for _ in range(5):
        p.record(30.0, 0.0, 40.0, when=when)
    assert p.save() is True
    assert path.exists()
    assert not list(tmp_path.glob(".*.tmp"))
    data = json.loads(path.read_text(encoding="utf-8"))  # complete, valid JSON
    assert isinstance(data["buckets"], list)
    assert isinstance(data["availability"], dict)
    assert "could not remove usage profile temporary file" not in caplog.text


def test_save_reports_a_persistence_failure(tmp_path):
    parent = tmp_path / "not-a-directory"
    profiler = UsageProfiler(path=parent / "prof.json")
    parent.write_text("blocked", encoding="utf-8")

    assert profiler.save() is False
    assert profiler.last_save_error


def test_successful_save_is_not_reversed_by_a_temp_cleanup_race(tmp_path, monkeypatch):
    path = tmp_path / "prof.json"
    profiler = UsageProfiler(path=path)
    original_unlink = Path.unlink

    def locked_temp(candidate, *args, **kwargs):
        if candidate.name.startswith(".prof.json."):
            raise PermissionError("scanner still holds the temporary path")
        return original_unlink(candidate, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_temp)

    assert profiler.save() is True
    assert path.exists()


def test_load_preserves_a_prior_corrupt_profile_before_starting_fresh(tmp_path):
    import json

    path = tmp_path / "prof.json"
    original = "{ this is not valid json"
    path.write_text(original, encoding="utf-8")
    p = UsageProfiler(path=path)
    p.record(30.0, 0.0, 40.0)
    assert p.load_warning
    assert p.recovered_profile_path is not None
    assert p.recovered_profile_path.read_text(encoding="utf-8") == original
    assert p.save() is True
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data["buckets"], list)


def test_failed_corrupt_profile_recovery_blocks_overwrite(tmp_path, monkeypatch):
    import os

    path = tmp_path / "prof.json"
    original = "{ this is not valid json"
    path.write_text(original, encoding="utf-8")

    def refuse_recovery(_source, _destination):
        raise PermissionError("profile is held open")

    monkeypatch.setattr(os, "replace", refuse_recovery)
    profiler = UsageProfiler(path=path)

    assert profiler.recovery_blocked is True
    with pytest.raises(OSError, match="could not be preserved"):
        profiler.assert_writable()
    assert profiler.save() is False
    assert path.read_text(encoding="utf-8") == original


def test_load_normalizes_valid_json_with_invalid_bucket_values(tmp_path):
    import json

    path = tmp_path / "prof.json"
    path.write_text(
        json.dumps(
            {
                "buckets": [
                    {
                        "n": "4",
                        "cpu_mean": 150,
                        "cpu_max": -10,
                        "cpu_min": "bad",
                        "gpu_mean": float("nan"),
                        "ram_mean": 45,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    profiler = UsageProfiler(path=path)
    bucket = profiler.buckets[0]

    assert bucket.n == 4
    assert bucket.cpu_mean == 100.0
    assert 0.0 <= bucket.cpu_min <= bucket.cpu_mean <= bucket.cpu_max <= 100.0
    assert bucket.gpu_mean == 0.0


def test_load_bounds_hostile_availability_values(tmp_path):
    import json

    path = tmp_path / "prof.json"
    path.write_text(
        json.dumps(
            {
                "buckets": [],
                "availability": {
                    "first_sample_at": 1e100,
                    "last_sample_at": 1e100,
                    "observed_seconds": 1e100,
                    "unavailable_seconds": 1e100,
                    "gap_count": 10**20,
                    "sample_count": 10**20,
                    "expected_interval_seconds": 1e100,
                },
            }
        ),
        encoding="utf-8",
    )

    availability = UsageProfiler(path=path).availability

    assert availability.first_sample_at <= 4_102_444_800.0
    assert availability.last_sample_at <= 4_102_444_800.0
    assert availability.observed_seconds <= 400 * 24 * 3600
    assert availability.unavailable_seconds <= 400 * 24 * 3600
    assert availability.gap_count <= 10_000_000
    assert availability.sample_count <= 10_000_000
    assert availability.expected_interval_seconds <= 3600.0


def test_assert_writable_rejects_directory_profile_path(tmp_path):
    path = tmp_path / "profile"
    path.mkdir()

    profiler = UsageProfiler(path=path)

    with pytest.raises(OSError):
        profiler.assert_writable()


def test_stale_bucket_resets(tmp_path):
    p = UsageProfiler(path=tmp_path / "prof.json")
    old = datetime(2026, 1, 1, 9, 0)
    for _ in range(20):
        p.record(80.0, 0.0, 0.0, when=old)
    assert p.profile_now(old).n == 20
    later = old + timedelta(days=42)  # same hour-of-week bucket, >35 days later -> stale
    p.record(10.0, 0.0, 0.0, when=later)
    stat = p.profile_now(later)
    assert stat.n == 1                       # reset, then this one sample
    assert abs(stat.cpu_mean - 10.0) < 1e-6


def test_system_cpu_sampler_range():
    s = SystemCpuSampler()
    assert s.sample() is None  # first call primes the baseline
    v = s.sample()
    assert v is None or (0.0 <= v <= 100.0)
