"""Unit tests for the pure attestation module: signature verification (against the trusted
AUTHORITY key, never the claim's own signer_pubkey), device binding, expiry, and the fail-closed
tier derivation matrix.

The orchestrator derives a device trust tier from a VERIFIED device-posture attestation instead of
the worker's self-report. Verification is inert until an authority key is configured, checks the
signature against THAT key, binds the claim to the registering device, and rejects an expired claim.
See src/trust/attestation.py and docs/device-attestation.md.
"""

from datetime import UTC, datetime, timedelta

from contracts import DeviceAttestation
from orchestrator.routing_policy import is_valid_tier
from trust import Signer, derive_tier, sign_attestation, verify_attestation
from trust.attestation import canonical_claims_bytes

NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _claims(worker_id: str = "w1", expires_at=None, **flags) -> DeviceAttestation:
    return DeviceAttestation(
        worker_id=worker_id,
        issued_at=NOW,
        expires_at=expires_at if expires_at is not None else NOW + timedelta(hours=1),
        **flags,
    )


# --------------------------- verification: inert / verified-only -------------


def test_inert_without_authority_key_rejects_even_a_valid_signature():
    # No trusted authority key configured -> the feature is inert and any attestation is ignored,
    # even a validly-signed one. This is what keeps default behavior unchanged.
    authority = Signer()
    att = sign_attestation(_claims(tee=True), authority)
    assert verify_attestation(att, None, "w1", NOW) is False
    assert verify_attestation(att, "", "w1", NOW) is False


def test_valid_authority_signature_verifies():
    authority = Signer()
    att = sign_attestation(_claims(managed=True, compliant=True), authority)
    assert verify_attestation(att, authority.public_key_hex, "w1", NOW) is True


def test_signature_from_a_different_key_is_rejected():
    # A claim signed by some other key (e.g. the worker self-signing its own posture) does not
    # verify against the trusted authority key -> fail closed.
    attacker = Signer()
    authority = Signer()
    att = sign_attestation(_claims(tee=True), attacker)
    assert verify_attestation(att, authority.public_key_hex, "w1", NOW) is False


def test_signer_pubkey_is_advisory_and_never_trusted():
    # An attacker signs with its own key AND sets signer_pubkey to match, so the claim is internally
    # consistent. Verification uses the configured AUTHORITY key, not signer_pubkey, so it is still
    # rejected. This is the core "never trust the self-report" invariant.
    attacker = Signer()
    authority = Signer()
    att = sign_attestation(_claims(tee=True), attacker)
    assert att.signer_pubkey == attacker.public_key_hex  # advisory field matches the attacker
    assert verify_attestation(att, authority.public_key_hex, "w1", NOW) is False
    # Even verifying against the (attacker) signer_pubkey would pass, proving why we must not:
    assert verify_attestation(att, att.signer_pubkey, "w1", NOW) is True


def test_tampered_claim_is_rejected():
    authority = Signer()
    att = sign_attestation(_claims(managed=True, compliant=True), authority)
    # Flip a posture flag after signing: the signature no longer covers the claim.
    tampered = att.model_copy(update={"tee": True})
    assert verify_attestation(tampered, authority.public_key_hex, "w1", NOW) is False


def test_unsigned_attestation_is_rejected():
    authority = Signer()
    att = _claims(tee=True)  # signature == ""
    assert att.signature == ""
    assert verify_attestation(att, authority.public_key_hex, "w1", NOW) is False


# --------------------------- verification: bound / expiry --------------------


def test_attestation_is_bound_to_its_worker_id():
    # An attestation minted for w1 cannot be replayed to elevate w2.
    authority = Signer()
    att = sign_attestation(_claims(worker_id="w1", tee=True), authority)
    assert verify_attestation(att, authority.public_key_hex, "w1", NOW) is True
    assert verify_attestation(att, authority.public_key_hex, "w2", NOW) is False


