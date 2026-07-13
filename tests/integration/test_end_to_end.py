"""End-to-end integration test (Chief-of-Staff G2 gate).

Wires T1 (orchestrator) and T2 (worker) together over an in-process ASGI transport
and proves the full slice: submit -> worker pulls -> runs -> returns -> ledger credits.
Only the FROZEN seams in docs/contracts.md are used here.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contracts import Capability, Requires, SubmitRequest
from orchestrator.app import create_app
from worker.agent import WorkerAgent


def _client(app) -> TestClient:
    # TestClient is a synchronous httpx.Client subclass that speaks to the ASGI app
    # in-process, so it can be injected straight into WorkerAgent(client=...).
    return TestClient(app)


def _elevate(client, worker_id, tier="managed") -> None:
    # A new worker defaults to the fail-closed 'untrusted' tier, so it may not receive the default
    # 'internal'-classified job. An operator elevates the device out-of-band; no submit_token is
    # configured in this app, so the admin gate is open.
    r = client.post(f"/workers/{worker_id}/tier", json={"trust_tier": tier})
    assert r.status_code == 200, r.text


def test_submit_run_credit_cpu() -> None:
    app = create_app(":memory:")
    client = _client(app)

    req = SubmitRequest(kind="data.transform", input={"items": [1, 2, 3], "op": "square"}, units=3)
    r = client.post("/jobs", json=req.model_dump())
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]

    cap = Capability(worker_id="w-int-cpu", cpus=4, ram_gb=8.0, has_gpu=False)
    agent = WorkerAgent("http://test", cap, client=client)
    agent.register()
    _elevate(client, cap.worker_id)
    rr = agent.run_once()

    assert rr is not None, "worker did not pick up the job"
    assert rr.status == "completed"
    assert rr.output is not None and rr.output["results"] == [1, 4, 9]

    state = client.get("/state").json()
    assert state["total_credits"] == 3.0  # CPU weight 1 * 3 units
    assert any(j["job_id"] == job_id and j["state"] == "completed" for j in state["jobs"])


def test_gpu_worker_earns_more() -> None:
    app = create_app(":memory:")
    client = _client(app)
    # Credit follows the JOB's GPU requirement, not the worker's self-claim: a GPU job pays 5x.
    # (A GPU worker running a plain CPU job would earn only the CPU rate; advertising a GPU alone
    # never inflates credit.)
    client.post(
        "/jobs",
        json=SubmitRequest(
            kind="data.transform",
            input={"items": [2, 3], "op": "square"},
            requires=Requires(needs_gpu=True),
            units=2,
        ).model_dump(),
    )
    cap = Capability(worker_id="w-int-gpu", cpus=8, ram_gb=16.0, has_gpu=True, accel=["cuda"], gpu_vram_gb=8)
    agent = WorkerAgent("http://test", cap, client=client)
    agent.register()
    _elevate(client, cap.worker_id)
    rr = agent.run_once()

    assert rr is not None and rr.status == "completed"
    state = client.get("/state").json()
    assert state["total_credits"] == 10.0  # GPU-job weight 5 * 2 units


def test_no_work_returns_none() -> None:
    app = create_app(":memory:")
    client = _client(app)
    cap = Capability(worker_id="w-idle", cpus=2, ram_gb=4.0)
    agent = WorkerAgent("http://test", cap, client=client)
    assert agent.run_once() is None  # nothing queued -> 204 -> None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
