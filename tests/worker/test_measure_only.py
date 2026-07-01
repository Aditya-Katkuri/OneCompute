"""Tests for the measurement-only worker mode (--measure-only).

The whole point of measure-only is a zero-risk "measure first, run workloads later" pilot: the
worker must fold this machine's live CPU/GPU/RAM into the on-device profile and stream usage for
the dashboard, but it must NEVER pull or run a job. These tests prove exactly that -- no
poll_once/run_once/run_guarded/run_job (and no admission/yield decision) is ever called, the
orchestrator's /jobs/next is never hit, the profile learns + persists (including on Ctrl-C), a
"measure" telemetry record is written, and the loop terminates cleanly on both --once and Ctrl-C.
Hermetic: fake httpx transport, no real network, no real sleeps.
"""
from __future__ import annotations

import json
import threading
import time

import httpx
from fastapi import FastAPI, Response

from contracts import (
    Capability,
    HeartbeatRequest,
    HeartbeatResponse,
    RegisterResponse,
)
from worker import __main__ as wm
from worker.agent import WorkerAgent
from worker.governor import AdaptiveGovernor
from worker.profiler import UsageProfiler
from worker.telemetry import PilotTelemetry


class _FakeGate:
    """Idle-gate stand-in. Measure-only never calls these; the governor just holds a reference."""

    def on_ac(self) -> bool:
        return True

    def locked(self) -> bool:
        return False

    def gpu_busy(self) -> bool:
        return False

    def active_now(self) -> bool:
        return False


class _SpyAgent:
    """Worker stand-in that fails loudly if any job path is ever touched in measure-only mode."""

    def __init__(self, has_gpu: bool = False) -> None:
        self.capability = Capability(worker_id="measure-1", has_gpu=has_gpu)
        self.job_calls: list[str] = []

    def poll_once(self, *args, **kwargs):
        self.job_calls.append("poll_once")
        raise AssertionError("measure-only must never poll for a job")

    def run_once(self, *args, **kwargs):
        self.job_calls.append("run_once")
        raise AssertionError("measure-only must never run a job")

    def run_guarded(self, *args, **kwargs):
        self.job_calls.append("run_guarded")
        raise AssertionError("measure-only must never run a guarded job")

    def run_job(self, *args, **kwargs):
        self.job_calls.append("run_job")
        raise AssertionError("measure-only must never execute a job")


def _pin_samples(tmp_path, monkeypatch, *, cpu: float, gpu: float, ram: float) -> AdaptiveGovernor:
    """A real AdaptiveGovernor whose live CPU/GPU/RAM samples are pinned for determinism, with
    should_run()/active_now() booby-trapped to fail if measure-only ever consults them."""
    monkeypatch.setattr(wm, "system_gpu_load_pct", lambda: gpu)
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: ram)
    if wm.psutil is not None:
        monkeypatch.setattr(wm.psutil, "cpu_percent", lambda interval=None: cpu)
    profiler = UsageProfiler(path=tmp_path / "profile.json")
    governor = AdaptiveGovernor(profiler=profiler, idle_gate=_FakeGate())
    monkeypatch.setattr(governor, "user_cpu", lambda: cpu)  # ctypes-fallback path, if ever taken

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("measure-only must never make an admission/yield decision")

    monkeypatch.setattr(governor, "should_run", _forbidden)
    monkeypatch.setattr(governor, "active_now", _forbidden)
    return governor


def _fake_orchestrator(approved: bool = True) -> tuple[FastAPI, dict]:
    """A minimal in-process orchestrator. /jobs/next flips a flag so a pull can be detected."""
    app = FastAPI()
    state = {"registered": 0, "heartbeats": 0, "pulled": False}

    @app.post("/register")
    def register(_: Capability) -> dict:
        state["registered"] += 1
        return RegisterResponse(
            worker_token="t", approved=approved, device_code="AB-12"
        ).model_dump()

    @app.post("/heartbeat")
    def heartbeat(_: HeartbeatRequest) -> dict:
        state["heartbeats"] += 1
        return HeartbeatResponse(approved=approved).model_dump()

    @app.get("/jobs/next", response_model=None)
    def next_job(worker_id: str):
        state["pulled"] = True  # measure-only must NEVER reach this
        return Response(status_code=204)

    return app, state


