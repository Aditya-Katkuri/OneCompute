"""Worker security policy: strict trust (pinned key) + fail-closed isolation (--require-isolation).

Two hardening controls:
1. A worker with a pinned trusted key refuses unsigned or differently-signed manifests before
   running anything (a compromised orchestrator can't inject a self-signed job).
2. A worker set to require isolation refuses to run a job unless a real OS-enforced sandbox is
   available, instead of silently degrading to the unsandboxed subprocess fallback (fail closed).

Hermetic: no network, no Docker, no real jobkit subprocess.
"""
import pytest

from contracts import Capability, JobAssignment, JobManifest, SignedManifest
from isolation import IsolationUnavailableError
from isolation import runner as iso_runner
from trust import Signer
from worker.agent import WorkerAgent


def _assignment(manifest: JobManifest, signer: Signer | None = None) -> JobAssignment:
    sm = signer.sign(manifest) if signer else SignedManifest(manifest=manifest)
    return JobAssignment(signed_manifest=sm, input={"items": [1], "op": "square"})


def _agent(**kwargs) -> WorkerAgent:
    kwargs.setdefault("runner", lambda *_a, **_k: {"should_not": "run"})
    return WorkerAgent("http://test", Capability(worker_id="w"), **kwargs)


# --- strict trust at the worker (out-of-band pinned key) ---------------------


def test_pinned_key_rejects_untrusted_signer():
    agent = _agent(trusted_public_key_hex=Signer().public_key_hex)
    attacker = Signer()
    result = agent.run_job(_assignment(JobManifest(job_id="evil", kind="data.transform"), attacker))
    assert result.status == "failed"
    assert result.output == {"error": "verification failed: untrusted_signer"}


def test_pinned_key_rejects_unsigned():
    agent = _agent(trusted_public_key_hex=Signer().public_key_hex)
    result = agent.run_job(_assignment(JobManifest(job_id="j", kind="data.transform")))
    assert result.status == "failed"
    assert result.output == {"error": "verification failed: unsigned_manifest"}


def test_pinned_key_accepts_trusted_signer_and_runs():
    trusted = Signer()
    ran = {}

    def runner(_manifest, _input, should_yield=None):
        ran["yes"] = True
        return {"results": [1]}

    agent = _agent(runner=runner, isolated=False, trusted_public_key_hex=trusted.public_key_hex)
    result = agent.run_job(_assignment(JobManifest(job_id="j", kind="data.transform"), trusted))
    assert result.status == "completed"
    assert ran.get("yes") is True


def test_no_pin_allows_unsigned_demo_path():
    ran = {}

    def runner(_manifest, _input, should_yield=None):
        ran["yes"] = True
        return {"results": [1]}

    agent = _agent(runner=runner, isolated=False)
    result = agent.run_job(_assignment(JobManifest(job_id="j", kind="data.transform")))
    assert result.status == "completed"
    assert ran.get("yes") is True


# --- fail-closed isolation at the library ------------------------------------


def test_require_isolation_refuses_host_side(monkeypatch):
    # A host-side (GPU/AI) job has no OS boundary; with allow_unsandboxed=False it must refuse.
    monkeypatch.setattr(iso_runner, "docker_available", lambda: False)
    with pytest.raises(IsolationUnavailableError):
        iso_runner.run_in_isolation(
            "ai.batch_infer", {"items": []}, host_side=True, allow_unsandboxed=False
        )


def test_require_isolation_refuses_when_no_docker(monkeypatch):
    monkeypatch.setattr(iso_runner, "docker_available", lambda: False)
    monkeypatch.setattr(iso_runner, "mxc_available", lambda: False)
    with pytest.raises(IsolationUnavailableError):
        iso_runner.run_in_isolation("data.transform", {"items": [1]}, allow_unsandboxed=False)


def test_require_isolation_satisfied_by_mxc(monkeypatch):
    # MXC is a real OS-enforced boundary, so with it available a require-isolation run proceeds
    # (not refused) even when Docker is absent.
    monkeypatch.setattr(iso_runner, "mxc_available", lambda: True)
    monkeypatch.setattr(iso_runner, "docker_available", lambda: False)
    sentinel = {"results": ["via-mxc"]}
    monkeypatch.setattr(iso_runner, "_run_mxc", lambda *_a, **_k: sentinel)
    out = iso_runner.run_in_isolation("data.transform", {"items": [1]}, allow_unsandboxed=False)
    assert out is sentinel


def test_unsandboxed_fallback_used_by_default_when_no_docker(monkeypatch):
    monkeypatch.setattr(iso_runner, "docker_available", lambda: False)
    monkeypatch.setattr(iso_runner, "mxc_available", lambda: False)
    called = {}

    def fake_sub(_in_path, _out_path, _work_dir, _limits, _should_yield):
        called["sub"] = True
        return {"results": ["ok"]}

    monkeypatch.setattr(iso_runner, "_run_subprocess_with_existing_files", fake_sub)
    out = iso_runner.run_in_isolation("data.transform", {"items": [1]})  # default allow_unsandboxed
    assert out == {"results": ["ok"]}
    assert called.get("sub") is True


# --- fail-closed propagates through the worker (job reported failed, never run) ----


def test_worker_require_isolation_reports_failed_without_running(monkeypatch):
    monkeypatch.setattr(iso_runner, "docker_available", lambda: False)
    monkeypatch.setattr(iso_runner, "mxc_available", lambda: False)
    agent = _agent(isolated=True, require_isolation=True)
    result = agent.run_job(_assignment(JobManifest(job_id="j", kind="data.transform")))
    assert result.status == "failed"
    assert "refused" in result.output["error"]
