"""Tests for opt-in device-identity binding (STRIDE Spoofing / boundary B3).

Binds a worker's bearer token to its TLS client-cert fingerprint (the lowercase hex SHA-256 of
the cert's DER bytes, carried in the ``X-Client-Cert-SHA256`` header) so a stolen token alone is
useless without the device key. Binding is opt-in (``create_app(bind_device_identity=True)``);
with it off, behavior is exactly as before.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from contracts import Capability
from orchestrator.app import create_app

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
    client = TestClient(create_app(":memory:", bind_device_identity=True))
    token = _register(client, "w1", headers={CERT_HEADER: FINGERPRINT})
    resp = client.get(
        "/jobs/next?worker_id=w1",
        headers={**_auth(token), CERT_HEADER: FINGERPRINT},
    )
    assert resp.status_code == 204  # token + matching device fingerprint -> authenticated
    assert not _auth_failed_details(client)


def test_binding_on_wrong_fingerprint_is_401_and_audited() -> None:
    client = TestClient(create_app(":memory:", bind_device_identity=True))
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
    client = TestClient(create_app(":memory:", bind_device_identity=True))
    token = _register(client, "w1", headers={CERT_HEADER: FINGERPRINT})
    resp = client.get("/jobs/next?worker_id=w1", headers=_auth(token))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "device_fingerprint_mismatch"
    assert "device_fingerprint_mismatch" in _auth_failed_details(client)


def test_binding_on_worker_without_stored_fingerprint_is_unaffected() -> None:
    # A worker that registered without presenting a fingerprint stays token-only even when
    # binding is enabled globally (nothing to enforce against), so a mixed fleet still works.
    client = TestClient(create_app(":memory:", bind_device_identity=True))
    token = _register(client, "w2")  # no cert header on register -> unbound
    resp = client.get("/jobs/next?worker_id=w2", headers=_auth(token))
    assert resp.status_code == 204
    assert not _auth_failed_details(client)


def test_fingerprint_match_is_case_insensitive() -> None:
    # Stored lowercased on register; the presented value is lowercased before the constant-time
    # compare, so an upper/mixed-case header from a proxy still matches.
    client = TestClient(create_app(":memory:", bind_device_identity=True))
    token = _register(client, "w1", headers={CERT_HEADER: FINGERPRINT.upper()})
    resp = client.get(
        "/jobs/next?worker_id=w1",
        headers={**_auth(token), CERT_HEADER: FINGERPRINT.upper()},
    )
    assert resp.status_code == 204
    assert not _auth_failed_details(client)
