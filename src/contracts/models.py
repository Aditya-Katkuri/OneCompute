"""OneCompute shared contracts (FROZEN).

These pydantic models are the single source of truth for the seams between teams.
T1 (orchestrator), T2 (worker), T4 (trust), and T5 (dashboard) all import from here.
Do not change a public field without Chief-of-Staff sign-off; other teams build against it.
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
MeasurementDeviceClass = Literal["laptop", "desktop", "devbox", "xbox", "unknown"]

# Data sensitivity carried by a real workload, low to high. Part of the SIGNED manifest, so it is
# tamper-evident. Drives classification-aware routing: a job may only land on a device whose
# server-assigned trust tier is high enough (see src/orchestrator/routing_policy.py). The
# conservative default is "internal", never "public", so an unclassified job is not treated as the
# least sensitive.
DataClassification = Literal["public", "internal", "confidential", "restricted"]


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- worker capability (architecture.md §3.1) --------------------------------

class DeviceAttestation(BaseModel):
    """A signed device-posture claim a worker presents at registration.

    The posture flags stand in for Intune/Entra device-compliance signals and an Azure Attestation
    (MAA) TEE claim; Ed25519 is the PoC placeholder for the real attestation authority. The
    orchestrator derives a routing ``trust_tier`` from these claims ONLY after
    ``src/trust/attestation.py:verify_attestation`` confirms the signature was produced by the
    trusted attestation AUTHORITY key it was configured with (``create_app(attestation_pubkey=...)``)
    -- never from ``signer_pubkey`` (advisory, carried for diagnostics) and never from the worker's
    own say-so. It is fail-closed and INERT until an authority key is configured. See
    docs/device-attestation.md.

    ``signature`` is a hex Ed25519 signature over ``canonical_claims_bytes(att)`` (the posture flags
    bound to ``worker_id`` and the issued/expiry window, EXCLUDING ``signature``/``signer_pubkey``),
    so the claim is bound to one device and time-boxed: another device's attestation cannot be
    replayed and an expired one is refused.
    """

    worker_id: str
    compliant: bool = False
    managed: bool = False
    sanctioned: bool = False
    tee: bool = False
    issued_at: datetime
    expires_at: datetime | None = None
    signature: str = ""        # hex Ed25519 sig over canonical_claims_bytes(att)
    signer_pubkey: str = ""    # hex; ADVISORY ONLY, NEVER trusted for the tier decision


class Capability(BaseModel):
    """What a worker advertises at registration. benchmarked_tops is a DISPLAY value;
    credit is metered server-side on the JOB's actual GPU requirement, never on this number.
    free_ram_gb is an optional snapshot of currently-available RAM at registration."""

    worker_id: str
    measurement_only: bool = False
    cpus: int = 1
    ram_gb: float = 1.0
    free_ram_gb: float | None = None
    has_gpu: bool = False
    gpu_model: str | None = None
    gpu_vram_gb: float | None = None
    accel: list[str] = Field(default_factory=list)  # e.g. ["cuda", "directml"]
    benchmarked_tops: float | None = None
    # NPU harvesting (roadmap: docs/npu-harvesting.md). Detection + advertisement only today; a
    # Copilot+ PC NPU / DirectML provider is surfaced here so the fleet picture can include it.
    # npu_tops is NAMEPLATE INT8 peak (spec sheet), never delivered throughput -- credit is still
    # metered on the job's actual GPU requirement server-side, never on this number.
    has_npu: bool = False
    npu_tops: float | None = None
    labels: list[str] = Field(default_factory=list)
    # ADVISORY ONLY. A worker may claim a device trust tier here, but the orchestrator NEVER uses
    # this for routing: the routing tier is assigned server-side (workers.trust_tier) and defaults
    # to the lowest tier. This mirrors the rule that credit is metered on the job's actual GPU
    # requirement, never on the worker's self-reported has_gpu. Kept for future attestation
    # tooling / diagnostics; treating a self-report as authoritative would let a rogue worker claim
    # a high tier to receive confidential data (see docs/routing-policy.md).
    attested_tier: str | None = None
    # OPTIONAL signed device-posture attestation the worker presents at registration. It is a
    # VERIFIABLE claim (not a self-report): the orchestrator derives a trust tier from it ONLY after
    # checking its Ed25519 signature against the trusted attestation-authority key it was configured
    # with (create_app attestation_pubkey). With no authority key configured the attestation is
    # INERT (ignored), so default behavior is unchanged. See src/trust/attestation.py and
    # docs/device-attestation.md.
    attestation: DeviceAttestation | None = None


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


class RoutingProvenance(BaseModel):
    """Where a routed job came from, stamped into the SIGNED manifest by the Foundry routing
    gateway (flow F9 / boundary B6, docs/foundry-gateway.md).

    Because it lives INSIDE the signed manifest it is tamper-evident: a compromised relay or a
    worker cannot rewrite which tenant/region a job was routed for without breaking the Ed25519
    signature. It is optional and defaults to None, so every job submitted through the ordinary
    ``/jobs`` path serializes and signs exactly as before.
    """

    tenant_id: str
    region: str = ""


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
    # Data sensitivity of this job's input. Signed as part of the manifest, so a worker cannot
    # downgrade it to receive data it is not cleared for. Defaults to the conservative "internal".
    data_classification: DataClassification = "internal"
    # Routing provenance stamped by the Foundry gateway (F9): the tenant/region this job was routed
    # for. Signed as part of the manifest, so it is tamper-evident. Default None keeps every
    # ordinary submission unchanged. See src/orchestrator/app.py POST /foundry/jobs.
    provenance: RoutingProvenance | None = None
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
    # Sensitivity of the submitted data. Carried into the signed manifest and enforced at routing.
    # Defaults to the conservative "internal" so an unclassified submission is never treated public.
    data_classification: DataClassification = "internal"
    units: int = 1


class SubmitResponse(BaseModel):
    job_id: str


# --- Foundry routing gateway (flow F9 / boundary B6, docs/foundry-gateway.md) -------------------

class FoundryTenant(BaseModel):
    """A tenant registered with the Foundry routing gateway (F9).

    PoC in-memory registry entry: a real deployment backs this with Entra tenants rather than a
    static config dict. ``max_classification`` is the HIGHEST data classification this tenant may
    route (compared by ``routing_policy.CLASSIFICATIONS`` rank, fail-closed) and ``allowed_regions``
    is its region allow-list (EMPTY = deny all, fail-closed). ``token`` is the shared secret the
    caller presents as a Bearer credential; the gateway compares it in constant time.
    """

    tenant_id: str
    token: str
    max_classification: DataClassification = "internal"
    allowed_regions: list[str] = Field(default_factory=list)


class FoundryRoutingRequest(BaseModel):
    """POST /foundry/jobs body: an Azure AI Foundry / tenant request to route one job onto the fleet.

    The gateway authenticates the tenant, enforces its per-tenant classification + region policy,
    stamps ``{tenant_id, region}`` provenance into the signed manifest, and enqueues through the
    same signed-submit path as ``/jobs``. ``data_classification`` is set AUTHORITATIVELY from this
    request (bounded by the tenant's clearance), never inferred from a worker's self-report.
    """

    tenant_id: str
    region: str
    kind: JobKind
    input: dict[str, Any] = Field(default_factory=dict)
    requires: Requires | None = None
    units: int = 1
    data_classification: DataClassification


class TierAssignmentRequest(BaseModel):
    """Admin body for POST /workers/{id}/tier: the SERVER-ASSIGNED device trust tier IT elevates a
    device to (out-of-band). Kept a plain string so an unknown/misspelled tier is rejected with an
    explicit 400 by the endpoint rather than silently coerced; the operator-token gate protects it."""

    trust_tier: str


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
    # SERVER-ASSIGNED device trust tier (never the worker's self-report). Defaults to the lowest
    # tier "untrusted"; drives classification-aware routing. See src/orchestrator/routing_policy.py.
    trust_tier: str = "untrusted"


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
    """A job record plus its parsed output: what a dashboard reads to show results."""

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
    """Launch a whole workload across the fleet in one call (split into tiles the pull queue hands out).

    ``oversubscribe`` opts into over-decomposition (docs/work-stealing.md): with the default of 1 the
    launcher keeps today's exact one-tile-per-worker behavior, while a value > 1 carves the workload
    into ~``oversubscribe`` x (approved worker count) smaller tiles so idle/fast machines keep pulling
    the next tile and a dropped tile requeues to whoever is free. An explicit ``n_tiles`` is respected
    verbatim as the tile count (it wins over the worker-count computation)."""

    kind: str
    n_tiles: int = 3
    oversubscribe: int = Field(default=1, ge=1, le=64)
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


# --- measurement pilot (privacy-minimized summary: POST /profile, GET /measurement) ------------

class UsageBucket(BaseModel):
    """Legacy measurement bucket accepted only during a rolling upgrade.

    New workers never send these per-hour values. The orchestrator collapses a legacy list into one
    compact summary in memory and discards the list rather than persisting an activity heatmap.
    """

    index: int = 0
    n: int = 0
    cpu_mean: float = 0.0
    cpu_max: float = 0.0
    gpu_mean: float = 0.0
    gpu_max: float = 0.0
    ram_mean: float = 0.0
    ram_max: float = 0.0
    ac_mean: float = 0.0    # % of samples on AC power (harvestable-window signal)
    idle_mean: float = 0.0  # % of samples with the human idle/away (prime-harvest-window signal)


class MetricSummary(BaseModel):
    """Fleet estimate for one resource: measured average/peak utilization and the ESTIMATED
    conservatively-recoverable headroom range (percent)."""

    avg: float = 0.0
    peak: float = 0.0
    recoverable_low: float = 0.0
    recoverable_high: float = 0.0


class ProfileAvailability(BaseModel):
    """Compact timing totals only, with no raw timestamps or per-hour presence pattern."""

    span_hours: float = 0.0
    observed_hours_per_day: float = 0.0
    unavailable_hours_per_day: float = 0.0
    sample_count: int = 0


class ProfileReport(BaseModel):
    """POST /profile body: one privacy-minimized, derived summary per volunteer device.

    Current workers send no raw samples, wall-clock timestamps, application data, input events,
    idle/presence field, or hour-of-week buckets. ``buckets`` is receive-only legacy compatibility.
    """

    worker_id: str
    device_class: MeasurementDeviceClass = "unknown"
    coverage_buckets: int = 0
    cpu: MetricSummary = Field(default_factory=MetricSummary)
    gpu: MetricSummary = Field(default_factory=MetricSummary)
    ram_avg: float = 0.0
    ram_headroom: float = 0.0
    ac_avg: float = 0.0
    availability: ProfileAvailability = Field(default_factory=ProfileAvailability)
    buckets: list[UsageBucket] = Field(default_factory=list, exclude=True)


class ProfileIngestResponse(BaseModel):
    accepted: bool = True
    coverage_buckets: int = 0
    buckets_stored: int = 0  # compatibility: compact-summary storage always keeps zero buckets


class MeasurementSummary(BaseModel):
    """GET /measurement read model: fleet-wide measured idle headroom for the dashboard and the
    pilot hand-off to Azure Compute + CISO. Every figure is an ESTIMATE from measured idle
    profiles, computed by ``measurement.headroom`` with the governor's comfort margin and a
    conservative harvest; the assumptions that produced it travel alongside it."""

    device_count: int = 0
    total_coverage_buckets: int = 0
    margin_pct: float = 25.0
    harvest_low: float = 0.20
    harvest_high: float = 0.40
    cpu: MetricSummary = Field(default_factory=MetricSummary)
    gpu: MetricSummary = Field(default_factory=MetricSummary)
    ram_avg: float = 0.0
    ram_headroom: float = 0.0
    ac_avg: float = 0.0    # fleet % of time on AC power
    observed_hours_per_day: float = 0.0
    unavailable_hours_per_day: float = 0.0
    timing_span_hours: float = 0.0
    device_classes: dict[str, int] = Field(default_factory=dict)
