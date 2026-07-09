"""Generate a CycloneDX 1.5 JSON SBOM from the repository's ``uv.lock``.

This is a pure-stdlib generator (``tomllib`` + ``json``): it reads the resolved
dependency graph that ``uv`` writes to ``uv.lock`` and emits a valid CycloneDX 1.5
Software Bill of Materials. One component is emitted per locked third-party package
(``type: library``) with a ``pkg:pypi/<name>@<version>`` Package URL; the OneCompute
project itself (read from ``pyproject.toml``) is recorded as the SBOM's
``metadata.component`` of type ``application``.

Honest scope: this SBOM is derived from ``uv.lock`` (the resolved dependency graph),
not from a full build or runtime attestation. It captures which package versions the
project resolves to, not the provenance of the built artifact. See
``docs/supply-chain.md`` for the provenance posture and roadmap.

Usage::

    uv run python scripts/generate_sbom.py                 # writes sbom.cyclonedx.json
    uv run python scripts/generate_sbom.py --output out.json
    uv run python scripts/generate_sbom.py --stdout
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Repo root is the parent of scripts/. Anchor lookups here so the generator works
# from any working directory.
REPO_ROOT = Path(__file__).resolve().parent.parent

TOOL_NAME = "onecompute-sbom"


def _load_project_metadata(pyproject_path: Path) -> tuple[str, str]:
    """Return ``(name, version)`` for the OneCompute project from pyproject.toml."""
    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    project = data.get("project", {})
    return project.get("name", "onecompute"), project.get("version", "0.0.0")


def _is_third_party(package: dict[str, Any]) -> bool:
    """True for resolved registry packages, False for the local editable project.

    The root project is recorded in the lock with an ``editable``/``virtual`` source
    and becomes the SBOM's ``metadata.component`` instead of a library component, so
    it must not get a ``pkg:pypi`` purl.
    """
    source = package.get("source", {})
    return not ({"editable", "virtual", "directory", "path"} & set(source))


def _build_components(packages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the sorted, deterministic list of library components."""
    components: list[dict[str, Any]] = []
    for package in packages:
        if not _is_third_party(package):
            continue
        name = package["name"]
        version = package.get("version", "")
        components.append(
            {
                "type": "library",
                "name": name,
                "version": version,
                "purl": f"pkg:pypi/{name}@{version}",
            }
        )
    components.sort(key=lambda component: (component["name"], component["version"]))
    return components


def generate_sbom(
    lock_path: Path | None = None,
    pyproject_path: Path | None = None,
    *,
    timestamp: datetime | None = None,
    serial_number: str | None = None,
) -> dict[str, Any]:
    """Read ``uv.lock`` and return a CycloneDX 1.5 SBOM as a plain dict.

    ``timestamp`` and ``serial_number`` may be supplied for reproducible output;
    when omitted the current UTC time and a random UUID urn are used.
    """
    lock_path = lock_path or (REPO_ROOT / "uv.lock")
    pyproject_path = pyproject_path or (REPO_ROOT / "pyproject.toml")

    if not lock_path.is_file():
        raise FileNotFoundError(
            f"uv.lock not found at {lock_path}. Run `uv sync --extra dev` to create it."
        )

    with lock_path.open("rb") as fh:
        lock_data = tomllib.load(fh)

    packages = lock_data.get("package", [])
    components = _build_components(packages)

    project_name, project_version = _load_project_metadata(pyproject_path)

    when = timestamp or datetime.now(UTC)
    serial = serial_number or f"urn:uuid:{uuid.uuid4()}"

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "timestamp": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tools": [
                {
                    "name": TOOL_NAME,
                    "vendor": "OneCompute",
                }
            ],
            "component": {
                "type": "application",
                "name": project_name,
                "version": project_version,
                "purl": f"pkg:pypi/{project_name}@{project_version}",
            },
        },
        "components": components,
    }


def _write_output(sbom: dict[str, Any], output_path: Path, *, to_stdout: bool) -> None:
    text = json.dumps(sbom, indent=2, sort_keys=False)
    if to_stdout:
        print(text)
        return
    output_path.write_text(text + "\n", encoding="utf-8")
    print(f"Wrote {len(sbom['components'])} components to {output_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a CycloneDX 1.5 SBOM from uv.lock (pure stdlib)."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "sbom.cyclonedx.json",
        help="Path to write the SBOM (default: sbom.cyclonedx.json at the repo root).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Write the SBOM to stdout instead of a file.",
    )
    args = parser.parse_args(argv)

    try:
        sbom = generate_sbom()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _write_output(sbom, args.output, to_stdout=args.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
