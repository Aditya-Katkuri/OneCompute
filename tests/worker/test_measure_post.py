"""Tests for the worker's opt-in measurement upload: WorkerAgent.report_profile and the
run_measure_loop wiring that calls it.

report_profile must send ONLY populated (n>0) hour-of-week buckets with their derived stats, be
offline-safe (never raise; return False when unregistered or when the POST fails), and the measure
loop must invoke it (first sample + on exit) so the central fleet view populates without ever
running a job. Hermetic: fake httpx transport, stub profiler, no real network or sleeps.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI

from contracts import (
    Capability,
    ProfileIngestResponse,
    ProfileReport,
    RegisterResponse,
)
from worker import __main__ as wm
from worker.agent import WorkerAgent
from worker.profiler import BUCKETS, BucketStat
from worker.telemetry import PilotTelemetry


class _StubProfiler:
    """Minimal UsageProfiler stand-in: just the positional ``buckets`` list report_profile reads,
    plus a ``record`` that captures folds for the loop-wiring test."""

    def __init__(self, buckets: list[BucketStat] | None = None) -> None:
        self.buckets = buckets if buckets is not None else []
        self.records: list[tuple[float, float, float]] = []

    def record(self, cpu: float, gpu: float, ram: float, when=None, on_ac=None, idle=None) -> None:
        self.records.append((cpu, gpu, ram))


def _capture_app() -> tuple[FastAPI, dict]:
    app = FastAPI()
    captured: dict = {}

    @app.post("/register")
    def register(_: Capability) -> dict:
        return RegisterResponse(worker_token="t", approved=True).model_dump()

    @app.post("/profile")
    def profile(req: ProfileReport) -> dict:
        captured["report"] = req.model_dump()
        return ProfileIngestResponse(accepted=True, buckets_stored=len(req.buckets)).model_dump()

    return app, captured


def _agent(app: FastAPI) -> WorkerAgent:
    client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return WorkerAgent("http://test", Capability(worker_id="w1"), client=client)


def test_report_profile_posts_only_populated_buckets() -> None:
    app, captured = _capture_app()
    agent = _agent(app)
    try:
        agent.register()
        buckets = [BucketStat() for _ in range(BUCKETS)]
        buckets[5] = BucketStat(
            n=10, cpu_mean=20, cpu_max=28, gpu_mean=5, gpu_max=9, ram_mean=40, ram_max=50
        )
        buckets[100] = BucketStat(
            n=3, cpu_mean=30, cpu_max=38, gpu_mean=0, gpu_max=0, ram_mean=50, ram_max=55
        )
        ok = agent.report_profile(_StubProfiler(buckets))
    finally:
        agent.close()

    assert ok is True
    assert captured["report"]["worker_id"] == "w1"
    sent = {b["index"]: b for b in captured["report"]["buckets"]}
    assert set(sent) == {5, 100}  # only the populated buckets, carrying their hour-of-week index
    assert sent[5]["cpu_mean"] == 20 and sent[5]["ram_max"] == 50
    assert sent[100]["cpu_mean"] == 30


def test_report_profile_unregistered_returns_false_without_posting() -> None:
    app, captured = _capture_app()
    agent = _agent(app)
    try:
        # never registered -> no token -> must not post
        assert agent.report_profile(_StubProfiler([BucketStat(n=5, cpu_mean=10)])) is False
    finally:
        agent.close()
    assert "report" not in captured


def test_report_profile_swallows_http_errors() -> None:
    # An orchestrator with no /profile route (e.g. older build) -> 404 -> report_profile returns
    # False instead of raising, so the pilot keeps learning locally.
    app = FastAPI()

    @app.post("/register")
    def register(_: Capability) -> dict:
        return RegisterResponse(worker_token="t", approved=True).model_dump()

    agent = _agent(app)
    try:
        agent.register()
        assert agent.report_profile(_StubProfiler([BucketStat(n=5, cpu_mean=10)])) is False
    finally:
        agent.close()


class _MeasureSpy:
    """Agent stand-in for the loop-wiring test: records report_profile calls, no job paths."""

    def __init__(self) -> None:
        self.capability = Capability(worker_id="w1", has_gpu=False)
        self.reports = 0

    def report_profile(self, profiler) -> bool:
        self.reports += 1
        return True


class _StubGovernor:
    def __init__(self) -> None:
        self.profiler = _StubProfiler()

    def user_cpu(self) -> float:
        return 0.0


def test_run_measure_loop_uploads_the_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(wm, "system_gpu_load_pct", lambda: 0.0)
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: 30.0)
    if wm.psutil is not None:
        monkeypatch.setattr(wm.psutil, "cpu_percent", lambda interval=None: 10.0)

    agent = _MeasureSpy()
    governor = _StubGovernor()
    telem = PilotTelemetry("w1", path=tmp_path / "t.jsonl", enabled=False)

    samples = wm.run_measure_loop(agent, governor, telem, interval=0.0, once=True)

    assert samples == 1
    # the live reading was folded into the (stub) profiler and the envelope was uploaded
    assert governor.profiler.records == [(10.0, 0.0, 30.0)]
    assert agent.reports >= 1
