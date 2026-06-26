from fastapi.testclient import TestClient

from orchestrator.app import create_app


def test_worker_and_ledger_survive_db_reopen(tmp_path):
    """A registered worker + earned credits persist across closing and re-opening the
    same file-backed SQLite DB: the property the LAN standup depends on."""
    db_path = str(tmp_path / "persist.db")

    app = create_app(db_path)
    client = TestClient(app)

    token = client.post("/register", json={"worker_id": "w1", "cpus": 4}).json()["worker_token"]
    auth = {"Authorization": f"Bearer {token}"}
    # x=3 -> challenge expects y = x*x + 1 = 10, so {"y": 10} is a valid answer.
    submit = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}, "units": 2})
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert client.get("/jobs/next", params={"worker_id": "w1"}, headers=auth).status_code == 200
    result = client.post(
        f"/results/{job_id}",
        json={
            "worker_id": "w1",
            "job_id": job_id,
            "status": "completed",
            "output": {"y": 10},
            "units": 2,
        },
        headers=auth,
    )
    assert result.status_code == 200
    assert result.json()["accepted"] is True
    credited = result.json()["credited"]
    assert credited > 0

    # Close the underlying connection (checkpoints WAL to the main DB file).
    app.state.conn.close()

    # Re-open a brand-new app against the same file; state must still be there.
    app2 = create_app(db_path)
    client2 = TestClient(app2)
    state = client2.get("/state").json()
    worker_ids = [w["worker_id"] for w in state["workers"]]
    assert "w1" in worker_ids
    assert state["total_credits"] == credited
    w1 = next(w for w in state["workers"] if w["worker_id"] == "w1")
    assert w1["credits"] == credited
    app2.state.conn.close()


def test_reopen_existing_db_does_not_error(tmp_path):
    """Re-running init_db (via create_app) on an existing file DB is idempotent."""
    db_path = str(tmp_path / "idempotent.db")
    app = create_app(db_path)
    app.state.conn.close()
    # Second open over the same file must not raise on CREATE TABLE / CREATE INDEX.
    app2 = create_app(db_path)
    assert TestClient(app2).get("/healthz").status_code == 200
    app2.state.conn.close()
