"""Canonical hashing for manifests, inputs, and results (FROZEN).

Shared so T1, T2, and T4 all compute byte-identical hashes. Canonical JSON =
sorted keys, no extra whitespace, UTF-8. Ed25519 signing (T4) signs the
canonical manifest bytes produced here.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(obj: Any) -> bytes:
    """Deterministic JSON encoding used for every hash/signature in the system."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def sha256_hex(obj: Any) -> str:
    """sha256 of the canonical encoding of `obj` (dict/list/str all accepted)."""
    if isinstance(obj, (bytes, bytearray)):
        return hashlib.sha256(bytes(obj)).hexdigest()
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()
