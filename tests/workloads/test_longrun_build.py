"""Builder + aggregator tests for the long-running NON-AI workloads."""

import pytest

from jobkit.execute import execute
from workloads.hashcrack import aggregate_hashcrack, build_hashcrack_jobs
from workloads.montecarlo import aggregate_montecarlo, build_montecarlo_jobs


@pytest.fixture(autouse=True)
def _single_core(monkeypatch):
    monkeypatch.setenv("ONECOMPUTE_MAX_WORKERS", "1")


def test_montecarlo_build_splits_paths_exactly():
    jobs = build_montecarlo_jobs(n_tiles=4, total_paths=10_000, horizon_days=20)
    assert len(jobs) == 4
    assert all(j["kind"] == "montecarlo" for j in jobs)
    assert sum(j["units"] for j in jobs) == 10_000  # no gaps/overlap in the path split


def test_montecarlo_aggregate_merges_and_computes_var():
    jobs = build_montecarlo_jobs(n_tiles=3, total_paths=18_000, horizon_days=40, hist_bins=60)
    results = [execute(j["kind"], j["input"]) for j in jobs]
    agg = aggregate_montecarlo(results)
    assert agg["paths"] == 18_000
    assert sum(agg["hist"]) == 18_000
    # VaR/CVaR present and ordered (99% tail loss >= 95% tail loss)
    assert "var_95" in agg and "var_99" in agg and "cvar_99" in agg
    assert agg["var_99"] >= agg["var_95"]


def test_hashcrack_build_splits_keyspace_exactly():
    jobs = build_hashcrack_jobs(n_tiles=5, keyspace=1_000_000, target_prefix="0000")
    assert len(jobs) == 5
    assert sum(j["units"] for j in jobs) == 1_000_000
    # contiguous, gap-free nonce ranges
    starts = sorted(j["input"]["nonce_start"] for j in jobs)
    ends = sorted(j["input"]["nonce_end"] for j in jobs)
    assert starts[0] == 0 and ends[-1] == 1_000_000


def test_hashcrack_aggregate_picks_winner_and_sums_hashes():
    jobs = build_hashcrack_jobs(n_tiles=4, keyspace=400_000, target_prefix="00")
    results = [execute(j["kind"], j["input"]) for j in jobs]
    agg = aggregate_hashcrack(results)
    assert agg["found"] is True
    assert agg["hash"].startswith("00")
    assert agg["hashes_tried"] > 0 and agg["tiles"] == 4
