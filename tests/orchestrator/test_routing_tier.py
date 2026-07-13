"""Server-assigned device-tier routing: registration defaults, the admin tier endpoint, and the
end-to-end scheduling behavior (classification-aware, fail-closed).

The device trust tier is assigned server-side (never from the worker's self-report) and defaults to
the lowest tier, so high-sensitivity data can never land on a low-trust device. See
src/orchestrator/routing_policy.py, the POST /workers/{id}/tier endpoint, and docs/routing-policy.md.
"""

from fastapi.testclient import TestClient

from orchestrator.app import create_app

# Built as a scheme variable (never a literal "Bearer <token>" f-string) on purpose.
SCHEME = "Bearer"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"{SCHEME} {token}"}


def _register(client: TestClient, worker_id: str = "w1", **cap) -> str:
    resp = client.post("/register", json={"worker_id": worker_id, "cpus": 4, **cap})
    assert resp.status_code == 200
    return resp.json()["worker_token"]


def _worker_view(client: TestClient, worker_id: str) -> dict:
    state = client.get("/state").json()
    return next(w for w in state["workers"] if w["worker_id"] == worker_id)


def _submit(client: TestClient, classification: str) -> str:
    resp = client.post(
        "/jobs",
        json={
            "kind": "data.transform",
            "input": {"items": ["a"], "op": "upper"},
            "units": 1,
            "data_classification": classification,
        },
    )
    assert resp.status_code == 200
    return resp.json()["job_id"]


# --------------------------- registration defaults ---------------------------


def test_register_defaults_new_worker_to_untrusted():
    client = TestClient(create_app(":memory:"))
    _register(client, "w1")
    assert _worker_view(client, "w1")["trust_tier"] == "untrusted"


def test_self_reported_capability_tier_is_ignored():
    # A worker can advertise any advisory tier in its Capability, but the orchestrator NEVER uses it
    # for routing (mirrors the has_gpu / credit rule). The server tier stays the fail-closed default.
    client = TestClient(create_app(":memory:"))
    token = _register(client, "liar", attested_tier="confidential_compute")
    assert _worker_view(client, "liar")["trust_tier"] == "untrusted"

    # The self-report grants no routing: a confidential job is still withheld (204).
    _submit(client, "confidential")
    assert client.get(
        "/jobs/next", params={"worker_id": "liar"}, headers=_auth(token)
    ).status_code == 204


# ----------------------------- the tier endpoint -----------------------------


def test_tier_endpoint_requires_operator_token_when_set():
    op_token = "op-secret-123"
    client = TestClient(create_app(":memory:", submit_token=op_token))
    _register(client, "w1")
    admin = _auth(op_token)

    # No credential and a wrong credential are both rejected; the tier stays the default.
    assert client.post("/workers/w1/tier", json={"trust_tier": "managed"}).status_code == 401
    assert client.post(
        "/workers/w1/tier", json={"trust_tier": "managed"}, headers=_auth("wrong")
    ).status_code == 401
    # Auth is checked BEFORE the 404: an unknown worker without a token is 401, not 404.
    assert client.post("/workers/nope/tier", json={"trust_tier": "managed"}).status_code == 401
    assert _worker_view(client, "w1")["trust_tier"] == "untrusted"

    # With the operator token it succeeds and the new tier is surfaced on /state.
    ok = client.post("/workers/w1/tier", json={"trust_tier": "sanctioned"}, headers=admin)
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "worker_id": "w1", "trust_tier": "sanctioned"}
    assert _worker_view(client, "w1")["trust_tier"] == "sanctioned"


def test_tier_endpoint_is_open_without_operator_token():
    # No operator token configured (the local demo): the admin gate is open, like /approve.
    client = TestClient(create_app(":memory:"))
    _register(client, "w1")
    ok = client.post("/workers/w1/tier", json={"trust_tier": "managed"})
    assert ok.status_code == 200
    assert _worker_view(client, "w1")["trust_tier"] == "managed"


