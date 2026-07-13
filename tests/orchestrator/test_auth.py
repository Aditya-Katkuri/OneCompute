from fastapi.testclient import TestClient

from orchestrator.app import MAX_RESULT_OUTPUT_BYTES, create_app


def _register(client: TestClient, worker_id: str = "worker") -> tuple[str, dict[str, str]]:
    response = client.post("/register", json={"worker_id": worker_id, "cpus": 2})
    assert response.status_code == 200
    token = response.json()["worker_token"]
    # Elevate to 'managed' so the default (internal) workloads these tests submit are routable; a
    # fresh worker defaults to the fail-closed 'untrusted' tier (see docs/routing-policy.md).
    assert (
        client.post(f"/workers/{worker_id}/tier", json={"trust_tier": "managed"}).status_code
        == 200
    )
    return token, {"Authorization": f"Bearer {token}"}


def test_protected_worker_routes_reject_missing_authorization() -> None:
    client = TestClient(create_app(":memory:"))
    _register(client)

    assert client.get("/jobs/next", params={"worker_id": "worker"}).status_code == 401
    assert client.post("/heartbeat", json={"worker_id": "worker"}).status_code == 401
    result = client.post(
        "/results/job-1",
        json={"worker_id": "worker", "job_id": "job-1", "status": "completed"},
    )
    assert result.status_code == 401
    events = client.get("/events").json()["events"]
    assert sum(event["type"] == "auth_failed" for event in events) == 3


def test_protected_worker_routes_reject_wrong_token() -> None:
    client = TestClient(create_app(":memory:"))
    _register(client)
    wrong_auth = {"Authorization": "Bearer wrong"}

    assert (
        client.get("/jobs/next", params={"worker_id": "worker"}, headers=wrong_auth).status_code
        == 401
    )
    assert (
        client.post("/heartbeat", json={"worker_id": "worker"}, headers=wrong_auth).status_code
        == 401
    )
    result = client.post(
        "/results/job-1",
        json={"worker_id": "worker", "job_id": "job-1", "status": "completed"},
        headers=wrong_auth,
    )
    assert result.status_code == 401


def test_correct_worker_token_allows_worker_flow() -> None:
    client = TestClient(create_app(":memory:"))
    _, auth = _register(client)
    submit = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}, "units": 2})
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]

    next_job = client.get("/jobs/next", params={"worker_id": "worker"}, headers=auth)
    assert next_job.status_code == 200
    heartbeat = client.post("/heartbeat", json={"worker_id": "worker"}, headers=auth)
    assert heartbeat.status_code == 200
    result = client.post(
        f"/results/{job_id}",
        json={
            "worker_id": "worker",
            "job_id": job_id,
            "status": "completed",
            "output": {"y": 10},
        },
        headers=auth,
    )
    assert result.status_code == 200
    assert result.json()["accepted"] is True


def test_security_headers_are_present_on_read_and_dashboard_routes() -> None:
    client = TestClient(create_app(":memory:"))

    for path in ("/state", "/"):
        response = client.get(path)
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_oversized_result_payload_is_rejected_without_credit_or_completion() -> None:
    client = TestClient(create_app(":memory:"))
    _, auth = _register(client)
    submit = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}, "units": 2})
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert client.get("/jobs/next", params={"worker_id": "worker"}, headers=auth).status_code == 200

    result = client.post(
        f"/results/{job_id}",
        json={
            "worker_id": "worker",
            "job_id": job_id,
            "status": "completed",
            "output": {"blob": "x" * (MAX_RESULT_OUTPUT_BYTES + 1024)},
        },
        headers=auth,
    )
    assert result.status_code == 200
    assert result.json() == {"accepted": False, "credited": 0.0, "reason": "payload_too_large"}
    state = client.get("/state").json()
    assert state["total_credits"] == 0.0
    assert state["jobs"][0]["state"] == "leased"
