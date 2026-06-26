# NightShift T1 research dossier - Orchestrator & Scheduler

## 1. How to use this

Use this as the build brief for Team T1. Start with the ranked list below, then read the linked deep dives when implementing scheduler, leases, transport, SQLite transactions, or signed result handling. This dossier is intentionally PoC-biased: implement the contract in `docs/contracts.md` first, and treat Ray/Kubernetes/Slurm/Temporal/NATS patterns as design references, not dependencies.

Deeper notes:

- [Capability matching and bin packing](capability-matching.md)
- [Leases, heartbeats, preemption, and transport](leases-and-transport.md)
- [SQLite queue design and scale boundary](sqlite-queue.md)
- [Signed manifests, idempotency, and duplicate-safe results](signed-manifests-and-results.md)

## 2. Executive summary - 5 highest-impact learning areas, ranked

1. **Capability matching as a first-class contract.** Jobs declare `Requires`; workers declare `Capability`; the scheduler does deterministic admission control before ranking. This is the highest-impact area because NightShift's core value is harvesting heterogeneous CPU/GPU/NPU-ish hardware without sending CUDA work to CPU-only PCs or memory-heavy jobs to low-VRAM GPUs. It informs `/register`, `GET /jobs/next`, `Requires` fields, server-side `class_weight`, and the PoC scheduler's match/rank query.
2. **Lease + heartbeat + reaper semantics for churning laptops.** NightShift workers are preemptible: users return, machines sleep, Wi-Fi drops, and GPU drivers fail. This area informs at-least-once assignment, short leases, `/heartbeat`, `preempt=true`, requeue-on-expiry, idempotent jobs, and duplicate-safe `/results/{job_id}`.
3. **Outbound-only short-poll transport with backpressure.** Managed employee PCs should never expose inbound ports. Workers polling only when idle/spare mirrors proven worker-polling models and avoids corporate NAT/firewall friction. It informs the frozen decision to use 1–2 s short-poll and 204-on-no-work rather than gRPC streaming, WebSocket, or a queue broker in the PoC.
4. **SQLite as a single-orchestrator queue: use it deliberately, know the cliff.** SQLite WAL is excellent for a one-process LAN PoC, but it still serializes writers and needs tight transactions. This informs `BEGIN IMMEDIATE` claiming, indexes, WAL/busy timeout, and the roadmap trigger for NATS/Postgres.
5. **Signed, content-addressed, duplicate-safe work units.** The manifest is the software boundary between submitter, orchestrator, worker, sandbox, verifier, and ledger. This informs local Ed25519 signing, code/input hashes, result hashes, accepted-result-once semantics, and why credits must be awarded only after verification.

## 3. Compute <-> hardware <-> software interconnection map for orchestration

| Compute need | Hardware reality | Software signal | T1 scheduling implication |
|---|---|---|---|
| CPU batch work | Cores are shareable but foreground responsiveness matters | `cpus`, current idle/util, `limits.cpu_pct` | Match minimum CPU; avoid oversubscription; prefer idle workers; enforce timeout/lease. |
| CUDA/DirectML GPU work | GPUs differ by vendor, model, driver, VRAM, thermal state | `has_gpu`, `gpu_vram_gb`, `accel`, NVML utilization/health | Never schedule GPU-required jobs to CPU-only workers; require `min_vram_gb`; rank by measured throughput, not claimed TOPS. |
| NPU/edge AI roadmap | Copilot+ class devices expose 40+ TOPS NPUs, but apps must target NPU-capable runtimes | `accel=["npu", "winml", "onnx"]` later | Model as accelerator labels/capacities now; do not build NPU execution in PoC. |
| Long-running work | Laptops sleep, hibernate, roam networks, or are reclaimed by humans | heartbeats, lease expiry, yielded status | At-least-once assignment; reaper requeues; worker must tolerate duplicate execution. |
| Sensitive runnable code | Worker machines are employee endpoints | signed manifest, hashes, sandbox policy | Orchestrator signs/fills manifest; worker verifies before execution; T1 should not accept unsigned/unknown mutations in roadmap. |
| Rewards | Hardware claims are gameable and heterogeneous | server-side class weight + verified units | Do not pay for advertised TOPS; credit once for accepted completion. |

## 4. Deep dives

### 4.1 Capability matching and bin-packing is the spine

