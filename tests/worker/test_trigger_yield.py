from __future__ import annotations

import httpx
from fastapi import FastAPI, Response

from contracts import Capability, JobAssignment, JobManifest, SignedManifest
from worker.agent import WorkerAgent


def test_trigger_yield_reports_yielded() -> None:
    app = FastAPI()

    @app.get("/jobs/next", response_model=None)
    def next_job(worker_id: str):
        if worker_id != "worker-1":
            return Response(status_code=404)
        return JobAssignment(
            signed_manifest=SignedManifest(
                manifest=JobManifest(job_id="job-yield", kind="data.transform")
            ),
            input={"items": list(range(10_000)), "op": "square"},
        ).model_dump()

    client = httpx.Client(transport=httpx.ASGITransport(app=app), base_url="http://test")
    agent = WorkerAgent("http://test", Capability(worker_id="worker-1"), client=client)

    try:
        assignment = agent.poll_once()
        assert assignment is not None
        agent.trigger_yield()
        rr = agent.run_job(assignment)
    finally:
        agent.close()

    assert rr.status == "yielded"
