"""The fleet job builders partition the WHOLE workload exactly (sum(units)==total, no
gaps) and produce valid SubmitRequests; the fractal assembler stitches a full image."""
from __future__ import annotations

import pytest

from contracts import SubmitRequest
from jobkit.execute import execute
from workloads.fractal import assemble_tiles, build_fractal_jobs
from workloads.optimize import build_optimize_jobs
from workloads.synth import build_synth_jobs


def _assert_submit_requests(jobs, kind):
    for job in jobs:
        SubmitRequest(**job)  # validates against the frozen contract
        assert job["kind"] == kind


def test_build_fractal_jobs_partitions_rows():
    width, height = 120, 90
    jobs = build_fractal_jobs(n_tiles=3, width=width, height=height, max_iter=60)
    _assert_submit_requests(jobs, "fractal")
    assert sum(j["units"] for j in jobs) == height
    # Bands are contiguous and cover [0, height) with no gaps/overlap.
    bands = sorted((j["input"]["row_start"], j["input"]["row_end"]) for j in jobs)
    cursor = 0
    for start, end in bands:
        assert start == cursor
        cursor = end
    assert cursor == height
    assert all(j["input"]["width"] == width for j in jobs)


def test_build_optimize_jobs_partitions_candidates():
    n_candidates = 900
    jobs = build_optimize_jobs(n_tiles=3, n_candidates=n_candidates, dims=6, seed=0)
    _assert_submit_requests(jobs, "optimize")
    assert sum(j["units"] for j in jobs) == n_candidates
    slices = sorted((j["input"]["idx_start"], j["input"]["idx_end"]) for j in jobs)
    cursor = 0
    for start, end in slices:
        assert start == cursor
        cursor = end
    assert cursor == n_candidates


def test_build_synth_jobs_partitions_rows_with_distinct_start_indices():
    total_rows = 30
    jobs = build_synth_jobs(n_tiles=3, total_rows=total_rows)
    _assert_submit_requests(jobs, "ai.synth")
    assert sum(j["units"] for j in jobs) == total_rows
    # Each tile's n_rows matches its units, and start_index values are distinct + ordered.
    starts = [j["input"]["start_index"] for j in jobs]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)
    for job in jobs:
        assert job["input"]["n_rows"] == job["units"]


def test_build_validators():
    with pytest.raises(ValueError):
        build_fractal_jobs(0)
    with pytest.raises(ValueError):
        build_optimize_jobs(0)
    with pytest.raises(ValueError):
        build_synth_jobs(0)


def test_fractal_assemble_produces_full_image_when_pil_present():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pytest.skip("PIL not installed; host-side assembler not exercised")

    width, height, max_iter = 60, 40, 40
    jobs = build_fractal_jobs(n_tiles=3, width=width, height=height, max_iter=max_iter)
    results = [execute("fractal", job["input"]) for job in jobs]
    image = assemble_tiles(results, width, height, max_iter)
    assert image.size == (width, height)  # PIL .size is (width, height)
    assert image.mode == "RGB"
