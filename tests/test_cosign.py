"""Tests for the cosign/Sigstore integration (``src/trust/cosign.py``) and its CLI.

These exercise the REAL code path (argv construction plus a real subprocess launch)
against a conforming STUB cosign (``tests/fake_cosign.py`` behind a generated shim), so
they never require a genuine cosign binary. They assert:

- ``cosign_available()`` is True with the stub on ``$COSIGN`` and False without it.
- ``sign_blob`` invokes the expected ``cosign sign-blob`` argv and returns success with
  a real signature file when the stub is present.
- the absent-cosign path returns the honest ``available=False`` result and never writes
  a fabricated signature.
- ``verify_blob`` maps the stub's exit code (0 / non-zero) to True / False.
- the ``status`` CLI reports availability correctly both ways.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

sys.path.insert(0, str(_ROOT / "src"))

from trust import cosign  # noqa: E402

_FAKE = Path(__file__).resolve().parent / "fake_cosign.py"


def _load_cli() -> object:
    spec = importlib.util.spec_from_file_location(
        "cosign_attest", _ROOT / "scripts" / "cosign_attest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_shim(tmp_path: Path) -> Path:
    """Create a launchable shim that runs the fake cosign under the current interpreter."""
    if os.name == "nt":
        shim = tmp_path / "cosign.cmd"
        shim.write_text(
            f'@echo off\r\n"{sys.executable}" "{_FAKE}" %*\r\n',
            encoding="utf-8",
        )
    else:
        shim = tmp_path / "cosign"
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{_FAKE}" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
    return shim


@pytest.fixture
def stub_cosign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    shim = _write_shim(tmp_path)
    monkeypatch.setenv(cosign.COSIGN_EXE_ENV, str(shim))
    return shim


@pytest.fixture
def no_cosign(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force cosign resolution to fail regardless of the host PATH."""
    monkeypatch.delenv(cosign.COSIGN_EXE_ENV, raising=False)
    monkeypatch.setattr(cosign.shutil, "which", lambda _name: None)


def test_cosign_available_true_with_stub(stub_cosign: Path) -> None:
    assert cosign.cosign_available() is True
    assert cosign.find_cosign_exe() == str(stub_cosign)


def test_cosign_available_false_without_stub(no_cosign: None) -> None:
    assert cosign.find_cosign_exe() is None
    assert cosign.cosign_available() is False


def test_sign_blob_invokes_expected_argv(stub_cosign: Path, tmp_path: Path) -> None:
    artifact = tmp_path / "sbom.cyclonedx.json"
    artifact.write_text('{"bomFormat": "CycloneDX"}\n', encoding="utf-8")
    key = tmp_path / "cosign.key"
    key.write_text("fake-key-material\n", encoding="utf-8")

    result = cosign.sign_blob(artifact, key=str(key))

    assert result.available is True
    assert result.ok is True
    assert result.returncode == 0
    # The real argv the module built and ran, in order.
    assert result.argv[0] == str(stub_cosign)
    assert result.argv[1] == "sign-blob"
    assert "--yes" in result.argv
    assert result.argv[result.argv.index("--key") + 1] == str(key)
    assert "--output-signature" in result.argv
    assert result.argv[result.argv.index("--tlog-upload") + 1] == "false"
    assert result.argv[-1] == str(artifact)

    sig_path = cosign.default_signature_path(artifact)
    assert sig_path.is_file()
    assert result.signature_path == str(sig_path)
    assert result.signature and "fake-cosign-signature" in result.signature


def test_sign_blob_writes_bundle_when_requested(stub_cosign: Path, tmp_path: Path) -> None:
    artifact = tmp_path / "attestation.intoto.jsonl"
    artifact.write_text("{}\n", encoding="utf-8")
    key = tmp_path / "cosign.key"
    key.write_text("fake-key-material\n", encoding="utf-8")
    bundle = tmp_path / "attestation.intoto.jsonl.cosign.bundle"

    result = cosign.sign_blob(artifact, key=str(key), bundle_path=bundle)

    assert result.ok is True
    assert bundle.is_file()
    assert result.bundle_path == str(bundle)
    assert "--bundle" in result.argv


def test_sign_blob_unavailable_is_honest(no_cosign: None, tmp_path: Path) -> None:
    artifact = tmp_path / "sbom.cyclonedx.json"
    artifact.write_text("{}\n", encoding="utf-8")

    result = cosign.sign_blob(artifact, key="cosign.key")

    assert result.available is False
    assert result.ok is False
    assert result.signature is None
    assert "fail-closed" in result.message
    # The honest path must NOT fabricate a signature file.
    assert not cosign.default_signature_path(artifact).exists()
    # It still reports what WOULD have run for transparency.
    assert result.argv[1] == "sign-blob"


def test_sign_blob_refuses_keyless_offline(stub_cosign: Path, tmp_path: Path) -> None:
    artifact = tmp_path / "sbom.cyclonedx.json"
    artifact.write_text("{}\n", encoding="utf-8")

    result = cosign.sign_blob(artifact)  # no key, allow_keyless defaults False

    assert result.available is True
    assert result.ok is False
    assert "keyless" in result.message
    assert not cosign.default_signature_path(artifact).exists()


def test_verify_blob_maps_success_exit(stub_cosign: Path, tmp_path: Path) -> None:
    artifact = tmp_path / "blob.txt"
    artifact.write_text("payload\n", encoding="utf-8")
    sig = cosign.default_signature_path(artifact)
    sig.write_text("fake-sig\n", encoding="utf-8")

    assert cosign.verify_blob(artifact, key="cosign.pub", signature_path=sig) is True


def test_verify_blob_maps_failure_exit(
    stub_cosign: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_COSIGN_VERIFY_RC", "1")
    artifact = tmp_path / "blob.txt"
    artifact.write_text("payload\n", encoding="utf-8")
    sig = cosign.default_signature_path(artifact)
    sig.write_text("fake-sig\n", encoding="utf-8")

    assert cosign.verify_blob(artifact, key="cosign.pub", signature_path=sig) is False


def test_verify_blob_false_when_unavailable(no_cosign: None, tmp_path: Path) -> None:
    artifact = tmp_path / "blob.txt"
    artifact.write_text("payload\n", encoding="utf-8")
    assert cosign.verify_blob(artifact) is False


def test_status_cli_reports_available(stub_cosign: Path, capsys: pytest.CaptureFixture) -> None:
    cli = _load_cli()
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cosign available: True" in out
    assert "sign-blob" in out


def test_status_cli_reports_unavailable(no_cosign: None, capsys: pytest.CaptureFixture) -> None:
    cli = _load_cli()
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cosign available: False" in out
    assert "inert" in out
