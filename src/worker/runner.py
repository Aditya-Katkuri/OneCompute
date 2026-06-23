"""Worker job runner — a thin wrapper over the shared `jobkit` so that in-process
execution (here) and isolated execution (T3, via `python -m jobkit`) are identical."""

from __future__ import annotations

from collections.abc import Callable

from contracts import JobManifest
from jobkit.execute import execute


def default_runner(
    manifest: JobManifest,
    input: dict,
    should_yield: Callable[[], bool] = lambda: False,
) -> dict:
    """Run a built-in job via the shared kit, honoring yield requests between chunks."""
    return execute(manifest.kind, input, should_yield)

