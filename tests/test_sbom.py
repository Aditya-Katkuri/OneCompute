"""Tests for scripts/generate_sbom.py: the CycloneDX SBOM generator.

These parse the *real* ``uv.lock`` through the generator and assert the CycloneDX 1.5
envelope, that components are non-empty and each carries a ``name``/``version`` and a
``pkg:pypi/...`` purl, that key security-relevant dependencies (``cryptography``, the
Ed25519 trust root, and ``fastapi``) are present, that the emitted document is valid
JSON, and that generation is deterministic (two runs produce an identical component
list). The full package set is intentionally not hardcoded so the test tracks whatever
``uv.lock`` resolves to.

``scripts`` is not an importable package (only ``src`` is on the path), so the script is
loaded by file location.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "generate_sbom",
    Path(__file__).resolve().parents[1] / "scripts" / "generate_sbom.py",
)
gs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gs)


def _sbom() -> dict:
    return gs.generate_sbom()


def test_cyclonedx_envelope() -> None:
    sbom = _sbom()
    assert sbom["bomFormat"] == "CycloneDX"
    assert sbom["specVersion"] == "1.5"
    assert sbom["version"] == 1
    assert sbom["serialNumber"].startswith("urn:uuid:")


def test_metadata_block() -> None:
    metadata = _sbom()["metadata"]
    assert metadata["timestamp"].endswith("Z")
    tool_names = {tool["name"] for tool in metadata["tools"]}
    assert "onecompute-sbom" in tool_names
    component = metadata["component"]
    assert component["type"] == "application"
    assert component["name"] == "onecompute"
    assert component["version"]


def test_components_non_empty_and_well_formed() -> None:
    components = _sbom()["components"]
    assert components, "expected at least one locked package"
    for component in components:
        assert component["type"] == "library"
        assert component["name"]
        assert component["version"]
        assert component["purl"] == f"pkg:pypi/{component['name']}@{component['version']}"
        assert component["purl"].startswith("pkg:pypi/")


def test_key_security_deps_present() -> None:
    names = {component["name"] for component in _sbom()["components"]}
    # cryptography is the Ed25519 trust root; fastapi is the control-plane API.
    assert "cryptography" in names
    assert "fastapi" in names


def test_project_itself_not_a_library_component() -> None:
    names = {component["name"] for component in _sbom()["components"]}
    assert "onecompute" not in names


def test_output_is_valid_json() -> None:
    text = json.dumps(_sbom(), indent=2)
    reparsed = json.loads(text)
    assert reparsed["bomFormat"] == "CycloneDX"


def test_generation_is_deterministic() -> None:
    first = gs.generate_sbom()["components"]
    second = gs.generate_sbom()["components"]
    assert first == second


def test_components_sorted_by_name() -> None:
    components = _sbom()["components"]
    keys = [(component["name"], component["version"]) for component in components]
    assert keys == sorted(keys)


def test_missing_lock_raises_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.lock"
    try:
        gs.generate_sbom(lock_path=missing)
    except FileNotFoundError as exc:
        assert "uv.lock" in str(exc)
    else:  # pragma: no cover - the call must raise
        raise AssertionError("expected FileNotFoundError for a missing lock file")
