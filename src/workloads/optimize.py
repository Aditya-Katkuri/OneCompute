"""NON-AI #2 -- distributed param-sweep optimization (host-side builder + aggregator).

The fleet searches one big candidate space by giving each machine a contiguous
SLICE of candidate indices (an ``optimize`` tile). Every index deterministically
maps to a config scored by a fixed objective (negative Rastrigin, in
``jobkit.execute``). Each tile returns its local best; ``aggregate_optimize``
takes the global max across tiles, so the SAME winner emerges on every run no
matter how the indices were split. Pure stdlib; no heavy deps.
"""

from __future__ import annotations

from workloads.partition import even_ranges, weighted_ranges


def build_optimize_jobs(
    n_tiles: int,
    n_candidates: int = 30_000,
    dims: int = 8,
    seed: int = 0,
    weights: list[float] | None = None,
) -> list[dict]:
    """Build ``n_tiles`` ``optimize`` SubmitRequest-shaped jobs, one per index-range slice.

    Candidate indices ``[0, n_candidates)`` are partitioned across tiles (evenly, or
    proportional to ``weights``). ``units`` is the candidate count of the slice, so
    ``sum(units) == n_candidates`` and the slices cover the space with no gaps/overlaps.
    Empty slices are skipped.
    """
    if n_tiles <= 0:
        raise ValueError("n_tiles must be positive")
    if n_candidates <= 0:
        raise ValueError("n_candidates must be positive")
    if dims <= 0:
        raise ValueError("dims must be positive")

    if weights is None:
        slices = even_ranges(n_candidates, n_tiles)
    else:
        if len(weights) != n_tiles:
            raise ValueError("weights length must equal n_tiles")
        slices = weighted_ranges(n_candidates, weights)

    jobs: list[dict] = []
    for idx_start, idx_end in slices:
        if idx_end <= idx_start:
            continue  # zero-candidate slice (n_candidates < n_tiles); skip
        jobs.append(
            {
                "kind": "optimize",
                "input": {
                    "idx_start": idx_start,
                    "idx_end": idx_end,
                    "dims": dims,
                    "seed": seed,
                },
                "units": idx_end - idx_start,
            }
        )
    return jobs


def aggregate_optimize(results: list[dict]) -> dict:
    """Reduce ``optimize`` tile results to the single global best across the fleet.

    Picks the tile result with the maximum ``best_score`` (ties broken by the lower
    ``best_index`` for determinism). Returns ``{best_score, best_params, best_index,
    evaluated}`` where ``evaluated`` is the total candidates the fleet scored. Returns
    a sentinel (``best_index == -1``, ``best_score == -inf``) if no tile evaluated
    anything.
    """
    best: dict | None = None
    total_evaluated = 0
    for tile in results:
        total_evaluated += int(tile.get("evaluated", 0))
        if int(tile.get("best_index", -1)) < 0:
            continue  # tile evaluated nothing (e.g. yielded before first candidate)
        if best is None:
            best = tile
            continue
        score = float(tile["best_score"])
        best_score = float(best["best_score"])
        if score > best_score or (score == best_score and tile["best_index"] < best["best_index"]):
            best = tile

    if best is None:
        return {
            "best_score": float("-inf"),
            "best_params": [],
            "best_index": -1,
            "evaluated": total_evaluated,
        }
    return {
        "best_score": float(best["best_score"]),
        "best_params": list(best["best_params"]),
        "best_index": int(best["best_index"]),
        "evaluated": total_evaluated,
    }