def test_tier_endpoint_rejects_unknown_tier():
    client = TestClient(create_app(":memory:"))
    _register(client, "w1")
    resp = client.post("/workers/w1/tier", json={"trust_tier": "super-secret"})
    assert resp.status_code == 400
    # A rejected assignment leaves the worker at the fail-closed default.
    assert _worker_view(client, "w1")["trust_tier"] == "untrusted"


def test_tier_endpoint_unknown_worker_is_404():
    client = TestClient(create_app(":memory:"))
    assert client.post("/workers/ghost/tier", json={"trust_tier": "managed"}).status_code == 404


def test_reregister_preserves_an_elevated_tier():
    client = TestClient(create_app(":memory:"))
    _register(client, "w1")
    assert client.post("/workers/w1/tier", json={"trust_tier": "sanctioned"}).status_code == 200
    # A worker restart (re-register) must NOT reset an admin-assigned tier back to the default.
    _register(client, "w1")
    assert _worker_view(client, "w1")["trust_tier"] == "sanctioned"


# ------------------------------ end-to-end routing ---------------------------


def test_confidential_job_denied_to_untrusted_then_allowed_after_elevation():
    client = TestClient(create_app(":memory:"))
    token = _register(client, "dev-box")
    auth = _auth(token)
    job_id = _submit(client, "confidential")

    # Untrusted device: the confidential job is withheld (204), never leaked to a low-trust box.
    assert client.get(
        "/jobs/next", params={"worker_id": "dev-box"}, headers=auth
    ).status_code == 204

    # 'managed' is still not enough for confidential data (it needs 'sanctioned').
    assert client.post("/workers/dev-box/tier", json={"trust_tier": "managed"}).status_code == 200
    assert client.get(
        "/jobs/next", params={"worker_id": "dev-box"}, headers=auth
    ).status_code == 204

    # IT elevates the device to 'sanctioned'; only now does the job route to it.
    assert client.post(
        "/workers/dev-box/tier", json={"trust_tier": "sanctioned"}
    ).status_code == 200
    nxt = client.get("/jobs/next", params={"worker_id": "dev-box"}, headers=auth)
    assert nxt.status_code == 200
    manifest = nxt.json()["signed_manifest"]["manifest"]
    assert manifest["job_id"] == job_id
    # The classification rides inside the SIGNED manifest, so it is tamper-evident.
    assert manifest["data_classification"] == "confidential"


def test_public_job_routes_to_an_untrusted_worker():
    client = TestClient(create_app(":memory:"))
    token = _register(client, "byod")
    job_id = _submit(client, "public")
    nxt = client.get("/jobs/next", params={"worker_id": "byod"}, headers=_auth(token))
    assert nxt.status_code == 200
    assert nxt.json()["signed_manifest"]["manifest"]["job_id"] == job_id


def test_default_unclassified_job_is_internal_and_needs_managed():
    # A job submitted with no classification defaults to the conservative "internal", which an
    # untrusted worker cannot run until it is elevated to at least 'managed'.
    client = TestClient(create_app(":memory:"))
    token = _register(client, "w1")
    auth = _auth(token)
    resp = client.post(
        "/jobs",
        json={"kind": "data.transform", "input": {"items": ["a"], "op": "upper"}, "units": 1},
    )
    assert resp.status_code == 200
    manifest_default = resp.json()
    job_id = manifest_default["job_id"]

    assert client.get("/jobs/next", params={"worker_id": "w1"}, headers=auth).status_code == 204
    assert client.post("/workers/w1/tier", json={"trust_tier": "managed"}).status_code == 200
    nxt = client.get("/jobs/next", params={"worker_id": "w1"}, headers=auth)
    assert nxt.status_code == 200
    got = nxt.json()["signed_manifest"]["manifest"]
    assert got["job_id"] == job_id
    assert got["data_classification"] == "internal"