Ray's resource model is the right abstraction to borrow: resources are named numeric quantities, including custom resources, used for admission control; Ray explicitly distinguishes logical resources from physical isolation and says resource requests do not themselves constrain physical CPU usage [1]. Kubernetes device plugins similarly advertise vendor resources such as `nvidia.com/gpu` to kubelet, which updates node allocatable capacity; device resources are integer-only and cannot be overcommitted [3]. Kubernetes' GPU guide adds node labels/affinity for heterogeneous GPU types and installed memory [4]. Slurm GRES shows the HPC version of the same pattern: jobs request `--gres=gpu:type:count`, `--mem-per-gpu`, or `--cpus-per-gpu`, while nodes enumerate and sanity-check GPUs via configuration or AutoDetect/NVML [5]. HTCondor's ClassAds generalize this into two-sided matching: machines and jobs both advertise constraints and ranks, and HTCondor matches only when both sides' requirements are satisfied [6][7].

For NightShift, compute is not a scalar. A single `units` queue is wrong because an AI batch slice may need CUDA + 6 GB VRAM, a data transform may need only CPU, and an NPU roadmap job may need WinML/ONNX capability. Hardware facts must be normalized into software fields: `cpus`, `ram_gb`, `has_gpu`, `gpu_vram_gb`, `accel`, and measured throughput. The PoC should implement a simple ClassAd-like predicate: `Requires.needs_gpu` implies `Capability.has_gpu`; `min_vram_gb <= gpu_vram_gb`; required accelerators intersect worker accelerators; and sandbox/runtime labels match. Ranking can be deterministic: oldest queued matching job first, then prefer scarce resources only when needed so GPU workers are not consumed by CPU-only work.

**PoC feature/decision:** implement capability matching in `GET /jobs/next?worker_id=...`; server assigns `class_weight` at `/register`; keep matching explainable and testable.

### 4.2 Leases, heartbeats, and preemption define correctness

Temporal's task queues are polled by workers over synchronous RPC; workers poll when they have spare capacity, tasks persist until workers process them, and workers do not need exposed ports [8]. Temporal's failure model also maps directly: long-running activities should heartbeat when they can report progress; heartbeat timeout detects a worker that silently crashed or lost communication and allows retry [10]. Celery's documentation makes the key distributed-systems tradeoff explicit: a message is not removed until acknowledged, redelivery after worker loss requires idempotent tasks, and late acknowledgment should be used only for idempotent tasks [14]. NATS JetStream consumers provide the queue-broker version: at-least-once delivery, `AckWait`, redelivery, `MaxAckPending`, and durable consumers [11].

NightShift's hardware makes this mandatory, not optional. Windows has sleep/hibernate/Modern Standby states where a laptop can vanish from the orchestrator's perspective [25]. `GetLastInputInfo` is session-specific and only detects input idle for the invoking session [21]; `WM_WTSSESSION_CHANGE` reports lock/unlock/logon/logoff events [23]; `GetSystemPowerStatus` reports AC/DC/battery status [22]. NVML exposes GPU monitoring and underlies `nvidia-smi`, but the library path and support differ on Windows and by GPU class [27]. Therefore the orchestrator must treat every assignment as tentative until an accepted result commits.

**PoC feature/decision:** use a short lease (~20–30 s), heartbeat renewals, `preempt=true`, and an on-demand or background reaper that atomically changes expired `leased` jobs back to `queued` with `assigned_worker=NULL`.

### 4.3 Outbound-only short-poll beats clever transports for the demo

Temporal's task queue docs validate the outbound-worker model: worker processes connect directly to the service, poll for tasks, load-balance naturally, and need no DNS or exposed ports [8]. gRPC can be efficient, but official guidance notes streams are harder to debug, cannot be load-balanced once started, and long-lived streams can reduce scalability unless they provide substantial application value [19]. Browser/client environments also illustrate gRPC's dependence on HTTP/2 features; Microsoft documents that standard browser clients cannot directly call normal gRPC services and require gRPC-Web or JSON transcoding [20]. WebSocket-style upgrades rely on HTTP protocol upgrade behavior, which is extra moving machinery relative to plain request/response polling [28].

NightShift's PoC has 2–3 workers on a LAN. The scheduler is not bottlenecked by HTTP overhead; it is bottlenecked by correctness, endpoint policy, and demo reliability. Short-polling `/jobs/next` every 1–2 seconds is simple, observable, proxy-friendly, and matches the frozen contract: return `JobAssignment` or 204. It also gives natural backpressure: workers only ask when registered, idle, and able to accept a lease.

