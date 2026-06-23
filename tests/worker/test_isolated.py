from __future__ import annotations

from contracts import Capability, JobAssignment, JobManifest, SignedManifest
from worker.agent import WorkerAgent


def _assignment(items: list[int]) -> JobAssignment:
    return JobAssignment(
        signed_manifest=SignedManifest(
            manifest=JobManifest(job_id="job-iso", kind="data.transform")
        ),
        input={"items": items, "op": "square"},
    )


def test_isolated_agent_yields_when_requested() -> None:
    """WorkerAgent(isolated=True) routes through run_in_isolation and preempts on yield.

    With the daemon down this exercises the subprocess+JobObject fallback; with it up it
    exercises the Docker path. Either way a forced yield must report status=yielded.
    """
    agent = WorkerAgent("http://test", Capability(worker_id="worker-iso"), isolated=True)
    agent._yield.set()
    try:
        rr = agent.run_job(_assignment(list(range(100_000))))
    finally:
        agent.close()

    assert rr.status == "yielded"
    assert rr.output is not None
    assert rr.output["yielded"] is True
    assert rr.output["results"] == []


def test_isolated_agent_completes_real_job() -> None:
    """A real isolated run (no yield) produces the same result as in-process execution."""
    agent = WorkerAgent("http://test", Capability(worker_id="worker-iso"), isolated=True)
    try:
        rr = agent.run_job(_assignment([1, 2, 3]))
    finally:
        agent.close()

    assert rr.status == "completed"
    assert rr.output["results"] == [1, 4, 9]
