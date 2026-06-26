"""GPU (host-side) demo workload for ReeveOS / OneCompute.

`render` jobs run a sized matmul that uses real CUDA (``cupy``) when a device is present and
an honest CPU fallback otherwise (see ``jobkit.execute._render``). They advertise
``needs_gpu`` so the scheduler routes them only to GPU-capable workers, and the worker runs
them **host-side under a Job Object** (never in a Linux container, which has no CUDA device).
"""

from __future__ import annotations


def generate_gpu_jobs(n_jobs: int = 2, size: int = 512, iters: int = 12) -> list[dict]:
    """Build deterministic `render` SubmitRequest-shaped GPU jobs.

    Each job sets ``requires.needs_gpu=True`` (so only GPU workers lease it and the worker
    runs it host-side) and ``units=iters`` (server-metered credit). The CPU fallback keeps
    the jobs runnable on a box with no GPU, honestly disclosing ``accelerator="cpu-fallback"``.
    """
    if n_jobs < 0:
        raise ValueError("n_jobs must be non-negative")
    if size <= 0 or iters <= 0:
        raise ValueError("size and iters must be positive")
    return [
        {
            "kind": "render",
            "input": {"size": size, "iters": iters, "seed": i},
            "requires": {"needs_gpu": True},
            "limits": {"timeout_s": 120},
            "units": iters,
        }
        for i in range(n_jobs)
    ]