**PoC feature/decision:** implement plain HTTPS/HTTP short-poll; no WebSocket, SSE, gRPC, or 60-second long-poll for T1.

### 4.4 SQLite queue design: strong enough for PoC, precise transactions required

SQLite WAL mode improves concurrency because readers do not block writers and writers do not block readers, but WAL still has only one writer at a time [15]. SQLite isolation docs are blunt: write transactions are serialized; `BEGIN IMMEDIATE` starts a write transaction up front and avoids later snapshot-upgrade failures [16]. WAL also requires all processes using the database to be on the same host, uses checkpointing, and long-running readers can slow checkpoint progress [15]. SQLite's own guidance says it works well as a server-side database when an application server serializes high-level requests, which is exactly the NightShift one-orchestrator PoC [17].

The queue claim operation must be a short transaction, not a Python read-then-update race. The safe shape is: start write transaction; requeue expired jobs; select the oldest matching queued job; update it to `leased` with worker, lease expiration, and attempt; commit. Reads for `/state` can run separately. Avoid long transactions during result verification or dashboard rendering.

**PoC feature/decision:** keep one orchestrator process and SQLite WAL; use atomic claim/update and idempotent result insertion. Roadmap to NATS JetStream WorkQueue or Postgres only when multi-orchestrator, high write concurrency, or broker-level redelivery is needed.

### 4.5 Signed manifests and result idempotency prevent double-credit and unsafe execution

Windows Sandbox configuration supports disabling networking, mapped folders, read-only mappings, logon commands, and memory settings; Microsoft also warns that networking and mapped folders can expose host/internal-network risk if misconfigured [26]. Job Objects let Windows manage process groups as a unit, enforce limits, account for resources, and terminate all associated processes [24]. These worker-side facts shape T1's manifest: it must carry immutable code/input hashes, limits, sandbox policy, and expiry. ONNX Runtime's execution-provider abstraction reinforces the need to express runtime/hardware capability separately from model code: providers are ordered by priority and can fall back from CUDA to CPU only if the app chooses that behavior [18].

For the orchestrator, "exactly once" execution is unrealistic because leases can expire after a worker finishes but before it posts results. The achievable guarantee is: a job may execute more than once, but only one valid result is accepted and only one ledger credit is posted. Celery's idempotency warning is the operational rule for every `runner`; NATS/Temporal show this is normal distributed-queue behavior, not a NightShift flaw [14][11][10].

**PoC feature/decision:** sign manifests on enqueue with local Ed25519; verify result hash/status; accept first valid terminal result using a transaction; reject/ignore later duplicates without double-credit.

## 5. Direct implications for OUR implementation

### Build now for the PoC

- **Schema:** workers, jobs, leases/attempt fields, results, ledger. Add indexes on `(state, created_at)`, `assigned_worker`, and `lease_expires`.
- **`POST /register`:** store normalized capability. Assign `class_weight` server-side (GPU=5, CPU=1) per frozen contract; never trust claimed TOPS for credits.
- **`GET /jobs/next`:** before selecting, run reaper logic. Select only jobs whose `Requires` fits that worker. Return 204 fast if none.
- **`POST /heartbeat`:** renew lease for current job if still leased to this worker. Return `preempt=true` when worker reports human returned / not idle / policy violation.
- **`POST /results/{job_id}`:** transactional terminal state change: if job already `completed`, return prior accepted state/points or ignore duplicate; if result valid, mark completed and insert one ledger row.
- **Manifest:** fill job id, kind, hashes, requirements, limits, issued/expires. Use local Ed25519 for PoC; make signature failure demoable.
- **Reaper:** on every poll/heartbeat/result, or periodic in-process task, requeue `leased` jobs whose `lease_expires < now`.

### Avoid for PoC

- No Ray/Kubernetes/Slurm/HTCondor dependency; borrow their models only.
- No NATS/Temporal/Postgres unless SQLite fails the 2–3 worker demo.
- No 60-second long-poll, WebSocket, SSE, or gRPC control path.
- No NPU execution, model sharding, adaptive replication system, or multi-orchestrator.
- No "exactly once" claim. State "at-least-once execution; exactly-once accepted result/credit".

### Roadmap triggers

- Move to **NATS JetStream WorkQueue** when multiple orchestrator instances or broker-level redelivery/flow-control matter; JetStream WorkQueue deletes messages after ack and supports at-least-once redelivery [12][11].
- Move to **Postgres** when write concurrency, analytics queries, or multi-process writers outgrow SQLite's single-writer model [16].
- Introduce **Temporal** only if workflows need durable multi-step orchestration, retries, compensation, and history beyond a simple job lease [8][10].
- Add **NPU scheduling** when the worker can report actual WinML/ONNX execution-provider support and measured throughput, not just a Copilot+ sticker [29][18].