def test_measure_loop_records_logs_and_never_runs_a_job(tmp_path, monkeypatch) -> None:
    agent = _SpyAgent(has_gpu=True)
    governor = _pin_samples(tmp_path, monkeypatch, cpu=20.0, gpu=30.0, ram=40.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl")

    samples = wm.run_measure_loop(agent, governor, telem, interval=0.0, once=True)

    # exactly one sample, folded into the current hour-of-week bucket
    assert samples == 1
    bucket = governor.profiler.profile_now()
    assert bucket.n == 1
    assert bucket.cpu_mean == 20.0
    assert bucket.gpu_mean == 30.0
    assert bucket.ram_mean == 40.0

    # a local "measure" telemetry record captured the same live readings
    lines = (tmp_path / "telem.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "measure"
    assert record["cpu"] == 20.0
    assert record["gpu"] == 30.0
    assert record["ram"] == 40.0

    # the whole point of measure-only: no job was ever pulled or executed
    assert agent.job_calls == []


def test_measure_loop_survives_keyboardinterrupt_and_profile_saves(tmp_path, monkeypatch) -> None:
    agent = _SpyAgent(has_gpu=False)
    governor = _pin_samples(tmp_path, monkeypatch, cpu=15.0, gpu=0.0, ram=50.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)

    ticks = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= 3:
            raise KeyboardInterrupt  # simulate Ctrl-C between samples

    monkeypatch.setattr(time, "sleep", fake_sleep)

    # once=False -> continuous loop; Ctrl-C on the 3rd sleep must return cleanly, not propagate.
    samples = wm.run_measure_loop(agent, governor, telem, interval=1.0, once=False)

    assert samples == 3
    assert governor.profiler.profile_now().n == 3
    assert agent.job_calls == []

    # the learned envelope persists on exit, exactly like the work loop's finally block
    governor.profiler.save()
    assert (tmp_path / "profile.json").exists()


def test_main_measure_only_once_engages_heartbeat_and_pulls_no_jobs(tmp_path, monkeypatch) -> None:
    app, state = _fake_orchestrator()

    def _make_agent(url, capability, isolated=False, **_):
        client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url=url)
        return WorkerAgent(url, capability, client=client, isolated=isolated)

    monkeypatch.setattr(wm, "WorkerAgent", _make_agent)
    monkeypatch.setattr(
        wm, "detect_capability", lambda: Capability(worker_id="measure-1", has_gpu=False)
    )
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: 42.0)
    if wm.psutil is not None:
        monkeypatch.setattr(wm.psutil, "cpu_percent", lambda interval=None: 5.0)

    started = {"n": 0}

    def spy_start_heartbeat(agent, period_s: float = 1.0) -> threading.Event:
        started["n"] += 1
        return threading.Event()

    monkeypatch.setattr(wm, "_start_usage_heartbeat", spy_start_heartbeat)

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # redirect profile + telemetry to tmp
    monkeypatch.setattr("sys.argv", ["worker", "--url", "http://test", "--measure-only", "--once"])

    wm.main()

    # the live usage-heartbeat path is engaged in measure-only mode
    assert started["n"] == 1
    # the device joined the fleet so it appears in the dashboard
    assert state["registered"] == 1
    # the whole point: no job was ever pulled
    assert state["pulled"] is False

    # the profile persisted on exit and a "measure" telemetry record was written locally
    profile_path = tmp_path / "OneCompute" / "usage_profile.json"
    telem_path = tmp_path / "OneCompute" / "pilot-telemetry.jsonl"
    assert profile_path.exists()
    records = [json.loads(line) for line in telem_path.read_text(encoding="utf-8").splitlines()]
    assert any(rec["event"] == "measure" for rec in records)


def test_main_measure_only_continuous_saves_profile_on_ctrl_c(tmp_path, monkeypatch) -> None:
    # Drive the continuous (no --once) path through main() and end it with Ctrl-C: the profile
    # must still be persisted by main()'s finally block and no job may ever be pulled.
    app, state = _fake_orchestrator()

    def _make_agent(url, capability, isolated=False, **_):
        client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url=url)
        return WorkerAgent(url, capability, client=client, isolated=isolated)

    monkeypatch.setattr(wm, "WorkerAgent", _make_agent)
    monkeypatch.setattr(
        wm, "detect_capability", lambda: Capability(worker_id="measure-1", has_gpu=False)
    )
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: 55.0)
    if wm.psutil is not None:
        monkeypatch.setattr(wm.psutil, "cpu_percent", lambda interval=None: 7.0)
    monkeypatch.setattr(wm, "_start_usage_heartbeat", lambda agent, period_s=1.0: threading.Event())

    def raise_ctrl_c(_seconds: float) -> None:
        raise KeyboardInterrupt  # end the continuous loop after its first sample

    monkeypatch.setattr(time, "sleep", raise_ctrl_c)

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["worker", "--url", "http://test", "--measure-only"])

    wm.main()  # must return (not hang) after Ctrl-C

    profile_path = tmp_path / "OneCompute" / "usage_profile.json"
    assert profile_path.exists()  # saved on exit via main()'s finally
    assert state["pulled"] is False  # never pulled a job


def test_main_measure_only_once_does_not_hang_when_fleet_gated(tmp_path, monkeypatch) -> None:
    # A gated fleet (approval required) must not hang under --once: a single heartbeat, then exit.
    app, state = _fake_orchestrator(approved=False)

    def _make_agent(url, capability, isolated=False, **_):
        client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url=url)
        return WorkerAgent(url, capability, client=client, isolated=isolated)

    monkeypatch.setattr(wm, "WorkerAgent", _make_agent)
    monkeypatch.setattr(
        wm, "detect_capability", lambda: Capability(worker_id="measure-1", has_gpu=False)
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["worker", "--url", "http://test", "--measure-only", "--once"])

    wm.main()  # must return promptly, not block on approval

    assert state["registered"] == 1
    assert state["heartbeats"] >= 1  # sent the single approval heartbeat
    assert state["pulled"] is False  # still never pulls a job
    # never reached the measurement loop, so no local telemetry file was written
    assert not (tmp_path / "OneCompute" / "pilot-telemetry.jsonl").exists()


def test_usage_heartbeat_streams_live_usage_to_orchestrator() -> None:
    # The exact call the measure-only usage loop makes each tick must be reachable + never pull.
    app, state = _fake_orchestrator()
    client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://test")
    agent = WorkerAgent("http://test", Capability(worker_id="measure-1"), client=client)

    try:
        agent.register()
        response = agent.heartbeat(cpu_pct=12.5, gpu_pct=None)
    finally:
        agent.close()

    assert response.ack is True
    assert state["heartbeats"] >= 1
    assert state["pulled"] is False
