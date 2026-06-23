from __future__ import annotations

from contracts import Capability, JobAssignment, JobManifest, SignedManifest
from worker.agent import WorkerAgent


def test_run_job_yields_when_requested() -> None:
    agent = WorkerAgent("http://test", Capability(worker_id="worker-1"))
    agent._yield.set()
    assignment = JobAssignment(
        signed_manifest=SignedManifest(manifest=JobManifest(job_id="job-1", kind="data.transform")),
        input={"items": list(range(20)), "op": "square"},
    )

    try:
        rr = agent.run_job(assignment)
    finally:
        agent.close()

    assert rr.status == "yielded"
    assert rr.output is not None
    assert rr.output["yielded"] is True
    assert len(rr.output["results"]) < 20
