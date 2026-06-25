"""Canonical NightShift job execution (FROZEN, shared).

ONE source of truth for "how to execute a job kind". Used both in-process by the
worker (T2) and inside the sandbox by isolation (T3, via `python -m jobkit`). This
unification guarantees a job produces the same result whether run directly or isolated.

Every executor takes (input: dict, should_yield: Callable[[], bool]) and returns a dict.
Chunkable executors check should_yield() between chunks and return early with
{"...": partial, "yielded": True} so the worker can preempt sub-second.
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait

from contracts.hashing import sha256_hex

YieldFn = Callable[[], bool]


# --- multi-core fan-out for CPU-bound kinds (saturate every core for high utilization) ------
# A single tile spreads across all of a machine's cores so it pins the whole machine (visible on
# the dashboard usage graph) AND finishes a big problem inside the ~15-min window. In a Linux
# sandbox container multiprocessing uses fast fork; on a Windows worker it uses spawn (so each
# task fn must be a top-level, picklable module function). Stays yield-able: should_yield() is
# polled in the PARENT (children never get the unpicklable closure); a yield cancels pending tasks
# and the worker's Job-Object/Docker kill is the hard backstop.

def _core_count() -> int:
    """Cores to fan a tile across. ``ONECOMPUTE_MAX_WORKERS`` caps it (set to 1 for hermetic,
    spawn-free tests; or to leave the employee some headroom)."""
    env = os.environ.get("ONECOMPUTE_MAX_WORKERS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, os.cpu_count() or 1)


def _parallel_map(
    fn: Callable, tasks: list, should_yield: YieldFn, max_workers: int | None = None
) -> tuple[list, bool]:
    """Run ``fn(task)`` for every task across cores; return ``(results, yielded)``.

    Falls back to in-process sequential when there's <=1 worker/task or the pool can't start
    (restricted sandbox), so a tile always completes. Results are in completion order.
    """
    workers = max_workers if max_workers is not None else _core_count()
    if workers > 1 and len(tasks) > 1:
        pool = None
        try:
            pool = ProcessPoolExecutor(max_workers=workers)
        except Exception:
            pool = None
        if pool is not None:
            results: list = []
            yielded = False
            try:
                pending = {pool.submit(fn, task) for task in tasks}
                while pending:
                    if should_yield():
                        yielded = True
                        break
                    done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                    for future in done:
                        try:
                            results.append(future.result())
                        except Exception:
                            pass
            finally:
                pool.shutdown(wait=False, cancel_futures=True)
            return results, yielded
    results = []
    for task in tasks:
        if should_yield():
            return results, True
        results.append(fn(task))
    return results, False


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


# Local model (Ollama) — the CPU-only, no-API inference backend (its OpenAI-compatible
# endpoint). Overridable per worker via env so a laptop can point at its own model.
_LLM_LOCAL_URL = os.environ.get("ONECOMPUTE_LLM_URL", "http://127.0.0.1:11434/v1")
_LLM_LOCAL_MODEL = os.environ.get("ONECOMPUTE_LLM_MODEL", "llama3.2:3b")
_ollama_cache: bool | None = None


def _ollama_available() -> bool:
    """True if a local Ollama server responds (cached). Never raises. A 1.5s probe of /api/tags;
    set ONECOMPUTE_NO_LLM=1 to force the disclosed fallback (hermetic tests / no-model machines)."""
    global _ollama_cache
    if os.environ.get("ONECOMPUTE_NO_LLM"):
        return False
    if _ollama_cache is not None:
        return _ollama_cache
    import urllib.request

    base = _LLM_LOCAL_URL.removesuffix("/v1")
    try:
        with urllib.request.urlopen(base + "/api/tags", timeout=1.5) as resp:
            _ollama_cache = getattr(resp, "status", 200) == 200
    except Exception:
        _ollama_cache = False
    return bool(_ollama_cache)


def _detect_ai_backend() -> str | None:
    """Pick the inference backend, preferring the LOCAL model (the demo fleet is CPU-only / no
    cloud): local Ollama -> OpenAI key -> Anthropic key -> None (disclosed fallback)."""
    if os.environ.get("ONECOMPUTE_NO_LLM"):
        return None
    if _ollama_available():
        return "ollama"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def _ai_one(backend: str | None, prompt: str, model: str, max_tokens: int) -> tuple[str, int]:
    """Run a single prompt. Real model call when a backend is available (local Ollama on the
    CPU fleet, else a cloud SDK), otherwise a disclosed token-proportional fallback so the
    parallelism stays real even with no model present (see architecture.md §13)."""
    if backend == "ollama":
        from openai import OpenAI  # Ollama serves an OpenAI-compatible API at /v1

        client = OpenAI(base_url=_LLM_LOCAL_URL, api_key="ollama")
        resp = client.chat.completions.create(
            model=model or _LLM_LOCAL_MODEL,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        text = resp.choices[0].message.content or ""
        used = getattr(getattr(resp, "usage", None), "total_tokens", max_tokens)
        return text, int(used)
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
            model=model or "claude-haiku-4-5",
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


def _fractal(input: dict, should_yield: YieldFn) -> dict:
    """Render a horizontal BAND of the Mandelbrot set as escape counts (PURE STDLIB).

    Each fleet tile owns rows ``[row_start, row_end)`` of a ``width x height`` image.
    For every pixel we iterate ``z = z*z + c`` (c mapped from pixel coords into the
    complex plane) until ``|z| > 2`` (escape) or we hit ``max_iter`` (treated as
    in-set). The returned escape count per pixel is what the host-side assembler
    colorizes; an in-set pixel has count == ``max_iter`` (rendered black).

    Chunked per ROW: ``should_yield()`` is checked before each row so a mouse-touch
    preempts sub-second, returning the rows finished so far with ``yielded: True``.
    No numpy/PIL -- this runs unchanged inside the stdlib-only slim container.
    """
    width = int(input.get("width", 900))
    height = int(input.get("height", 600))
    row_start = int(input.get("row_start", 0))
    row_end = int(input.get("row_end", height))
    max_iter = int(input.get("max_iter", 120))
    x_min = float(input.get("x_min", -2.5))
    x_max = float(input.get("x_max", 1.0))
    y_min = float(input.get("y_min", -1.25))
    y_max = float(input.get("y_max", 1.25))

    # Precompute the per-pixel real-axis coordinates once (shared by every row).
    dx = (x_max - x_min) / width if width else 0.0
    dy = (y_max - y_min) / height if height else 0.0
    xs = [x_min + px * dx for px in range(width)]

    rows: list[list[int]] = []
    for py in range(row_start, row_end):
        if should_yield():
            return {
                "width": width,
                "row_start": row_start,
                "row_end": row_end,
                "max_iter": max_iter,
                "rows": rows,
                "yielded": True,
            }
        cy = y_min + py * dy
        row: list[int] = []
        append = row.append
        for cx in xs:
            zr = 0.0
            zi = 0.0
            count = 0
            # Iterate z = z^2 + c with |z|^2 escape test (avoids a sqrt per step).
            while count < max_iter and zr * zr + zi * zi <= 4.0:
                zr, zi = zr * zr - zi * zi + cx, 2.0 * zr * zi + cy
                count += 1
            append(count)
        rows.append(row)
    return {
        "width": width,
        "row_start": row_start,
        "row_end": row_end,
        "max_iter": max_iter,
        "rows": rows,
        "yielded": False,
    }


# Fixed objective for `optimize`: a hidden optimum the fleet collectively hunts for.
# Higher score is better; the global maximum (score 0.0) sits at this point, so the
# winning candidate is the one whose random config lands nearest it. Deterministic.
_OPTIMIZE_K = 1_000_003  # large stride so per-index RNG streams don't overlap
_OPTIMIZE_LO = -5.12
_OPTIMIZE_HI = 5.12


def _optimize_config(index: int, dims: int, seed: int) -> list[float]:
    """Deterministically synthesize candidate ``index``'s config (a list of ``dims`` floats)."""
    rng = random.Random(seed * _OPTIMIZE_K + index)
    return [rng.uniform(_OPTIMIZE_LO, _OPTIMIZE_HI) for _ in range(dims)]


