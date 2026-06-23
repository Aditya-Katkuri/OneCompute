from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from orchestrator.app import create_app
from orchestrator.db import connect, write_lock


def test_expired_lease_requeues_for_another_worker(tmp_path):
    db_path = str(tmp_path / "t.db")
    client = TestClient(create_app(db_path))
    for worker_id in ("worker-a", "worker-b"):
        response = client.post("/register", json={"worker_id": worker_id, "cpus": 4})
        assert response.status_code == 200
    submit = client.post(
        "/jobs",
        json={"kind": "challenge", "input": {"x": 7}, "requires": {"min_cpus": 1}},
    )
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]

    first = client.get("/jobs/next", params={"worker_id": "worker-a"})
    assert first.status_code == 200
    assert first.json()["signed_manifest"]["manifest"]["job_id"] == job_id

    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    conn = connect(db_path)
    with write_lock:
        conn.execute("UPDATE jobs SET lease_expires = ? WHERE job_id = ?", (expired, job_id))
        conn.commit()
    conn.close()

    second = client.get("/jobs/next", params={"worker_id": "worker-b"})
    assert second.status_code == 200
    assert second.json()["signed_manifest"]["manifest"]["job_id"] == job_id


def test_heartbeat_does_not_renew_expired_lease(tmp_path):
    db_path = str(tmp_path / "heartbeat.db")
    client = TestClient(create_app(db_path))
    assert client.post("/register", json={"worker_id": "worker", "cpus": 4}).status_code == 200
    submit = client.post("/jobs", json={"kind": "challenge", "input": {"x": 1}})
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert client.get("/jobs/next", params={"worker_id": "worker"}).status_code == 200

    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    conn = connect(db_path)
    with write_lock:
        conn.execute("UPDATE jobs SET lease_expires = ? WHERE job_id = ?", (expired, job_id))
        conn.commit()
    conn.close()

    heartbeat = client.post(
        "/heartbeat",
        json={"worker_id": "worker", "idle": False, "current_job_id": job_id},
    )
    assert heartbeat.status_code == 200
    state = client.get("/state").json()
    assert state["jobs"][0]["state"] == "queued"
