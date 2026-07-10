"""CLI: sign and verify OneCompute's supply-chain artifacts with Sigstore cosign.

This ties the cosign integration (``src/trust/cosign.py``) to the existing artifact
generators. It can regenerate the CycloneDX SBOM (``scripts/generate_sbom.py``) and the
Ed25519 SLSA provenance attestation (``scripts/generate_provenance.py``) and then sign
each with ``cosign sign-blob``, writing the signature (and optional bundle) next to the
artifact. When no cosign binary is present it reports that cleanly and produces no
fabricated signature (fail-closed / inert), mirroring the MXC seam.

Subcommands::

    status                 # is cosign available, and what would run
    sign [--sbom] [--attestation] [--key <cosign.key>] [--output-dir DIR]
    verify --blob PATH [--signature SIG] [--key <cosign.pub>]

Signing offline requires a local key (``--key``). The keyless production/CI form
(``cosign sign-blob --yes`` + Fulcio/OIDC + Rekor) is documented in ``docs/cosign.md``
and is not run here. See that doc for the honest offline scope.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Make the installed src packages importable when this file is run directly.
_SRC = REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from trust import cosign  # noqa: E402

SBOM_NAME = "sbom.cyclonedx.json"
ATTESTATION_NAME = "attestation.intoto.jsonl"


def _load_script(name: str) -> Any:
    """Load a scripts/<name>.py module by file location (scripts is not a package)."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"could not load scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _generate_sbom(output_dir: Path) -> Path:
    gs = _load_script("generate_sbom")
    sbom = gs.generate_sbom()
    out = output_dir / SBOM_NAME
    out.write_text(_json_dumps(sbom), encoding="utf-8")
    return out


def _generate_attestation(output_dir: Path) -> Path:
    gp = _load_script("generate_provenance")
    envelope = gp.sign_statement(gp.build_statement())
    out = output_dir / ATTESTATION_NAME
    out.write_text(gp._envelope_text(envelope) + "\n", encoding="utf-8")
    return out


def _json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, indent=2, sort_keys=False) + "\n"


def _report_result(result: cosign.CosignResult) -> None:
    print(f"  argv: {' '.join(result.argv)}")
    if not result.available:
        print(f"  cosign unavailable: {result.message}")
        return
    if result.ok:
        print(f"  signed -> {result.signature_path}")
        if result.bundle_path:
            print(f"  bundle -> {result.bundle_path}")
    else:
        print(f"  not signed: {result.message}")


def _cmd_status(args: argparse.Namespace) -> int:
    available = cosign.cosign_available()
    exe = cosign.find_cosign_exe()
    print(f"cosign available: {available}")
    print(f"cosign executable: {exe if exe else '(none on PATH or $COSIGN)'}")
    sample = cosign.build_sign_argv(
        exe or cosign.COSIGN_EXE,
        REPO_ROOT / SBOM_NAME,
        key=args.key,
        signature_path=cosign.default_signature_path(REPO_ROOT / SBOM_NAME),
    )
    print("would run (key-based, offline): " + " ".join(sample))
    keyless = cosign.build_sign_argv(
        exe or cosign.COSIGN_EXE, REPO_ROOT / SBOM_NAME, tlog_upload=True
    )
    print("production (keyless OIDC + Rekor, not run here): " + " ".join(keyless))
    if not available:
        print("status: inert (fail-closed); signing is a no-op until cosign is installed")
    return 0


def _cmd_sign(args: argparse.Namespace) -> int:
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    do_sbom = args.sbom or not args.attestation
    do_attestation = args.attestation or not args.sbom

    targets: list[Path] = []
    if do_sbom:
        targets.append(_generate_sbom(output_dir))
    if do_attestation:
        targets.append(_generate_attestation(output_dir))

    available = cosign.cosign_available()
    print(f"cosign available: {available}")
    if not available:
        for target in targets:
            print(f"artifact: {target}")
            result = cosign.sign_blob(target, key=args.key, allow_keyless=args.allow_keyless)
            _report_result(result)
        print("cosign is unavailable; artifacts generated but unsigned (fail-closed, inert)")
        return 0

    rc = 0
    for target in targets:
        print(f"artifact: {target}")
        result = cosign.sign_blob(
            target,
            key=args.key,
            bundle_path=(target.with_name(target.name + ".cosign.bundle") if args.bundle else None),
            allow_keyless=args.allow_keyless,
        )
        _report_result(result)
        if not result.ok:
            rc = 1
    return rc


def _cmd_verify(args: argparse.Namespace) -> int:
    blob: Path = args.blob
    if not blob.is_file():
        print(f"error: blob not found: {blob}", file=sys.stderr)
        return 2
    signature = args.signature or cosign.default_signature_path(blob)
    if not cosign.cosign_available():
        print("cosign unavailable: cannot verify without a cosign binary (fail-closed)")
        return 1
    ok = cosign.verify_blob(blob, key=args.key, signature_path=signature)
    if ok:
        print(f"OK: cosign verify-blob succeeded for {blob}")
        return 0
    print(f"FAIL: cosign verify-blob failed for {blob}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sign/verify OneCompute supply-chain artifacts with Sigstore cosign."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    st = sub.add_parser("status", help="Report cosign availability and what would run.")
    st.add_argument("--key", default=None, help="Local cosign key path shown in the sample argv.")
    st.set_defaults(func=_cmd_status)

    sg = sub.add_parser("sign", help="Generate and sign the SBOM and/or attestation.")
    sg.add_argument("--sbom", action="store_true", help="Sign only the SBOM.")
    sg.add_argument("--attestation", action="store_true", help="Sign only the provenance attestation.")
    sg.add_argument("--key", default=None, help="Local cosign private key (offline signing).")
    sg.add_argument("--bundle", action="store_true", help="Also write a cosign bundle next to each artifact.")
    sg.add_argument(
        "--allow-keyless",
        action="store_true",
        help="Permit the keyless OIDC path (needs network + identity provider).",
    )
    sg.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT,
        help="Directory for the generated artifacts and signatures (default: repo root).",
    )
    sg.set_defaults(func=_cmd_sign)

    vf = sub.add_parser("verify", help="Verify a blob signature with cosign verify-blob.")
    vf.add_argument("--blob", type=Path, required=True, help="Path to the signed artifact.")
    vf.add_argument("--signature", type=Path, default=None, help="Signature file (default: <blob>.sig).")
    vf.add_argument("--key", default=None, help="Local cosign public key for verification.")
    vf.set_defaults(func=_cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