def _optimize_score(params: list[float]) -> float:
    """Fixed objective: negative Rastrigin (higher is better, global max 0.0 at all-zeros)."""
    n = len(params)
    total = 10.0 * n
    for x in params:
        total += x * x - 10.0 * math.cos(2.0 * math.pi * x)
    return -total


def _optimize(input: dict, should_yield: YieldFn) -> dict:
    """Evaluate a SLICE of candidate configs against a fixed objective (PURE STDLIB).

    Each fleet tile owns candidate indices ``[idx_start, idx_end)``. Every index
    deterministically maps to a config (via ``random.Random(seed*K + i)``) scored by a
    FIXED objective (negative Rastrigin -- higher is better). We track the single best
    candidate in this slice; the host-side aggregator then takes the max across tiles,
    so the SAME global winner emerges on every run regardless of how work was split.

    Chunked per candidate: ``should_yield()`` is checked before each evaluation, so a
    yield returns the best-so-far with ``yielded: True`` (and ``best_index == -1`` /
    ``best_score == -inf`` if it yielded before evaluating anything).
    """
    idx_start = int(input.get("idx_start", 0))
    idx_end = int(input.get("idx_end", 0))
    dims = int(input.get("dims", 8))
    seed = int(input.get("seed", 0))

    best_score = float("-inf")
    best_params: list[float] = []
    best_index = -1
    evaluated = 0
    for i in range(idx_start, idx_end):
        if should_yield():
            return {
                "best_score": best_score,
                "best_params": best_params,
                "best_index": best_index,
                "evaluated": evaluated,
                "yielded": True,
            }
        params = _optimize_config(i, dims, seed)
        score = _optimize_score(params)
        evaluated += 1
        if score > best_score:
            best_score = score
            best_params = params
            best_index = i
    return {
        "best_score": best_score,
        "best_params": best_params,
        "best_index": best_index,
        "evaluated": evaluated,
        "yielded": False,
    }


