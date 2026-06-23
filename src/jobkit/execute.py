"""Canonical NightShift job execution (FROZEN, shared).

ONE source of truth for "how to execute a job kind". Used both in-process by the
worker (T2) and inside the sandbox by isolation (T3, via `python -m jobkit`). This
unification guarantees a job produces the same result whether run directly or isolated.

Every executor takes (input: dict, should_yield: Callable[[], bool]) and returns a dict.
Chunkable executors check should_yield() between chunks and return early with
{"...": partial, "yielded": True} so the worker can preempt sub-second.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from contracts.hashing import sha256_hex

YieldFn = Callable[[], bool]


def _data_transform(input: dict, should_yield: YieldFn) -> dict:
    items = input.get("items", [])
    op = input.get("op", "square")
    results: list = []
    for item in items:
        if should_yield():
            return {"results": results, "yielded": True}
        if op == "square":
            results.append(item * item)
        elif op == "upper":
            results.append(str(item).upper())
        elif op == "sha256":
            results.append(sha256_hex(item))
        else:
            raise ValueError(f"unknown data.transform op: {op!r}")
    return {"results": results, "yielded": False}


def _challenge(input: dict, should_yield: YieldFn) -> dict:
    # Deterministic, integer-exact (T4 verifies bitwise — no FP ambiguity).
    x = int(input["x"])
    return {"y": x * x + 1}


def _detect_ai_backend() -> str | None:
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def _ai_one(backend: str | None, prompt: str, model: str, max_tokens: int) -> tuple[str, int]:
    """Run a single prompt. Real SDK call when a key is present, else a disclosed
    token-proportional fallback (parallelism stays real; see architecture.md §13)."""
    if backend == "openai":
        from openai import OpenAI

        client = OpenAI()
        resp = client.chat.completions.create(
            model=model or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        text = resp.choices[0].message.content or ""
        used = getattr(getattr(resp, "usage", None), "total_tokens", max_tokens)
        return text, int(used)
    if backend == "anthropic":
        from anthropic import Anthropic

        client = Anthropic()
        resp = client.messages.create(
            model=model or "claude-3-5-haiku-latest",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content)
        used = getattr(getattr(resp, "usage", None), "output_tokens", max_tokens)
        return text, int(used)
    # Fallback: proportional to prompt size, capped; deterministic stub completion.
    time.sleep(min(0.02 * len(prompt.split()), 0.4))
    return f"[fallback completion for {len(prompt)} chars]", max_tokens


def _ai_batch_infer(input: dict, should_yield: YieldFn) -> dict:
    prompts = input.get("prompts", [])
    model = input.get("model", "")
    max_tokens = int(input.get("max_tokens", 64))
    backend = _detect_ai_backend()
    results: list = []
    for prompt in prompts:
        if should_yield():
            return {"results": results, "backend": backend or "fallback", "yielded": True}
        text, tokens = _ai_one(backend, prompt, model, max_tokens)
        results.append({"prompt": prompt, "completion": text, "tokens": tokens})
    return {"results": results, "backend": backend or "fallback", "yielded": False}


EXECUTORS: dict[str, Callable[[dict, YieldFn], dict]] = {
    "data.transform": _data_transform,
    "challenge": _challenge,
    "ai.batch_infer": _ai_batch_infer,
    "eval": _data_transform,   # eval reuses the deterministic transform path in the PoC
}


def execute(kind: str, input: dict, should_yield: YieldFn = lambda: False) -> dict:
    """Execute a job of `kind` over `input`. Raises ValueError on an unknown kind."""
    executor = EXECUTORS.get(kind)
    if executor is None:
        raise ValueError(f"no executor registered for job kind: {kind!r}")
    return executor(input, should_yield)
