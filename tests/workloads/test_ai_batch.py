from contracts import SubmitRequest
from workloads.ai_batch import build_prompt_jobs


def test_build_prompt_jobs_are_submit_requests() -> None:
    jobs = build_prompt_jobs(["a", "b", "c", "d"], slice_size=2)

    assert len(jobs) == 2
    for job in jobs:
        SubmitRequest(**job)
        assert job["kind"] == "ai.batch_infer"
        assert job["units"] == 2

