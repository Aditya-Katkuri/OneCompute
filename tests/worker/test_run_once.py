from __future__ import annotations

import httpx
from fastapi import FastAPI, Response

from contracts import (
    Capability,
    JobAssignment,
    JobManifest,
    RegisterResponse,
    ResultRequest,
    ResultResponse,
    SignedManifest,
)
from worker.agent import WorkerAgent


def test_run_once_completes_square_transform() -> None:
    app = FastAPI()
    calls = {"next": 0, "credited": 0}

    @app.post("/register")
    def register(_: Capability) -> dict:
        return RegisterResponse(worker_token="t").model_dump()

    @app.get("/jobs/next", response_model=None)
    def next_job(worker_id: str):
        calls["next"] += 1
        if calls["next"] > 1:
            return Response(status_code=204)
        assignment = JobAssignment(
            signed_manifest=SignedManifest(
                manifest=JobManifest(job_id="job-1", kind="data.transform")
            ),
            input={"items": [1, 2, 3], "op": "square"},
        )
        assert worker_id == "worker-1"
        return assignment.model_dump()

    @app.post("/results/{job_id}")
    def results(job_id: str, result: ResultRequest) -> dict:
        assert job_id == "job-1"
        assert result.output == {"results": [1, 4, 9], "yielded": False}
        calls["credited"] = result.units
        return ResultResponse(accepted=True, credited=3).model_dump()

    client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://test")
    agent = WorkerAgent("http://test", Capability(worker_id="worker-1"), client=client)

    try:
        rr = agent.run_once()
    finally:
        agent.close()

    assert rr is not None
    assert rr.status == "completed"
    assert rr.output["results"] == [1, 4, 9]
    assert calls["credited"] == 3
