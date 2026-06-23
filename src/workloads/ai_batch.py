"""AI prompt-slice job builders for the NightShift demo."""

from __future__ import annotations

DEFAULT_PROMPTS = [
    "Summarize the NightShift demo in one sentence.",
    "List two risks for idle compute harvesting.",
    "Draft a friendly worker opt-in message.",
    "Explain why measured throughput beats TOPS.",
    "Name one good batch workload for NightShift.",
    "Write a short judge-facing value prop.",
    "Describe the instant-yield moment.",
    "Suggest a trust signal for worker results.",
    "Create a concise dashboard headline.",
    "Explain the ghost bar in plain English.",
    "Name a privacy-preserving safeguard.",
    "Close the pitch with an honest ceiling claim.",
]


def build_prompt_jobs(
    prompts: list[str] | None = None,
    slice_size: int = 3,
    model: str = "",
    max_tokens: int = 48,
) -> list[dict]:
    """Split prompts into `ai.batch_infer` SubmitRequest-shaped jobs."""
    if slice_size <= 0:
        raise ValueError("slice_size must be positive")
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")

    prompt_list = list(DEFAULT_PROMPTS if prompts is None else prompts)
    jobs: list[dict] = []
    for start in range(0, len(prompt_list), slice_size):
        prompt_slice = prompt_list[start : start + slice_size]
        jobs.append(
            {
                "kind": "ai.batch_infer",
                "input": {
                    "prompts": prompt_slice,
                    "model": model,
                    "max_tokens": max_tokens,
                },
                "units": len(prompt_slice),
            }
        )
    return jobs

