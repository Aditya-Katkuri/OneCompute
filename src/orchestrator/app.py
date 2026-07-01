from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import HTMLResponse

from contracts import (
    LAUNCHABLE_KINDS,
    Capability,
    FleetState,
    HeartbeatRequest,
    HeartbeatResponse,
    JobAssignment,
    JobDetail,
    JobManifest,
    JobView,
    MeasurementSummary,
    MetricSummary,
    ProfileIngestResponse,
    ProfileReport,
    RegisterResponse,
    ResultRequest,
    ResultResponse,
    SignedManifest,
    SubmitRequest,
    SubmitResponse,
    UsageBucket,
    WorkerView,
    WorkloadLaunchRequest,
    WorkloadLaunchResponse,
    WorkloadView,
    sha256_hex,
)
from dashboard.paths import INDEX_HTML
from measurement.headroom import (
    BUCKETS_PER_WEEK,
    DEFAULT_HARVEST_HIGH,
    DEFAULT_HARVEST_LOW,
    DEFAULT_MARGIN_PCT,
    aggregate,
    normalize_buckets,
    summarize_profile,
)
from orchestrator.db import open_serialized_db, write_lock
from orchestrator.scheduler import class_weight_for, pick_job_for
from orchestrator.submit import submit_job
from trust import Signer, check_challenge

# Cap on a single job's result payload. Must comfortably fit a Mandelbrot image tile: the
# fractal result carries the pixel rows the dashboard reassembles into one image, so a default
# 720x480 single-worker tile is ~0.8 MB and higher-res split tiles run to a few MB. 8 MiB keeps
# the flagship fractal workload working with any fleet size while still bounding abuse.
MAX_RESULT_OUTPUT_BYTES = 8 * 1024 * 1024

# Least-utilized-first routing. When a worker polls for fresh work, briefly let a clearly
# less-loaded, live, free peer claim it instead, so load flows to the most idle machines first.
# Bounded by the job's age so a worker is never starved if the lighter peer doesn't poll.
LOAD_PRIORITY_GRACE_S = 1.5   # only defer while a job is this fresh (seconds)
LOAD_PRIORITY_MARGIN = 15.0   # a peer must be at least this many load-points (%) lighter to win priority
LIVE_PEER_WINDOW_S = 3.0      # a peer only counts as a live competitor if it heartbeat within this window
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # The demo dashboard uses inline CSS/JS and data: fonts/images.
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "font-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'self'"
    ),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


# Crockford-ish alphabet: no 0/1/O/I so a code read aloud / typed from a screen is unambiguous.
_DEVICE_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _make_device_code() -> str:
    """A short human code like 'WX7Q-12' derived from uuid4 entropy (6 chars, dash after 4)."""
    raw = uuid4().hex
    chars = [_DEVICE_CODE_ALPHABET[int(raw[i : i + 2], 16) % len(_DEVICE_CODE_ALPHABET)]
             for i in range(0, 12, 2)]
    return f"{''.join(chars[:4])}-{''.join(chars[4:6])}"


