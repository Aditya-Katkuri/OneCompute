"""Tests for the worker's opt-in measurement upload: WorkerAgent.report_profile and the
run_measure_loop wiring that calls it.

report_profile must send only one compact derived summary, never per-hour buckets or idle/presence
data. It remains offline-safe, and the measurement loop invokes it without ever running a job.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from contracts import (
    Capability,
    ProfileIngestResponse,
    ProfileReport,
    RegisterResponse,
)
from measurement.availability import AvailabilityTracker
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
        self.availability = AvailabilityTracker()

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
        return ProfileIngestResponse(
            accepted=True,
            coverage_buckets=req.coverage_buckets,
            buckets_stored=0,
        ).model_dump()

    return app, captured


def _agent(app: FastAPI, *, has_gpu: bool = False) -> WorkerAgent:
    client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://test")
    return WorkerAgent(
        "http://test",
        Capability(worker_id="w1", has_gpu=has_gpu),
        client=client,
        measurement_only=True,
    )


def test_report_profile_posts_only_compact_summary() -> None:
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
        profiler = _StubProfiler(buckets)
        profiler.availability.observed_seconds = 3600
        profiler.availability.unavailable_seconds = 3600
        profiler.availability.sample_count = 120
        ok = agent.report_profile(profiler, device_class="devbox")
    finally:
        agent.close()

    assert ok is True
    assert captured["report"]["worker_id"] == "w1"
    assert captured["report"]["device_class"] == "devbox"
    assert captured["report"]["coverage_buckets"] == 2
    assert captured["report"]["cpu"]["avg"] == 25
    assert captured["report"]["cpu"]["peak"] == 38
    assert captured["report"]["gpu_sampled"] is False
    assert captured["report"]["gpu"]["recoverable_high"] == 0.0
    assert captured["report"]["ram_avg"] == 45
    assert captured["report"]["availability"]["sample_count"] == 120
    assert "buckets" not in captured["report"]
    assert "idle" not in str(captured["report"]).lower()


def test_report_profile_distinguishes_an_idle_gpu_from_no_gpu() -> None:
    app, captured = _capture_app()
    agent = _agent(app, has_gpu=True)
    try:
        agent.register()
        buckets = [
            BucketStat(
                n=10,
                cpu_mean=20,
                cpu_max=30,
                gpu_mean=0,
                gpu_max=0,
                gpu_n=10,
            )
        ]
        assert agent.report_profile(_StubProfiler(buckets)) is True
    finally:
        agent.close()

    assert captured["report"]["gpu_sampled"] is True
    assert captured["report"]["gpu"]["avg"] == 0.0
    assert captured["report"]["gpu"]["recoverable_high"] == 40.0


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


def test_profile_auth_failure_invalidates_enrollment_for_retry() -> None:
    app = FastAPI()

    @app.post("/register")
    def register(_: Capability) -> dict:
        return RegisterResponse(worker_token="t", approved=True).model_dump()

    @app.post("/profile")
    def profile(_: ProfileReport) -> None:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="expired")

    agent = _agent(app)
    try:
        assert agent.register() is True
        assert agent.report_profile(_StubProfiler([BucketStat(n=5, cpu_mean=10)])) is False
        assert agent.registered is False
        assert agent.worker_token is None
    finally:
        agent.close()


def test_measurement_enrollment_retries_after_a_startup_outage() -> None:
    app = FastAPI()
    calls = {"register": 0}

    @app.post("/register")
    def register(_: Capability):
        calls["register"] += 1
        if calls["register"] == 1:
            return JSONResponse(status_code=503, content={"detail": "offline"})
        return RegisterResponse(worker_token="t", approved=True).model_dump()

    agent = _agent(app)
    try:
        assert agent.ensure_measurement_enrollment() is False
        assert agent.registered is False
        assert agent.ensure_measurement_enrollment() is True
        assert agent.registered is True
        assert agent.approved is True
    finally:
        agent.close()

    assert calls["register"] == 2


def test_pending_enrollment_does_not_become_approved_when_heartbeat_fails() -> None:
    app = FastAPI()

    @app.post("/register")
    def register(_: Capability) -> dict:
        return RegisterResponse(
            worker_token="t",
            approved=False,
            device_code="ABCD-12",
        ).model_dump()

    @app.post("/heartbeat")
    def heartbeat() -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "offline"})

    agent = _agent(app)
    try:
        assert agent.ensure_measurement_enrollment() is False
        assert agent.registered is True
        assert agent.approved is False
        assert agent.device_code == "ABCD-12"
    finally:
        agent.close()


class _MeasureSpy:
    """Agent stand-in for the loop-wiring test: records report_profile calls, no job paths."""

    def __init__(self) -> None:
        self.capability = Capability(worker_id="w1", has_gpu=False)
        self.reports = 0

    def report_profile(self, profiler, *, device_class: str = "unknown") -> bool:
        self.reports += 1
        assert device_class == "laptop"
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

    samples = wm.run_measure_loop(
        agent,
        governor,
        telem,
        interval=0.0,
        once=True,
        device_class="laptop",
    )

    assert samples == 1
    # the live reading was folded into the (stub) profiler and the envelope was uploaded
    assert governor.profiler.records == [(10.0, None, 30.0)]
    assert agent.reports >= 1


def test_run_measure_loop_does_not_upload_an_undurable_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(wm, "system_gpu_load_pct", lambda: 0.0)
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: 30.0)
    if wm.psutil is not None:
        monkeypatch.setattr(wm.psutil, "cpu_percent", lambda interval=None: 10.0)

    agent = _MeasureSpy()
    governor = _StubGovernor()
    governor.profiler.save = lambda: False
    telem = PilotTelemetry("w1", path=tmp_path / "t.jsonl", enabled=False)

    samples = wm.run_measure_loop(
        agent,
        governor,
        telem,
        interval=0.0,
        once=True,
        device_class="laptop",
    )

    assert samples == 1
    assert agent.reports == 0
