# NightShift — Frozen Contracts (Phase 0)

> The seams between teams. **Frozen** — change only with Chief-of-Staff sign-off.
> Data models: [`src/contracts/models.py`](../src/contracts/models.py). Hashing:
> [`src/contracts/hashing.py`](../src/contracts/hashing.py). DB: [`src/contracts/schema.sql`](../src/contracts/schema.sql).
> Import everything as `from contracts import ...` (tests run with `pythonpath = ["src"]`).

## 1. HTTP control-plane API (T1 owns; T2/T5 consume)

Outbound-only from workers; short-poll. All bodies are the pydantic models in `contracts`.

| Method & path | Request | Response | Notes |
|---|---|---|---|
| `POST /register` | `Capability` | `RegisterResponse` | assigns `class_weight` (GPU=5, CPU=1) server-side |
| `GET /jobs/next?worker_id=…` | — | `JobAssignment` (200) or **204** | 204 = no matching work; capability-matched. **Also 204 until the worker is approved** (gated flows) |
| `POST /heartbeat` | `HeartbeatRequest` | `HeartbeatResponse` | renews lease; `preempt=true` asks worker to yield |
| `POST /results/{job_id}` | `ResultRequest` | `ResultResponse` | verifies, credits ledger, returns points |
| `POST /jobs` | `SubmitRequest` | `SubmitResponse` | submitter enqueues a job (orchestrator fills hashes/signs) |
| `GET /state` | — | `FleetState` | dashboard read model (T5) |
| `POST /workers/{worker_id}/approve` | — | `{"ok": true, "worker_id": …}` (200) or **404** | **(additive)** admits a pending worker; emits the `approved` event |
| `GET /jobs/{job_id}` | — | `JobDetail` (200) or **404** | **(additive)** one job + its parsed output; registered after `/jobs/next` so the long-poll literal route still wins |
| `POST /workloads` | `WorkloadLaunchRequest` | `WorkloadLaunchResponse` | **(additive)** one-call fleet launch; **400** on unknown/non-launchable kind |
| `GET /workloads/{workload_id}` | — | `WorkloadView` (200) or **404** | **(additive)** all jobs + outputs for a launched workload; registered after `/workloads/catalog` |
| `GET /workloads/catalog` | — | `{"workloads": [...]}` | **(additive)** the launchable example workloads (UI launch buttons) |

## 2. Callable seams (pin these names exactly — parallel builds depend on them)

**T1 — orchestrator** (`src/orchestrator/`)
```python
from orchestrator.app import create_app          # create_app(db_path: str = ":memory:", signer=None, require_approval: bool = False) -> FastAPI
from orchestrator.db import init_db, connect      # init_db(db_path) -> sqlite3.Connection (schema applied, WAL)
from orchestrator.submit import submit_job        # submit_job(conn, req: SubmitRequest, workload_id: str | None = None) -> str (job_id)
```
- `create_app` wires routes over a single SQLite connection/path. A background (or on-demand) **reaper**
  requeues jobs whose `lease_expires` has passed (state `leased` → `queued`, `assigned_worker=NULL`).
- Scheduler: capability bin-fit (`Requires` vs `Capability`); never hand a `needs_gpu` job to a CPU-only worker.
  Matched dimensions: `needs_gpu`, `min_vram_gb`, `min_ram_gb`, `min_cpus`, `accel`. `min_ram_gb` is gated on the
  worker's **live free RAM** — reported at registration (`Capability.free_ram_gb`) and refreshed every
  `/heartbeat` (`HeartbeatRequest.free_ram_gb`) — falling back to total `ram_gb` until the first heartbeat.
- On accepted result: `credits = units * class_weight`, written to `ledger`; job → `completed`.

