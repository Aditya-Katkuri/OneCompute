"""Sigstore ``cosign`` integration for signing OneCompute's supply-chain artifacts.

This is the cosign/Sigstore counterpart to the MXC isolation seam
(``src/isolation/mxc.py``): detect the real runtime, use it when it is present, and
otherwise stay **inert and honest** rather than fabricate a result. It lets the
CycloneDX SBOM (``scripts/generate_sbom.py``) and the Ed25519 SLSA provenance
attestation (``scripts/generate_provenance.py``) be signed with ``cosign sign-blob``
when a ``cosign`` binary is available, and verified with ``cosign verify-blob``.

Two signing modes exist in Sigstore:

- **Key-based (offline-capable):** ``cosign sign-blob --key <key> ... <blob>`` signs a
  blob with a local key pair and, with ``--tlog-upload=false``, never touches the
  network. This is the mode this module actually executes.
- **Keyless (production/CI):** ``cosign sign-blob --yes <blob>`` obtains a short-lived
  OIDC identity, gets a Fulcio certificate, and records a Rekor transparency-log entry.
  That path needs network access and an OIDC identity provider, so it is **documented
  but never executed here** (see ``docs/cosign.md``). To avoid accidentally starting a
  network OIDC flow in an offline environment, :func:`sign_blob` refuses the keyless
  path unless ``allow_keyless=True`` is passed explicitly.

Availability is probed like MXC: a resolvable executable that answers ``cosign version``
with exit 0. Missing binaries, probe failures, and timeouts all read as *unavailable*
(fail-closed), and no function in this module ever raises for those conditions.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Windows ships ``cosign.exe``; POSIX ships ``cosign``. Both names are probed on PATH.
COSIGN_EXE = "cosign.exe" if os.name == "nt" else "cosign"

# Explicit override: a path to (or name of) a cosign binary. Mirrors ``ONECOMPUTE_MXC_EXE``.
COSIGN_EXE_ENV = "COSIGN"

_PROBE_TIMEOUT_S = 5.0
_RUN_TIMEOUT_S = 120.0

_UNAVAILABLE_MESSAGE = (
    "cosign not found on PATH or via $COSIGN; fail-closed (inert), no signature produced"
)


@dataclass(frozen=True)
class CosignResult:
    """Outcome of a cosign invocation (or of declining to run one).

    ``argv`` is always populated with the command that ran (or that *would* run when
    cosign is unavailable), so callers and tests can inspect the exact invocation
    without a real binary. ``available`` distinguishes "cosign is missing" from "cosign
    ran and failed". ``ok`` is True only when a signature was actually produced.
    """

    action: str
    available: bool
    ok: bool
    argv: list[str]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    message: str = ""
    signature_path: str | None = None
    bundle_path: str | None = None
    signature: str | None = None


def _exe_names() -> tuple[str, ...]:
    return ("cosign.exe", "cosign")


def _resolve_override(raw: str) -> str | None:
    expanded = Path(raw).expanduser()
    try:
        if expanded.is_file():
            return str(expanded)
    except OSError:
        pass
    return shutil.which(raw)


def find_cosign_exe() -> str | None:
    """Resolve a cosign executable from ``$COSIGN`` or PATH, or ``None`` if absent.

    An explicit ``$COSIGN`` override may name either a full path to a binary/shim or a
    command resolvable on PATH, matching how ``ONECOMPUTE_MXC_EXE`` resolves ``wxc-exec``.
    """
    override = os.environ.get(COSIGN_EXE_ENV)
    if override:
        return _resolve_override(override)
    for name in _exe_names():
        found = shutil.which(name)
        if found:
            return found
    return None


def _exe_for_display() -> str:
    """The executable to show in ``argv`` even when cosign is unavailable."""
    return find_cosign_exe() or COSIGN_EXE


def cosign_available() -> bool:
    """Return whether a usable cosign binary is present. Never raises.

    Availability means the executable resolves *and* answers ``cosign version`` with a
    zero exit code, not merely that a name exists. Absent binaries, non-zero probes,
    timeouts, and any other error all read as unavailable (fail-closed), so a machine
    with no cosign keeps behaving exactly as before.
    """
    exe = find_cosign_exe()
    if exe is None:
        return False
    try:
        proc = subprocess.run(
            [exe, "version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except Exception:
        return False
    return proc.returncode == 0


def build_sign_argv(
    exe: str,
    path: str | os.PathLike[str],
    *,
    key: str | None = None,
    signature_path: str | os.PathLike[str] | None = None,
    bundle_path: str | os.PathLike[str] | None = None,
    tlog_upload: bool = False,
    yes: bool = True,
    extra_args: Sequence[str] | None = None,
) -> list[str]:
    """Construct the ``cosign sign-blob`` argv.

    With ``key`` set this is the offline key-based form; with ``key`` omitted it is the
    keyless (Fulcio/OIDC) form documented as the production path. ``--tlog-upload`` is
    forced to ``false`` by default so the key-based path never contacts Rekor.
    """
    argv = [exe, "sign-blob"]
    if yes:
        argv.append("--yes")
    if key is not None:
        argv += ["--key", str(key)]
    if signature_path is not None:
        argv += ["--output-signature", str(signature_path)]
    if bundle_path is not None:
        argv += ["--bundle", str(bundle_path)]
    argv += ["--tlog-upload", "true" if tlog_upload else "false"]
    if extra_args:
        argv += list(extra_args)
    argv.append(str(path))
    return argv


def build_verify_argv(
    exe: str,
    path: str | os.PathLike[str],
    *,
    key: str | None = None,
    signature_path: str | os.PathLike[str] | None = None,
    bundle_path: str | os.PathLike[str] | None = None,
    ignore_tlog: bool = True,
    extra_args: Sequence[str] | None = None,
) -> list[str]:
    """Construct the ``cosign verify-blob`` argv (key-based, offline by default)."""
    argv = [exe, "verify-blob"]
    if key is not None:
        argv += ["--key", str(key)]
    if signature_path is not None:
        argv += ["--signature", str(signature_path)]
    if bundle_path is not None:
        argv += ["--bundle", str(bundle_path)]
    if ignore_tlog:
        argv += ["--insecure-ignore-tlog", "true"]
    if extra_args:
        argv += list(extra_args)
    argv.append(str(path))
    return argv


def default_signature_path(path: str | os.PathLike[str]) -> Path:
    """The ``<artifact>.sig`` path written next to the signed artifact."""
    p = Path(path)
    return p.with_name(p.name + ".sig")


def sign_blob(
    path: str | os.PathLike[str],
    *,
    key: str | None = None,
    signature_path: str | os.PathLike[str] | None = None,
    bundle_path: str | os.PathLike[str] | None = None,
    tlog_upload: bool = False,
    allow_keyless: bool = False,
    extra_args: Sequence[str] | None = None,
) -> CosignResult:
    """Sign ``path`` with ``cosign sign-blob``; return a :class:`CosignResult`.

    When cosign is present and a ``key`` is supplied, this invokes the offline
    key-based ``sign-blob``, writing ``<path>.sig`` (and a bundle if requested) next to
    the artifact and returning ``ok=True`` with the signature. When cosign is absent it
    returns an honest ``available=False`` result and **never** writes a fabricated
    signature. The keyless OIDC path (``key=None``) is refused unless
    ``allow_keyless=True`` so an offline environment cannot accidentally start a network
    identity flow.
    """
    target = Path(path)
    sig_path = Path(signature_path) if signature_path is not None else default_signature_path(target)
    bundle = Path(bundle_path) if bundle_path is not None else None

    exe_display = _exe_for_display()
    intended_argv = build_sign_argv(
        exe_display,
        target,
        key=key,
        signature_path=sig_path,
        bundle_path=bundle,
        tlog_upload=tlog_upload,
        extra_args=extra_args,
    )

    if not cosign_available():
        return CosignResult(
            action="sign-blob",
            available=False,
            ok=False,
            argv=intended_argv,
            message=_UNAVAILABLE_MESSAGE,
            signature_path=str(sig_path),
            bundle_path=str(bundle) if bundle is not None else None,
        )

    if not target.is_file():
        return CosignResult(
            action="sign-blob",
            available=True,
            ok=False,
            argv=intended_argv,
            message=f"target artifact not found: {target}",
        )

    if key is None and not allow_keyless:
        return CosignResult(
            action="sign-blob",
            available=True,
            ok=False,
            argv=intended_argv,
            message=(
                "keyless OIDC signing needs network + an identity provider and is not "
                "run offline; pass key=<cosign.key> for offline signing, or "
                "allow_keyless=True to run the production path"
            ),
        )

    exe = find_cosign_exe() or exe_display
    argv = build_sign_argv(
        exe,
        target,
        key=key,
        signature_path=sig_path,
        bundle_path=bundle,
        tlog_upload=tlog_upload,
        extra_args=extra_args,
    )
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_RUN_TIMEOUT_S,
            check=False,
        )
    except Exception as exc:
        return CosignResult(
            action="sign-blob",
            available=True,
            ok=False,
            argv=argv,
            message=f"cosign invocation failed: {exc}",
        )

    ok = proc.returncode == 0
    signature = None
    if ok and sig_path.is_file():
        try:
            signature = sig_path.read_text(encoding="utf-8").strip()
        except OSError:
            signature = None

    return CosignResult(
        action="sign-blob",
        available=True,
        ok=ok,
        argv=argv,
        returncode=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
        message="signed" if ok else "cosign sign-blob returned a non-zero exit",
        signature_path=str(sig_path) if ok and sig_path.is_file() else None,
        bundle_path=str(bundle) if ok and bundle is not None and bundle.is_file() else None,
        signature=signature,
    )


def verify_blob(
    path: str | os.PathLike[str],
    *,
    key: str | None = None,
    signature_path: str | os.PathLike[str] | None = None,
    bundle_path: str | os.PathLike[str] | None = None,
    ignore_tlog: bool = True,
    extra_args: Sequence[str] | None = None,
) -> bool:
    """Verify a blob's signature with ``cosign verify-blob``; return True on success.

    Returns False when cosign is unavailable (it cannot verify without a binary) and
    when cosign runs but exits non-zero. Never raises.
    """
    exe = find_cosign_exe()
    if exe is None or not cosign_available():
        return False
    argv = build_verify_argv(
        exe,
        path,
        key=key,
        signature_path=signature_path,
        bundle_path=bundle_path,
        ignore_tlog=ignore_tlog,
        extra_args=extra_args,
    )
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_RUN_TIMEOUT_S,
            check=False,
        )
    except Exception:
        return False
    return proc.returncode == 0
