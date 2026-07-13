"""Dashboard-approval gate (device-code admission).

require_approval=True: a joining worker is PENDING (approved False, device_code shown),
GET /jobs/next yields nothing until an admin approves, then work flows normally and the
heartbeat reflects the flipped approval state. require_approval=False keeps the old behavior.
"""

from fastapi.testclient import TestClient

from orchestrator.app import create_app

CPU_CAP = {"worker_id": "cpu-1", "cpus": 8, "has_gpu": False}


def _submit_cpu_job(client: TestClient) -> str:
    resp = client.post(
        "/jobs",
        json={
            "kind": "data.transform",
            "input": {"items": ["a"], "op": "upper"},
            "units": 1,
        },
    )
    assert resp.status_code == 200
    return resp.json()["job_id"]


def test_gated_worker_is_pending_then_approved_then_leases():
    client = TestClient(create_app(":memory:", require_approval=True))

    reg = client.post("/register", json=CPU_CAP)
    assert reg.status_code == 200
    # Elevate to 'managed' so the default (internal) job is routable once approved; fresh workers
    # default to the fail-closed 'untrusted' tier (see docs/routing-policy.md). The approval gate is
    # independent and still blocks a pending worker below.
    assert client.post(
        "/workers/cpu-1/tier", json={"trust_tier": "managed"}
    ).status_code == 200
    body = reg.json()
    auth = {"Authorization": f"Bearer {body['worker_token']}"}
    assert body["approved"] is False
    assert body["device_code"]  # short human code is present while pending

    # /state surfaces the pending worker with its device code for the dashboard.
    state = client.get("/state").json()
    pending = next(w for w in state["workers"] if w["worker_id"] == "cpu-1")
    assert pending["approved"] is False
    assert pending["device_code"] == body["device_code"]

    job_id = _submit_cpu_job(client)

    # A matching job exists, but a pending worker gets no work.
    assert client.get("/jobs/next", params={"worker_id": "cpu-1"}, headers=auth).status_code == 204

    # Heartbeat reports the worker is still not approved.
    hb = client.post("/heartbeat", json={"worker_id": "cpu-1"}, headers=auth)
    assert hb.status_code == 200
    assert hb.json()["approved"] is False

    # Admin approves via the dashboard endpoint.
    approve = client.post("/workers/cpu-1/approve")
    assert approve.status_code == 200
    assert approve.json() == {"ok": True, "worker_id": "cpu-1"}

    # Heartbeat now reflects approval; device code is cleared on /state.
    assert client.post("/heartbeat", json={"worker_id": "cpu-1"}, headers=auth).json()["approved"] is True
    state = client.get("/state").json()
    joined = next(w for w in state["workers"] if w["worker_id"] == "cpu-1")
    assert joined["approved"] is True
    assert joined["device_code"] is None

    # The matching job now leases to the approved worker.
    nxt = client.get("/jobs/next", params={"worker_id": "cpu-1"}, headers=auth)
    assert nxt.status_code == 200
    assert nxt.json()["signed_manifest"]["manifest"]["job_id"] == job_id


def test_approve_unknown_worker_is_404():
    client = TestClient(create_app(":memory:", require_approval=True))
    assert client.post("/workers/nope/approve").status_code == 404


def test_default_flow_is_unchanged_without_approval():
    client = TestClient(create_app(":memory:"))  # require_approval defaults to False

    reg = client.post("/register", json=CPU_CAP)
    assert reg.status_code == 200
    # Elevate to 'managed' so the default (internal) job routes; fresh workers default to the
    # fail-closed 'untrusted' tier (see docs/routing-policy.md).
    assert client.post(
        "/workers/cpu-1/tier", json={"trust_tier": "managed"}
    ).status_code == 200
    body = reg.json()
    auth = {"Authorization": f"Bearer {body['worker_token']}"}
    assert body["approved"] is True
    assert body["device_code"] is None

    job_id = _submit_cpu_job(client)

    # No approval needed: work leases immediately.
    nxt = client.get("/jobs/next", params={"worker_id": "cpu-1"}, headers=auth)
    assert nxt.status_code == 200
    assert nxt.json()["signed_manifest"]["manifest"]["job_id"] == job_id

    # Heartbeat reports approved by default; /state shows no pending code.
    assert client.post("/heartbeat", json={"worker_id": "cpu-1"}, headers=auth).json()["approved"] is True
    joined = next(w for w in client.get("/state").json()["workers"] if w["worker_id"] == "cpu-1")
    assert joined["approved"] is True
    assert joined["device_code"] is None


def test_reregister_does_not_demote_approved_worker():
    client = TestClient(create_app(":memory:", require_approval=True))
    client.post("/register", json=CPU_CAP)
    client.post("/workers/cpu-1/approve")

    # A re-register (e.g. worker restart) must not push an approved worker back to pending.
    reg = client.post("/register", json=CPU_CAP)
    assert reg.json()["approved"] is True
    assert reg.json()["device_code"] is None


def test_admin_endpoints_require_the_operator_token_when_set():
    # With an operator token configured, approve/disconnect are admin-gated so the pending worker
    # (the very actor the approval gate excludes) cannot self-approve. Closes the B3 self-admit
    # bypass where an unauthenticated approve let a rogue device lease real work and accrue credit.
    scheme = "Bearer"
    token = "op-secret-123"
    client = TestClient(create_app(":memory:", require_approval=True, submit_token=token))
    client.post("/register", json=CPU_CAP)

    # No credential and a wrong credential are both rejected; the worker stays pending.
    assert client.post("/workers/cpu-1/approve").status_code == 401
    assert client.post(
        "/workers/cpu-1/approve", headers={"Authorization": f"{scheme} wrong"}
    ).status_code == 401
    assert client.delete("/workers/cpu-1").status_code == 401
    assert client.get("/state").json()["workers"][0]["approved"] is False

    admin = {"Authorization": f"{scheme} {token}"}
    ok = client.post("/workers/cpu-1/approve", headers=admin)
    assert ok.status_code == 200 and ok.json() == {"ok": True, "worker_id": "cpu-1"}

    # The same operator token gates disconnect.
    assert client.delete("/workers/cpu-1", headers=admin).status_code == 200

