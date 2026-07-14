"""Attestation-derived device trust tiers (fail-closed, inert until configured).

A worker may present a signed :class:`~contracts.models.DeviceAttestation` at registration: a
device-posture claim (``compliant``/``managed``/``sanctioned``/``tee``) bound to its ``worker_id``
and a time window. The orchestrator derives a routing ``trust_tier`` from that claim ONLY after
verifying its Ed25519 signature against the trusted attestation AUTHORITY key it was configured with
(``create_app(attestation_pubkey=...)``). This mirrors deriving a device's tier from Intune/Entra
device compliance plus an Azure Attestation (MAA) TEE claim, with Ed25519 standing in for the real
authority in the PoC. See docs/device-attestation.md, docs/azure-routing.md §5, and
docs/routing-policy.md.

Security posture (deliberately mirrors the pinned-signer manifest trust in ``trust.signing`` and the
fail-closed, inert MXC backend in ``isolation.mxc``):

* **INERT until configured.** With no trusted authority key, ``verify_attestation`` returns False, so
  no tier is ever derived. Default behavior is therefore unchanged: a worker stays ``untrusted``.
* **VERIFIED-ONLY.** The signature is checked against the trusted authority key, NEVER against the
  attestation's own advisory ``signer_pubkey`` and NEVER against the worker's self-report. A claim
  signed by any other key (including the worker signing its own posture) fails closed.
* **BOUND to the device.** The claim's ``worker_id`` must equal the registering worker, so one
  device's attestation cannot be replayed to elevate another.
* **TIME-BOXED.** An expired attestation (``expires_at`` in the past) is rejected.
* **FAIL-CLOSED derivation.** Anything short of a verified, sufficient posture maps to ``untrusted``.

This module is pure (no I/O, no orchestrator imports) and ``verify_attestation`` never raises, so it
is safe on the registration path.
"""

from __future__ import annotations

from datetime import datetime

from contracts import DeviceAttestation
from contracts.hashing import canonical_bytes
from trust.signing import Signer, verify_signature

# Routing tiers this module can derive, low to high. These MUST match the ladder in
# src/orchestrator/routing_policy.py (TIERS); tiering lives one layer up, so we name the strings
# here to avoid importing the orchestrator into the trust layer. tests/trust/test_attestation.py
# pins that every derived tier is a valid routing tier.
TIER_UNTRUSTED = "untrusted"
TIER_MANAGED = "managed"
TIER_SANCTIONED = "sanctioned"
TIER_CONFIDENTIAL_COMPUTE = "confidential_compute"

# The exact claim fields the signature covers: posture flags bound to the device and time window,
# EXCLUDING signature/signer_pubkey (which are not part of what is signed).
_CLAIM_FIELDS = frozenset(
    {"worker_id", "compliant", "managed", "sanctioned", "tee", "issued_at", "expires_at"}
)


def canonical_claims_bytes(att: DeviceAttestation) -> bytes:
    """Deterministic bytes the authority signs and the orchestrator verifies.

    Serializes only the posture claims bound to the device and time window (``worker_id`` plus the
    four posture flags plus ``issued_at``/``expires_at``), EXCLUDING ``signature`` and
    ``signer_pubkey``. Uses pydantic's JSON mode (datetimes as ISO-8601 strings) then the shared
    canonical JSON encoder, so the signer and verifier compute byte-identical input even across an
    HTTP round trip.
    """
    claims = att.model_dump(mode="json", include=_CLAIM_FIELDS)
    return canonical_bytes(claims)


def verify_attestation(
    att: DeviceAttestation,
    trusted_authority_pubkey_hex: str | None,
    worker_id: str,
    now: datetime,
) -> bool:
    """Return True only if EVERY guard holds; fail closed (False) otherwise. Never raises.

    Guards, in order:

    1. **Configured.** A trusted authority key is set (else the feature is inert -> False).
    2. **Verified.** The signature verifies against THAT trusted authority key over
       ``canonical_claims_bytes(att)`` -- not against ``att.signer_pubkey`` (advisory only).
    3. **Bound.** ``att.worker_id`` equals the registering ``worker_id`` (no cross-device replay).
    4. **Unexpired.** ``att.expires_at`` is None or strictly after ``now``.

    Any malformed input (bad hex, wrong type, naive/aware datetime mismatch, missing signature) is
    swallowed and returns False, so a hostile payload can never elevate a device or raise.
    """
    try:
        if not trusted_authority_pubkey_hex:
            return False  # inert until an attestation authority is configured
        if not isinstance(att, DeviceAttestation):
            return False
        if not att.signature:
            return False
        if att.worker_id != worker_id:
            return False  # bound to this device: another device's attestation cannot be replayed
        if att.expires_at is not None and att.expires_at <= now:
            return False  # expired
        # Verify against the TRUSTED AUTHORITY key, never att.signer_pubkey (advisory).
        return verify_signature(
            att.signature, canonical_claims_bytes(att), trusted_authority_pubkey_hex
        )
    except Exception:
        return False


def derive_tier(att: DeviceAttestation) -> str:
    """Map a VERIFIED posture claim to a routing tier, fail-closed.

    Call ONLY after ``verify_attestation`` has accepted the claim. The mapping, highest first:

    * ``tee``                                -> ``confidential_compute``
    * ``managed and compliant and sanctioned`` -> ``sanctioned``
    * ``managed and compliant``              -> ``managed``
    * anything less (or malformed)           -> ``untrusted``

    Any error reading the claim collapses to ``untrusted`` (fail closed).
    """
    try:
        if att.tee:
            return TIER_CONFIDENTIAL_COMPUTE
        if att.managed and att.compliant and att.sanctioned:
            return TIER_SANCTIONED
        if att.managed and att.compliant:
            return TIER_MANAGED
    except Exception:
        return TIER_UNTRUSTED
    return TIER_UNTRUSTED


def sign_attestation(claims: DeviceAttestation, authority: Signer) -> DeviceAttestation:
    """Mint a valid authority-signed attestation from ``claims`` (test/tooling helper).

    Signs ``canonical_claims_bytes(claims)`` with the attestation ``authority``'s Ed25519 key and
    stamps the hex signature plus the (advisory) ``signer_pubkey`` onto a copy. The SERVER verifies
    against its own configured authority key, so ``signer_pubkey`` here is diagnostic only. This
    lets tests and provisioning tooling produce a claim that ``verify_attestation`` will accept when
    that same authority key is configured on the orchestrator.
    """
    signature = authority.sign_bytes(canonical_claims_bytes(claims))
    return claims.model_copy(
        update={"signature": signature, "signer_pubkey": authority.public_key_hex}
    )
