"""AI #2 -- distributed synthetic-data generation (host-side builder + merger).

The fleet generates ONE synthetic dataset by giving each machine a contiguous
SLICE of rows (an ``ai.synth`` tile) with a distinct ``start_index`` so seeds
never collide. With a backend key the executor asks the LLM for one JSON record
per row; without a key it uses a disclosed deterministic fallback. ``merge_synth``
concatenates the tile rows back in tile order into the final dataset.
"""

from __future__ import annotations

from workloads.partition import even_ranges, weighted_ranges

DEFAULT_FIELDS = ["name", "role", "team", "summary"]


def build_synth_jobs(
    n_tiles: int,
    total_rows: int = 300,
    fields: list[str] | None = None,
    model: str = "",
    spec: str = "a software employee record",
    weights: list[float] | None = None,
) -> list[dict]:
    """Build ``n_tiles`` ``ai.synth`` SubmitRequest-shaped jobs, one per row-slice.

    Rows ``[0, total_rows)`` are partitioned across tiles (evenly, or proportional to
    ``weights``). Each tile gets a distinct ``start_index`` equal to its first row, so
    fallback seeds (and LLM record numbers) never collide and the merged dataset has no
    duplicate rows. ``units`` is the slice's row count, so ``sum(units) == total_rows``
    with no gaps/overlaps. Empty slices are skipped.
    """
    if n_tiles <= 0:
        raise ValueError("n_tiles must be positive")
    if total_rows <= 0:
        raise ValueError("total_rows must be positive")
    field_list = list(DEFAULT_FIELDS if fields is None else fields)

    if weights is None:
        slices = even_ranges(total_rows, n_tiles)
    else:
        if len(weights) != n_tiles:
            raise ValueError("weights length must equal n_tiles")
        slices = weighted_ranges(total_rows, weights)

    jobs: list[dict] = []
    for start, end in slices:
        if end <= start:
            continue  # zero-row slice (total_rows < n_tiles); skip
        jobs.append(
            {
                "kind": "ai.synth",
                "input": {
                    "n_rows": end - start,
                    "spec": spec,
                    "fields": field_list,
                    "model": model,
                    "start_index": start,
                },
                "units": end - start,
            }
        )
    return jobs


def merge_synth(results: list[dict]) -> list[dict]:
    """Concatenate ``ai.synth`` tile rows into one dataset, ordered by ``start_index``.

    The fleet finishes tiles out of order, so we sort by each tile's ``start_index`` when
    callers attach it (a stable sort preserves incoming order for any tiles that don't).
    Each tile's ``rows`` is a list of dict records; the result is their concatenation.
    """
    ordered = sorted(
        enumerate(results),
        key=lambda pair: (int(pair[1].get("start_index", 0)), pair[0]),
    )
    merged: list[dict] = []
    for _, tile in ordered:
        merged.extend(tile.get("rows", []))
    return merged
