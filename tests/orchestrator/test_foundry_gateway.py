"""Foundry routing gateway (flow F9 / boundary B6): the single ingestion point where an Azure AI
Foundry / tenant request becomes a signed, classified OneCompute job.

These tests pin the gateway's four invariants:

1. INERT by default - with no ``foundry_tenants`` configured, ``POST /foundry/jobs`` refuses every
   request and enqueues nothing; ordinary ``/jobs`` behavior is unchanged.
2. TENANT AUTH - the claimed tenant must exist AND present its own Bearer token (constant-time);
   unknown tenant / missing / wrong token / wrong scheme all fail closed with 401.
3. FAIL-CLOSED POLICY - a tenant may not route above its ``max_classification`` (by CLASSIFICATIONS
   rank) and may only route into an allow-listed region (empty allow-list = deny all); both 403.
4. SIGNED PROVENANCE - a routed job carries ``data_classification`` AND ``provenance`` inside the
   SIGNED manifest, and the classification still flows through the existing device-tier gate.

See src/orchestrator/app.py POST /foundry/jobs and docs/foundry-gateway.md.
"""

from fastapi.testclient import TestClient

from contracts import FoundryTenant, SignedManifest
from orchestrator.app import create_app
from trust import verify_manifest

# Built as a scheme variable (never a literal "Bearer <token>" f-string) so the edit tooling cannot
# rewrite it, mirroring tests/orchestrator/test_routing_tier.py.
SCHEME = "Bearer"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"{SCHEME} {token}"}


def _tenant(
    tenant_id: str = "contoso",
    token: str = "tenant-secret-abc",
    max_classification: str = "confidential",
    allowed_regions: tuple[str, ...] = ("eastus",),
) -> FoundryTenant:
    return FoundryTenant(
        tenant_id=tenant_id,
        token=token,
        max_classification=max_classification,
        allowed_regions=list(allowed_regions),
    )


def _registry(*tenants: FoundryTenant) -> dict[str, FoundryTenant]:
    return {t.tenant_id: t for t in tenants}


def _route_body(**over) -> dict:
    body = {
        "tenant_id": "contoso",
        "region": "eastus",
        "kind": "data.transform",
        "input": {"items": ["a"], "op": "upper"},
        "units": 1,
        "data_classification": "confidential",
    }
    body.update(over)
    return body


def _register(client: TestClient, worker_id: str = "w1", **cap) -> str:
    resp = client.post("/register", json={"worker_id": worker_id, "cpus": 4, **cap})
    assert resp.status_code == 200
    return resp.json()["worker_token"]


def _events(client: TestClient) -> list[dict]:
    return client.get("/events").json()["events"]


def _jobs(client: TestClient) -> list[dict]:
    return client.get("/state").json()["jobs"]


# ------------------------------- inert by default ----------------------------


def test_gateway_is_inert_without_configured_tenants():
    # No foundry_tenants -> the feature is off: the route exists but refuses everything and enqueues
    # nothing, so existing behavior is unchanged.
    client = TestClient(create_app(":memory:"))
    resp = client.post("/foundry/jobs", json=_route_body(), headers=_auth("anything"))
    assert resp.status_code == 401
    assert _jobs(client) == []
    # The refusal is audited as an auth failure prefixed "foundry:".
    assert any(
        e["type"] == "auth_failed" and (e["detail"] or "").startswith("foundry:")
        for e in _events(client)
    )


def test_inert_gateway_does_not_touch_plain_submit():
    # /jobs is untouched by the (off) gateway: it still enqueues, and its manifest carries no
    # provenance (provenance is the gateway's exclusive stamp).
    client = TestClient(create_app(":memory:"))
    token = _register(client, "w1")
    resp = client.post(
        "/jobs",
        json={"kind": "data.transform", "input": {"items": ["a"], "op": "upper"}, "units": 1},
    )
    assert resp.status_code == 200
    assert client.post("/workers/w1/tier", json={"trust_tier": "managed"}).status_code == 200
    nxt = client.get("/jobs/next", params={"worker_id": "w1"}, headers=_auth(token))
    assert nxt.status_code == 200
    assert nxt.json()["signed_manifest"]["manifest"]["provenance"] is None


# --------------------------------- tenant auth -------------------------------


def test_unknown_tenant_is_401():
    client = TestClient(create_app(":memory:", foundry_tenants=_registry(_tenant())))
    resp = client.post(
        "/foundry/jobs",
        json=_route_body(tenant_id="ghost"),
        headers=_auth("tenant-secret-abc"),
    )
    assert resp.status_code == 401
    assert _jobs(client) == []


def test_missing_and_wrong_token_are_401():
    client = TestClient(create_app(":memory:", foundry_tenants=_registry(_tenant())))
    # No Authorization header at all.
    assert client.post("/foundry/jobs", json=_route_body()).status_code == 401
    # Wrong token.
    assert client.post(
        "/foundry/jobs", json=_route_body(), headers=_auth("not-the-secret")
    ).status_code == 401
    # Wrong scheme (Basic instead of Bearer).
    assert client.post(
        "/foundry/jobs", json=_route_body(), headers={"Authorization": "Basic tenant-secret-abc"}
    ).status_code == 401
    assert _jobs(client) == []
    assert any(e["type"] == "auth_failed" for e in _events(client))


def test_correct_token_proceeds():
    client = TestClient(create_app(":memory:", foundry_tenants=_registry(_tenant())))
    resp = client.post(
        "/foundry/jobs", json=_route_body(), headers=_auth("tenant-secret-abc")
    )
    assert resp.status_code == 200
    assert resp.json()["job_id"]
    assert len(_jobs(client)) == 1