def test_expired_attestation_is_rejected():
    authority = Signer()
    att = sign_attestation(
        _claims(managed=True, compliant=True, expires_at=NOW - timedelta(seconds=1)), authority
    )
    assert verify_attestation(att, authority.public_key_hex, "w1", NOW) is False


def test_expires_at_exactly_now_is_rejected():
    authority = Signer()
    att = sign_attestation(_claims(managed=True, compliant=True, expires_at=NOW), authority)
    assert verify_attestation(att, authority.public_key_hex, "w1", NOW) is False


def test_no_expiry_is_accepted():
    authority = Signer()
    claims = DeviceAttestation(worker_id="w1", issued_at=NOW, expires_at=None, managed=True,
                               compliant=True)
    att = sign_attestation(claims, authority)
    assert verify_attestation(att, authority.public_key_hex, "w1", NOW) is True


def test_malformed_authority_key_never_raises_and_fails_closed():
    authority = Signer()
    att = sign_attestation(_claims(tee=True), authority)
    assert verify_attestation(att, "not-hex", "w1", NOW) is False


# --------------------------- derivation matrix (fail-closed) -----------------


def test_derive_tee_is_confidential_compute():
    assert derive_tier(_claims(tee=True)) == "confidential_compute"


def test_derive_tee_wins_over_lower_flags():
    assert derive_tier(_claims(tee=True, managed=True, compliant=True, sanctioned=True)) == (
        "confidential_compute"
    )


def test_derive_managed_compliant_sanctioned_is_sanctioned():
    assert derive_tier(_claims(managed=True, compliant=True, sanctioned=True)) == "sanctioned"


def test_derive_managed_compliant_is_managed():
    assert derive_tier(_claims(managed=True, compliant=True)) == "managed"


def test_derive_sanctioned_without_managed_or_compliant_is_untrusted():
    # sanctioned alone (not managed+compliant) is not enough: fail closed to untrusted.
    assert derive_tier(_claims(sanctioned=True)) == "untrusted"
    assert derive_tier(_claims(managed=True, sanctioned=True)) == "untrusted"
    assert derive_tier(_claims(compliant=True, sanctioned=True)) == "untrusted"


def test_derive_managed_only_is_untrusted():
    # managed without compliant does not clear even the managed tier.
    assert derive_tier(_claims(managed=True)) == "untrusted"


def test_derive_compliant_only_is_untrusted():
    assert derive_tier(_claims(compliant=True)) == "untrusted"


def test_derive_empty_claims_is_untrusted():
    assert derive_tier(_claims()) == "untrusted"


def test_every_derivable_tier_is_a_valid_routing_tier():
    # The strings derive_tier can return must all be real routing tiers so may_route can rank them.
    for att in (
        _claims(tee=True),
        _claims(managed=True, compliant=True, sanctioned=True),
        _claims(managed=True, compliant=True),
        _claims(),
    ):
        assert is_valid_tier(derive_tier(att))


# --------------------------- canonical bytes ---------------------------------


def test_canonical_claims_bytes_excludes_signature_and_signer_pubkey():
    authority = Signer()
    att = sign_attestation(_claims(tee=True), authority)
    raw = canonical_claims_bytes(att)
    assert b"signature" not in raw
    assert b"signer_pubkey" not in raw
    # Changing only the signature/signer_pubkey must not change the signed bytes.
    same = att.model_copy(update={"signature": "deadbeef", "signer_pubkey": "cafe"})
    assert canonical_claims_bytes(same) == raw


def test_canonical_claims_bytes_survive_json_round_trip():
    # The bytes the authority signs and the bytes the server verifies must match across an HTTP
    # round trip (pydantic JSON serialize -> parse), or no attestation would ever verify server-side.
    authority = Signer()
    att = sign_attestation(_claims(managed=True, compliant=True), authority)
    reparsed = DeviceAttestation.model_validate_json(att.model_dump_json())
    assert canonical_claims_bytes(reparsed) == canonical_claims_bytes(att)
    assert verify_attestation(reparsed, authority.public_key_hex, "w1", NOW) is True
