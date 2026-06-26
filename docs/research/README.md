# NightShift - Research Layer (Staff-Engineer Dossiers)

> Each Staff Engineer ran a deep web-research pass on the **compute ↔ hardware ↔ software**
> interconnections most relevant to their subsystem, and filed a cited dossier here. These are
> the **knowledge base** each lead uses when directing its team. Read your own dossier before
> building; consult the others at the seams. Curated by the Chief of Staff.

## The five dossiers

| Team | Dossier | Top high-impact areas (ranked) |
|---|---|---|
| **T1** Orchestrator | [`orchestrator/`](./orchestrator/README.md) | capability matching as a contract · lease/heartbeat/reaper · outbound short-poll · SQLite-as-queue · signed/duplicate-safe units |
| **T2** Worker | [`worker-agent/`](./worker-agent/README.md) | session-aware idle gate · hard-yield+requeue (Job Object) · polite governance (EcoQoS/caps) · GPU/NPU truth via NVML · physical idle reality (AC/thermal) |
| **T3** Isolation | [`isolation/`](./isolation/README.md) | boundary vs governance · Docker-default/Sandbox-spike · GPU host-side · container spectrum · TEEs (roadmap) |
| **T4** Trust | [`trust-rewards/`](./trust-rewards/README.md) | FP determinism + tolerance-aware verify · signed manifests/roots of trust · challenge tasks · anti-Sybil identity · validated-work rewards |
| **T5** Dashboard | [`dashboard-demo/`](./dashboard-demo/README.md) | measured throughput > TOPS · embarrassingly-parallel workloads · dashboard reliability > transport cleverness · displaced-batch economics · visual+honest trust beats |

## Cross-cutting themes (these bind the teams - guard them at the seams)

1. **"Measure, don't trust the nameplate" is a system-wide law.** It recurs in every dossier:
   T1 ranks workers by *measured* throughput, T2 benchmarks real throughput and NVML-gates GPU use,
   T4 meters credit on the *server-assigned* `class_weight` (never the agent's claimed TOPS), and T5
   presents the *measured harvested* number beside the 1.8-ExaOPS ceiling. Nameplate TOPS is marketing; the system runs on measurement.

2. **The Job Object is a shared contract between T2 and T3.** `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`
   simultaneously provides T3's resource governance *and* T2's sub-second instant-yield. Build and review
   the kill-on-close handshake **together** - it is the single most demo-critical seam.

3. **`Capability` ⟷ `Requires` is the spine.** T1's scheduler is only as good as the honesty of T2's
   capability dict. Co-design them; never let advertised capability drift from what the worker can prove.

4. **The demo-reliability triad de-risks the show more than any feature:** Docker-default isolation (T3)
   + outbound short-poll (T1) + foreground-user-session agent (T2). All three are "boring choice wins" findings.

5. **FP non-determinism scopes what is verifiable.** T4's finding (heterogeneous CPU/GPU/compiler FMA &
   rounding differences break bitwise comparison) means the PoC challenge task stays **integer/exact**, and
   tolerance-aware comparators are roadmap. This directly constrains which workloads T5 may present as "verified."

6. **The PoC ↔ roadmap line holds across all five.** Every dossier independently reaffirms the
   `architecture.md` §13 cut-list (TEE, NPU execution, cosign/OIDC, model-sharding, adaptive replication = roadmap).

## How to use this layer

- **Staff Engineers:** read your dossier's §2 (ranked areas) and §5 (implications for our contracts)
  before each build task; cite the relevant deep dive when briefing your elite-engineer subagents.
- **Chief of Staff:** use the six cross-cutting themes as the G2 checklist when integrating across teams.
- **Keep it living:** when research changes a decision, update the dossier and note it in your status report.
