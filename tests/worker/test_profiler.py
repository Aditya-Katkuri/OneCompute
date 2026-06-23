"""Tests for the rolling usage profiler (the 'learn the envelope' half of the governor)."""
from __future__ import annotations

from datetime import datetime, timedelta

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
    p1.save()
    p2 = UsageProfiler(path=path)  # reloads from disk
    assert p2.profile_now(when).n == 10
    assert abs(p2.profile_now(when).cpu_mean - p1.profile_now(when).cpu_mean) < 1e-6


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
