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
| `GET /jobs/next?worker_id=…` | — | `JobAssignment` (200) or **204** | 204 = no matching work; capability-matched |
| `POST /heartbeat` | `HeartbeatRequest` | `HeartbeatResponse` | renews lease; `preempt=true` asks worker to yield |
| `POST /results/{job_id}` | `ResultRequest` | `ResultResponse` | verifies, credits ledger, returns points |
| `POST /jobs` | `SubmitRequest` | `SubmitResponse` | submitter enqueues a job (orchestrator fills hashes/signs) |
| `GET /state` | — | `FleetState` | dashboard read model (T5) |

## 2. Callable seams (pin these names exactly — parallel builds depend on them)

**T1 — orchestrator** (`src/orchestrator/`)
```python
from orchestrator.app import create_app          # create_app(db_path: str = ":memory:", signer=None) -> FastAPI
from orchestrator.db import init_db, connect      # init_db(db_path) -> sqlite3.Connection (schema applied, WAL)
from orchestrator.submit import submit_job        # submit_job(conn, req: SubmitRequest) -> str (job_id)
```
- `create_app` wires routes over a single SQLite connection/path. A background (or on-demand) **reaper**
  requeues jobs whose `lease_expires` has passed (state `leased` → `queued`, `assigned_worker=NULL`).
- Scheduler: capability bin-fit (`Requires` vs `Capability`); never hand a `needs_gpu` job to a CPU-only worker.
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
