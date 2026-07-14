"""Ed25519 signing and verification for frozen job manifests."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from contracts import JobManifest, SignedManifest


def _manifest_bytes(manifest: JobManifest) -> bytes:
    return manifest.model_dump_json().encode("utf-8")


class Signer:
    def __init__(self, private_key_hex: str | None = None):
        if private_key_hex is None:
            self._private_key = Ed25519PrivateKey.generate()
        else:
            self._private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))

        private_bytes = self._private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = self._private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.private_key_hex = private_bytes.hex()
        self.public_key_hex = public_bytes.hex()

    def sign(self, manifest: JobManifest) -> SignedManifest:
        signature = self._private_key.sign(_manifest_bytes(manifest)).hex()
        return SignedManifest(
            manifest=manifest,
            signature=signature,
            public_key=self.public_key_hex,
        )

    def sign_bytes(self, data: bytes) -> str:
        """Ed25519-sign arbitrary canonical bytes, returning a hex signature.

        The low-level primitive behind manifest signing, reused for signing device-attestation
        claims (see src/trust/attestation.py) so both share one audited signing path.
        """
        return self._private_key.sign(data).hex()


def verify_signature(signature_hex: str, data: bytes, public_key_hex: str) -> bool:
    """Verify a hex Ed25519 ``signature_hex`` over ``data`` against ``public_key_hex``.

    Pure and total: any malformed input, empty signature, or verification failure returns False
    (fail closed) and this never raises. Used by attestation verification, which must decide a
    trust tier without ever throwing on a hostile payload.
    """
    try:
        if not signature_hex or not public_key_hex:
            return False
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature_hex), data)
    except Exception:
        return False
    return True


def verify_manifest(sm: SignedManifest, trusted_public_key_hex: str | None = None) -> bool:
    """Verify a signed manifest's Ed25519 signature.

    By default (PoC) the signature is checked against the public key carried IN the manifest. That
    proves *integrity* -- the manifest was not altered after signing -- but NOT *provenance*: any
    party that can hand a worker a ``SignedManifest`` could sign it with its own key and set
    ``public_key`` to match, so a compromised or spoofed control plane could inject a self-signed
    job. Pass ``trusted_public_key_hex`` (a key pinned on the worker out-of-band) to also enforce
    provenance: the manifest is accepted only when it is signed by exactly that key. This is the
    "pass, not bypass" answer -- the worker trusts an operator-provisioned key, not whatever the
    orchestrator sends.
    """
    try:
        if sm.signature == "":
            return False
        key_hex = sm.public_key
        if trusted_public_key_hex is not None:
            # Out-of-band trust: only the operator's pinned key is acceptable. A manifest that
            # carries any other key (a compromised orchestrator self-signing) is rejected even
            # though its own signature is internally consistent.
            if not sm.public_key or sm.public_key.strip().lower() != trusted_public_key_hex.strip().lower():
                return False
            key_hex = trusted_public_key_hex
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
        public_key.verify(bytes.fromhex(sm.signature), _manifest_bytes(sm.manifest))
    except Exception:
        return False
    return True
