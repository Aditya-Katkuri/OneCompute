"""Generate and verify a signed SLSA-v1 build-provenance attestation (offline PoC).

This takes a concrete, offline step toward the build-provenance roadmap item in
``docs/supply-chain.md``. It builds an in-toto Statement
(``https://in-toto.io/Statement/v1``) whose ``predicateType`` is SLSA Provenance v1
(``https://slsa.dev/provenance/v1``), then Ed25519-signs the statement's canonical
bytes using the *same* signing primitive that signs job manifests
(``src/trust/signing.Signer``). The signed result is emitted as a DSSE-lite JSON
envelope that carries the public key so ``verify`` is self-contained for the PoC.

Honest scope: this is a *lock-and-source* attestation signed offline. It proves that a
given SBOM and source tree were attested together by the holder of an Ed25519 key. It is
NOT a hardware-rooted, transparency-logged build proof. Full DSSE PAE framing, cosign /
OIDC keyless signing, and a Rekor transparency-log entry are the production upgrade,
documented in ``docs/supply-chain.md``.

Subjects attested (both deterministic):

- ``sbom.cyclonedx.json`` -- the CycloneDX SBOM regenerated in-process by the existing
  ``scripts/generate_sbom.py`` generator (with a fixed timestamp and serial so the digest
  is reproducible), hashed over its canonical bytes.
- ``src-tree`` -- a deterministic digest of the tracked ``src/**/*.py`` sources: the
  sha256 of the sorted list of ``[posix-relative-path, sha256-of-content]`` pairs.

Usage::

    uv run python scripts/generate_provenance.py generate            # writes attestation.intoto.jsonl
    uv run python scripts/generate_provenance.py generate --stdout
    uv run python scripts/generate_provenance.py generate --output build/attestation.intoto.jsonl
    uv run python scripts/generate_provenance.py verify attestation.intoto.jsonl

A signing key may be supplied as a hex Ed25519 private key via ``--private-key`` or the
``ONECOMPUTE_PROVENANCE_KEY`` environment variable; otherwise an ephemeral key is
generated (mirroring ``Signer``'s default). The public key is recorded in the envelope so
verification is self-contained. Out-of-band key pinning / cosign keyless signing is the
upgrade, mirroring the worker ``--trusted-key`` story.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Repo root is the parent of scripts/. Anchor lookups here so the tool works from any cwd.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Ensure the installed src packages (contracts, trust) are importable even when this file
# is run directly rather than through the installed console entry points.
_SRC = REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from contracts.hashing import canonical_bytes, sha256_hex  # noqa: E402
from trust.signing import Signer  # noqa: E402

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
BUILD_TYPE = "https://onecompute.dev/provenance/buildtypes/offline-attestation/v1"
BUILDER_ID = "https://onecompute.dev/provenance/builders/offline-ed25519/v1"
PAYLOAD_TYPE = "application/vnd.in-toto+json"
PRIVATE_KEY_ENV = "ONECOMPUTE_PROVENANCE_KEY"

# Fixed values so the SBOM subject digest is reproducible: the SBOM's own timestamp and
# random serialNumber are non-deterministic by design, so a stable pair is pinned here and
# used identically by the generator and the tests.
_SBOM_TIMESTAMP = datetime(2020, 1, 1, tzinfo=UTC)
_SBOM_SERIAL = "urn:uuid:00000000-0000-0000-0000-000000000000"


def _load_sbom_generator() -> Any:
    """Load scripts/generate_sbom.py by file location (scripts is not an importable pkg)."""
    spec = importlib.util.spec_from_file_location(
        "generate_sbom", REPO_ROOT / "scripts" / "generate_sbom.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("could not load scripts/generate_sbom.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sbom_subject() -> dict[str, Any]:
    """Regenerate the SBOM in-process and return its subject entry (name + sha256 digest)."""
    gs = _load_sbom_generator()
    sbom = gs.generate_sbom(timestamp=_SBOM_TIMESTAMP, serial_number=_SBOM_SERIAL)
    return {
        "name": "sbom.cyclonedx.json",
        "digest": {"sha256": sha256_hex(canonical_bytes(sbom))},
    }


def _source_tree_digest(src_root: Path | None = None) -> str:
    """Deterministic digest of tracked ``src/**/*.py``: sha256 of sorted [path, content-sha]."""
    src_root = src_root or _SRC
    entries: list[list[str]] = []
    for path in sorted(src_root.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        content_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append([rel, content_sha])
    entries.sort()
    return sha256_hex(entries)


def _source_subject() -> dict[str, Any]:
    return {"name": "src-tree", "digest": {"sha256": _source_tree_digest()}}


def _dep_sha256(package: dict[str, Any]) -> str | None:
    """Best-effort per-package sha256 from uv.lock (sdist first, then the first wheel)."""
    sdist = package.get("sdist")
    if isinstance(sdist, dict):
        digest = sdist.get("hash", "")
        if isinstance(digest, str) and digest.startswith("sha256:"):
            return digest.split(":", 1)[1]
    for wheel in package.get("wheels", []) or []:
        digest = wheel.get("hash", "")
        if isinstance(digest, str) and digest.startswith("sha256:"):
            return digest.split(":", 1)[1]
    return None


def _resolved_dependencies(lock_path: Path | None = None) -> list[dict[str, Any]]:
    """Read uv.lock and return SLSA resolvedDependencies as {uri: pkg:pypi/..., digest}."""
    lock_path = lock_path or (REPO_ROOT / "uv.lock")
    with lock_path.open("rb") as fh:
        lock_data = tomllib.load(fh)

    deps: list[dict[str, Any]] = []
    for package in lock_data.get("package", []):
        source = package.get("source", {})
        # Skip the local editable/virtual project; only registry packages get a purl.
        if {"editable", "virtual", "directory", "path"} & set(source):
            continue
        name = package["name"]
        version = package.get("version", "")
        entry: dict[str, Any] = {"uri": f"pkg:pypi/{name}@{version}"}
        digest = _dep_sha256(package)
        if digest is not None:
            entry["digest"] = {"sha256": digest}
        deps.append(entry)
    deps.sort(key=lambda dep: dep["uri"])
    return deps


def build_statement() -> dict[str, Any]:
    """Build the in-toto Statement with an SLSA Provenance v1 predicate."""
    subject = [_sbom_subject(), _source_subject()]
    predicate = {
        "buildDefinition": {
            "buildType": BUILD_TYPE,
            # externalParameters: the operator-visible inputs to this attestation build.
            "externalParameters": {
                "project": "onecompute",
                "entryPoint": "scripts/generate_provenance.py",
                "subjects": ["sbom.cyclonedx.json", "src-tree"],
            },
            # internalParameters is intentionally empty: this offline PoC has no build-system
            # internal configuration to record (documented stub in docs/supply-chain.md).
            "internalParameters": {},
            "resolvedDependencies": _resolved_dependencies(),
        },
        "runDetails": {
            # builder.id identifies the offline Ed25519 attestor. metadata (invocationId,
            # startedOn/finishedOn timestamps) is deliberately omitted to keep the statement
            # byte-reproducible; recording a signed Rekor entry with real build metadata is the
            # roadmap upgrade (see docs/supply-chain.md).
            "builder": {"id": BUILDER_ID},
        },
    }
    return {
        "_type": STATEMENT_TYPE,
        "subject": subject,
        "predicateType": PREDICATE_TYPE,
        "predicate": predicate,
    }


def sign_statement(statement: dict[str, Any], private_key_hex: str | None = None) -> dict[str, Any]:
    """Ed25519-sign a statement's canonical bytes and return a DSSE-lite envelope.

    The signing key is managed by ``trust.signing.Signer`` (the same primitive that signs
    job manifests). Because ``Signer.sign`` is manifest-shaped, the raw statement bytes are
    signed with the Signer-held key material directly; the public key is recorded in the
    envelope so ``verify_envelope`` is self-contained for the PoC.
    """
    signer = Signer(private_key_hex)
    payload_bytes = canonical_bytes(statement)
    private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signer.private_key_hex))
    signature = private_key.sign(payload_bytes).hex()
    return {
        "payloadType": PAYLOAD_TYPE,
        "payload": statement,
        "signatures": [
            {
                "sig": signature,
                "keyid": signer.public_key_hex,
                "publicKey": signer.public_key_hex,
            }
        ],
    }


def verify_envelope(envelope: dict[str, Any]) -> bool:
    """Recompute the statement bytes and verify the Ed25519 signature.

    Returns False if the payload or any signature is tampered, or if the envelope shape is
    invalid. The public key is taken from the envelope (self-contained PoC verification);
    pinning a trusted key out of band is the provenance upgrade, mirroring the worker
    ``--trusted-key`` story in ``src/trust/signing.py``.
    """
    try:
        payload = envelope["payload"]
        signatures = envelope["signatures"]
        if not signatures:
            return False
        payload_bytes = canonical_bytes(payload)
        for entry in signatures:
            sig_hex = entry.get("sig", "")
            key_hex = entry.get("publicKey") or entry.get("keyid") or ""
            if not sig_hex or not key_hex:
                return False
            public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
            public_key.verify(bytes.fromhex(sig_hex), payload_bytes)
        return True
    except Exception:
        return False


def _envelope_text(envelope: dict[str, Any]) -> str:
    """Serialize the envelope as a single JSON line (.intoto.jsonl convention)."""
    return json.dumps(envelope, sort_keys=True)


def _cmd_generate(args: argparse.Namespace) -> int:
    private_key_hex = args.private_key or os.environ.get(PRIVATE_KEY_ENV)
    statement = build_statement()
    envelope = sign_statement(statement, private_key_hex)
    text = _envelope_text(envelope)
    if args.stdout:
        print(text)
        return 0
    output_path: Path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text + "\n", encoding="utf-8")
    subjects = ", ".join(s["name"] for s in statement["subject"])
    print(f"Wrote signed provenance for [{subjects}] to {output_path}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    path: Path = args.file
    if not path.is_file():
        print(f"error: attestation not found: {path}", file=sys.stderr)
        return 2
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if verify_envelope(envelope):
        print(f"OK: signature verifies for {path}")
        return 0
    print(f"FAIL: signature does not verify for {path}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate/verify a signed SLSA-v1 provenance attestation (offline PoC)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Build and sign the attestation.")
    gen.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "attestation.intoto.jsonl",
        help="Path to write the signed attestation (default: attestation.intoto.jsonl at repo root).",
    )
    gen.add_argument("--stdout", action="store_true", help="Write the envelope to stdout.")
    gen.add_argument(
        "--private-key",
        default=None,
        help=f"Hex Ed25519 private key (or set ${PRIVATE_KEY_ENV}). Ephemeral if omitted.",
    )
    gen.set_defaults(func=_cmd_generate)

    ver = sub.add_parser("verify", help="Verify a signed attestation file.")
    ver.add_argument("file", type=Path, help="Path to the attestation to verify.")
    ver.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
