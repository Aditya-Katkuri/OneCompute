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


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path):
    # A hard crash mid-save must never leave a half-written profile: save writes a temp file then
    # atomically swaps it in, so the final file is always complete and no .tmp lingers.
    import json

    path = tmp_path / "prof.json"
    when = datetime(2026, 6, 22, 9, 0)
    p = UsageProfiler(path=path)
    for _ in range(5):
        p.record(30.0, 0.0, 40.0, when=when)
    p.save()
    assert path.exists()
    assert not (tmp_path / "prof.json.tmp").exists()
    data = json.loads(path.read_text(encoding="utf-8"))  # complete, valid JSON
    assert isinstance(data["buckets"], list)


def test_save_replaces_a_prior_corrupt_profile_cleanly(tmp_path):
    # Even if a previous run left a corrupt file, load tolerates it (starts fresh) and the next
    # atomic save replaces it with a valid profile -- a week is never stuck behind one bad write.
    import json

    path = tmp_path / "prof.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    p = UsageProfiler(path=path)
    p.record(30.0, 0.0, 40.0)
    p.save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data["buckets"], list)


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
