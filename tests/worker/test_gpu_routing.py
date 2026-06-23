"""The worker must route GPU jobs (needs_gpu) to the host-side isolation path."""
from __future__ import annotations

import worker.agent as agent_mod
from contracts import Capability, JobAssignment, JobManifest, Requires, SignedManifest
from worker.agent import WorkerAgent


def _assignment(kind, needs_gpu):
    manifest = JobManifest(job_id="j1", kind=kind, requires=Requires(needs_gpu=needs_gpu))
    return JobAssignment(
        signed_manifest=SignedManifest(manifest=manifest),
        input={"size": 8, "iters": 2},
    )


def _capture_host_side(monkeypatch):
    captured = {}

    def fake_run_in_isolation(kind, inp, limits, should_yield=None, host_side=False):
        captured["host_side"] = host_side
        return {"results": {}, "yielded": False}

    monkeypatch.setattr(agent_mod, "run_in_isolation", fake_run_in_isolation)
    return captured


def test_gpu_job_routes_host_side(monkeypatch):
    captured = _capture_host_side(monkeypatch)
    worker = WorkerAgent("http://x", Capability(worker_id="g1", has_gpu=True), isolated=True)
    worker.run_job(_assignment("render", needs_gpu=True))
    worker.close()
    assert captured["host_side"] is True


def test_cpu_job_does_not_force_host_side(monkeypatch):
    captured = _capture_host_side(monkeypatch)
    worker = WorkerAgent("http://x", Capability(worker_id="c1"), isolated=True)
    worker.run_job(_assignment("data.transform", needs_gpu=False))
    worker.close()
    assert captured["host_side"] is False