# Small fixed pools for the disclosed no-key synthetic-data fallback (stdlib-only).
_SYNTH_FIRST = ["Ada", "Bjarne", "Grace", "Linus", "Margaret", "Dennis", "Barbara", "Ken",
                "Radia", "Guido", "Anita", "Donald", "Joan", "Alan", "Karen", "Brian"]
_SYNTH_LAST = ["Lovelace", "Hopper", "Torvalds", "Hamilton", "Ritchie", "Liskov", "Perlman",
               "Thompson", "Knuth", "Goldberg", "Turing", "Borg", "Cerf", "Allen", "Wing"]
_SYNTH_ROLES = ["Software Engineer", "Data Scientist", "Product Manager", "SRE",
                "Security Engineer", "ML Engineer", "Designer", "Engineering Manager"]
_SYNTH_TEAMS = ["Platform", "Infrastructure", "Growth", "Security", "Research",
                "Payments", "Mobile", "Developer Experience"]


def _synth_fallback_row(index: int, spec: str, fields: list[str]) -> dict:
    """Deterministic, disclosed stub record for index ``index`` (NO SDK, NO key needed).

    Imports nothing beyond ``random`` so it stays import-safe inside the slim container.
    """
    rng = random.Random(index)
    first = rng.choice(_SYNTH_FIRST)
    last = rng.choice(_SYNTH_LAST)
    name = f"{first} {last}"
    role = rng.choice(_SYNTH_ROLES)
    team = rng.choice(_SYNTH_TEAMS)
    record: dict = {}
    for field in fields:
        low = field.lower()
        if low == "name":
            record[field] = name
        elif low == "role" or low == "title":
            record[field] = role
        elif low == "team" or low == "department":
            record[field] = team
        elif low == "summary" or low == "bio":
            record[field] = f"{name} is a {role} on the {team} team ({spec})."
        elif low in ("id", "employee_id"):
            record[field] = f"EMP-{index:05d}"
        elif low == "email":
            record[field] = f"{first}.{last}@example.com".lower()
        else:
            # Unknown field: deterministic disclosed stub so callers always get every field.
            record[field] = f"{field}-{index}"
    return record


