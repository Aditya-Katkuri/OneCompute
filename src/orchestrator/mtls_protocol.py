"""Uvicorn HTTP protocol that exposes the verified TLS peer certificate to ASGI."""

from __future__ import annotations

import asyncio
import hashlib

from uvicorn.protocols.http.h11_impl import H11Protocol

CLIENT_CERT_SCOPE_KEY = "onecompute.client_cert_sha256"


def peer_certificate_fingerprint(transport: asyncio.Transport) -> str | None:
    """Return the SHA-256 fingerprint of the TLS-verified peer certificate."""
    ssl_object = transport.get_extra_info("ssl_object")
    if ssl_object is None or not hasattr(ssl_object, "getpeercert"):
        return None
    try:
        certificate = ssl_object.getpeercert(binary_form=True)
    except (AttributeError, ValueError):
        return None
    if not certificate:
        return None
    return hashlib.sha256(certificate).hexdigest()


class VerifiedClientCertH11Protocol(H11Protocol):
    """Inject the verified peer-certificate fingerprint into each HTTP ASGI scope."""

    def connection_made(self, transport: asyncio.Transport) -> None:
        super().connection_made(transport)
        self.verified_client_cert_sha256 = peer_certificate_fingerprint(transport)

    def handle_events(self) -> None:
        previous_scope = self.scope
        super().handle_events()
        if self.scope is not None and self.scope is not previous_scope:
            self.scope[CLIENT_CERT_SCOPE_KEY] = self.verified_client_cert_sha256  # type: ignore[typeddict-unknown-key]