def _job_detail(row: sqlite3.Row) -> JobDetail:
    """Build a dashboard JobDetail (incl. parsed output) from a jobs-table row."""
    output = json.loads(row["result_json"]) if row["result_json"] else None
    return JobDetail(
        job_id=row["job_id"],
        kind=row["kind"],
        state=row["state"],
        assigned_worker=row["assigned_worker"],
        units=row["units"],
        workload_id=row["workload_id"],
        output=output,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _build_workload_jobs(kind: str, n_tiles: int, params: dict) -> list[dict]:
    """Build the split SubmitRequest dicts for a launchable workload kind.

    Lazy imports keep the orchestrator import-light and avoid pulling the workload builders
    (and their optional PIL/numpy deps) unless POST /workloads is actually used. The hardcoded
    fleet split lives in the builders (one tile per machine via workloads.partition)."""
    if kind == "fractal":
        from workloads.fractal import build_fractal_jobs
        return build_fractal_jobs(n_tiles=n_tiles, **params)
    if kind == "optimize":
        from workloads.optimize import build_optimize_jobs
        return build_optimize_jobs(n_tiles=n_tiles, **params)
    if kind == "ai.synth":
        from workloads.synth import build_synth_jobs
        return build_synth_jobs(n_tiles=n_tiles, **params)
    if kind == "ai.batch_infer":
        from workloads.ai_batch import build_prompt_jobs
        return build_prompt_jobs(**params)  # slices by slice_size, not n_tiles
    if kind == "data.transform":
        from workloads.cpu_fanout import generate_jobs
        return generate_jobs(n_jobs=n_tiles, **params)
    if kind == "montecarlo":
        from workloads.montecarlo import build_montecarlo_jobs
        return build_montecarlo_jobs(n_tiles=n_tiles, **params)
    if kind == "hashcrack":
        from workloads.hashcrack import build_hashcrack_jobs
        return build_hashcrack_jobs(n_tiles=n_tiles, **params)
    if kind == "ai.infer":
        from workloads.infer import build_infer_jobs
        return build_infer_jobs(n_tiles=n_tiles, **params)
    if kind == "ai.eval":
        from workloads.eval import build_eval_jobs
        return build_eval_jobs(n_tiles=n_tiles, **params)
    if kind == "ai.graph":
        from workloads.graph import build_graph_jobs
        return build_graph_jobs(n_tiles=n_tiles, **params)
    raise ValueError(f"unknown or non-launchable workload kind: {kind!r}")


# The example workloads a dashboard offers as launch buttons. A UI renders these without
# hardcoding kinds/params; add an entry here (and the kind must be in LAUNCHABLE_KINDS) to
# expose a new example. `split`: "per_machine" => pass n_tiles = number of approved workers;
# "slice_size" => the builder splits internally, n_tiles is ignored.
WORKLOAD_CATALOG: list[dict] = [
    {
        "kind": "fractal", "label": "Fractal render", "category": "non-AI", "ai": False,
        "blurb": "Mandelbrot image rendered in bands across the fleet, reassembled into one picture.",
        "default_params": {"width": 720, "height": 480, "max_iter": 120}, "split": "per_machine",
    },
    {
        "kind": "optimize", "label": "Param-sweep optimize", "category": "non-AI", "ai": False,
        "blurb": "Distributed search over thousands of candidate configs; the global best wins.",
        "default_params": {"n_candidates": 30000, "dims": 8}, "split": "per_machine",
    },
    {
        "kind": "ai.batch_infer", "label": "AI inference", "category": "AI", "ai": True,
        "blurb": "Model inference over a prompt set, each machine scoring a slice.",
        "default_params": {"slice_size": 3}, "split": "slice_size",
    },
    {
        "kind": "ai.synth", "label": "AI synthetic data", "category": "AI", "ai": True,
        "blurb": "Each machine generates synthetic records via an LLM; merged into one dataset.",
        "default_params": {"total_rows": 30}, "split": "per_machine",
    },
    {
        "kind": "montecarlo", "label": "Monte-Carlo finance risk", "category": "non-AI", "ai": False,
        "blurb": "Millions of market paths price a portfolio and its Value-at-Risk; every core pinned.",
        "default_params": {"total_paths": 3000000, "horizon_days": 252}, "split": "per_machine",
    },
    {
        "kind": "hashcrack", "label": "Hash crack (proof-of-work)", "category": "non-AI", "ai": False,
        "blurb": "The fleet brute-forces a SHA-256 with a target prefix; live hash-rate, then the winning nonce.",
        "default_params": {"keyspace": 300000000, "target_prefix": "000000"}, "split": "per_machine",
    },
    {
        "kind": "ai.infer", "label": "Local LLM inference", "category": "AI", "ai": True,
        "blurb": "A big batch of prompts runs through the on-device model on every machine, no cloud.",
        "default_params": {"n_prompts": 120}, "split": "per_machine",
    },
    {
        "kind": "ai.eval", "label": "Model evaluation (LLM judge)", "category": "AI", "ai": True,
        "blurb": "The fleet grades answers with a local-model judge into a leaderboard + score chart.",
        "default_params": {}, "split": "per_machine",
    },
    {
        "kind": "ai.graph", "label": "Knowledge graph", "category": "AI", "ai": True,
        "blurb": "The fleet extracts entities + relations from a corpus into one rendered graph.",
        "default_params": {}, "split": "per_machine",
    },
]


def _aggregate_workload(kind: str, outputs: list[dict]) -> dict | None:
    """Render-ready merged summary for a workload, computed server-side from its completed tiles'
    outputs, so the dashboard draws it instead of re-implementing each merge in JS. Returns None
    when nothing has completed (or for fractal, which the dashboard reassembles tile-by-tile).
    Never raises; lazy imports keep the workload deps out of the orchestrator's import path."""
    if not outputs:
        return None
    try:
        if kind == "montecarlo":
            from workloads.montecarlo import aggregate_montecarlo
            return aggregate_montecarlo(outputs)
        if kind == "hashcrack":
            from workloads.hashcrack import aggregate_hashcrack
            return aggregate_hashcrack(outputs)
        if kind == "optimize":
            from workloads.optimize import aggregate_optimize
            return aggregate_optimize(outputs)
        if kind == "ai.eval":
            from workloads.eval import aggregate_eval
            return aggregate_eval(outputs)
        if kind == "ai.graph":
            from workloads.graph import aggregate_graph
            return aggregate_graph(outputs)
        if kind in ("ai.infer", "ai.batch_infer"):
            results = [r for out in outputs for r in (out.get("results") or [])]
            return {"count": len(results), "backend": outputs[0].get("backend"), "results": results[:200]}
        if kind == "ai.synth":
            from workloads.synth import merge_synth
            rows = merge_synth(outputs)
            return {"count": len(rows), "backend": outputs[0].get("backend"), "rows": rows[:500]}
    except Exception:
        return None
    return None


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


def _load_score(cpu_pct, gpu_pct) -> float:
    """A worker's current utilization on a 0..100 scale: the busier of its CPU and GPU."""
    return max(float(cpu_pct or 0.0), float(gpu_pct or 0.0))


def _age_seconds(iso_ts: str | None, now: datetime) -> float:
    if not iso_ts:
        return 1e9
    try:
        return (now - datetime.fromisoformat(iso_ts)).total_seconds()
    except ValueError:
        return 1e9


def _defer_to_less_loaded(conn: sqlite3.Connection, worker_id: str, job_created_at: str) -> bool:
    """Least-utilized-first: True if this polling worker should let a clearly lighter, live, free
    peer take this fresh job first. Returns False once the job ages past the grace window, so a
    worker is never starved if the lighter peer never polls."""
    now = datetime.now(UTC)
    if _age_seconds(job_created_at, now) >= LOAD_PRIORITY_GRACE_S:
        return False
    me = conn.execute(
        "SELECT cpu_pct, gpu_pct FROM workers WHERE worker_id = ?", (worker_id,)
    ).fetchone()
    if me is None:
        return False
    my_load = _load_score(me["cpu_pct"], me["gpu_pct"])
    live_cutoff = (now - timedelta(seconds=LIVE_PEER_WINDOW_S)).isoformat()
    peers = conn.execute(
        """
        SELECT w.cpu_pct, w.gpu_pct FROM workers w
        WHERE w.worker_id != ? AND w.approved = 1 AND w.blacklisted = 0
          AND w.last_heartbeat IS NOT NULL AND w.last_heartbeat >= ?
          AND NOT EXISTS (
              SELECT 1 FROM jobs j WHERE j.assigned_worker = w.worker_id AND j.state = 'leased'
          )
        """,
        (worker_id, live_cutoff),
    ).fetchall()
    return any(
        my_load - _load_score(p["cpu_pct"], p["gpu_pct"]) >= LOAD_PRIORITY_MARGIN for p in peers
    )


def _worker_or_404(conn: sqlite3.Connection, worker_id: str) -> sqlite3.Row:
    worker = conn.execute("SELECT * FROM workers WHERE worker_id = ?", (worker_id,)).fetchone()
    if worker is None:
        raise HTTPException(status_code=404, detail="worker not found")
    return worker


def _require_worker_token(
    conn: sqlite3.Connection,
    worker_id: str,
    authorization: str | None,
) -> sqlite3.Row:
    worker = conn.execute(
        "SELECT * FROM workers WHERE worker_id = ?",
        (worker_id,),
    ).fetchone()
    detail = "invalid worker token"
    if authorization is None:
        detail = "missing bearer token"
    elif not authorization.startswith("Bearer "):
        detail = "invalid authorization scheme"
    else:
        token = authorization.removeprefix("Bearer ").strip()
        stored_token = str(worker["token"]) if worker is not None else "0" * 32
        token_matches = bool(token) and compare_digest(token, stored_token)
        if worker is not None and token_matches:
            return worker
    # A request for a worker we don't have (e.g. one an admin disconnected, or one that never
    # registered) isn't a credential attack: fail it quietly so a removed-but-still-running agent
    # can't flood the activity feed. Only a token mismatch on a KNOWN worker is worth surfacing.
    if worker is not None:
        _emit(conn, "auth_failed", worker_id=worker_id, detail=detail)
    raise HTTPException(status_code=401, detail=detail)


def _emit(
    conn: sqlite3.Connection,
    event_type: str,
    worker_id: str | None = None,
    job_id: str | None = None,
    detail: str | None = None,
) -> None:
    """Append a row to the activity feed the dashboard streams from GET /events."""
    with write_lock:
        conn.execute(
            "INSERT INTO events (ts, type, worker_id, job_id, detail) VALUES (?, ?, ?, ?, ?)",
            (_now(), event_type, worker_id, job_id, detail),
        )
        conn.commit()


def _clamp_pct(value: object) -> float:
    """Clamp an incoming percentage to a finite 0..100. The wire is never trusted."""
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN
        return 0.0
    return max(0.0, min(100.0, v))


def _sanitize_profile_buckets(buckets: list[UsageBucket]) -> list[dict]:
    """Keep only populated (n>0), in-range, de-duplicated hour-of-week buckets, each clamped to a
    finite 0..100 percentage. Defends the store and the rollup against a malformed or hostile
    profile report: the scan is bounded, out-of-range/duplicate indices are dropped, at most 168
    buckets are kept, and every value is coerced. Returns plain dicts ready for JSON storage and for
    ``measurement.headroom.normalize_buckets``."""
    clean: list[dict] = []
    seen: set[int] = set()
    for b in buckets[: 3 * BUCKETS_PER_WEEK]:  # bounded even if the client over-sends
        if b.n <= 0:
            continue
        idx = int(b.index)
        if idx < 0 or idx >= BUCKETS_PER_WEEK or idx in seen:
            continue
        seen.add(idx)
        clean.append(
            {
                "index": idx,
                "n": int(b.n),
                "cpu_mean": _clamp_pct(b.cpu_mean),
                "cpu_max": _clamp_pct(b.cpu_max),
                "gpu_mean": _clamp_pct(b.gpu_mean),
                "gpu_max": _clamp_pct(b.gpu_max),
                "ram_mean": _clamp_pct(b.ram_mean),
                "ram_max": _clamp_pct(b.ram_max),
            }
        )
        if len(clean) >= BUCKETS_PER_WEEK:
            break
    return clean


def create_app(db_path: str = ":memory:", signer=None, require_approval: bool = False) -> FastAPI:
    conn = open_serialized_db(db_path)
    if signer is None:
        signer = Signer()  # signing is ON by default; the worker verifies before running
    app = FastAPI(title="OneCompute Orchestrator")
    app.state.conn = conn

    @app.middleware("http")
    async def add_security_headers(request, call_next):
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    @app.post("/register", response_model=RegisterResponse)
    def register(cap: Capability) -> RegisterResponse:
        token = uuid4().hex
        now = _now()
        weight = class_weight_for(cap)
        approved = 0 if require_approval else 1
        # Only pending workers carry a device code; an auto-approved (non-gated) worker has none.
        device_code = None if approved else _make_device_code()
        with write_lock:
            conn.execute(
                """
                INSERT INTO workers (
                    worker_id, token, capability_json, class_weight, free_ram_gb, idle,
                    approved, device_code, registered_at, last_heartbeat
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    token = excluded.token,
                    capability_json = excluded.capability_json,
                    class_weight = excluded.class_weight,
                    free_ram_gb = excluded.free_ram_gb,
                    idle = 1,
                    -- never demote an already-approved worker on re-register; only a
                    -- pending worker keeps its (refreshed) device code while gated.
                    approved = MAX(workers.approved, excluded.approved),
                    device_code = CASE WHEN workers.approved = 1 THEN NULL
                                       ELSE excluded.device_code END,
                    registered_at = excluded.registered_at,
                    last_heartbeat = excluded.last_heartbeat
                """,
                (
                    cap.worker_id, token, cap.model_dump_json(), weight,
                    cap.free_ram_gb if cap.free_ram_gb is not None else cap.ram_gb,
                    approved, device_code,
                    now, now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT approved, device_code FROM workers WHERE worker_id = ?",
                (cap.worker_id,),
            ).fetchone()
        is_approved = bool(row["approved"])
        _emit(conn, "registered", worker_id=cap.worker_id,
              detail="gpu" if cap.has_gpu else "cpu")
        return RegisterResponse(
            worker_token=token,
            device_code=None if is_approved else row["device_code"],
            approved=is_approved,
        )

    @app.post("/jobs", response_model=SubmitResponse)
    def submit(req: SubmitRequest) -> SubmitResponse:
        # Production gates submit/read endpoints behind SSO; the PoC leaves them open.
        try:
            job_id = submit_job(conn, req)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _emit(conn, "submitted", job_id=job_id, detail=req.kind)
        return SubmitResponse(job_id=job_id)

    @app.get("/jobs/next", response_model=JobAssignment)
    def jobs_next(worker_id: str, authorization: str | None = Header(default=None)):
        worker = _require_worker_token(conn, worker_id, authorization)
        if worker["blacklisted"]:
            return Response(status_code=204)
        if not worker["approved"]:
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
            job = pick_job_for(conn, cap, free_ram_gb=worker["free_ram_gb"])
            if job is None:
                return Response(status_code=204)
            # Least-utilized-first: hold this fresh job back if a clearly lighter, live, free peer
            # is around to take it. Bounded by the job's age, so loaded machines still get work.
            if _defer_to_less_loaded(conn, worker_id, job["created_at"]):
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
        _emit(conn, "assigned", worker_id=worker_id, job_id=job["job_id"], detail=manifest.kind)
        return JobAssignment(
            signed_manifest=signed_manifest,
            input=json.loads(job["input_json"] or "{}"),
        )

    @app.post("/heartbeat", response_model=HeartbeatResponse)
    def heartbeat(
        req: HeartbeatRequest,
        authorization: str | None = Header(default=None),
    ) -> HeartbeatResponse:
        _require_worker_token(conn, req.worker_id, authorization)
        worker = _worker_or_404(conn, req.worker_id)
        approved = bool(worker["approved"])
        now = _now()
        with write_lock:
            conn.execute(
                """
                UPDATE workers
                SET idle = ?, cpu_pct = ?, gpu_pct = ?, on_ac = ?,
                    free_ram_gb = COALESCE(?, free_ram_gb), last_heartbeat = ?
                WHERE worker_id = ?
                """,
                (
                    int(req.idle),
                    req.cpu_pct,
                    req.gpu_pct,
                    int(req.on_ac),
                    req.free_ram_gb,
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
        return HeartbeatResponse(ack=True, preempt=False, approved=approved)

    @app.post("/workers/{worker_id}/approve")
    def approve(worker_id: str) -> dict:
        _worker_or_404(conn, worker_id)
        with write_lock:
            conn.execute(
                "UPDATE workers SET approved = 1, device_code = NULL WHERE worker_id = ?",
                (worker_id,),
            )
            conn.commit()
        _emit(conn, "approved", worker_id=worker_id, detail="admitted via dashboard")
        return {"ok": True, "worker_id": worker_id}

    @app.delete("/workers/{worker_id}")
    def disconnect_worker(worker_id: str) -> dict:
        """Disconnect a device from the fleet (admin action from the dashboard, same gate as
        approve). Any job it currently holds is re-queued immediately so work isn't stuck until
        the lease expires, then the worker is dropped. A still-running agent simply fails auth on
        its next call; restarting it rejoins as a fresh pending device."""
        _worker_or_404(conn, worker_id)
        now = _now()
        with write_lock:
            conn.execute(
                """
                UPDATE jobs
                SET state = 'queued', assigned_worker = NULL, lease_expires = NULL, updated_at = ?
                WHERE assigned_worker = ? AND state = 'leased'
                """,
                (now, worker_id),
            )
            conn.execute("DELETE FROM workers WHERE worker_id = ?", (worker_id,))
            conn.commit()
        _emit(conn, "removed", worker_id=worker_id, detail="disconnected via dashboard")
        return {"ok": True, "worker_id": worker_id}

    @app.post("/results/{job_id}", response_model=ResultResponse)
    def results(
        job_id: str,
        req: ResultRequest,
        authorization: str | None = Header(default=None),
    ) -> ResultResponse:
        if req.job_id != job_id:
            raise HTTPException(status_code=400, detail="job id mismatch")
        worker = _require_worker_token(conn, req.worker_id, authorization)
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
            output = req.output or {}
            output_json = json.dumps(output, separators=(",", ":"))
            if len(output_json.encode("utf-8")) > MAX_RESULT_OUTPUT_BYTES:
                return ResultResponse(accepted=False, credited=0.0, reason="payload_too_large")
            if req.status == "completed":
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
                    if not check_challenge(output, expected):
                        conn.execute(
                            "UPDATE workers SET blacklisted = 1 WHERE worker_id = ?",
                            (req.worker_id,),
                        )
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
                        _emit(conn, "blacklisted", worker_id=req.worker_id, job_id=job_id,
                              detail="failed integrity challenge")
                        return ResultResponse(
                            accepted=False, credited=0.0, reason="cheater_blacklisted"
                        )
                credits = float(job["units"]) * float(worker["class_weight"])
                conn.execute(
                    """
                    UPDATE jobs
                    SET state = 'completed', result_json = ?, lease_expires = NULL, updated_at = ?
                    WHERE job_id = ? AND state = 'leased' AND assigned_worker = ?
                    """,
                    (output_json, now, job_id, req.worker_id),
                )
                conn.execute(
                    """
                    INSERT INTO ledger (worker_id, job_id, credits, reason, created_at)
                    VALUES (?, ?, ?, 'completed', ?)
                    """,
                    (req.worker_id, job_id, credits, now),
                )
                conn.commit()
                _emit(conn, "completed", worker_id=req.worker_id, job_id=job_id,
                      detail=f"+{credits:g} credits")
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
                _emit(conn, req.status, worker_id=req.worker_id, job_id=job_id)
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
                    free_ram_gb=worker["free_ram_gb"],
                    blacklisted=bool(worker["blacklisted"]),
                    credits=float(credits),
                    approved=bool(worker["approved"]),
                    device_code=worker["device_code"],
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

    @app.post("/profile", response_model=ProfileIngestResponse)
    def ingest_profile(
        req: ProfileReport,
        authorization: str | None = Header(default=None),
    ) -> ProfileIngestResponse:
        """Opt-in measurement pilot: a worker uploads its on-device usage envelope (derived
        hour-of-week stats only, never raw activity) so the control plane can roll up fleet-wide
        MEASURED idle headroom without job execution. Same bearer-token auth as /heartbeat; the
        report is sanitized and clamped before storage (the wire is never trusted). One row per
        worker, replaced on each report."""
        _require_worker_token(conn, req.worker_id, authorization)
        clean = _sanitize_profile_buckets(req.buckets)
        now = _now()
        with write_lock:
            conn.execute(
                """
                INSERT INTO worker_profiles (worker_id, buckets_json, coverage, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    buckets_json = excluded.buckets_json,
                    coverage = excluded.coverage,
                    updated_at = excluded.updated_at
                """,
                (req.worker_id, json.dumps(clean, separators=(",", ":")), len(clean), now),
            )
            conn.commit()
        return ProfileIngestResponse(accepted=True, buckets_stored=len(clean))

    @app.get("/measurement", response_model=MeasurementSummary)
    def measurement() -> MeasurementSummary:
        """Fleet-wide MEASURED idle headroom, rolled up from every worker's uploaded profile with
        the same governor-consistent math (``measurement.headroom``) the offline CLI report uses.
        Read by the dashboard's measured-headroom beat and the pilot hand-off to Azure Compute +
        CISO. Empty fleet -> zeros. Everything is an ESTIMATE and the assumptions travel with it."""
        rows = conn.execute("SELECT worker_id, buckets_json FROM worker_profiles").fetchall()
        summaries: list[dict] = []
        for row in rows:
            try:
                raw = json.loads(row["buckets_json"])
            except (ValueError, TypeError):
                continue  # a corrupt row never breaks the fleet rollup
            summaries.append(
                summarize_profile(
                    {"device": row["worker_id"], "populated": normalize_buckets(raw)}
                )
            )
        agg = aggregate(summaries)
        return MeasurementSummary(
            device_count=agg["device_count"],
            total_coverage_buckets=agg["total_coverage_buckets"],
            margin_pct=DEFAULT_MARGIN_PCT,
            harvest_low=DEFAULT_HARVEST_LOW,
            harvest_high=DEFAULT_HARVEST_HIGH,
            cpu=MetricSummary(
                avg=agg["cpu"]["avg"],
                peak=agg["cpu"]["peak"],
                recoverable_low=agg["cpu"]["recoverable_low"],
                recoverable_high=agg["cpu"]["recoverable_high"],
            ),
            gpu=MetricSummary(
                avg=agg["gpu"]["avg"],
                peak=agg["gpu"]["peak"],
                recoverable_low=agg["gpu"]["recoverable_low"],
                recoverable_high=agg["gpu"]["recoverable_high"],
            ),
            ram_avg=agg["ram"]["avg"],
            ram_headroom=agg["ram"]["headroom"],
        )

    @app.get("/jobs/{job_id}", response_model=JobDetail)
    def job_detail(job_id: str) -> JobDetail:
        """Full job record incl. its output: the dashboard reads this to show a job's result.
        Defined after /jobs/next so the literal route still wins for the long-poll."""
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_detail(row)

    @app.post("/workloads", response_model=WorkloadLaunchResponse)
    def launch_workload(req: WorkloadLaunchRequest) -> WorkloadLaunchResponse:
        """Launch a whole workload across the fleet in one call: build the hardcoded split
        (one tile per machine) and enqueue every tile tagged with a shared workload_id."""
        if req.kind not in LAUNCHABLE_KINDS:
            raise HTTPException(
                status_code=400, detail=f"kind must be one of {list(LAUNCHABLE_KINDS)}"
            )
        try:
            specs = _build_workload_jobs(req.kind, req.n_tiles, req.params)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"could not build workload: {exc}") from exc
        workload_id = uuid4().hex
        job_ids: list[str] = []
        for spec in specs:
            try:
                sub = SubmitRequest(**spec)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"invalid job spec: {exc}") from exc
            jid = submit_job(conn, sub, workload_id=workload_id)
            job_ids.append(jid)
            _emit(conn, "submitted", job_id=jid, detail=req.kind)
        return WorkloadLaunchResponse(workload_id=workload_id, kind=req.kind, job_ids=job_ids)

    @app.get("/workloads/catalog")
    def workloads_catalog() -> dict:
        """The launchable example workloads (kind, label, category, default params) so a UI can
        render launch buttons without hardcoding. Registered before /workloads/{workload_id} so
        'catalog' is not parsed as a workload id."""
        return {"workloads": WORKLOAD_CATALOG}

    @app.get("/workloads/{workload_id}", response_model=WorkloadView)
    def workload_detail(workload_id: str) -> WorkloadView:
        """All jobs (with outputs) for a launched workload: the dashboard results panel."""
        rows = conn.execute(
            "SELECT * FROM jobs WHERE workload_id = ? ORDER BY created_at ASC, job_id ASC",
            (workload_id,),
        ).fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="workload not found")
        jobs = [_job_detail(row) for row in rows]
        completed = sum(1 for row in rows if row["state"] == "completed")
        outputs = [
            json.loads(row["result_json"])
            for row in rows
            if row["state"] == "completed" and row["result_json"]
        ]
        return WorkloadView(
            workload_id=workload_id,
            kind=rows[0]["kind"],
            total=len(rows),
            completed=completed,
            jobs=jobs,
            summary=_aggregate_workload(rows[0]["kind"], outputs),
        )

    @app.get("/events")
    def events(since: int = 0) -> dict:
        rows = conn.execute(
            "SELECT id, ts, type, worker_id, job_id, detail FROM events "
            "WHERE id > ? ORDER BY id ASC LIMIT 200",
            (since,),
        ).fetchall()
        items = [dict(row) for row in rows]
        last_id = items[-1]["id"] if items else since
        return {"events": items, "last_id": last_id}

    @app.get("/healthz")
    def healthz() -> dict:
        """Lightweight reachability probe (additive; not part of the frozen contract)."""
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML.read_text(encoding="utf-8"))

    return app