# ----------------------------- classification policy -------------------------


def test_classification_above_clearance_is_403():
    # A tenant cleared only to "internal" cannot route "confidential" data.
    tenant = _tenant(max_classification="internal")
    client = TestClient(create_app(":memory:", foundry_tenants=_registry(tenant)))
    resp = client.post(
        "/foundry/jobs",
        json=_route_body(data_classification="confidential"),
        headers=_auth(tenant.token),
    )
    assert resp.status_code == 403
    assert _jobs(client) == []
    assert any(e["type"] == "foundry_denied" for e in _events(client))


def test_classification_at_or_below_clearance_allowed():
    tenant = _tenant(max_classification="internal")
    client = TestClient(create_app(":memory:", foundry_tenants=_registry(tenant)))
    for cls in ("internal", "public"):
        resp = client.post(
            "/foundry/jobs",
            json=_route_body(data_classification=cls),
            headers=_auth(tenant.token),
        )
        assert resp.status_code == 200, cls
    assert len(_jobs(client)) == 2


# -------------------------------- region policy ------------------------------


def test_region_not_allowed_is_403():
    tenant = _tenant(allowed_regions=("eastus",))
    client = TestClient(create_app(":memory:", foundry_tenants=_registry(tenant)))
    resp = client.post(
        "/foundry/jobs", json=_route_body(region="westeurope"), headers=_auth(tenant.token)
    )
    assert resp.status_code == 403
    assert _jobs(client) == []


def test_empty_allowed_regions_denies_all():
    tenant = _tenant(allowed_regions=())
    client = TestClient(create_app(":memory:", foundry_tenants=_registry(tenant)))
    resp = client.post(
        "/foundry/jobs", json=_route_body(region="eastus"), headers=_auth(tenant.token)
    )
    assert resp.status_code == 403
    assert _jobs(client) == []


# -------------------------- signed provenance (happy path) -------------------


def test_routed_job_carries_signed_classification_and_provenance():
    tenant = _tenant(max_classification="confidential", allowed_regions=("eastus",))
    client = TestClient(create_app(":memory:", foundry_tenants=_registry(tenant)))
    resp = client.post(
        "/foundry/jobs",
        json=_route_body(data_classification="public", region="eastus"),
        headers=_auth(tenant.token),
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    # A public job routes even to an untrusted worker; pull the signed assignment to inspect it.
    token = _register(client, "byod")
    nxt = client.get("/jobs/next", params={"worker_id": "byod"}, headers=_auth(token))
    assert nxt.status_code == 200
    payload = nxt.json()["signed_manifest"]
    manifest = payload["manifest"]
    assert manifest["job_id"] == job_id
    assert manifest["data_classification"] == "public"
    # Provenance round-trips inside the manifest.
    assert manifest["provenance"] == {"tenant_id": "contoso", "region": "eastus"}
    # The manifest is genuinely signed and its signature verifies over the provenance-bearing body.
    assert payload["signature"] != ""
    assert verify_manifest(SignedManifest.model_validate(payload)) is True

    # The routing is audited with tenant/region/classification and the job id.
    routed = [e for e in _events(client) if e["type"] == "foundry_routed"]
    assert len(routed) == 1
    assert routed[0]["job_id"] == job_id
    assert "tenant=contoso" in routed[0]["detail"]
    assert "region=eastus" in routed[0]["detail"]


# --------------------- end-to-end with the existing tier gate ----------------


def test_confidential_routed_job_respects_the_device_tier_gate():
    tenant = _tenant(max_classification="confidential", allowed_regions=("eastus",))
    client = TestClient(create_app(":memory:", foundry_tenants=_registry(tenant)))
    resp = client.post(
        "/foundry/jobs",
        json=_route_body(data_classification="confidential"),
        headers=_auth(tenant.token),
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    worker_token = _register(client, "dev-box")
    auth = _auth(worker_token)
    # Untrusted device: the confidential routed job is withheld (204), same gate as /jobs uses.
    assert client.get(
        "/jobs/next", params={"worker_id": "dev-box"}, headers=auth
    ).status_code == 204

    # IT elevates the device to 'sanctioned'; only now does the confidential job route to it.
    assert client.post(
        "/workers/dev-box/tier", json={"trust_tier": "sanctioned"}
    ).status_code == 200
    nxt = client.get("/jobs/next", params={"worker_id": "dev-box"}, headers=auth)
    assert nxt.status_code == 200
    manifest = nxt.json()["signed_manifest"]["manifest"]
    assert manifest["job_id"] == job_id
    assert manifest["data_classification"] == "confidential"
    assert manifest["provenance"] == {"tenant_id": "contoso", "region": "eastus"}


def test_public_routed_job_reaches_an_untrusted_worker():
    tenant = _tenant(max_classification="confidential", allowed_regions=("eastus",))
    client = TestClient(create_app(":memory:", foundry_tenants=_registry(tenant)))
    resp = client.post(
        "/foundry/jobs",
        json=_route_body(data_classification="public"),
        headers=_auth(tenant.token),
    )
    assert resp.status_code == 200
    job_id = resp.json()["job_id"]

    worker_token = _register(client, "byod")
    nxt = client.get("/jobs/next", params={"worker_id": "byod"}, headers=_auth(worker_token))
    assert nxt.status_code == 200
    assert nxt.json()["signed_manifest"]["manifest"]["job_id"] == job_id
