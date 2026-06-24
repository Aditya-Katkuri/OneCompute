from datetime import UTC, datetime, timedelta

from contracts import Capability, JobAssignment, JobManifest, SignedManifest
from worker.agent import WorkerAgent


def test_worker_refuses_expired_manifest_before_running() -> None:
    agent = WorkerAgent(
        "http://test",
        Capability(worker_id="worker"),
        runner=lambda *_args, **_kwargs: {"should_not": "run"},
    )
    assignment = JobAssignment(
        signed_manifest=SignedManifest(
            manifest=JobManifest(
                job_id="expired",
                kind="data.transform",
                expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        ),
        input={"items": [1], "op": "square"},
    )

    result = agent.run_job(assignment)

    assert result.status == "failed"
    assert result.output == {"error": "verification failed: manifest_expired"}
