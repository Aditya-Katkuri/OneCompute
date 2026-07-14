"""Attestation-derived device tiering at the orchestrator: registration wiring, the inert default,
verified-only tier derivation, admin-pin precedence, and end-to-end classification-aware routing.

A worker's trust tier can be DERIVED from a signed device-posture attestation verified against the
trusted authority key configured on ``create_app(attestation_pubkey=...)``. It is fail-closed and
INERT until that key is configured, never trusts the worker's self-report, and never overrides an
admin-pinned tier. See src/trust/attestation.py, the /register handler, and docs/device-attestation.md.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from contracts import DeviceAttestation
from orchestrator.app import create_app
from trust import Signer, sign_attestation

# Built as a scheme variable (never a literal Bearer f-string) on purpose.
SCHEME = "Bearer"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"{SCHEME} {token}"}


def _mint(authority: Signer, worker_id: str = "w1", ttl_hours: float = 1.0, **flags) -> dict:
    now = datetime.now(UTC)
    claims = DeviceAttestation(
        worker_id=worker_id,
        issued_at=now,
        expires_at=now + timedelta(hours=ttl_hours),
        **flags,
    )
    return sign_attestation(claims, authority).model_dump(mode="json")


def _register(client: TestClient, worker_id: str = "w1", attestation: dict | None = None) -> str:
    body: dict = {"worker_id": worker_id, "cpus": 4}
    if attestation is not None:
        body["attestation"] = attestation
    resp = client.post("/register", json=body)
    assert resp.status_code == 200
    return resp.json()["worker_token"]


def _tier(client: TestClient, worker_id: str) -> str:
    state = client.get("/state").json()
    return next(w for w in state["workers"] if w["worker_id"] == worker_id)["trust_tier"]


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


# --------------------------- inert by default --------------------------------


def test_inert_when_no_authority_key_configured():
    # No attestation_pubkey configured (today's default): a worker presenting even a validly
    # self-signed attestation is registered 'untrusted'. The attestation is ignored entirely.
    self_signer = Signer()
    client = TestClient(create_app(":memory:"))
    att = _mint(self_signer, "w1", tee=True)
    _register(client, "w1", attestation=att)
    assert _tier(client, "w1") == "untrusted"


def test_registration_without_attestation_is_unchanged():
    # Even with an authority key configured, a plain Capability-only registration still defaults to
    # the fail-closed 'untrusted' tier, so existing worker behavior is unchanged.
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    _register(client, "w1")
    assert _tier(client, "w1") == "untrusted"


# --------------------------- verified-only -----------------------------------


def test_authority_signed_attestation_sets_the_derived_tier():
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    _register(client, "w1", attestation=_mint(authority, "w1", managed=True, compliant=True))
    assert _tier(client, "w1") == "managed"


def test_attestation_signed_by_a_different_key_stays_untrusted():
    authority = Signer()
    attacker = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    _register(client, "w1", attestation=_mint(attacker, "w1", tee=True))
    assert _tier(client, "w1") == "untrusted"


def test_tampered_attestation_stays_untrusted():
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    att = _mint(authority, "w1", managed=True, compliant=True)
    att["tee"] = True  # tamper: claim a TEE the signature does not cover
    _register(client, "w1", attestation=att)
    assert _tier(client, "w1") == "untrusted"


def test_attestation_bound_to_another_worker_is_rejected():
    # A valid attestation minted for 'other' cannot be replayed by 'w1' to elevate itself.
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    stolen = _mint(authority, "other", tee=True)
    _register(client, "w1", attestation=stolen)
    assert _tier(client, "w1") == "untrusted"


def test_expired_attestation_stays_untrusted():
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    _register(client, "w1", attestation=_mint(authority, "w1", tee=True, ttl_hours=-1.0))
    assert _tier(client, "w1") == "untrusted"


def test_reregister_rederives_tier():
    # A re-register re-derives from the freshly presented attestation.
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    _register(client, "w1", attestation=_mint(authority, "w1", managed=True, compliant=True))
    assert _tier(client, "w1") == "managed"
    # Re-register with a stronger (tee) attestation: the tier moves up.
    _register(client, "w1", attestation=_mint(authority, "w1", tee=True))
    assert _tier(client, "w1") == "confidential_compute"


def test_tier_derived_event_is_audited():
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    _register(client, "w1", attestation=_mint(authority, "w1", managed=True, compliant=True))
    events = client.get("/events").json()["events"]
    derived = [e for e in events if e["type"] == "tier_derived" and e["worker_id"] == "w1"]
    assert derived and derived[-1]["detail"] == "managed"


# --------------------------- admin-pin precedence ----------------------------


def test_admin_pinned_tier_is_not_overridden_by_attestation():
    # IT explicitly pins 'sanctioned'. A later re-register with an attestation that would derive
    # only 'managed' must NOT downgrade the admin's decision.
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    _register(client, "w1")
    assert client.post("/workers/w1/tier", json={"trust_tier": "sanctioned"}).status_code == 200
    assert _tier(client, "w1") == "sanctioned"
    _register(client, "w1", attestation=_mint(authority, "w1", managed=True, compliant=True))
    assert _tier(client, "w1") == "sanctioned"  # pin wins


def test_admin_pin_holds_even_against_a_stronger_attestation():
    # The pin is authoritative in both directions: even a tee attestation cannot move a pinned tier.
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    _register(client, "w1", attestation=_mint(authority, "w1", managed=True, compliant=True))
    assert _tier(client, "w1") == "managed"
    assert client.post("/workers/w1/tier", json={"trust_tier": "managed"}).status_code == 200
    _register(client, "w1", attestation=_mint(authority, "w1", tee=True))
    assert _tier(client, "w1") == "managed"  # pinned; the stronger attestation is ignored


# --------------------------- end-to-end routing ------------------------------


def test_tee_attestation_enables_a_restricted_job_end_to_end():
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    token = _register(client, "tee-box", attestation=_mint(authority, "tee-box", tee=True))
    assert _tier(client, "tee-box") == "confidential_compute"
    job_id = _submit(client, "restricted")
    nxt = client.get("/jobs/next", params={"worker_id": "tee-box"}, headers=_auth(token))
    assert nxt.status_code == 200
    assert nxt.json()["signed_manifest"]["manifest"]["job_id"] == job_id


def test_without_attestation_a_restricted_job_is_denied():
    # Same worker, same restricted job, but no attestation -> stays untrusted -> withheld (204).
    authority = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    token = _register(client, "byod")
    _submit(client, "restricted")
    assert client.get(
        "/jobs/next", params={"worker_id": "byod"}, headers=_auth(token)
    ).status_code == 204


def test_forged_attestation_leaves_restricted_job_denied():
    # A worker forging a tee attestation with an untrusted key cannot pull a restricted job.
    authority = Signer()
    attacker = Signer()
    client = TestClient(create_app(":memory:", attestation_pubkey=authority.public_key_hex))
    token = _register(client, "liar", attestation=_mint(attacker, "liar", tee=True))
    assert _tier(client, "liar") == "untrusted"
    _submit(client, "restricted")
    assert client.get(
        "/jobs/next", params={"worker_id": "liar"}, headers=_auth(token)
    ).status_code == 204
