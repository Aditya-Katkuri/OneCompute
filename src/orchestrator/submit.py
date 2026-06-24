from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from contracts import JobManifest, SubmitRequest, sha256_hex
from orchestrator.db import write_lock


def _now() -> str:
    return datetime.now(UTC).isoformat()


def submit_job(
    conn: sqlite3.Connection, req: SubmitRequest, workload_id: str | None = None
) -> str:
    if req.units <= 0:
        raise ValueError("units must be positive")
    job_id = uuid4().hex
    manifest = JobManifest(
        job_id=job_id,
        kind=req.kind,
        input_sha256=sha256_hex(req.input),
        requires=req.requires,
        limits=req.limits,
    )
    now = _now()
    with write_lock:
        conn.execute(
            """
            INSERT INTO jobs (
                job_id, kind, manifest_json, input_json, state, units, workload_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (
                job_id,
                req.kind,
                json.dumps(manifest.model_dump(mode="json"), separators=(",", ":")),
                json.dumps(req.input, separators=(",", ":")),
                req.units,
                workload_id,
                now,
                now,
            ),
        )
        conn.commit()
    return job_id


