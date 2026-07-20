"""Tests for opt-in device-identity binding (STRIDE Spoofing / boundary B3).

Binds a worker's bearer token to the SHA-256 fingerprint derived from the TLS-verified peer
certificate, so a stolen token alone is useless without the device key. Tests inject the
fingerprint into the same ASGI scope key as the production Uvicorn protocol.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from contracts import Capability
from orchestrator.app import create_app
from orchestrator.mtls_protocol import CLIENT_CERT_SCOPE_KEY

CERT_HEADER = "X-Client-Cert-SHA256"
FINGERPRINT = "a" * 64          # a plausible lowercase hex SHA-256
OTHER_FINGERPRINT = "b" * 64


def _auth(token: str) -> dict[str, str]:
    # Assemble the scheme so the token literal is never redacted to asterisks.
    return {"Authorization": f"{'Bearer'} {token}"}


def _register(client: TestClient, worker_id: str, headers: dict[str, str] | None = None) -> str:
    resp = client.post(
        "/register", json=Capability(worker_id=worker_id).model_dump(), headers=headers
    )
    assert resp.status_code == 200
    return resp.json()["worker_token"]


def _bound_client(db_path: str = ":memory:") -> TestClient:
    app = create_app(db_path, bind_device_identity=True)

    @app.middleware("http")
    async def inject_verified_test_certificate(request, call_next):
        fingerprint = request.headers.get(CERT_HEADER)
        if fingerprint:
            request.scope[CLIENT_CERT_SCOPE_KEY] = fingerprint
        return await call_next(request)

    return TestClient(app)


def _auth_failed_details(client: TestClient) -> list[str]:
    events = client.get("/events").json()["events"]
    return [e["detail"] for e in events if e["type"] == "auth_failed"]


def test_binding_off_authenticated_call_needs_no_cert_header() -> None:
    # Regression guard: with binding OFF the existing token-only flow works and no cert header
    # is required on register or on an authenticated call.
    client = TestClient(create_app(":memory:"))
    token = _register(client, "w1")
    resp = client.get("/jobs/next?worker_id=w1", headers=_auth(token))
    assert resp.status_code == 204  # authed OK; simply no work queued
    assert not _auth_failed_details(client)


def test_binding_on_matching_fingerprint_succeeds() -> None:
    client = _bound_client()
    token = _register(client, "w1", headers={CERT_HEADER: FINGERPRINT})
    resp = client.get(
        "/jobs/next?worker_id=w1",
        headers={**_auth(token), CERT_HEADER: FINGERPRINT},
    )
    assert resp.status_code == 204  # token + matching device fingerprint -> authenticated
    assert not _auth_failed_details(client)


def test_binding_on_wrong_fingerprint_is_401_and_audited() -> None:
    client = _bound_client()
    token = _register(client, "w1", headers={CERT_HEADER: FINGERPRINT})
    resp = client.get(
        "/jobs/next?worker_id=w1",
        headers={**_auth(token), CERT_HEADER: OTHER_FINGERPRINT},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "device_fingerprint_mismatch"
    assert "device_fingerprint_mismatch" in _auth_failed_details(client)


def test_binding_on_absent_fingerprint_is_401_and_audited() -> None:
    # The right token but NO device fingerprint header is rejected for a bound worker: this is
    # exactly the stolen-token-without-the-device case B3 defends against.
    client = _bound_client()
    token = _register(client, "w1", headers={CERT_HEADER: FINGERPRINT})
    resp = client.get("/jobs/next?worker_id=w1", headers=_auth(token))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "device_fingerprint_mismatch"
    assert "device_fingerprint_mismatch" in _auth_failed_details(client)


def test_binding_on_requires_fingerprint_at_registration() -> None:
    client = _bound_client()
    response = client.post("/register", json=Capability(worker_id="w2").model_dump())

    assert response.status_code == 401
    assert response.json()["detail"] == "verified client certificate fingerprint required"


def test_fingerprint_match_is_case_insensitive() -> None:
    # Stored lowercased on register; the presented value is lowercased before the constant-time
    # compare, so an upper/mixed-case header from a proxy still matches.
    client = _bound_client()
    token = _register(client, "w1", headers={CERT_HEADER: FINGERPRINT.upper()})
    resp = client.get(
        "/jobs/next?worker_id=w1",
        headers={**_auth(token), CERT_HEADER: FINGERPRINT.upper()},
    )
    assert resp.status_code == 204
    assert not _auth_failed_details(client)


def test_mismatched_reregistration_is_rejected_before_token_rotation() -> None:
    client = _bound_client()
    original = _register(client, "w1", headers={CERT_HEADER: FINGERPRINT})

    collision = client.post(
        "/register",
        json=Capability(worker_id="w1").model_dump(),
        headers={CERT_HEADER: OTHER_FINGERPRINT},
    )

    assert collision.status_code == 403
    still_valid = client.get(
        "/jobs/next?worker_id=w1",
        headers={**_auth(original), CERT_HEADER: FINGERPRINT},
    )
    assert still_valid.status_code == 204


def test_matching_reregistration_can_rotate_token() -> None:
    client = _bound_client()
    original = _register(client, "w1", headers={CERT_HEADER: FINGERPRINT})

    replacement = _register(client, "w1", headers={CERT_HEADER: FINGERPRINT})

    assert replacement != original
    old = client.get(
        "/jobs/next?worker_id=w1",
        headers={**_auth(original), CERT_HEADER: FINGERPRINT},
    )
    assert old.status_code == 401
    current = client.get(
        "/jobs/next?worker_id=w1",
        headers={**_auth(replacement), CERT_HEADER: FINGERPRINT},
    )
    assert current.status_code == 204


def test_legacy_unbound_id_must_be_removed_before_bound_enrollment(tmp_path) -> None:
    db = tmp_path / "pilot.db"
    legacy = TestClient(create_app(str(db)))
    _register(legacy, "w1")
    secured = _bound_client(str(db))

    response = secured.post(
        "/register",
        json=Capability(worker_id="w1").model_dump(),
        headers={CERT_HEADER: FINGERPRINT},
    )

    assert response.status_code == 409
    assert "remove and re-enroll" in response.json()["detail"]


def test_client_supplied_fingerprint_header_is_never_trusted_directly() -> None:
    client = TestClient(create_app(":memory:", bind_device_identity=True))

    response = client.post(
        "/register",
        json=Capability(worker_id="w1").model_dump(),
        headers={CERT_HEADER: FINGERPRINT},
    )

    assert response.status_code == 401
