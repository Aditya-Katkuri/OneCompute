from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response

from contracts import (
    Capability,
    FleetState,
    HeartbeatRequest,
    HeartbeatResponse,
    JobAssignment,
    JobManifest,
    JobView,
    RegisterResponse,
    ResultRequest,
    ResultResponse,
    SignedManifest,
    SubmitRequest,
    SubmitResponse,
    WorkerView,
    sha256_hex,
)
from orchestrator.db import init_db, write_lock
from orchestrator.scheduler import class_weight_for, pick_job_for
from orchestrator.submit import submit_job


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _lease_deadline() -> str:
    return (datetime.now(UTC) + timedelta(seconds=20)).isoformat()


def reap_expired(conn: sqlite3.Connection) -> None:
    with write_lock:
        conn.execute(
            """
            UPDATE jobs
            SET state = 'queued', assigned_worker = NULL, lease_expires = NULL, updated_at = ?
            WHERE state = 'leased' AND lease_expires < ?
            """,
            (_now(), _now()),
        )
        conn.commit()


def _worker_or_404(conn: sqlite3.Connection, worker_id: str) -> sqlite3.Row:
    worker = conn.execute("SELECT * FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
    if worker is None:
        raise HTTPException(status_code=404, detail="worker not found")
    return worker


def create_app(db_path: str = ":memory:", signer=None) -> FastAPI:
    conn = init_db(db_path)
    app = FastAPI(title="NightShift Orchestrator")
    app.state.conn = conn

    @app.post("/register", response_model=RegisterResponse)
    def register(cap: Capability) -> RegisterResponse:
        token = uuid4().hex
        now = _now()
        weight = class_weight_for(cap)
        with write_lock:
            conn.execute(
                """
                INSERT INTO workers (
                    worker_id, token, capability_json, class_weight, idle, registered_at,
                    last_heartbeat
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    token = excluded.token,
                    capability_json = excluded.capability_json,
                    class_weight = excluded.class_weight,
                    idle = 1,
                    registered_at = excluded.registered_at,
                    last_heartbeat = excluded.last_heartbeat
                """,
                (cap.worker_id, token, cap.model_dump_json(), weight, now, now),
            )
            conn.commit()
        return RegisterResponse(worker_token=token)

    @app.post("/jobs", response_model=SubmitResponse)
    def submit(req: SubmitRequest) -> SubmitResponse:
        try:
            return SubmitResponse(job_id=submit_job(conn, req))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/jobs/next", response_model=JobAssignment)
    def jobs_next(worker_id: str):
        worker = _worker_or_404(conn, worker_id)
        if worker["blacklisted"]:
            return Response(status_code=204)
        reap_expired(conn)
        cap = Capability(**json.loads(worker["capability_json"]))
        with write_lock:
            busy = conn.execute(
                "SELECT 1 FROM jobs WHERE assigned_worker = ? AND state = 'leased' LIMIT 1",
                (worker_id,),
            ).fetchone()
            if busy is not None:
                return Response(status_code=204)
            job = pick_job_for(conn, cap)
            if job is None:
                return Response(status_code=204)
            deadline = _lease_deadline()
            now = _now()
            conn.execute(
                """
                UPDATE jobs
                SET state = 'leased', assigned_worker = ?, lease_expires = ?,
                    attempts = attempts + 1, updated_at = ?
                WHERE job_id = ? AND state = 'queued'
                """,
                (worker_id, deadline, now, job["job_id"]),
            )
            conn.execute(
                "UPDATE workers SET idle = 0, last_heartbeat = ? WHERE worker_id = ?",
                (now, worker_id),
            )
            conn.commit()
        manifest = JobManifest(**json.loads(job["manifest_json"]))
        signed_manifest = signer.sign(manifest) if signer else SignedManifest(manifest=manifest)
        return JobAssignment(
            signed_manifest=signed_manifest,
            input=json.loads(job["input_json"] or "{}"),
        )

    @app.post("/heartbeat", response_model=HeartbeatResponse)
    def heartbeat(req: HeartbeatRequest) -> HeartbeatResponse:
        _worker_or_404(conn, req.worker_id)
        now = _now()
        with write_lock:
            conn.execute(
                """
                UPDATE workers
                SET idle = ?, cpu_pct = ?, gpu_pct = ?, on_ac = ?, last_heartbeat = ?
                WHERE worker_id = ?
                """,
                (
                    int(req.idle),
                    req.cpu_pct,
                    req.gpu_pct,
                    int(req.on_ac),
                    now,
                    req.worker_id,
                ),
            )
            if req.current_job_id:
                conn.execute(
                    """
                    UPDATE jobs
                    SET state = 'queued', assigned_worker = NULL, lease_expires = NULL, updated_at = ?
                    WHERE job_id = ? AND assigned_worker = ? AND state = 'leased'
                        AND lease_expires <= ?
                    """,
                    (now, req.current_job_id, req.worker_id, now),
                )
                conn.execute(
                    """
                    UPDATE jobs
                    SET lease_expires = ?, updated_at = ?
                    WHERE job_id = ? AND assigned_worker = ? AND state = 'leased'
                        AND lease_expires > ?
                    """,
                    (_lease_deadline(), now, req.current_job_id, req.worker_id, now),
                )
            conn.commit()
        return HeartbeatResponse(ack=True, preempt=False)

    @app.post("/results/{job_id}", response_model=ResultResponse)
    def results(job_id: str, req: ResultRequest) -> ResultResponse:
        if req.job_id != job_id:
            raise HTTPException(status_code=400, detail="job id mismatch")
        worker = _worker_or_404(conn, req.worker_id)
        if worker["blacklisted"]:
            return ResultResponse(accepted=False, reason="blacklisted")
        now = _now()
        with write_lock:
            job = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if job is None:
                raise HTTPException(status_code=404, detail="job not found")
            if job["state"] != "leased" or job["assigned_worker"] != req.worker_id:
                return ResultResponse(accepted=False, credited=0.0, reason="not_leased")
            if not job["lease_expires"] or job["lease_expires"] <= now:
                conn.execute(
                    """
                    UPDATE jobs
                    SET state = 'queued', assigned_worker = NULL, lease_expires = NULL, updated_at = ?
                    WHERE job_id = ? AND state = 'leased' AND assigned_worker = ?
                    """,
                    (now, job_id, req.worker_id),
                )
                conn.commit()
                return ResultResponse(accepted=False, credited=0.0, reason="lease_expired")
            if req.status == "completed":
                output = req.output or {}
                if req.proof_sha256 and req.proof_sha256 != sha256_hex(output):
                    conn.execute(
                        """
                        UPDATE jobs
                        SET state = 'queued', assigned_worker = NULL, lease_expires = NULL,
                            updated_at = ?
                        WHERE job_id = ? AND state = 'leased' AND assigned_worker = ?
                        """,
                        (now, job_id, req.worker_id),
                    )
                    conn.commit()
                    return ResultResponse(accepted=False, credited=0.0, reason="invalid_proof")
                manifest = json.loads(job["manifest_json"])
                if manifest["kind"] == "challenge":
                    job_input = json.loads(job["input_json"] or "{}")
                    expected = {"y": job_input["x"] * job_input["x"] + 1}
                    if output != expected:
                        conn.execute(
                            """
                            UPDATE jobs
                            SET state = 'queued', assigned_worker = NULL, lease_expires = NULL,
                                updated_at = ?
                            WHERE job_id = ? AND state = 'leased' AND assigned_worker = ?
                            """,
                            (now, job_id, req.worker_id),
                        )
                        conn.commit()
                        return ResultResponse(accepted=False, credited=0.0, reason="invalid_result")
                credits = float(job["units"]) * float(worker["class_weight"])
                result_json = json.dumps(output, separators=(",", ":"))
                conn.execute(
                    """
                    UPDATE jobs
                    SET state = 'completed', result_json = ?, lease_expires = NULL, updated_at = ?
                    WHERE job_id = ? AND state = 'leased' AND assigned_worker = ?
                    """,
                    (result_json, now, job_id, req.worker_id),
                )
                conn.execute(
                    """
                    INSERT INTO ledger (worker_id, job_id, credits, reason, created_at)
                    VALUES (?, ?, ?, 'completed', ?)
                    """,
                    (req.worker_id, job_id, credits, now),
                )
                conn.commit()
                return ResultResponse(accepted=True, credited=credits)
            if req.status in {"failed", "yielded"}:
                conn.execute(
                    """
                    UPDATE jobs
                    SET state = 'queued', assigned_worker = NULL, lease_expires = NULL, updated_at = ?
                    WHERE job_id = ? AND state = 'leased' AND assigned_worker = ?
                    """,
                    (now, job_id, req.worker_id),
                )
                conn.commit()
                return ResultResponse(accepted=False, credited=0.0, reason=req.status)
        return ResultResponse(accepted=False, reason="unknown status")

    @app.get("/state", response_model=FleetState)
    def state() -> FleetState:
        worker_rows = conn.execute("SELECT * FROM workers ORDER BY registered_at ASC").fetchall()
        workers: list[WorkerView] = []
        for worker in worker_rows:
            cap = Capability(**json.loads(worker["capability_json"]))
            busy_row = conn.execute(
                """
                SELECT 1 FROM jobs
                WHERE assigned_worker = ? AND state = 'leased'
                LIMIT 1
                """,
                (worker["worker_id"],),
            ).fetchone()
            credits = conn.execute(
                "SELECT COALESCE(SUM(credits), 0) AS credits FROM ledger WHERE worker_id = ?",
                (worker["worker_id"],),
            ).fetchone()["credits"]
            workers.append(
                WorkerView(
                    worker_id=worker["worker_id"],
                    idle=bool(worker["idle"]),
                    busy=busy_row is not None,
                    has_gpu=cap.has_gpu,
                    cpu_pct=worker["cpu_pct"] or 0.0,
                    gpu_pct=worker["gpu_pct"],
                    blacklisted=bool(worker["blacklisted"]),
                    credits=float(credits),
                )
            )
        job_rows = conn.execute("SELECT * FROM jobs ORDER BY created_at ASC").fetchall()
        jobs = [
            JobView(
                job_id=job["job_id"],
                kind=job["kind"],
                state=job["state"],
                assigned_worker=job["assigned_worker"],
            )
            for job in job_rows
        ]
        total_credits = conn.execute(
            "SELECT COALESCE(SUM(credits), 0) AS credits FROM ledger"
        ).fetchone()["credits"]
        return FleetState(workers=workers, jobs=jobs, total_credits=float(total_credits))

    return app






