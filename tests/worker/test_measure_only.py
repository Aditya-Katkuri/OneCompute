"""Tests for the measurement-only worker mode (--measure-only).

The worker folds live CPU/GPU/RAM into the on-device profile, but it never pulls a job, streams live
utilization, or writes a timestamped sample timeline. The compact profile upload remains allowed.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import httpx
import pytest
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
from worker.profile_lock import ProfileLock
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

    def user_idle(self) -> bool:
        return True


class _SpyAgent:
    """Worker stand-in that fails loudly if any job path is ever touched in measure-only mode."""

    def __init__(self, has_gpu: bool = False) -> None:
        self.capability = Capability(worker_id="measure-1", has_gpu=has_gpu)
        self.job_calls: list[str] = []
        self.profile_reports = 0

    def report_profile(self, profiler, *, device_class: str = "unknown") -> bool:
        # Uploading the on-device envelope is an ALLOWED measure-only action (no job runs), so
        # this records the call rather than failing like the job paths below.
        self.profile_reports += 1
        return True

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
    monkeypatch.setattr(governor, "system_cpu_sample", lambda: cpu)

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("measure-only must never make an admission/yield decision")

    monkeypatch.setattr(governor, "should_run", _forbidden)
    monkeypatch.setattr(governor, "active_now", _forbidden)
    return governor


def _fake_orchestrator(approved: bool = True) -> tuple[FastAPI, dict]:
    """A minimal in-process orchestrator. /jobs/next flips a flag so a pull can be detected."""
    app = FastAPI()
    state = {
        "registered": 0,
        "registrations": [],
        "heartbeats": 0,
        "heartbeat_payloads": [],
        "pulled": False,
    }

    @app.post("/register")
    def register(capability: Capability) -> dict:
        state["registered"] += 1
        state["registrations"].append(capability.model_dump())
        return RegisterResponse(
            worker_token="t", approved=approved, device_code="AB-12"
        ).model_dump()

    @app.post("/heartbeat")
    def heartbeat(request: HeartbeatRequest) -> dict:
        state["heartbeats"] += 1
        state["heartbeat_payloads"].append(request.model_dump())
        return HeartbeatResponse(approved=approved).model_dump()

    @app.get("/jobs/next", response_model=None)
    def next_job(worker_id: str):
        state["pulled"] = True  # measure-only must NEVER reach this
        return Response(status_code=204)

    return app, state


def test_measure_loop_records_without_timeline_and_never_runs_a_job(tmp_path, monkeypatch) -> None:
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
    assert governor.profiler.availability.first_sample_at > 0.0

    # Measurement mode never creates a timestamped per-sample telemetry file.
    assert not (tmp_path / "telem.jsonl").exists()

    # the whole point of measure-only: no job was ever pulled or executed
    assert agent.job_calls == []
    # the learned envelope was uploaded to the orchestrator (opt-in, best-effort)
    assert agent.profile_reports >= 1


def test_measure_loop_local_mode_records_but_never_uploads(tmp_path, monkeypatch) -> None:
    # Local mode (no --url): the loop must record + persist the profile locally but NEVER call
    # report_profile, so a solo/offline pilot makes no network calls at all.
    agent = _SpyAgent()
    governor = _pin_samples(tmp_path, monkeypatch, cpu=20.0, gpu=0.0, ram=40.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)

    samples = wm.run_measure_loop(
        agent, governor, telem, interval=0.0, once=True, upload=False
    )

    assert samples == 1
    assert agent.profile_reports == 0            # no upload attempted in local mode
    assert agent.job_calls == []                 # and of course no job ran
    assert governor.profiler.path.exists()       # but the profile IS persisted locally
    assert governor.profiler.profile_now().n == 1


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
    samples = wm.run_measure_loop(agent, governor, telem, interval=5.0, once=False)

    assert samples == 3
    assert governor.profiler.profile_now().n == 3
    assert agent.job_calls == []

    # the learned envelope persists on exit, exactly like the work loop's finally block
    governor.profiler.save()
    assert (tmp_path / "profile.json").exists()


def test_measure_loop_persists_profile_during_the_run_not_only_on_exit(tmp_path, monkeypatch) -> None:
    """A reboot/power-loss must cost at most one upload window: the loop saves the profile locally
    on the upload cadence, not only in its finally block."""
    agent = _SpyAgent(has_gpu=False)
    governor = _pin_samples(tmp_path, monkeypatch, cpu=10.0, gpu=0.0, ram=20.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)

    saves = {"n": 0}
    real_save = governor.profiler.save

    def counting_save() -> None:
        saves["n"] += 1
        real_save()

    monkeypatch.setattr(governor.profiler, "save", counting_save)

    ticks = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= 13:
            raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", fake_sleep)

    # At a 5-second cadence the loop saves immediately and about every minute, before its final save.
    wm.run_measure_loop(agent, governor, telem, interval=5.0, once=False)

    assert saves["n"] >= 3
    assert (tmp_path / "profile.json").exists()


def test_measure_loop_survives_transient_sample_errors(tmp_path, monkeypatch) -> None:
    """One bad sample (a psutil hiccup, a disk blip) must never kill a week-long observer: the
    iteration is logged and skipped, and the loop keeps going."""
    agent = _SpyAgent(has_gpu=False)
    governor = _pin_samples(tmp_path, monkeypatch, cpu=10.0, gpu=0.0, ram=20.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)

    calls = {"n": 0}

    def flaky_ram() -> float:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("transient psutil hiccup")
        return 20.0

    monkeypatch.setattr(wm, "system_ram_load_pct", flaky_ram)

    ticks = {"n": 0}

    def fake_sleep(_seconds: float) -> None:
        ticks["n"] += 1
        if ticks["n"] >= 4:
            raise KeyboardInterrupt

    monkeypatch.setattr(time, "sleep", fake_sleep)

    samples = wm.run_measure_loop(agent, governor, telem, interval=5.0, once=False)

    # 4 iterations ran: the first 2 raised (recorded nothing, did NOT crash) and the last 2 folded
    # a sample. The loop survived to a clean Ctrl-C, and no job was ever touched.
    assert ticks["n"] == 4
    assert samples == 2
    assert governor.profiler.profile_now().n == 2
    assert agent.job_calls == []


def test_main_measure_only_once_avoids_live_heartbeat_and_pulls_no_jobs(
    tmp_path, monkeypatch
) -> None:
    app, state = _fake_orchestrator()

    def _make_agent(url, capability, isolated=False, **kwargs):
        client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url=url)
        return WorkerAgent(
            url,
            capability,
            client=client,
            isolated=isolated,
            measurement_only=kwargs.get("measurement_only", False),
        )

    monkeypatch.setattr(wm, "WorkerAgent", _make_agent)
    observed_ids: list[str | None] = []

    def detect(worker_id=None):
        observed_ids.append(worker_id)
        return Capability(worker_id=worker_id or "measure-1", has_gpu=False)

    monkeypatch.setattr(wm, "detect_capability", detect)
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: 42.0)
    if wm.psutil is not None:
        monkeypatch.setattr(wm.psutil, "cpu_percent", lambda interval=None: 5.0)

    started = {"n": 0}

    def spy_start_heartbeat(agent, period_s: float = 1.0):
        started["n"] += 1
        raise AssertionError("measurement mode must not stream live usage")

    monkeypatch.setattr(wm, "_start_usage_heartbeat", spy_start_heartbeat)

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # redirect profile + telemetry to tmp
    monkeypatch.setattr("sys.argv", ["worker", "--url", "https://test", "--measure-only", "--once"])

    wm.main()

    assert started["n"] == 0
    # the device joined the fleet so it appears in the dashboard
    assert state["registered"] == 1
    registration = state["registrations"][0]
    assert registration["measurement_only"] is True
    assert registration["free_ram_gb"] is None
    assert registration["has_gpu"] is False
    # the whole point: no job was ever pulled
    assert state["pulled"] is False

    # The profile and pseudonymous identity persist, but no measurement timeline is created.
    profile_path = tmp_path / "OneCompute" / "usage_profile.json"
    telem_path = tmp_path / "OneCompute" / "pilot-telemetry.jsonl"
    assert profile_path.exists()
    assert not telem_path.exists()
    identity = (tmp_path / "OneCompute" / "observer-id").read_text(encoding="utf-8").strip()
    assert identity.startswith("observer-")
    assert observed_ids == [identity]


def test_main_duplicate_observer_does_not_save_after_profile_lock_failure(
    tmp_path, monkeypatch
) -> None:
    saves = {"n": 0}
    registrations = {"n": 0}
    profile_path = tmp_path / "profile.json"
    profile_path.write_text("{corrupt", encoding="utf-8")
    blocker = ProfileLock(profile_path)
    blocker.acquire()

    class NoNetworkAgent:
        def __init__(self, _url, capability, **_kwargs) -> None:
            self.capability = capability
            self.registered = False
            self.approved = False

        def register(self) -> bool:
            registrations["n"] += 1
            return True

        def close(self) -> None:
            pass

    def count_save(_self) -> bool:
        saves["n"] += 1
        return True

    monkeypatch.setattr(wm.UsageProfiler, "save", count_save)
    monkeypatch.setattr(wm, "WorkerAgent", NoNetworkAgent)
    monkeypatch.setattr(wm, "build_client", lambda *_args, **_kwargs: object())
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(
        "sys.argv",
        [
            "worker",
            "--url",
            "http://localhost",
            "--measure-only",
            "--once",
            "--profile",
            str(profile_path),
        ],
    )

    try:
        with pytest.raises(SystemExit):
            wm.main()
    finally:
        blocker.release()

    assert saves["n"] == 0
    assert registrations["n"] == 0
    assert profile_path.read_text(encoding="utf-8") == "{corrupt"
    assert not list(tmp_path.glob("profile.corrupt-*.json"))


def test_main_local_measurement_bootstraps_profile_availability_from_telemetry(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        wm,
        "detect_capability",
        lambda worker_id=None: Capability(worker_id=worker_id or "measure-1", has_gpu=False),
    )
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: 42.0)
    if wm.psutil is not None:
        monkeypatch.setattr(wm.psutil, "cpu_percent", lambda interval=None: 5.0)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    onecompute = tmp_path / "OneCompute"
    onecompute.mkdir()
    now = time.time()
    historic = [
        {"ts": datetime.fromtimestamp(now - 3_600, UTC).isoformat(), "event": "measure"},
        {"ts": datetime.fromtimestamp(now - 3_570, UTC).isoformat(), "event": "measure"},
        {"ts": datetime.fromtimestamp(now - 30, UTC).isoformat(), "event": "measure"},
    ]
    (onecompute / "pilot-telemetry.jsonl").write_text(
        "\n".join(json.dumps(record) for record in historic) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["worker", "--measure-only", "--once"])

    wm.main()

    profile = UsageProfiler(path=onecompute / "usage_profile.json")
    assert profile.availability.gap_count == 1
    assert profile.availability.sample_count >= 4
    assert profile.availability.unavailable_seconds > 3_000


def test_main_measure_only_continuous_saves_profile_on_ctrl_c(tmp_path, monkeypatch) -> None:
    # Drive the continuous (no --once) path through main() and end it with Ctrl-C: the profile
    # must still be persisted by main()'s finally block and no job may ever be pulled.
    app, state = _fake_orchestrator()

    def _make_agent(url, capability, isolated=False, **kwargs):
        client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url=url)
        return WorkerAgent(
            url,
            capability,
            client=client,
            isolated=isolated,
            measurement_only=kwargs.get("measurement_only", False),
        )

    monkeypatch.setattr(wm, "WorkerAgent", _make_agent)
    monkeypatch.setattr(
        wm,
        "detect_capability",
        lambda worker_id=None: Capability(worker_id=worker_id or "measure-1", has_gpu=False),
    )
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: 55.0)
    if wm.psutil is not None:
        monkeypatch.setattr(wm.psutil, "cpu_percent", lambda interval=None: 7.0)

    def raise_ctrl_c(_seconds: float) -> None:
        raise KeyboardInterrupt  # end the continuous loop after its first sample

    monkeypatch.setattr(time, "sleep", raise_ctrl_c)

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["worker", "--url", "https://test", "--measure-only"])

    wm.main()  # must return (not hang) after Ctrl-C

    profile_path = tmp_path / "OneCompute" / "usage_profile.json"
    assert profile_path.exists()  # saved on exit via main()'s finally
    assert state["pulled"] is False  # never pulled a job


def test_main_measure_only_once_does_not_hang_when_fleet_gated(tmp_path, monkeypatch) -> None:
    # A gated fleet must not block local collection while the central profile remains unapproved.
    app, state = _fake_orchestrator(approved=False)

    def _make_agent(url, capability, isolated=False, **kwargs):
        client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url=url)
        return WorkerAgent(
            url,
            capability,
            client=client,
            isolated=isolated,
            measurement_only=kwargs.get("measurement_only", False),
        )

    monkeypatch.setattr(wm, "WorkerAgent", _make_agent)
    monkeypatch.setattr(
        wm,
        "detect_capability",
        lambda worker_id=None: Capability(worker_id=worker_id or "measure-1", has_gpu=False),
    )
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: 42.0)
    if wm.psutil is not None:
        monkeypatch.setattr(wm.psutil, "cpu_percent", lambda interval=None: 5.0)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("sys.argv", ["worker", "--url", "https://test", "--measure-only", "--once"])

    wm.main()  # must return promptly, not block on approval

    assert state["registered"] == 1
    assert state["heartbeats"] >= 1  # sent the single approval heartbeat
    assert all(payload["free_ram_gb"] is None for payload in state["heartbeat_payloads"])
    assert all(payload["cpu_pct"] == 0.0 for payload in state["heartbeat_payloads"])
    assert state["pulled"] is False  # still never pulls a job
    assert (tmp_path / "OneCompute" / "usage_profile.json").exists()
    assert not (tmp_path / "OneCompute" / "pilot-telemetry.jsonl").exists()


def test_continuous_measurement_rejects_a_busy_loop_interval(tmp_path, monkeypatch) -> None:
    agent = _SpyAgent()
    governor = _pin_samples(tmp_path, monkeypatch, cpu=10.0, gpu=0.0, ram=20.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)

    with pytest.raises(ValueError, match="at least 5 seconds"):
        wm.run_measure_loop(agent, governor, telem, interval=0.0, once=False)


def test_continuous_measurement_rejects_an_impractically_slow_interval(
    tmp_path, monkeypatch
) -> None:
    agent = _SpyAgent()
    governor = _pin_samples(tmp_path, monkeypatch, cpu=10.0, gpu=0.0, ram=20.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)

    with pytest.raises(ValueError, match="at most 3600 seconds"):
        wm.run_measure_loop(agent, governor, telem, interval=3601.0, once=False)


@pytest.mark.parametrize("interval", [float("nan"), float("inf"), float("-inf")])
def test_measurement_rejects_nonfinite_intervals(tmp_path, monkeypatch, interval) -> None:
    agent = _SpyAgent()
    governor = _pin_samples(tmp_path, monkeypatch, cpu=10.0, gpu=0.0, ram=20.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)

    with pytest.raises(ValueError, match="must be finite"):
        wm.run_measure_loop(agent, governor, telem, interval=interval, once=False)


def test_measurement_skips_a_sample_when_required_sensor_data_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    agent = _SpyAgent()
    governor = _pin_samples(tmp_path, monkeypatch, cpu=10.0, gpu=0.0, ram=20.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: None)

    samples = wm.run_measure_loop(agent, governor, telem, interval=0.0, once=True)

    assert samples == 0
    assert governor.profiler.profile_now().n == 0


def test_measurement_skips_a_sample_when_cpu_sensors_are_unavailable(
    tmp_path, monkeypatch
) -> None:
    agent = _SpyAgent()
    governor = _pin_samples(tmp_path, monkeypatch, cpu=10.0, gpu=0.0, ram=20.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)
    monkeypatch.setattr(governor, "system_cpu_sample", lambda: None)
    if wm.psutil is not None:
        def unavailable_cpu(interval=None):
            raise RuntimeError("sensor unavailable")

        monkeypatch.setattr(wm.psutil, "cpu_percent", unavailable_cpu)

    samples = wm.run_measure_loop(agent, governor, telem, interval=0.0, once=True)

    assert samples == 0
    assert governor.profiler.profile_now().n == 0


def test_transient_gpu_failure_retains_cpu_and_ram_coverage(tmp_path, monkeypatch) -> None:
    agent = _SpyAgent(has_gpu=True)
    governor = _pin_samples(tmp_path, monkeypatch, cpu=10.0, gpu=0.0, ram=20.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)
    monkeypatch.setattr(wm, "system_gpu_load_pct", lambda: None)

    samples = wm.run_measure_loop(agent, governor, telem, interval=0.0, once=True)

    bucket = governor.profiler.profile_now()
    assert samples == 1
    assert bucket.n == 1
    assert bucket.cpu_mean == 10.0
    assert bucket.ram_mean == 20.0
    assert bucket.gpu_n == 0


def test_measurement_keeps_unknown_power_and_idle_out_of_the_profile(
    tmp_path, monkeypatch, capsys
) -> None:
    agent = _SpyAgent()
    governor = _pin_samples(tmp_path, monkeypatch, cpu=10.0, gpu=0.0, ram=20.0)
    telem = PilotTelemetry("measure-1", path=tmp_path / "telem.jsonl", enabled=False)
    governor.gate.on_ac_state = lambda: None
    governor.gate.user_idle_state = lambda: None

    samples = wm.run_measure_loop(agent, governor, telem, interval=0.0, once=True)

    bucket = governor.profiler.profile_now()
    assert samples == 1
    assert bucket.n == 1
    assert bucket.ac_mean == 0.0
    assert bucket.idle_mean == 0.0
    assert "ac=? idle=?" in capsys.readouterr().out


def test_job_mode_usage_heartbeat_call_remains_available() -> None:
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


def test_adaptive_governor_uses_custom_profile_path(tmp_path) -> None:
    p = tmp_path / "alice.json"
    gov = wm._adaptive_governor(60.0, str(p))
    assert gov.profiler.path == p


def test_adaptive_governor_defaults_profile_path_when_none() -> None:
    gov = wm._adaptive_governor(60.0, None)
    assert gov.profiler.path.name == "usage_profile.json"


def test_main_measure_only_writes_to_the_custom_profile_path(tmp_path, monkeypatch) -> None:
    # --profile lets each device write a distinctly-named profile so a coordinator can collect several
    # into one folder for a single aggregate readout (multi-person pilot).
    app, _state = _fake_orchestrator()

    def _make_agent(url, capability, isolated=False, **kwargs):
        client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url=url)
        return WorkerAgent(
            url,
            capability,
            client=client,
            isolated=isolated,
            measurement_only=kwargs.get("measurement_only", False),
        )

    monkeypatch.setattr(wm, "WorkerAgent", _make_agent)
    monkeypatch.setattr(
        wm,
        "detect_capability",
        lambda worker_id=None: Capability(worker_id=worker_id or "measure-1", has_gpu=False),
    )
    monkeypatch.setattr(wm, "system_ram_load_pct", lambda: 42.0)
    if wm.psutil is not None:
        monkeypatch.setattr(wm.psutil, "cpu_percent", lambda interval=None: 5.0)

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # where the DEFAULT profile would land
    custom = tmp_path / "alice.json"
    monkeypatch.setattr(
        "sys.argv",
        ["worker", "--url", "https://test", "--measure-only", "--once", "--profile", str(custom)],
    )

    wm.main()

    assert custom.exists()  # wrote to the named profile
    assert not (tmp_path / "OneCompute" / "usage_profile.json").exists()  # not the default location


def test_remote_measurement_http_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["worker", "--url", "http://pilot.example", "--measure-only", "--once"],
    )

    with pytest.raises(SystemExit):
        wm.main()


def test_remote_measurement_http_is_rejected_case_insensitively(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["worker", "--url", "HTTP://pilot.example", "--measure-only", "--once"],
    )

    with pytest.raises(SystemExit):
        wm.main()


def test_tls_material_on_http_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "worker",
            "--url",
            "http://127.0.0.1:8080",
            "--measure-only",
            "--once",
            "--tls-ca",
            "ca.pem",
        ],
    )

    with pytest.raises(SystemExit):
        wm.main()
