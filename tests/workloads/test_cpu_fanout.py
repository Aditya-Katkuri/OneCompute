from contracts import SubmitRequest
from workloads.cpu_fanout import generate_jobs, ghost_bar_seconds


def test_generate_jobs_are_submit_requests() -> None:
    jobs = generate_jobs(3, 10)

    assert len(jobs) == 3
    for job in jobs:
        SubmitRequest(**job)
        assert job["kind"] == "data.transform"
        assert job["units"] == 10
        assert len(job["input"]["items"]) == 10


def test_ghost_bar_seconds_positive() -> None:
    assert ghost_bar_seconds(1000) > 0

