from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from orchestrator.app import create_app
from orchestrator.db import connect, write_lock


def test_completed_result_credits_gpu_weight():
    client = TestClient(create_app(":memory:"))
    register = client.post(
        "/register",
        json={
            "worker_id": "gpu-1",
            "cpus": 8,
            "has_gpu": True,
            "gpu_vram_gb": 8,
            "accel": ["cuda"],
        },
    )
    assert register.status_code == 200
    submit = client.post(
        "/jobs",
        json={"kind": "challenge", "input": {"x": 3}, "requires": {"needs_gpu": True}, "units": 3},
    )
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assignment = client.get("/jobs/next", params={"worker_id": "gpu-1"})
    assert assignment.status_code == 200

    result = client.post(
        f"/results/{job_id}",
        json={
            "worker_id": "gpu-1",
            "job_id": job_id,
            "status": "completed",
            "output": {"y": 10},
            "units": 3,
        },
    )
    assert result.status_code == 200
    assert result.json()["accepted"] is True
    assert result.json()["credited"] == 15.0

    state = client.get("/state")
    assert state.status_code == 200
    assert state.json()["total_credits"] == 15.0


def test_results_require_lease_owner_and_do_not_double_credit():
    client = TestClient(create_app(":memory:"))
    assert client.post("/register", json={"worker_id": "owner", "cpus": 2}).status_code == 200
    assert client.post("/register", json={"worker_id": "other", "cpus": 2}).status_code == 200
    submit = client.post(
        "/jobs",
        json={"kind": "challenge", "input": {"x": 2}, "units": 2},
    )
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert client.get("/jobs/next", params={"worker_id": "owner"}).status_code == 200

    stolen = client.post(
        f"/results/{job_id}",
        json={"worker_id": "other", "job_id": job_id, "status": "completed", "units": 99},
    )
    assert stolen.status_code == 200
    assert stolen.json() == {"accepted": False, "credited": 0.0, "reason": "not_leased"}

    accepted = client.post(
        f"/results/{job_id}",
        json={"worker_id": "owner", "job_id": job_id, "status": "completed", "output": {"y": 5}, "units": 99},
    )
    assert accepted.status_code == 200
    assert accepted.json()["credited"] == 2.0

    duplicate = client.post(
        f"/results/{job_id}",
        json={"worker_id": "owner", "job_id": job_id, "status": "completed", "output": {"y": 5}, "units": 99},
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == {"accepted": False, "credited": 0.0, "reason": "not_leased"}
    assert client.get("/state").json()["total_credits"] == 2.0


def test_expired_result_is_requeued_without_credit(tmp_path):
    db_path = str(tmp_path / "results.db")
    client = TestClient(create_app(db_path))
    assert client.post("/register", json={"worker_id": "worker", "cpus": 2}).status_code == 200
    submit = client.post("/jobs", json={"kind": "challenge", "input": {"x": 5}, "units": 4})
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert client.get("/jobs/next", params={"worker_id": "worker"}).status_code == 200

    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    conn = connect(db_path)
    with write_lock:
        conn.execute("UPDATE jobs SET lease_expires = ? WHERE job_id = ?", (expired, job_id))
        conn.commit()
    conn.close()

    result = client.post(
        f"/results/{job_id}",
        json={"worker_id": "worker", "job_id": job_id, "status": "completed", "units": 4},
    )
    assert result.status_code == 200
    assert result.json() == {"accepted": False, "credited": 0.0, "reason": "lease_expired"}
    state = client.get("/state").json()
    assert state["total_credits"] == 0.0
    assert state["jobs"][0]["state"] == "queued"


def test_invalid_challenge_result_is_requeued_without_credit():
    client = TestClient(create_app(":memory:"))
    assert client.post("/register", json={"worker_id": "worker", "cpus": 2}).status_code == 200
    submit = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}, "units": 4})
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert client.get("/jobs/next", params={"worker_id": "worker"}).status_code == 200

    result = client.post(
        f"/results/{job_id}",
        json={"worker_id": "worker", "job_id": job_id, "status": "completed", "output": {"y": 999}},
    )
    assert result.status_code == 200
    assert result.json() == {"accepted": False, "credited": 0.0, "reason": "invalid_result"}
    state = client.get("/state").json()
    assert state["total_credits"] == 0.0
    assert state["jobs"][0]["state"] == "queued"

