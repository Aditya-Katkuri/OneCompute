"""Disconnect-a-device endpoint + least-utilized-first job routing."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from orchestrator.app import _defer_to_less_loaded, _load_score, _now, create_app
from orchestrator.db import open_serialized_db


def _register(client: TestClient, worker_id: str = "w", cpus: int = 2) -> dict[str, str]:
    response = client.post("/register", json={"worker_id": worker_id, "cpus": cpus})
    assert response.status_code == 200
    # Elevate to 'managed' so default (internal) jobs route to it; fresh workers default to the
    # fail-closed 'untrusted' tier (see docs/routing-policy.md).
    assert (
        client.post(f"/workers/{worker_id}/tier", json={"trust_tier": "managed"}).status_code
        == 200
    )
    return {"Authorization": f"Bearer {response.json()['worker_token']}"}


def _add_worker(conn, worker_id: str, cpu_pct, last_heartbeat: str, gpu_pct=None) -> None:
    conn.execute(
        "INSERT INTO workers (worker_id, token, capability_json, class_weight, cpu_pct, gpu_pct, "
        "approved, last_heartbeat, registered_at) VALUES (?, 't', '{}', 1, ?, ?, 1, ?, ?)",
        (worker_id, cpu_pct, gpu_pct, last_heartbeat, last_heartbeat),
    )
    conn.commit()


# --------------------------- disconnect endpoint ---------------------------


def test_disconnect_removes_worker_from_fleet():
    client = TestClient(create_app(":memory:"))
    _register(client, worker_id="w1")
    assert any(w["worker_id"] == "w1" for w in client.get("/state").json()["workers"])

    resp = client.delete("/workers/w1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "worker_id": "w1"}
    assert not any(w["worker_id"] == "w1" for w in client.get("/state").json()["workers"])


def test_disconnect_unknown_worker_is_404():
    client = TestClient(create_app(":memory:"))
    assert client.delete("/workers/nope").status_code == 404


def test_disconnect_requeues_in_flight_job():
    client = TestClient(create_app(":memory:"))
    auth = _register(client, worker_id="w1", cpus=4)
    job_id = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}, "units": 1}).json()[
        "job_id"
    ]
    assert client.get("/jobs/next", params={"worker_id": "w1"}, headers=auth).status_code == 200

    client.delete("/workers/w1")  # the job was leased to w1: disconnect must return it to the queue

    job = next(j for j in client.get("/state").json()["jobs"] if j["job_id"] == job_id)
    assert job["state"] == "queued"
    assert not job["assigned_worker"]


def test_disconnected_worker_does_not_spam_auth_feed():
    # A removed-but-still-running agent fails auth quietly (no auth_failed flood in the feed).
    client = TestClient(create_app(":memory:"))
    auth = _register(client, worker_id="w1")
    client.delete("/workers/w1")
    assert client.post("/heartbeat", json={"worker_id": "w1"}, headers=auth).status_code == 401
    feed = client.get("/events", params={"since": 0}).json()["events"]
    assert not any(e["type"] == "auth_failed" for e in feed)


# ----------------------- least-utilized-first routing -----------------------


def test_load_score_uses_busier_resource():
    assert _load_score(30, 80) == 80.0
    assert _load_score(90, 10) == 90.0
    assert _load_score(None, None) == 0.0


def test_defers_to_clearly_lighter_live_peer():
    conn = open_serialized_db(":memory:")
    now = _now()
    _add_worker(conn, "busy", 90, now)
    _add_worker(conn, "idle", 5, now)
    # the heavily-loaded worker yields fresh work to the much lighter idle peer
    assert _defer_to_less_loaded(conn, "busy", now) is True
    # the idle worker has no lighter peer, so it keeps the work itself
    assert _defer_to_less_loaded(conn, "idle", now) is False


def test_no_defer_when_loads_are_similar():
    conn = open_serialized_db(":memory:")
    now = _now()
    _add_worker(conn, "a", 40, now)
    _add_worker(conn, "b", 35, now)  # within the margin, no priority flip
    assert _defer_to_less_loaded(conn, "a", now) is False


def test_no_defer_once_job_is_past_grace():
    conn = open_serialized_db(":memory:")
    _add_worker(conn, "busy", 90, _now())
    _add_worker(conn, "idle", 5, _now())
    stale_job = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    assert _defer_to_less_loaded(conn, "busy", stale_job) is False  # no starvation


def test_no_defer_to_a_stale_peer():
    conn = open_serialized_db(":memory:")
    now = _now()
    stale = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    _add_worker(conn, "busy", 90, now)
    _add_worker(conn, "ghost", 5, stale)  # idle but not heartbeating, not a live competitor
    assert _defer_to_less_loaded(conn, "busy", now) is False
