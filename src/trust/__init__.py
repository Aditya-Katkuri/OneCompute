"""Trust, verification, and rewards helpers for OneCompute."""

from trust.attestation import (
    canonical_claims_bytes,
    derive_tier,
    sign_attestation,
    verify_attestation,
)
from trust.challenge import check_challenge, make_challenge
from trust.metering import credits
from trust.signing import Signer, verify_manifest, verify_signature
from trust.tls import (
    build_client,
    build_server_context,
    client_ssl_params,
    server_ssl_kwargs,
)

__all__ = [
    "Signer",
    "verify_manifest",
    "verify_signature",
    "canonical_claims_bytes",
    "verify_attestation",
    "derive_tier",
    "sign_attestation",
    "make_challenge",
    "check_challenge",
    "credits",
    "build_client",
    "build_server_context",
    "client_ssl_params",
    "server_ssl_kwargs",
]