def _ai_synth(input: dict, should_yield: YieldFn) -> dict:
    """Generate a SLICE of synthetic records, one per row (AI #2).

    Follows the ``ai.batch_infer`` pattern: with a backend key we ask the LLM to emit
    one JSON object per row and parse defensively (a parse failure falls back to a stub
    row, so a flaky model never breaks the run); WITHOUT a key we use a fully disclosed
    deterministic fallback (``random.Random(start_index + i)``) that needs no SDK and no
    key, so the executor is import-safe in the stdlib-only slim container. Each fleet
    tile owns rows ``[start_index, start_index + n_rows)`` (distinct ``start_index`` per
    tile) so the merged dataset has no duplicate seeds.

    Chunked per row: ``should_yield()`` is checked before each row, returning the partial
    rows with ``yielded: True``.
    """
    n_rows = int(input.get("n_rows", 0))
    spec = input.get("spec", "a software employee record")
    fields = list(input.get("fields", ["name", "role", "team", "summary"]))
    model = input.get("model", "")
    start_index = int(input.get("start_index", 0))
    max_tokens = int(input.get("max_tokens", 160))
    backend = _detect_ai_backend()

    rows: list[dict] = []
    for offset in range(n_rows):
        if should_yield():
            return {
                "rows": rows,
                "backend": backend or "fallback",
                "start_index": start_index,
                "yielded": True,
            }
        index = start_index + offset
        if backend is None:
            rows.append(_synth_fallback_row(index, spec, fields))
            continue
        prompt = (
            f"Generate ONE realistic synthetic record describing {spec}. "
            f"Return ONLY a JSON object with exactly these keys: {', '.join(fields)}. "
            f"No prose, no markdown, no code fence. Record #{index}."
        )
        text, _tokens = _ai_one(backend, prompt, model, max_tokens)
        row = _parse_synth_record(text, fields)
        if row is None:  # defensive: model returned unparseable text -> disclosed stub
            row = _synth_fallback_row(index, spec, fields)
        rows.append(row)
    return {
        "rows": rows,
        "backend": backend or "fallback",
        "start_index": start_index,
        "yielded": False,
    }


def _parse_synth_record(text: str, fields: list[str]) -> dict | None:
    """Best-effort parse of an LLM completion into a record with the requested fields.

    Extracts the first ``{...}`` block and JSON-decodes it; returns ``None`` on any
    failure so the caller can substitute a disclosed stub row. Every requested field is
    guaranteed present in the returned dict (missing keys become ``""``).
    """
    import json

    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    return {field: parsed.get(field, "") for field in fields}


def _gpu_backend() -> tuple[object, str, str]:
    """Return ``(array_module, accelerator, device)``. Lazy + guarded: tries ``cupy`` (real
    CUDA) and falls back to ``numpy`` on the CPU when no CUDA stack/device is present. All
    imports happen here, never at module import, so the sandbox payload that copies this file
    stays stdlib-clean and the Docker/CPU kinds never pull in a GPU stack.
    """
    try:
        import cupy as xp  # type: ignore[import-not-found]

        if int(xp.cuda.runtime.getDeviceCount()) < 1:
            raise RuntimeError("no CUDA device")
        try:
            name = xp.cuda.runtime.getDeviceProperties(0)["name"]
            device = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
        except Exception:
            device = "cuda-device"
        return xp, "cuda", device
    except Exception:
        import numpy as xp  # CPU fallback; always available

        return xp, "cpu-fallback", "cpu"


def _sample_gpu_util() -> float | None:
    """Current GPU utilization percent via ``pynvml``, or ``None`` when unavailable. Never raises."""
    try:
        import pynvml  # type: ignore[import-not-found]

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu)
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception:
        return None


def _render(input: dict, should_yield: YieldFn) -> dict:
    """GPU-capable compute (a sized matmul -- the classic embarrassingly-parallel GPU job).

    Runs on CUDA via ``cupy`` when a device is present, else an HONEST CPU/``numpy`` fallback;
    the result discloses ``accelerator`` (``"cuda"`` | ``"cpu-fallback"``) and ``gpu_available``
    so we never claim GPU work that didn't happen. Chunked across ``iters`` so a mouse-touch
    yield preempts in-process; on the isolated host-side path the Job Object kills the process
    tree (kill-on-close), so a GPU job is just as preemptible as a CPU one.
    """
    size = max(1, int(input.get("size", 256)))
    iters = max(1, int(input.get("iters", 8)))
    seed = int(input.get("seed", 0))
    xp, accelerator, device = _gpu_backend()

    base = xp.full((size, size), 1.0 + (seed % 7) * 0.01, dtype=xp.float32)
    factor = xp.full((size, size), 1.0001, dtype=xp.float32)
    checksum = 0.0
    util_peak: float | None = None
    done = 0
    for i in range(iters):
        if should_yield():
            break
        product = (base * (1.0 + i * 1e-3)) @ factor
        if accelerator == "cuda":
            try:
                xp.cuda.Device(0).synchronize()
            except Exception:
                pass
            sample = _sample_gpu_util()
            if sample is not None:
                util_peak = sample if util_peak is None else max(util_peak, sample)
        checksum += float(product.sum())
        done += 1
    return {
        "results": {"checksum": float(checksum), "iters_done": done},
        "accelerator": accelerator,
        "device": device,
        "gpu_available": accelerator == "cuda",
        "gpu_util_peak": util_peak,
        "yielded": done < iters,
    }


