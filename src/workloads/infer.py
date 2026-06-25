"""AI: distributed local-model inference at scale (host-side builder).

The fleet runs a big batch of prompts through the LOCAL model (Ollama on CPU), each machine
scoring a slice. The ``ai.infer`` kind reuses the batch-inference executor; this builder just
manufactures a deterministic prompt batch so a one-click launch has work to do. Scale
``n_prompts`` for runtime (each prompt is one CPU inference on the local model).
"""

from __future__ import annotations

from workloads.partition import even_ranges, weighted_ranges

_INFER_TEMPLATES = [
    "Explain {t} in two sentences for a new engineer.",
    "List three real-world risks of {t}.",
    "Write a one-line analogy for {t}.",
    "Name one common misconception about {t}.",
    "Give a concrete production example of {t}.",
    "Summarize {t} as a single tweet.",
]
_INFER_TOPICS = [
    "idle compute harvesting", "distributed scheduling", "model evaluation",
    "Monte-Carlo simulation", "proof-of-work", "vector search", "lease-based work queues",
    "synthetic data", "knowledge graphs", "sandbox isolation", "CPU utilization", "load balancing",
]


def build_infer_jobs(
    n_tiles: int,
    n_prompts: int = 120,
    topic: str | None = None,
    model: str = "",
    max_tokens: int = 80,
    weights: list[float] | None = None,
) -> list[dict]:
    """Build ``n_tiles`` ``ai.infer`` jobs over a deterministic batch of ``n_prompts`` prompts."""
    if n_tiles <= 0:
        raise ValueError("n_tiles must be positive")
    if n_prompts <= 0:
        raise ValueError("n_prompts must be positive")

    prompts: list[str] = []
    i = 0
    while len(prompts) < n_prompts:
        template = _INFER_TEMPLATES[i % len(_INFER_TEMPLATES)]
        subject = topic or _INFER_TOPICS[i % len(_INFER_TOPICS)]
        prompts.append(template.format(t=subject))
        i += 1

    if weights is None:
        ranges = even_ranges(n_prompts, n_tiles)
    else:
        if len(weights) != n_tiles:
            raise ValueError("weights length must equal n_tiles")
        ranges = weighted_ranges(n_prompts, weights)

    jobs: list[dict] = []
    for start, end in ranges:
        if end <= start:
            continue
        jobs.append(
            {
                "kind": "ai.infer",
                "input": {"prompts": prompts[start:end], "model": model, "max_tokens": max_tokens},
                "units": end - start,
            }
        )
    return jobs


__all__ = ["build_infer_jobs"]
