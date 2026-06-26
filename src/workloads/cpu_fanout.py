"""Deterministic CPU fan-out jobs for the OneCompute demo."""

from __future__ import annotations


def generate_jobs(
    n_jobs: int = 8,
    items_per_job: int = 200,
    op: str = "square",
) -> list[dict]:
    """Build deterministic `data.transform` SubmitRequest-shaped jobs."""
    if n_jobs < 0:
        raise ValueError("n_jobs must be non-negative")
    if items_per_job <= 0:
        raise ValueError("items_per_job must be positive")

    jobs: list[dict] = []
    for index in range(n_jobs):
        start = index * items_per_job
        items = list(range(start, start + items_per_job))
        jobs.append(
            {
                "kind": "data.transform",
                "input": {"items": items, "op": op},
                "units": items_per_job,
            }
        )
    return jobs


def ghost_bar_seconds(total_items: int, per_item_s: float = 0.0005) -> float:
    """Estimate the single-machine baseline for the dashboard ghost bar."""
    if total_items < 0:
        raise ValueError("total_items must be non-negative")
    if per_item_s < 0:
        raise ValueError("per_item_s must be non-negative")
    return total_items * per_item_s

