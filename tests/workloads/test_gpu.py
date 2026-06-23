"""The GPU workload generator emits host-side, GPU-routed `render` jobs."""
from __future__ import annotations

import pytest

from workloads.gpu import generate_gpu_jobs


def test_generate_gpu_jobs_shape():
    jobs = generate_gpu_jobs(n_jobs=3, size=128, iters=10)
    assert len(jobs) == 3
    for job in jobs:
        assert job["kind"] == "render"
        assert job["requires"]["needs_gpu"] is True
        assert job["units"] == 10
        assert job["input"]["size"] == 128
        assert job["input"]["iters"] == 10
    assert {job["input"]["seed"] for job in jobs} == {0, 1, 2}


def test_generate_gpu_jobs_validates():
    assert generate_gpu_jobs(0) == []
    with pytest.raises(ValueError):
        generate_gpu_jobs(1, size=0)
    with pytest.raises(ValueError):
        generate_gpu_jobs(1, iters=0)