**T2 — worker** (`src/worker/`)
```python
from worker.capability import detect_capability   # detect_capability() -> Capability  (pynvml guarded → has_gpu=False)
from worker.agent import WorkerAgent
#   WorkerAgent(base_url: str, capability: Capability, runner=None, client: httpx.Client | None = None)
#     .register() -> None
#     .poll_once() -> JobAssignment | None
#     .run_job(assignment: JobAssignment) -> ResultRequest
#     .heartbeat(current_job_id: str | None = None) -> HeartbeatResponse
#     .run_once() -> ResultRequest | None      # register→poll→run→report one job (used by integration test)
```
- `client` injection lets tests pass an ASGI-backed `httpx.Client` (no real socket).
- The job loop is **chunked**: between chunks it checks `self.should_yield`; on yield it stops fast and
  reports `status="yielded"` (Phase 2 wires this to the Job Object kill-on-close).

**Runner interface** (T2 provides a default; T3 swaps in isolation in Phase 2)
```python
def runner(manifest: JobManifest, input: dict, should_yield=lambda: False) -> dict:
    """Execute the job, return the output dict. Must check should_yield() between chunks."""
```
Default runner (Phase 1) handles:
- `data.transform`: input `{"items": [...], "op": "sha256|upper|square"}` → `{"results": [...]}`.
- `challenge`: input `{"x": int}` → `{"y": x*x + 1}` (deterministic; T4 uses this for ringer checks).

## 3. Trust seam (T4 owns; stubs allowed in Phase 1)
```python
from contracts.hashing import sha256_hex          # already frozen + shared
# T4 will add src/trust/signing.py: sign(manifest)->SignedManifest, verify(sm: SignedManifest)->bool
```
Phase-1 skeleton: manifests may be unsigned (`signature=""`) and the worker treats unsigned as acceptable.
Phase-3: orchestrator signs on enqueue; worker calls `verify()` and refuses on mismatch (byte-flip demo).

## 4. How to run (no global PATH assumptions)
```
uv = C:\Users\t-cfinney\AppData\Local\Programs\Python\Python312-arm64\Scripts\uv.exe
& $uv run pytest -q                       # all tests (pythonpath=src)
& $uv run ruff check src                   # lint
& $uv run uvicorn orchestrator.app:create_app --factory --port 8080   # run orchestrator (PYTHONPATH=src)
& $uv run python -m worker --url http://127.0.0.1:8080                 # run a worker
```

---

## 5. Phase-2 seams (FROZEN)

### 5.1 Shared execution — `jobkit` (COS-owned, DONE)
- `from jobkit.execute import execute` → `execute(kind: str, input: dict, should_yield=lambda: False) -> dict`.
  The single source of truth for executing a job kind. Used in-process by the worker **and** inside the
  sandbox by isolation, so a job's result is identical either way.
- Sandbox entrypoint: `python -m jobkit <in.json> <out.json>` (in.json = `{"kind","input"}`). Requires
  `src` on `PYTHONPATH` (the caller — T3 — sets it when spawning).
- Output shapes: `data.transform`→`{"results":[...], "yielded": bool}`; `challenge`→`{"y": int}`;
  `ai.batch_infer`→`{"results":[{"prompt","completion","tokens"}], "backend":"openai|anthropic|fallback", "yielded": bool}`.

### 5.2 `ai.batch_infer` input (T5 generates → jobkit executes)
`input = {"prompts": [str, ...], "model": str?, "max_tokens": int=64}`. Each worker scores a *slice* of the
prompt set; real SDK call if `OPENAI_API_KEY`/`ANTHROPIC_API_KEY` is set, else a disclosed token-proportional fallback.

### 5.3 Trust — `src/trust/` (T4 owns)
```python
from trust import Signer, verify_manifest, make_challenge, check_challenge
# Signer(private_key_hex: str | None = None): .public_key_hex; .sign(m: JobManifest) -> SignedManifest
#   (Ed25519 over contracts.canonical_bytes(m.model_dump()))
# verify_manifest(sm: SignedManifest) -> bool   (empty signature is INVALID once signing is on)
# make_challenge() -> tuple[dict, dict]          (job_input, expected_output) for a `challenge` job
# check_challenge(output: dict, expected: dict) -> bool   (exact, integer — no FP)
```
Integration (Wave B, COS): `create_app(signer=Signer())` signs on assignment; the worker calls
`verify_manifest(sm)` **and** checks `sha256_hex(input) == manifest.input_sha256` before running (tamper-refusal).

