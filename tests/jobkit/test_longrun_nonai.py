"""Executor tests for the long-running NON-AI kinds (montecarlo, hashcrack).

ONECOMPUTE_MAX_WORKERS=1 keeps these hermetic + fast (sequential, no process spawn); the
multi-core path is the same code with the cap lifted.
"""

import pytest

from jobkit.execute import execute


@pytest.fixture(autouse=True)
def _single_core(monkeypatch):
    monkeypatch.setenv("ONECOMPUTE_MAX_WORKERS", "1")


def test_montecarlo_moments_and_histogram():
    out = execute(
        "montecarlo",
        {"n_paths": 20_000, "horizon_days": 60, "mu": 0.07, "sigma": 0.20, "hist_bins": 50},
    )
    assert out["paths"] == 20_000
    assert sum(out["hist"]) == 20_000  # every path is binned exactly once
    assert out["yielded"] is False
    assert -0.6 < out["mean_return"] < 0.8  # GBM with positive drift
    assert 0.0 < out["stdev"] < 1.5
    assert out["worst_return"] <= 0.0


def test_montecarlo_yield_returns_partial():
    out = execute("montecarlo", {"n_paths": 200_000, "horizon_days": 252}, should_yield=lambda: True)
    assert out["yielded"] is True
    assert out["paths"] < 200_000  # preempted before finishing all paths


def test_hashcrack_finds_an_easy_target():
    out = execute(
        "hashcrack",
        {"prefix": "onecompute", "target_prefix": "00", "nonce_start": 0, "nonce_end": 200_000},
    )
    assert out["found"] is True
    assert out["nonce"] is not None
    assert out["hash"].startswith("00")
    assert out["hashes_tried"] > 0


def test_hashcrack_counts_full_keyspace_when_not_found():
    # An impossible target within a tiny range -> scans it all, reports the count, no winner.
    out = execute(
        "hashcrack",
        {"target_prefix": "fffffffff", "nonce_start": 0, "nonce_end": 5_000},
    )
    assert out["found"] is False and out["nonce"] is None
    assert out["hashes_tried"] == 5_000


def test_hashcrack_yield_returns_partial():
    out = execute("hashcrack", {"target_prefix": "ffffff", "nonce_end": 10_000_000},
                  should_yield=lambda: True)
    assert out["yielded"] is True
