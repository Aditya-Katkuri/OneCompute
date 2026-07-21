"""Stable pseudonymous identity for measurement-only observers."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

_VALID_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$")


def default_measurement_id_path() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".onecompute"))
    return root / "OneCompute" / "observer-id"


def validate_measurement_id(value: str) -> str:
    candidate = value.strip()
    if not _VALID_ID.fullmatch(candidate):
        raise ValueError(
            "measurement ID must be 8-64 characters using letters, numbers, '.', '_', or '-'"
        )
    return candidate


def load_or_create_measurement_id(
    *,
    requested: str | None = None,
    path: str | Path | None = None,
) -> str:
    """Return an explicit fleet alias or persist a random hostname-free observer ID."""
    if requested:
        return validate_measurement_id(requested)
    identity_path = Path(path) if path is not None else default_measurement_id_path()
    try:
        existing = identity_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing = ""
    if existing:
        return validate_measurement_id(existing)

    identity = f"observer-{uuid.uuid4().hex[:24]}"
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = identity_path.with_name(f".{identity_path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(identity + "\n", encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, identity_path)
    return identity
