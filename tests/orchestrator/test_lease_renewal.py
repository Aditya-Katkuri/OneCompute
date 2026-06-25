"""A heartbeat carrying current_job_id renews a running tile's lease (so a 15-min job
isn't reaped mid-run); a pure-telemetry heartbeat (no job id) leaves the lease untouched."""

import time

from fastapi.testclient import TestClient

from orchestrator.app import create_app


def _auth(client: TestClient, worker_id: str = "w1") -> dict[str, str]:
    token = client.post("/register", json={"worker_id": worker_id, "cpus": 2}).json()["worker_token"]
    return {"Authorization": f"Bearer {token}"}


def _lease_expires(client: TestClient, job_id: str) -> str:
    row = client.app.state.conn.execute(
        "SELECT lease_expires FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    return row["lease_expires"]


def test_heartbeat_with_current_job_renews_lease():
    client = TestClient(create_app(":memory:"))
    auth = _auth(client)
    job_id = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}, "units": 1}).json()[
        "job_id"
    ]
    assert client.get("/jobs/next", params={"worker_id": "w1"}, headers=auth).status_code == 200
    before = _lease_expires(client, job_id)

    time.sleep(0.02)  # let wall-clock advance so the renewed deadline is measurably later
    hb = client.post(
        "/heartbeat", json={"worker_id": "w1", "idle": False, "current_job_id": job_id}, headers=auth
    )
    assert hb.status_code == 200
    after = _lease_expires(client, job_id)
    assert after > before  # lease deadline pushed forward
    # job is still leased to this worker, not requeued
    assert client.get(f"/jobs/{job_id}").json()["state"] == "leased"


def test_telemetry_heartbeat_without_job_id_does_not_touch_lease():
    client = TestClient(create_app(":memory:"))
    auth = _auth(client)
    job_id = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}, "units": 1}).json()[
        "job_id"
    ]
    assert client.get("/jobs/next", params={"worker_id": "w1"}, headers=auth).status_code == 200
    before = _lease_expires(client, job_id)

    time.sleep(0.02)
    client.post("/heartbeat", json={"worker_id": "w1", "cpu_pct": 50.0}, headers=auth)
    assert _lease_expires(client, job_id) == before  # unchanged