## 6. Pitfalls & open questions

1. **Race: two workers claim same job.** Fix with a single write transaction around select+update; test with concurrent polling.
2. **Race: lease expires while result posts.** Accept result only if terminal transition is valid; otherwise ignore duplicate safely.
3. **GPU starvation.** If CPU-only jobs can run anywhere, reserve GPU workers for GPU jobs when GPU queue is non-empty.
4. **Long dashboard reads.** Avoid read transactions that hold snapshots and slow WAL checkpoints.
5. **Worker capability drift.** GPU driver, AC power, and idle status change after registration. Heartbeat should update volatile availability; registration stores static-ish capability.
6. **Clock skew.** Lease expiry should use orchestrator time only.
7. **Idempotency of workloads.** `data.transform` and `challenge` are safe; future side-effecting jobs require dedupe keys or should be rejected.
8. **Manifest mutation boundaries.** Decide which fields T1 signs and which worker-reported runtime facts are outside the signature.
9. **NPU label taxonomy.** Future `accel` should distinguish `npu`, `winml`, `directml`, `qnn`, and model/operator support.
10. **Verification depth.** PoC can do deterministic challenge tasks; adaptive replication is roadmap.

## 7. Sources

[1] Ray Core resources - https://docs.ray.io/en/latest/ray-core/scheduling/resources.html  
[2] Ray accelerator support - https://docs.ray.io/en/latest/ray-core/scheduling/accelerators.html  
[3] Kubernetes device plugins - https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/  
[4] Kubernetes GPU scheduling - https://kubernetes.io/docs/tasks/manage-gpus/scheduling-gpus/  
[5] Slurm Generic Resource Scheduling - https://slurm.schedmd.com/gres.html  
[6] HTCondor matchmaking with ClassAds - https://htcondor.readthedocs.io/en/v10_0/users-manual/matchmaking-with-classads.html  
[7] HTCondor ClassAd mechanism - https://htcondor.readthedocs.io/en/latest/classads/classad-mechanism.html  
[8] Temporal Task Queues - https://docs.temporal.io/task-queue  
[9] Temporal worker performance - https://docs.temporal.io/develop/worker-performance  
[10] Temporal detecting Activity failures / heartbeats - https://docs.temporal.io/encyclopedia/detecting-activity-failures  
[11] NATS JetStream consumers - https://docs.nats.io/nats-concepts/jetstream/consumers  
[12] NATS JetStream streams / WorkQueue retention - https://docs.nats.io/nats-concepts/jetstream/streams  
[13] Celery tasks - https://docs.celeryq.dev/en/stable/userguide/tasks.html  
[14] Celery optimizing / prefetch / late ack - https://docs.celeryq.dev/en/stable/userguide/optimizing.html  
[15] SQLite WAL - https://www.sqlite.org/wal.html  
[16] SQLite isolation - https://www.sqlite.org/isolation.html  
[17] SQLite when to use - https://www.sqlite.org/whentouse.html  
[18] ONNX Runtime execution providers - https://onnxruntime.ai/docs/execution-providers/  
[19] gRPC performance best practices - https://grpc.io/docs/guides/performance/  
[20] Microsoft: use gRPC in browser apps - https://learn.microsoft.com/en-us/aspnet/core/grpc/browser?view=aspnetcore-10.0  
[21] Microsoft GetLastInputInfo - https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo  
[22] Microsoft GetSystemPowerStatus - https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getsystempowerstatus  
[23] Microsoft WM_WTSSESSION_CHANGE - https://learn.microsoft.com/en-us/windows/win32/termserv/wm-wtssession-change  
[24] Microsoft Job Objects - https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects  
[25] Microsoft system power states - https://learn.microsoft.com/en-us/windows/win32/power/system-power-states  
[26] Microsoft Windows Sandbox configuration - https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file  
[27] NVIDIA NVML API reference - https://docs.nvidia.com/deploy/nvml-api/nvml-api-reference.html  
[28] MDN HTTP protocol upgrade mechanism - https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Protocol_upgrade_mechanism  
[29] Microsoft Copilot+ PCs developer guide / NPUs - https://learn.microsoft.com/en-us/windows/ai/npu-devices/