### 5.4 Isolation — `src/isolation/` (T3 owns)
```python
from isolation import run_in_isolation, isolation_proof, JobHandle
# run_in_isolation(kind: str, input: dict, limits: Limits, should_yield=lambda: False) -> dict
#   executes the job via jobkit inside a boundary: Docker (--network none, ro mounts, --rm) if available,
#   else a restricted subprocess under a Windows Job Object (CPU/mem cap + KILL_ON_JOB_CLOSE).
# isolation_proof() -> dict   evidence the sandbox cannot read the host user profile (demo beat)
# JobHandle.kill()            closes the Job Object handle -> process tree dies sub-second (powers T2 yield)
```

### 5.5 Dashboard read models (T1 serves → T5 reads)
- `GET /state` → `FleetState` (exists). `WorkerView` now also carries **live** `cpu_pct`/`gpu_pct`/`free_ram_gb`
  (the worker streams `HeartbeatRequest` every ~1s, so `/state` reflects live per-device usage).
- `GET /events?since=<id>` → `{"events":[{"id","ts","type":"registered|approved|submitted|assigned|completed|yielded|failed|blacklisted",
  "worker_id","job_id","detail"}], "last_id": int}` — COS adds this in integration for the live activity feed.

---

## 6. Additive seams (Wave C, COS) — frozen contracts above still hold

These were **added** on top of the frozen Phase-0/Phase-2 seams; nothing above changed.

### 6.1 New JobKinds
`JobKind` gained `"fractal"`, `"optimize"`, `"ai.synth"` (alongside the existing
`ai.batch_infer | eval | data.transform | render | challenge`). See [`docs/workloads.md`](workloads.md)
for the four launchable workloads.

### 6.2 Device-code approval gate (additive; off by default)
- `create_app(db_path=":memory:", signer=None, require_approval: bool = False)`. With `require_approval=False`
  (default) every worker auto-approves and all flows above are unchanged.
- When gated, a freshly-registered worker is **pending**: it gets a short human `device_code` and
  `GET /jobs/next` returns **204** until it is admitted.
- `POST /workers/{worker_id}/approve` → `{"ok": true, "worker_id": …}` (404 unknown) flips it to approved,
  clears the `device_code`, and emits a new `"approved"` event.
- Model fields added (defaults keep non-gated flows identical):
  - `RegisterResponse`: `device_code: str | None = None`, `approved: bool = True`.
  - `HeartbeatResponse`: `approved: bool = True` (current approval state; flips true once admitted).
  - `WorkerView`: `approved: bool = True`, `device_code: str | None = None`.
- `workers` table gained `approved INTEGER NOT NULL DEFAULT 1` and `device_code TEXT` (default 1 keeps
  non-gated fleets unchanged).

### 6.3 Dashboard API (additive)
One-call fleet launch + per-job/per-workload output retrieval. Full UI-integration reference:
[`docs/dashboard-api.md`](dashboard-api.md).
- **Launch:** `POST /workloads` `WorkloadLaunchRequest{ kind, n_tiles=3, params }` → `WorkloadLaunchResponse{
  workload_id, kind, job_ids }`. Builds the hardcoded split (one tile per machine) and enqueues each tile
  tagged with a shared `workload_id`. **400** on an unknown/non-launchable kind.
- **Catalog:** `GET /workloads/catalog` → `{"workloads": [ {kind,label,category,ai,blurb,default_params,split} ]}`
  — the launchable examples a UI renders as buttons (registered before `/workloads/{workload_id}`).
- **Output retrieval:** `GET /jobs/{job_id}` → `JobDetail` (job record + parsed output; registered after
  `/jobs/next`) and `GET /workloads/{workload_id}` → `WorkloadView{ workload_id, kind, total, completed,
  jobs:[JobDetail] }`.
- New contracts models/exports: `JobDetail`, `WorkloadLaunchRequest`, `WorkloadLaunchResponse`,
  `WorkloadView`, and the constant `LAUNCHABLE_KINDS = ("fractal","optimize","ai.batch_infer","ai.synth","data.transform")`.
- `jobs` table gained `workload_id TEXT` (groups jobs launched together); `submit_job(conn, req, workload_id=None)`.