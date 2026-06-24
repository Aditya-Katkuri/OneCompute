"""The `optimize` executor evaluates candidate slices deterministically; the aggregator
picks the global best across the fleet."""
from __future__ import annotations

from jobkit.execute import execute
from workloads.optimize import aggregate_optimize


def test_best_is_deterministic_across_a_range():
    a = execute("optimize", {"idx_start": 0, "idx_end": 500, "dims": 8, "seed": 0})
    b = execute("optimize", {"idx_start": 0, "idx_end": 500, "dims": 8, "seed": 0})
    assert a["best_index"] == b["best_index"]
    assert a["best_score"] == b["best_score"]
    assert a["best_params"] == b["best_params"]
    assert a["evaluated"] == 500
    assert 0 <= a["best_index"] < 500
    # Negative-Rastrigin objective: best score is <= 0 (global max 0.0 at all-zeros).
    assert a["best_score"] <= 0.0


def test_yield_returns_partial_and_flag():
    out = execute(
        "optimize",
        {"idx_start": 0, "idx_end": 1000, "dims": 8, "seed": 0},
        should_yield=lambda: True,
    )
    assert out["yielded"] is True
    assert out["evaluated"] == 0
    assert out["best_index"] == -1


def test_aggregate_picks_global_max():
    # Split [0, 600) across three tiles; the aggregate winner must equal the winner of
    # the full range (deterministic global best regardless of how the work was split).
    full = execute("optimize", {"idx_start": 0, "idx_end": 600, "dims": 8, "seed": 0})
    tiles = [
        execute("optimize", {"idx_start": s, "idx_end": e, "dims": 8, "seed": 0})
        for s, e in [(0, 200), (200, 400), (400, 600)]
    ]
    agg = aggregate_optimize(tiles)
    assert agg["best_index"] == full["best_index"]
    assert agg["best_score"] == full["best_score"]
    assert agg["best_params"] == full["best_params"]
    assert agg["evaluated"] == 600


def test_aggregate_handles_all_empty_tiles():
    empty = [
        execute(
            "optimize",
            {"idx_start": 0, "idx_end": 10, "dims": 4, "seed": 0},
            should_yield=lambda: True,
        )
        for _ in range(3)
    ]
    agg = aggregate_optimize(empty)
    assert agg["best_index"] == -1
    assert agg["evaluated"] == 0