# --- NON-AI: Monte-Carlo portfolio risk (multi-core, pure stdlib) ---------------------------

def _mc_chunk(args: tuple) -> tuple:
    """One core's slice of GBM paths -> (count, sum, sumsq, worst_return, hist_counts)."""
    seed, n_paths, steps, mu, sigma, lo, hi, nbins = args
    rng = random.Random(seed)
    gauss = rng.gauss
    dt = 1.0 / steps if steps else 1.0
    drift = (mu - 0.5 * sigma * sigma) * dt
    vol = sigma * math.sqrt(dt)
    width = (hi - lo) / nbins if nbins else 1.0
    hist = [0] * nbins
    total = 0.0
    total_sq = 0.0
    worst = 0.0
    for _ in range(n_paths):
        log_ret = 0.0
        for _ in range(steps):
            log_ret += drift + vol * gauss(0.0, 1.0)
        ret = math.exp(log_ret) - 1.0  # terminal portfolio return
        total += ret
        total_sq += ret * ret
        if ret < worst:
            worst = ret
        bucket = int((ret - lo) / width) if width else 0
        hist[min(nbins - 1, max(0, bucket))] += 1
    return n_paths, total, total_sq, worst, hist


def _montecarlo(input: dict, should_yield: YieldFn) -> dict:
    """Portfolio risk via Monte-Carlo GBM simulation (NON-AI, multi-core, pure stdlib).

    Each fleet tile owns a slice of paths; cores split the slice. Returns a MERGEABLE
    histogram of terminal returns + moments + worst loss, so the host-side aggregator computes
    fleet-wide VaR / CVaR and renders the risk distribution. Compute scales with
    ``n_paths * horizon_days`` -- size ``n_paths`` to hit the demo runtime.
    """
    n_paths = int(input.get("n_paths", 200_000))
    steps = max(1, int(input.get("horizon_days", 252)))
    mu = float(input.get("mu", 0.07))
    sigma = float(input.get("sigma", 0.20))
    seed = int(input.get("seed", 0))
    lo = float(input.get("hist_lo", -1.0))
    hi = float(input.get("hist_hi", 2.0))
    nbins = int(input.get("hist_bins", 120))

    cores = _core_count()
    # Many small chunks (not one per core) so should_yield() is honored promptly and the pool
    # stays load-balanced; distinct seeds per chunk so paths never repeat.
    n_chunks = max(cores, min(2048, (n_paths + 1999) // 2000))
    base, rem = divmod(n_paths, n_chunks)
    tasks = [
        (seed * 1_000_003 + c, base + (1 if c < rem else 0), steps, mu, sigma, lo, hi, nbins)
        for c in range(n_chunks)
        if base + (1 if c < rem else 0) > 0
    ]
    parts, yielded = _parallel_map(_mc_chunk, tasks, should_yield, max_workers=cores)

    count = sum(p[0] for p in parts)
    total = sum(p[1] for p in parts)
    total_sq = sum(p[2] for p in parts)
    worst = min((p[3] for p in parts), default=0.0)
    hist = [0] * nbins
    for part in parts:
        for i, value in enumerate(part[4]):
            hist[i] += value
    mean = total / count if count else 0.0
    variance = (total_sq / count - mean * mean) if count else 0.0
    return {
        "paths": count,
        "mean_return": mean,
        "stdev": math.sqrt(max(0.0, variance)),
        "worst_return": worst,
        "hist": hist,
        "hist_lo": lo,
        "hist_hi": hi,
        "horizon_days": steps,
        "yielded": yielded,
    }


# --- NON-AI: distributed hash crack / proof-of-work (multi-core, pure stdlib) ----------------

def _hash_chunk(args: tuple) -> tuple:
    """One core's nonce range -> (found_nonce|None, found_hash|None, hashes_tried)."""
    prefix, target, start, end = args
    pre = prefix.encode()
    for nonce in range(start, end):
        digest = hashlib.sha256(pre + str(nonce).encode()).hexdigest()
        if digest.startswith(target):
            return nonce, digest, nonce - start + 1
    return None, None, end - start


def _hashcrack(input: dict, should_yield: YieldFn) -> dict:
    """Distributed proof-of-work search (NON-AI, multi-core, pure stdlib).

    Each fleet tile scans a nonce range for ``sha256(prefix + nonce)`` whose hex starts with
    ``target_prefix``; cores split the range. Mergeable: the aggregator takes the winner and
    sums hashes for a fleet hash-rate. Lengthen ``target_prefix`` / widen the range for runtime.
    """
    prefix = str(input.get("prefix", "onecompute"))
    target = str(input.get("target_prefix", "00000")).lower()
    start = int(input.get("nonce_start", 0))
    end = int(input.get("nonce_end", start + 2_000_000))

    cores = _core_count()
    span = max(0, end - start)
    # Many small nonce ranges so should_yield() lands promptly and cores stay balanced.
    n_chunks = max(cores, min(4096, span // 500_000 + 1))
    base, rem = divmod(span, n_chunks)
    tasks = []
    pos = start
    for c in range(n_chunks):
        size = base + (1 if c < rem else 0)
        if size <= 0:
            continue
        tasks.append((prefix, target, pos, pos + size))
        pos += size
    parts, yielded = _parallel_map(_hash_chunk, tasks, should_yield, max_workers=cores)

    hashes_tried = sum(p[2] for p in parts)
    winner = next((p for p in parts if p[0] is not None), None)
    return {
        "prefix": prefix,
        "target_prefix": target,
        "found": winner is not None,
        "nonce": winner[0] if winner else None,
        "hash": winner[1] if winner else None,
        "hashes_tried": hashes_tried,
        "nonce_start": start,
        "nonce_end": end,
        "yielded": yielded,
    }


# --- AI: model evaluation (LLM-as-judge) -- local model, host-side --------------------------

def _ai_eval_one(
    backend: str | None, question: str, answer: str, rubric: str, model: str, max_tokens: int
) -> tuple[int, str]:
    """Grade one answer 0-10 against a rubric -> (score, short verdict)."""
    if backend is None:
        # Disclosed deterministic heuristic so the workload runs with no model present.
        score = int(sha256_hex(question + "||" + answer)[:4], 16) % 11
        return score, "[fallback heuristic score — no model]"
    prompt = (
        "Grade the ANSWER to the QUESTION from 0-10 using the RUBRIC. Reply with ONLY a JSON "
        'object: {"score": <integer 0-10>, "verdict": "<one short sentence>"}.\n'
        f"RUBRIC: {rubric}\nQUESTION: {question}\nANSWER: {answer}"
    )
    import json

    text, _ = _ai_one(backend, prompt, model, max_tokens)
    score, verdict = 0, text[:200]
    try:
        start, end = text.find("{"), text.rfind("}")
        obj = json.loads(text[start : end + 1])
        score = int(round(float(obj.get("score", 0))))
        verdict = str(obj.get("verdict", ""))[:200]
    except Exception:
        pass
    return max(0, min(10, score)), verdict


def _ai_eval(input: dict, should_yield: YieldFn) -> dict:
    """Score a batch of (question, answer) items with an LLM judge (AI, local model).

    Each item may carry a ``label`` (which system produced the answer) so the aggregator can
    build a leaderboard. Chunked per item; a yield returns the partial scores.
    """
    items = input.get("items", [])
    rubric = input.get("rubric", "correctness, clarity, and completeness")
    model = input.get("model", "")
    max_tokens = int(input.get("max_tokens", 120))
    backend = _detect_ai_backend()
    results: list = []
    for item in items:
        if should_yield():
            return {"results": results, "backend": backend or "fallback", "yielded": True}
        score, verdict = _ai_eval_one(
            backend, str(item.get("question", "")), str(item.get("answer", "")),
            rubric, model, max_tokens,
        )
        row = {"question": item.get("question", ""), "score": score, "verdict": verdict}
        if "label" in item:
            row["label"] = item["label"]
        results.append(row)
    return {"results": results, "backend": backend or "fallback", "yielded": False}


# --- AI: knowledge-graph extraction -- local model, host-side -------------------------------

def _ai_graph_one(
    backend: str | None, doc: str, model: str, max_tokens: int
) -> tuple[list[str], list[dict]]:
    """Extract (entities, relations) from one document. relations are {source,relation,target}."""
    if backend is None:
        # Disclosed fallback: capitalized-token entities chained by an 'related_to' edge.
        import re

        entities: list[str] = []
        for word in re.findall(r"\b[A-Z][a-zA-Z]{2,}\b", doc):
            if word not in entities:
                entities.append(word)
        entities = entities[:8]
        relations = [
            {"source": entities[i], "relation": "related_to", "target": entities[i + 1]}
            for i in range(len(entities) - 1)
        ]
        return entities, relations
    prompt = (
        "Extract a knowledge graph from the TEXT. Reply with ONLY JSON: "
        '{"entities": ["..."], "relations": [{"source":"...","relation":"...","target":"..."}]}.\n'
        f"TEXT: {doc}"
    )
    import json

    text, _ = _ai_one(backend, prompt, model, max_tokens)
    try:
        start, end = text.find("{"), text.rfind("}")
        obj = json.loads(text[start : end + 1])
        entities = [str(e) for e in obj.get("entities", []) if e][:24]
        relations = [
            {
                "source": str(r.get("source", "")),
                "relation": str(r.get("relation", "")),
                "target": str(r.get("target", "")),
            }
            for r in obj.get("relations", [])
            if isinstance(r, dict) and r.get("source") and r.get("target")
        ][:48]
        return entities, relations
    except Exception:
        return [], []


def _ai_graph(input: dict, should_yield: YieldFn) -> dict:
    """Build a knowledge graph from a batch of documents (AI, local model).

    Each fleet tile processes a slice of docs; the aggregator merges nodes/edges into one graph
    and renders it. Chunked per doc; a yield returns the partial graph.
    """
    docs = input.get("docs", [])
    model = input.get("model", "")
    max_tokens = int(input.get("max_tokens", 220))
    backend = _detect_ai_backend()
    nodes: list[str] = []
    seen: set[str] = set()
    edges: list[dict] = []
    for doc in docs:
        if should_yield():
            return {"nodes": nodes, "edges": edges, "backend": backend or "fallback", "yielded": True}
        entities, relations = _ai_graph_one(backend, str(doc), model, max_tokens)
        for entity in entities:
            if entity and entity not in seen:
                seen.add(entity)
                nodes.append(entity)
        edges.extend(relations)
    return {"nodes": nodes, "edges": edges, "backend": backend or "fallback", "yielded": False}


EXECUTORS: dict[str, Callable[[dict, YieldFn], dict]] = {
    "data.transform": _data_transform,
    "render": _render,
    "challenge": _challenge,
    "ai.batch_infer": _ai_batch_infer,
    "ai.infer": _ai_batch_infer,  # AI: distributed local-model inference (alias, demo headline)
    "eval": _data_transform,   # eval reuses the deterministic transform path in the PoC
    "fractal": _fractal,       # NON-AI: distributed Mandelbrot tile (pure stdlib)
    "optimize": _optimize,     # NON-AI: distributed param-sweep slice (pure stdlib)
    "montecarlo": _montecarlo, # NON-AI: distributed portfolio-risk simulation (multi-core)
    "hashcrack": _hashcrack,   # NON-AI: distributed proof-of-work search (multi-core)
    "ai.synth": _ai_synth,     # AI: distributed synthetic-data slice (local model / host-side)
    "ai.eval": _ai_eval,       # AI: distributed model evaluation / LLM-as-judge (local model)
    "ai.graph": _ai_graph,     # AI: distributed knowledge-graph extraction (local model)
}


def execute(kind: str, input: dict, should_yield: YieldFn = lambda: False) -> dict:
    """Execute a job of `kind` over `input`. Raises ValueError on an unknown kind."""
    executor = EXECUTORS.get(kind)
    if executor is None:
        raise ValueError(f"no executor registered for job kind: {kind!r}")
    return executor(input, should_yield)
