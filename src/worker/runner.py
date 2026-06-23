"""Default Phase-1 chunked job runner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from contracts import JobManifest, sha256_hex


def _transform_item(item: Any, op: str) -> Any:
    if op == "sha256":
        return sha256_hex(item)
    if op == "upper":
        return str(item).upper()
    if op == "square":
        return item * item
    raise ValueError(f"unknown data.transform op: {op}")


def default_runner(
    manifest: JobManifest,
    input: dict,
    should_yield: Callable[[], bool] = lambda: False,
) -> dict:
    """Run a built-in job in small chunks, honoring yield requests between chunks."""
    if manifest.kind == "data.transform":
        items = list(input.get("items", []))
        op = input.get("op")
        results = []
        chunk_size = 1
        for start in range(0, len(items), chunk_size):
            chunk = items[start : start + chunk_size]
            results.extend(_transform_item(item, op) for item in chunk)
            if should_yield():
                return {"results": results, "yielded": True}
        return {"results": results, "yielded": False}

    if manifest.kind == "challenge":
        x = int(input["x"])
        return {"y": x * x + 1}

    raise ValueError(f"unknown job kind: {manifest.kind}")
