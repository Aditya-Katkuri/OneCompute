"""Unified end-to-end flow (Chief-of-Staff G2 gate for the full product).

Exercises every team's work together through the real contracts: signing (T4) ->
verification (T2) -> capability routing + ledger (T1) -> isolated execution (T3) ->
the cheater blacklist (T4+T1) -> instant yield + requeue (T2+T1) -> the dashboard
read models (T1 -> T5). Uses the in-process app via TestClient; one case spawns a
real isolation subprocess.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from contracts import Capability, JobAssignment, SubmitRequest
from orchestrator.app import create_app
from worker.agent import WorkerAgent


def _client(app) -> TestClient:
    return TestClient(app)


def _submit(client, **kw) -> str:
    r = client.post("/jobs", json=SubmitRequest(**kw).model_dump())
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def test_signed_job_is_verified_run_and_credited():
    app = create_app(":memory:")  # signing ON by default
    with TestClient(app) as client:
        _submit(client, kind="data.transform", input={"items": [1, 2, 3], "op": "square"}, units=3)
        agent = WorkerAgent("http://test", Capability(worker_id="w1", cpus=4, ram_gb=8.0), client=client)
        rr = agent.run_once()
        assert rr is not None and rr.status == "completed"
        assert rr.output["results"] == [1, 4, 9]
        assert client.get("/state").json()["total_credits"] == 3.0


def test_isolated_execution_path_completes():
    app = create_app(":memory:")
    with TestClient(app) as client:
        _submit(client, kind="data.transform", input={"items": [2, 3, 4], "op": "square"}, units=3)
        agent = WorkerAgent(
            "http://test", Capability(worker_id="iso", cpus=4, ram_gb=8.0),
            client=client, isolated=True,  # runs jobkit inside a real sandbox subprocess
        )
        rr = agent.run_once()
        assert rr is not None and rr.status == "completed"
        assert rr.output["results"] == [4, 9, 16]


def test_tampered_input_is_refused_before_running():
    app = create_app(":memory:")
    with TestClient(app) as client:
        _submit(client, kind="data.transform", input={"items": [1], "op": "square"}, units=1)
        register = client.post("/register", json=Capability(worker_id="w", cpus=2).model_dump())
        auth = {"Authorization": f"Bearer {register.json()['worker_token']}"}
        assignment = JobAssignment(
            **client.get("/jobs/next", params={"worker_id": "w"}, headers=auth).json()
        )
        assignment.input = {"items": [999], "op": "square"}  # tamper after signing
        agent = WorkerAgent("http://test", Capability(worker_id="w", cpus=2), client=client)
        rr = agent.run_job(assignment)
        assert rr.status == "failed"
        assert "verification failed" in rr.output["error"]


def test_cheater_is_caught_and_blacklisted():
    app = create_app(":memory:")
    with TestClient(app) as client:
        _submit(client, kind="challenge", input={"x": 5}, units=1)

        def cheat(manifest, _input, should_yield=lambda: False):
            return {"y": 0}  # wrong answer

        agent = WorkerAgent("http://test", Capability(worker_id="cheat", cpus=2),
                            client=client, runner=cheat)
        agent.run_once()
        wv = next(w for w in client.get("/state").json()["workers"] if w["worker_id"] == "cheat")
        assert wv["blacklisted"] is True
        assert wv["credits"] == 0.0


def test_yield_requeues_and_another_worker_finishes():
    app = create_app(":memory:")
    with TestClient(app) as client:
        _submit(client, kind="data.transform",
                input={"items": list(range(50)), "op": "square"}, units=50)
        yielder = WorkerAgent("http://test", Capability(worker_id="yielder", cpus=4, ram_gb=8.0),
                              client=client)
        yielder.register()
        assignment = yielder.poll_once()
        assert assignment is not None
        yielder.trigger_yield()
        rr = yielder.run_job(assignment)
        assert rr.status == "yielded"
        yielder.report_result(rr)
        assert client.get("/state").json()["jobs"][0]["state"] == "queued"

        finisher = WorkerAgent("http://test", Capability(worker_id="finisher", cpus=4, ram_gb=8.0),
                               client=client)
        rr2 = finisher.run_once()
        assert rr2 is not None and rr2.status == "completed"
        assert client.get("/state").json()["total_credits"] == 50.0


def test_events_feed_and_dashboard_served():
    app = create_app(":memory:")
    with TestClient(app) as client:
        _submit(client, kind="data.transform", input={"items": [1], "op": "square"}, units=1)
        feed = client.get("/events").json()
        assert any(e["type"] == "submitted" for e in feed["events"])
        assert feed["last_id"] >= 1
        root = client.get("/")
        assert root.status_code == 200
        assert "/state" in root.text  # the dashboard polls /state
