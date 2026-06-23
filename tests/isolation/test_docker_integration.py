"""Daemon-guarded integration tests for the real Docker-per-job boundary.

These only run when the Docker daemon is actually reachable. On the demo SKU the engine
is intermittently down (HTTP 500); when it is, every test here skips at runtime so the
suite stays green. When it is up, they prove (a) a CPU job runs correctly *inside* the
container and (b) a long job is yielded/killed sub-second.
"""

from __future__ import annotations

import time

import pytest

from contracts import Limits
from isolation import active_boundary, docker_available, run_in_isolation


def _require_docker() -> None:
    if not docker_available(force=True):
        pytest.skip("Docker daemon unreachable; live container path not exercised")


def test_docker_cpu_job_runs_inside_container():
    _require_docker()
    assert active_boundary() == "docker"
    # challenge is deterministic + stdlib-only: proves jobkit executes in the slim image.
    assert run_in_isolation("challenge", {"x": 6}) == {"y": 37}
    out = run_in_isolation("data.transform", {"items": [1, 2, 3, 4], "op": "square"})
    assert out["results"] == [1, 4, 9, 16]


def test_docker_long_job_yields_subsecond():
    _require_docker()
    started = time.monotonic()
    out = run_in_isolation(
        "data.transform",
        {"items": list(range(5_000_000)), "op": "square"},
        limits=Limits(timeout_s=600),
        should_yield=lambda: True,
    )
    elapsed = time.monotonic() - started
    assert out == {"yielded": True, "results": []}
    # Container start (image pre-pulled) + kill-by-name must complete quickly.
    assert elapsed < 20, f"yield took {elapsed:.2f}s; container was not killed promptly"
