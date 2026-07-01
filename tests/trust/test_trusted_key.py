"""Out-of-band trusted-key verification: verify_manifest(sm, trusted_public_key_hex=...).

Default (no pin) is trust-on-first-use: the signature is checked against the key carried in the
manifest, which proves integrity but not provenance. Pinning a trusted key adds provenance -- only
manifests signed by exactly that key are accepted -- so a compromised or spoofed control plane
cannot inject a self-signed job.
"""
from contracts import JobManifest, SignedManifest
from trust import Signer, verify_manifest


def test_trusted_key_accepts_manifest_signed_by_that_key():
    signer = Signer()
    sm = signer.sign(JobManifest(job_id="j", kind="challenge"))
    assert verify_manifest(sm, trusted_public_key_hex=signer.public_key_hex) is True


def test_trusted_key_rejects_manifest_signed_by_a_different_key():
    # A compromised/spoofed orchestrator signs a job with ITS OWN key and sets public_key to match,
    # so the manifest is internally consistent. TOFU accepts it (the vulnerability); a pinned key
    # rejects it (the fix).
    attacker = Signer()
    sm = attacker.sign(JobManifest(job_id="evil", kind="challenge"))
    assert verify_manifest(sm) is True  # no pin -> accepted (this is the gap)
    trusted = Signer().public_key_hex
    assert verify_manifest(sm, trusted_public_key_hex=trusted) is False  # pin -> rejected


def test_trusted_key_rejects_unsigned():
    sm = SignedManifest(manifest=JobManifest(job_id="j", kind="challenge"))
    assert verify_manifest(sm, trusted_public_key_hex=Signer().public_key_hex) is False


def test_trusted_key_rejects_tamper_even_with_matching_pin():
    signer = Signer()
    sm = signer.sign(JobManifest(job_id="j", kind="challenge"))
    tampered = SignedManifest(
        manifest=JobManifest(job_id="tampered", kind="challenge"),
        signature=sm.signature,
        public_key=sm.public_key,
    )
    assert verify_manifest(tampered, trusted_public_key_hex=signer.public_key_hex) is False


def test_trusted_key_is_case_insensitive_hex():
    signer = Signer()
    sm = signer.sign(JobManifest(job_id="j", kind="challenge"))
    assert verify_manifest(sm, trusted_public_key_hex=signer.public_key_hex.upper()) is True


def test_no_pin_preserves_tofu_behavior():
    sm = Signer().sign(JobManifest(job_id="j", kind="challenge"))
    assert verify_manifest(sm) is True  # backward compatible default
