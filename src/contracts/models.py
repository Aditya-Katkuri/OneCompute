"""NightShift shared contracts (FROZEN).

These pydantic models are the single source of truth for the seams between teams.
T1 (orchestrator), T2 (worker), T4 (trust), and T5 (dashboard) all import from here.
Do not change a public field without Chief-of-Staff sign-off - other teams build against it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# --- enums (string literals keep the JSON contract obvious) ------------------

JobKind = Literal[
    "ai.batch_infer", "eval", "data.transform", "render", "challenge",
    "fractal", "optimize", "ai.synth",
    "montecarlo", "hashcrack",                 # NON-AI long-running, multi-core
    "ai.infer", "ai.eval", "ai.graph",         # AI long-running, local-model (Ollama)
]
JobState = Literal["queued", "leased", "completed", "failed"]
ResultStatus = Literal["completed", "failed", "yielded"]
SandboxType = Literal["docker", "windows_sandbox", "job_object"]
Network = Literal["none", "host"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- worker capability (architecture.md §3.1) --------------------------------

class Capability(BaseModel):
    """What a worker advertises at registration. benchmarked_tops is a DISPLAY value;
    credit is metered on the server-assigned class_weight, never on this number.
    free_ram_gb is an optional snapshot of currently-available RAM at registration."""

    worker_id: str
    cpus: int = 1
    ram_gb: float = 1.0
    free_ram_gb: float | None = None
    has_gpu: bool = False
    gpu_model: str | None = None
    gpu_vram_gb: float | None = None
    accel: list[str] = Field(default_factory=list)  # e.g. ["cuda", "directml"]
    benchmarked_tops: float | None = None
    labels: list[str] = Field(default_factory=list)


# --- job / manifest (architecture.md §5) -------------------------------------

class Requires(BaseModel):
    needs_gpu: bool = False
    min_vram_gb: float | None = None
    min_ram_gb: float | None = None
    accel: list[str] = Field(default_factory=list)
    min_cpus: int = 1


class Limits(BaseModel):
    cpu_pct: int = 60
    mem_gb: float = 8.0
    timeout_s: int = 600
    network: Network = "none"


class SandboxPolicy(BaseModel):
    type: SandboxType = "docker"
    vgpu: bool = False
    mapped_ro: list[str] = Field(default_factory=lambda: ["in/"])


class JobManifest(BaseModel):
    """The signed trust contract. `code_sha256`/`input_sha256` are verified before a run."""

    job_id: str
    kind: JobKind
    code_ref: str = "builtin"          # PoC: a registered runner kind, not a remote OCI image
    code_sha256: str = ""
    input_sha256: str = ""
    requires: Requires = Field(default_factory=Requires)
    limits: Limits = Field(default_factory=Limits)
    sandbox: SandboxPolicy = Field(default_factory=SandboxPolicy)
    issued_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None


class SignedManifest(BaseModel):
    """A manifest plus its Ed25519 signature (T4). signature == '' means unsigned (PoC skeleton)."""

    manifest: JobManifest
    signature: str = ""        # hex
    public_key: str = ""       # hex (convenience for the PoC; real flow trusts an out-of-band key)


# --- control-plane request/response models (T1 HTTP API) ---------------------

class RegisterResponse(BaseModel):
    worker_token: str
    poll_interval_s: float = 1.5
    device_code: str | None = None     # short human code shown for dashboard approval (gated flows)
    approved: bool = True              # False when the fleet requires dashboard approval to join


class JobAssignment(BaseModel):
    """Returned by GET /jobs/next when a matching job exists (200). 204 == no work."""

    signed_manifest: SignedManifest
    input: dict[str, Any] = Field(default_factory=dict)


class HeartbeatRequest(BaseModel):
    worker_id: str
    idle: bool = True
    cpu_pct: float = 0.0
    gpu_pct: float | None = None
    on_ac: bool = True
    free_ram_gb: float | None = None   # live available RAM, drives free-RAM gating
    current_job_id: str | None = None


class HeartbeatResponse(BaseModel):
    ack: bool = True
    preempt: bool = False      # server asks the worker to yield the current job
    approved: bool = True      # current approval state; flips true once an admin approves the worker


class ResultRequest(BaseModel):
    worker_id: str
    job_id: str
    status: ResultStatus
    output: dict[str, Any] | None = None
    proof_sha256: str | None = None
    duration_s: float | None = None
    units: int = 1


class ResultResponse(BaseModel):
    accepted: bool
    credited: float = 0.0
    reason: str | None = None


class SubmitRequest(BaseModel):
    """Submitter side: enqueue a job. The orchestrator fills hashes + signs (T4)."""

    kind: JobKind
    input: dict[str, Any] = Field(default_factory=dict)
    requires: Requires = Field(default_factory=Requires)
    limits: Limits = Field(default_factory=Limits)
    units: int = 1


class SubmitResponse(BaseModel):
    job_id: str


# --- dashboard read model (T5 reads GET /state) ------------------------------

class WorkerView(BaseModel):
    worker_id: str
    idle: bool
    busy: bool
    has_gpu: bool
    cpu_pct: float = 0.0
    gpu_pct: float | None = None
    free_ram_gb: float | None = None
    blacklisted: bool = False
    credits: float = 0.0
    approved: bool = True              # whether the worker has been admitted (dashboard approval gate)
    device_code: str | None = None     # short pending code shown until an admin approves


class JobView(BaseModel):
    job_id: str
    kind: JobKind
    state: JobState
    assigned_worker: str | None = None


class FleetState(BaseModel):
    workers: list[WorkerView] = Field(default_factory=list)
    jobs: list[JobView] = Field(default_factory=list)
    total_credits: float = 0.0


# --- dashboard: job/workload detail (output retrieval + one-call launch) ------

# Workload kinds a dashboard can launch across the fleet with POST /workloads.
LAUNCHABLE_KINDS: tuple[str, ...] = (
    "fractal", "optimize", "ai.batch_infer", "ai.synth", "data.transform",
    "montecarlo", "hashcrack", "ai.infer", "ai.eval", "ai.graph",
)


class JobDetail(BaseModel):
    """A job record plus its parsed output - what a dashboard reads to show results."""

    job_id: str
    kind: JobKind
    state: JobState
    assigned_worker: str | None = None
    units: int = 1
    workload_id: str | None = None
    output: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


class WorkloadLaunchRequest(BaseModel):
    """Launch a whole workload across the fleet in one call (hardcoded split into n_tiles)."""

    kind: str
    n_tiles: int = 3
    params: dict[str, Any] = Field(default_factory=dict)


class WorkloadLaunchResponse(BaseModel):
    workload_id: str
    kind: str
    job_ids: list[str] = Field(default_factory=list)


class WorkloadView(BaseModel):
    """A launched workload's jobs + outputs, for the dashboard results panel."""

    workload_id: str
    kind: str
    total: int = 0
    completed: int = 0
    jobs: list[JobDetail] = Field(default_factory=list)
    # Render-ready merged result computed server-side over the completed tiles (None until any
    # tile finishes). Shape depends on kind -- see docs/dashboard-api.md. The dashboard draws
    # this directly instead of re-implementing each workload's aggregation in the browser.
    summary: dict[str, Any] | None = None
