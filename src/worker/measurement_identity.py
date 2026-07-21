"""Stable pseudonymous identity for measurement-only observers."""

from __future__ import annotations

import os
import re
import time
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


def _read_identity(
    path: Path,
    *,
    wait_for_complete_write: bool = False,
    attempts: int = 20,
) -> str | None:
    for attempt in range(attempts):
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        if text.strip() and (not wait_for_complete_write or text.endswith("\n")):
            return validate_measurement_id(text)
        if attempt + 1 < attempts:
            time.sleep(0.01)
    if wait_for_complete_write:
        raise ValueError(f"measurement identity file is incomplete: {path}")
    return validate_measurement_id(text)


def load_or_create_measurement_id(
    *,
    requested: str | None = None,
    path: str | Path | None = None,
) -> str:
    """Return an explicit fleet alias or persist a random hostname-free observer ID."""
    if requested:
        return validate_measurement_id(requested)
    identity_path = Path(path) if path is not None else default_measurement_id_path()
    existing = _read_identity(identity_path, wait_for_complete_write=True)
    if existing is not None:
        return existing

    identity = f"observer-{uuid.uuid4().hex[:24]}"
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = identity_path.with_name(f".{identity_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(identity + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o600)
        except OSError:
            pass

        try:
            # Publish only a complete file. A hard link is atomic and refuses to replace an ID
            # created by another observer process racing this one.
            os.link(temporary, identity_path)
            return identity
        except FileExistsError:
            existing = _read_identity(identity_path, wait_for_complete_write=True)
            if existing is None:
                raise OSError(
                    f"measurement identity disappeared during creation: {identity_path}"
                ) from None
            return existing
        except OSError:
            # Some redirected user-profile filesystems do not support hard links. O_EXCL still
            # preserves the one-winner rule; racing readers wait for the terminating newline.
            try:
                descriptor = os.open(
                    identity_path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                    0o600,
                )
            except FileExistsError:
                existing = _read_identity(identity_path, wait_for_complete_write=True)
                if existing is None:
                    raise OSError(
                        f"measurement identity disappeared during creation: {identity_path}"
                    ) from None
                return existing
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(identity + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                try:
                    identity_path.unlink()
                except OSError:
                    pass
                raise
            return identity
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
