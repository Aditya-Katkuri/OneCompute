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


def verify_manifest(sm: SignedManifest) -> bool:
    try:
        if sm.signature == "":
            return False
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(sm.public_key))
        public_key.verify(bytes.fromhex(sm.signature), _manifest_bytes(sm.manifest))
    except Exception:
        return False
    return True
