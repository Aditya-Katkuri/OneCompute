"""AI: distributed model evaluation / LLM-as-judge (host-side builder + aggregator).

The fleet grades a batch of (question, answer) items with a local-model judge; each answer
carries a ``label`` (which system produced it) so ``aggregate_eval`` builds a leaderboard +
score distribution. This is the canonical NightShift use case (model eval / benchmarking).
Scale the item count for runtime (each item is one judging call on the local model).
"""

from __future__ import annotations

from workloads.partition import even_ranges, weighted_ranges

# A small built-in eval set: two "systems" answer the same questions, one stronger than the
# other, so the leaderboard separates them. Supply your own `items` to grade real outputs.
_QA = [
    ("What does a load balancer do?",
     "It spreads incoming requests across multiple servers so no single one is overwhelmed.",
     "It makes the internet faster."),
    ("Why use a write-ahead log?",
     "Changes are durably appended before being applied, so a crash can be recovered without data loss.",
     "To write logs ahead of time."),
    ("What is a race condition?",
     "A bug where the outcome depends on the unpredictable timing of concurrent operations on shared state.",
     "When two computers race."),
    ("What is idempotency?",
     "An operation is idempotent if applying it multiple times has the same effect as applying it once.",
     "Doing something once."),
    ("Why shard a database?",
     "To split data across nodes so capacity and throughput scale horizontally beyond one machine.",
     "To break the database."),
    ("What is a Merkle tree?",
     "A tree of hashes where each node hashes its children, enabling efficient tamper-evident verification.",
     "A kind of plant."),
]
DEFAULT_EVAL_ITEMS: list[dict] = (
    [{"question": q, "answer": good, "label": "strong-model"} for q, good, _ in _QA]
    + [{"question": q, "answer": weak, "label": "weak-model"} for q, _, weak in _QA]
)


def build_eval_jobs(
    n_tiles: int,
    items: list[dict] | None = None,
    rubric: str = "correctness, clarity, and completeness",
    model: str = "",
    weights: list[float] | None = None,
) -> list[dict]:
    """Build ``n_tiles`` ``ai.eval`` jobs, splitting the items to grade across the fleet."""
    if n_tiles <= 0:
        raise ValueError("n_tiles must be positive")
    data = list(items if items is not None else DEFAULT_EVAL_ITEMS)
    if not data:
        raise ValueError("no items to evaluate")

    if weights is None:
        ranges = even_ranges(len(data), n_tiles)
    else:
        if len(weights) != n_tiles:
            raise ValueError("weights length must equal n_tiles")
        ranges = weighted_ranges(len(data), weights)

    jobs: list[dict] = []
    for start, end in ranges:
        if end <= start:
            continue
        jobs.append(
            {
                "kind": "ai.eval",
                "input": {"items": data[start:end], "rubric": rubric, "model": model},
                "units": end - start,
            }
        )
    return jobs


def aggregate_eval(results: list[dict]) -> dict:
    """Merge ``ai.eval`` tiles into mean score, a 0-10 distribution, and a per-label leaderboard."""
    rows = [row for res in results if res for row in res.get("results", [])]
    if not rows:
        return {"n": 0, "mean_score": 0.0, "leaderboard": [], "distribution": [0] * 11}

    distribution = [0] * 11
    by_label: dict[str, list[int]] = {}
    for row in rows:
        score = max(0, min(10, int(row.get("score", 0))))
        distribution[score] += 1
        by_label.setdefault(str(row.get("label", "(unlabeled)")), []).append(score)

    leaderboard = sorted(
        (
            {"label": label, "mean_score": sum(scores) / len(scores), "n": len(scores)}
            for label, scores in by_label.items()
        ),
        key=lambda entry: -entry["mean_score"],
    )
    return {
        "n": len(rows),
        "mean_score": sum(r.get("score", 0) for r in rows) / len(rows),
        "leaderboard": leaderboard,
        "distribution": distribution,
    }


__all__ = ["build_eval_jobs", "aggregate_eval", "DEFAULT_EVAL_ITEMS"]
