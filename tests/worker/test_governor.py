"""Tests for the demand-adaptive governor: headroom admission + demand/saturation yield."""
from __future__ import annotations

from datetime import datetime

from worker.governor import AdaptiveGovernor
from worker.profiler import UsageProfiler

WHEN = datetime(2026, 6, 22, 9, 0)


class _FakeGate:
    def __init__(self, on_ac=True, locked=False, gpu_busy=False, active=False):
        self._on_ac, self._locked, self._gpu, self._active = on_ac, locked, gpu_busy, active

    def on_ac(self):
        return self._on_ac

    def locked(self):
        return self._locked

    def gpu_busy(self):
        return self._gpu

    def active_now(self):
        return self._active


def _gov(monkeypatch, tmp_path, user, gate, profile_cpu_mean=15.0):
    monkeypatch.setattr("worker.governor.system_gpu_load_pct", lambda: 0.0)
    monkeypatch.setattr("worker.governor.system_ram_load_pct", lambda: 0.0)
    prof = UsageProfiler(path=tmp_path / "p.json")
    for _ in range(30):
        prof.record(profile_cpu_mean, 0.0, 0.0, when=WHEN)
    g = AdaptiveGovernor(profiler=prof, idle_gate=gate)
    monkeypatch.setattr(g, "user_cpu", lambda: user)  # the EMPLOYEE's attributed demand
    return g


def test_admits_during_light_use(monkeypatch, tmp_path):
    # profiled mean 15 + margin 25 = 40 threshold; user demand 20 < 40, headroom 60 >= 15 -> admit.
    g = _gov(monkeypatch, tmp_path, user=20.0, gate=_FakeGate(), profile_cpu_mean=15.0)
    assert g.should_run(when=WHEN) is True


def test_holds_off_when_user_busy(monkeypatch, tmp_path):
    g = _gov(monkeypatch, tmp_path, user=85.0, gate=_FakeGate(), profile_cpu_mean=15.0)
    assert g.should_run(when=WHEN) is False  # user demand above the admission threshold


def test_no_headroom_blocks(monkeypatch, tmp_path):
    # profiled mean 80 -> headroom 100-80-25 = 0 < 15 -> blocked regardless of live demand.
    g = _gov(monkeypatch, tmp_path, user=10.0, gate=_FakeGate(), profile_cpu_mean=80.0)
    assert g.should_run(when=WHEN) is False


def test_requires_ac_and_unlocked(monkeypatch, tmp_path):
    g_batt = _gov(monkeypatch, tmp_path, 10.0, _FakeGate(on_ac=False))
    assert g_batt.should_run(when=WHEN) is False
    g_lock = _gov(monkeypatch, tmp_path, 10.0, _FakeGate(locked=True))
    assert g_lock.should_run(when=WHEN) is False


def test_gpu_busy_blocks(monkeypatch, tmp_path):
    g = _gov(monkeypatch, tmp_path, 10.0, _FakeGate(gpu_busy=True))
    assert g.should_run(when=WHEN) is False


def test_yields_when_user_demand_spikes(monkeypatch, tmp_path):
    # profiled mean 15 -> admission 40, yield threshold 40 + 10 hysteresis = 50; user 90 > 50.
    g = _gov(monkeypatch, tmp_path, user=90.0, gate=_FakeGate(), profile_cpu_mean=15.0)
    assert g.active_now(when=WHEN) is False  # sample 1 (needs 3 sustained)
    assert g.active_now(when=WHEN) is False  # sample 2
    assert g.active_now(when=WHEN) is True   # sample 3 -> sustained spike -> yield
    monkeypatch.setattr(g, "user_cpu", lambda: 20.0)  # employee demand falls back
    assert g.active_now(when=WHEN) is False  # resets


def test_does_not_yield_on_light_use(monkeypatch, tmp_path):
    # Employee at low CPU (25%, e.g. typing) below the yield threshold (50) -> never yields,
    # however many times we poll. This is the whole point: we run *while* they work.
    g = _gov(monkeypatch, tmp_path, user=25.0, gate=_FakeGate(), profile_cpu_mean=15.0)
    assert all(g.active_now(when=WHEN) is False for _ in range(10))


def test_active_now_never_records_into_profile(monkeypatch, tmp_path):
    # Invariant: yield polling during our job must not pollute the learned envelope.
    g = _gov(monkeypatch, tmp_path, user=95.0, gate=_FakeGate(), profile_cpu_mean=15.0)
    before = g.profiler.profile_now(when=WHEN).n
    for _ in range(5):
        g.active_now(when=WHEN)
    assert g.profiler.profile_now(when=WHEN).n == before


def test_headroom_and_thresholds(monkeypatch, tmp_path):
    g = _gov(monkeypatch, tmp_path, 10.0, _FakeGate(), profile_cpu_mean=30.0)
    assert abs(g.admission_threshold(when=WHEN) - 55.0) < 1.0  # 30 + 25 margin
    assert abs(g.yield_threshold(when=WHEN) - 65.0) < 1.0      # admission 55 + 10 hysteresis
    assert abs(g.headroom_now(when=WHEN) - 45.0) < 1.0         # 100 - 30 - 25
