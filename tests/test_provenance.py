"""Tests for scripts/generate_provenance.py: the signed SLSA-v1 provenance attestation.

These exercise the real generator end to end: the in-toto Statement shape, that subjects
carry sha256 digests, that the SBOM subject digest matches a fresh SBOM regenerated with
the same fixed timestamp/serial, that resolvedDependencies is non-empty and purl-formatted,
that the Ed25519 signature verifies, that flipping one byte of the payload or of the
signature is caught (tamper detection), and that a generate -> verify round-trip passes.

``scripts`` is not an importable package (only ``src`` is on the path), so the script is
loaded by file location, mirroring tests/test_sbom.py.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> object:
    spec = importlib.util.spec_from_file_location(name, _ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gp = _load("generate_provenance")
gs = _load("generate_sbom")


def _flip_hex_char(value: str) -> str:
    """Return ``value`` with its first hex nibble changed, so the bytes differ by one."""
    first = value[0]
    replacement = "0" if first != "0" else "1"
    return replacement + value[1:]


def test_statement_types() -> None:
    statement = gp.build_statement()
    assert statement["_type"] == "https://in-toto.io/Statement/v1"
    assert statement["predicateType"] == "https://slsa.dev/provenance/v1"


def test_subjects_have_sha256_digests() -> None:
    statement = gp.build_statement()
    subjects = statement["subject"]
    assert subjects, "expected at least one subject"
    names = {s["name"] for s in subjects}
    assert {"sbom.cyclonedx.json", "src-tree"} <= names
    for subject in subjects:
        digest = subject["digest"]["sha256"]
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


def test_sbom_subject_digest_matches_fresh_sbom() -> None:
    statement = gp.build_statement()
    sbom_subject = next(s for s in statement["subject"] if s["name"] == "sbom.cyclonedx.json")
    fresh = gs.generate_sbom(timestamp=gp._SBOM_TIMESTAMP, serial_number=gp._SBOM_SERIAL)
    expected = gp.sha256_hex(gp.canonical_bytes(fresh))
    assert sbom_subject["digest"]["sha256"] == expected


def test_predicate_build_and_run_details() -> None:
    predicate = gp.build_statement()["predicate"]
    build_def = predicate["buildDefinition"]
    assert build_def["buildType"] == gp.BUILD_TYPE
    assert build_def["buildType"].startswith("https://onecompute.dev/")
    assert "externalParameters" in build_def
    builder_id = predicate["runDetails"]["builder"]["id"]
    assert builder_id == gp.BUILDER_ID
    assert builder_id.startswith("https://onecompute.dev/")


def test_resolved_dependencies_non_empty_and_purl_formatted() -> None:
    deps = gp.build_statement()["predicate"]["buildDefinition"]["resolvedDependencies"]
    assert deps, "expected at least one resolved dependency from uv.lock"
    for dep in deps:
        assert dep["uri"].startswith("pkg:pypi/")
        assert "@" in dep["uri"]
    # cryptography is the Ed25519 trust root; it must appear in the locked graph.
    uris = " ".join(dep["uri"] for dep in deps)
    assert "pkg:pypi/cryptography@" in uris


def test_resolved_dependencies_carry_sha256_digests() -> None:
    deps = gp.build_statement()["predicate"]["buildDefinition"]["resolvedDependencies"]
    with_digest = [d for d in deps if "digest" in d]
    assert with_digest, "expected at least one dependency with a recorded sha256"
    for dep in with_digest:
        assert len(dep["digest"]["sha256"]) == 64


def test_signature_verifies() -> None:
    statement = gp.build_statement()
    envelope = gp.sign_statement(statement)
    assert gp.verify_envelope(envelope) is True


def test_envelope_shape() -> None:
    envelope = gp.sign_statement(gp.build_statement())
    assert envelope["payloadType"] == gp.PAYLOAD_TYPE
    assert envelope["payload"]["_type"] == "https://in-toto.io/Statement/v1"
    sig = envelope["signatures"][0]
    assert sig["sig"]
    assert sig["publicKey"]


def test_tampered_payload_fails_verification() -> None:
    envelope = gp.sign_statement(gp.build_statement())
    tampered = copy.deepcopy(envelope)
    # Flip one subject digit: the payload bytes now differ from what was signed.
    digest = tampered["payload"]["subject"][0]["digest"]["sha256"]
    tampered["payload"]["subject"][0]["digest"]["sha256"] = _flip_hex_char(digest)
    assert gp.verify_envelope(tampered) is False


def test_tampered_signature_fails_verification() -> None:
    envelope = gp.sign_statement(gp.build_statement())
    tampered = copy.deepcopy(envelope)
    sig = tampered["signatures"][0]["sig"]
    tampered["signatures"][0]["sig"] = _flip_hex_char(sig)
    assert gp.verify_envelope(tampered) is False


def test_round_trip_generate_then_verify(tmp_path: Path) -> None:
    out = tmp_path / "attestation.intoto.jsonl"
    rc = gp.main(["generate", "--output", str(out)])
    assert rc == 0
    assert out.is_file()
    envelope = json.loads(out.read_text(encoding="utf-8"))
    assert gp.verify_envelope(envelope) is True
    assert gp.main(["verify", str(out)]) == 0


def test_verify_cli_rejects_tampered_file(tmp_path: Path) -> None:
    out = tmp_path / "attestation.intoto.jsonl"
    assert gp.main(["generate", "--output", str(out)]) == 0
    envelope = json.loads(out.read_text(encoding="utf-8"))
    envelope["signatures"][0]["sig"] = _flip_hex_char(envelope["signatures"][0]["sig"])
    out.write_text(json.dumps(envelope, sort_keys=True) + "\n", encoding="utf-8")
    assert gp.main(["verify", str(out)]) == 1


def test_provided_key_is_recorded_and_deterministic() -> None:
    # A fixed private key yields a deterministic Ed25519 signature (RFC 8032) and records
    # the matching public key so verification is self-contained.
    key_hex = "00" * 32
    first = gp.sign_statement(gp.build_statement(), key_hex)
    second = gp.sign_statement(gp.build_statement(), key_hex)
    assert first["signatures"][0]["sig"] == second["signatures"][0]["sig"]
    assert first["signatures"][0]["publicKey"] == second["signatures"][0]["publicKey"]
    assert gp.verify_envelope(first) is True
