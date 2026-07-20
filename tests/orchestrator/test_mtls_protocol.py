import hashlib

from uvicorn.protocols.http.h11_impl import H11Protocol

from orchestrator.mtls_protocol import (
    CLIENT_CERT_SCOPE_KEY,
    VerifiedClientCertH11Protocol,
    peer_certificate_fingerprint,
)


class _FakeSslObject:
    def __init__(self, certificate: bytes | None) -> None:
        self.certificate = certificate

    def getpeercert(self, *, binary_form: bool = False):
        assert binary_form is True
        return self.certificate


class _FakeTransport:
    def __init__(self, certificate: bytes | None) -> None:
        self.ssl_object = _FakeSslObject(certificate)

    def get_extra_info(self, name: str):
        return self.ssl_object if name == "ssl_object" else None


def test_peer_certificate_fingerprint_uses_verified_tls_peer_der() -> None:
    certificate = b"verified-client-certificate"

    fingerprint = peer_certificate_fingerprint(_FakeTransport(certificate))

    assert fingerprint == hashlib.sha256(certificate).hexdigest()


def test_protocol_injects_verified_fingerprint_into_request_scope(monkeypatch) -> None:
    protocol = object.__new__(VerifiedClientCertH11Protocol)
    protocol.scope = None
    protocol.verified_client_cert_sha256 = "a" * 64

    def create_scope(self) -> None:
        self.scope = {"type": "http"}

    monkeypatch.setattr(H11Protocol, "handle_events", create_scope)

    protocol.handle_events()

    assert protocol.scope[CLIENT_CERT_SCOPE_KEY] == "a" * 64
