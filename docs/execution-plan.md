# NightShift — Execution Plan & Work Distribution

> How to split NightShift across the team and the agent harness. Pairs with
> [`harness.md`](./harness.md) (how the org runs) and the doctrine in
> [`.github/copilot-instructions.md`](../.github/copilot-instructions.md).
> Build order is the de-risked sequence from [`architecture.md`](./architecture.md) §13.

---

## 1. People & how they map to teams

| Person | Strength | Owns | As… |
|---|---|---|---|
| **You** (acting CEO) | programmer + business | **T1 Orchestrator & Scheduler** (the spine) + overall technical direction | CEO *and* hands-on owner of the integration backbone |
| **Aditya** | most technically skilled | **T2 Worker Agent & Instant-Yield** + **T3 Isolation & Sandbox** | the two hardest, tightly-coupled worker-side subsystems |
| **Ethan** | software developer | **T4 Trust, Verification & Rewards** | a crisply-scoped, high-impact slice (~hundreds of lines) |
| **Praj** | business + presentation | **T5 Dashboard, Workloads & Demo** (story + run-of-show) | owns what judges see; directs the agent team that builds it |
| **Chief of Staff** (me) | orchestration + QA | integration, the end-to-end slice, the quality bar | amplifies all four humans with agent teams |

**Why this split**
- **You own the spine (T1).** Every team integrates against the orchestrator's API + manifest, so owning it
  keeps you central to the technical implementation *and* gives you the leverage point a CEO wants. It's also
  the cleanest subsystem to hold stable while others move fast around it.
- **Aditya gets the hard core (T2+T3).** The instant-yield + isolation work is the highest-risk, highest-skill
  area and the two pieces share the kill-on-close handshake — one strong owner avoids a seam through the riskiest code.
- **Ethan gets T4.** Signing + one challenge task + a class-weight ledger is well-bounded with a sharp spec —
  ideal for a solid dev to own end-to-end and land two crowd-pleasing demo beats (cheater caught, tamper refused).
- **Praj gets T5.** It's the presentation surface and the pitch. Praj owns the narrative, the ghost-bar story,
  and the ExaOPS framing; the agent team writes the Streamlit code under that direction.

> Each human is an **owner/director**; the agent teams (Staff Engineer → elite engineers) do the bulk of the
> typing. A human + an agent team is one "team." You scale by directing, not by out-typing the work.

---

## 2. Subsystems, owned code, and demo beats

| Team | Owns (proposed layout) | Publishes (contract) | Demo beat |
|---|---|---|---|
| **T1** Orchestrator | `src/orchestrator/` | HTTP API + manifest schema + SQLite schema | fan-out, requeue-on-yield, results |
| **T2** Worker+Yield | `src/worker/` | capability dict, result/proof, `run(manifest,input)` | **mouse-touch → 0.3 s yield** |
| **T3** Isolation | `src/isolation/` | `run_in_isolation(job)` + Job Object handle | denied `C:\Users` read + wipe |
| **T4** Trust+Rewards | `src/trust/` | `sign`/`verify`, `inject_challenge`/`verify_result`, `credit` | cheater blacklisted, tamper refused |
| **T5** Dash+Demo | `src/dashboard/`, `src/workloads/`, `docs/demo-script.md` | demo workloads + run-of-show | every beat is staged here |

---

## 3. The contracts (build these first — they let teams parallelize)

The whole plan hinges on a few interfaces being agreed **day 0** so teams build against stable seams:

1. **HTTP API** (T1 → everyone): `POST /register`, `GET /jobs/next` (short-poll), `POST /heartbeat`,
   `POST /results/{job_id}`, `GET /` (fleet+ledger state).
2. **Manifest schema** (T1 → T2/T4): `job_id, kind, code_sha256, input_sha256, requires, limits, sandbox, expires`.
3. **Isolation entry point** (T3 → T2): `run_in_isolation(job) -> result` + a closable Job Object handle.
4. **Trust API** (T4 → T1/T2): `sign(manifest)`, `verify(manifest)->bool`, `inject_challenge()`,
   `verify_result()->bool`, `credit(worker, units, weight)`.
5. **Capability dict & result/proof** (T2 → T1): the JSON a worker registers and returns.

> COS owns these contracts. Any change goes through COS so a silent break can't ripple across teams.

---

## 4. Milestones (de-risked — lock the floor before anything fragile)

### Phase 0 — Day 0, in writing (no code until these are decided)
Owner: **CEO + COS**. Confirm: 1 GPU + 1 plain physical PC with **local admin**; a trivial `.wsb` launches
(else commit to **Docker** isolation); pick the **orchestrator-host PC** and prove a worker reaches it over
HTTPS within the hour; pre-stage model/binary + AV exclusions; **freeze the §3 contracts.**

### Phase 1 — The skeleton (green end-to-end ASAP)
- **T1:** FastAPI `register / jobs/next (short-poll) / heartbeat / results` + SQLite + manifest model.
- **T2:** agent v0 — register → poll → run a trivial chunked job → heartbeat → report.
- **Exit:** one worker registers, runs a job, returns a result. *Keep this runnable from here on.*

### Phase 2 — The money shot + the surface (build the demo's heart while there's time)
- **T2:** the **yield loop** (idle flag → close Job Object → sub-second kill → requeue).
- **T3:** **Docker-per-job** isolation + Job Object caps/kill-on-close.
- **T5:** dashboard on **seeded data** (tiles, points ticker, throughput, green→amber yield flip).
- **Exit:** touch the mouse → tile flips "yielded 0.3 s" → slice requeues and completes.

### Phase 3 — Throughput, AI, trust
- **T5:** CPU **fan-out + ghost-bar baseline**; wire dashboard to **live** T1 data; then the **AI SDK job**.
- **T4:** **Ed25519** sign/verify + byte-flip refusal; one **challenge task** → blacklist + forfeit; **ledger** metering.
- **T2:** full idle gate (input/lock/AC/GPU) + capability advert with guarded `pynvml`.
- **Exit:** fan-out beats the ghost bar; points tick (GPU faster); a `--cheat` worker is caught and earns zero.

### Phase 4 — Hardening, GPU upside, rehearsal
- **T3:** one pre-warmed **Windows Sandbox** CPU job (isolation proof) + **GPU host-side** path (pure upside, last).
- **All:** G2 integration review; run the full **4–5 min run-of-show twice**; measure real harvested throughput.
- **Exit:** demo is rehearsed, twice-green, and honest (measured throughput shown beside the ExaOPS ceiling).

---

## 5. Critical path & parallelism

- **Critical path:** Contracts (P0) → T1 skeleton → T2 yield → integrated slice → rehearsal.
  Protect T2's yield — it's both highest-risk and the headline.
- **Runs in parallel once contracts are frozen:** T4 (against the manifest), T5 dashboard (against seeded
  data), T3 Docker path (against the isolation interface). Use `/fleet` to run these teams concurrently.
- **Assume-fail spikes (never on the critical path):** Windows Sandbox, GPU-in-Sandbox, corporate-LAN
  transport. Each has a pre-built fallback (Docker, host-side GPU, hotspot/short-poll).

---

## 6. Risk register (owner ▸ mitigation)

| # | Risk | Owner | Mitigation |
|---|---|---|---|
| 1 | Cryptojacking false-positive / Defender quarantine | CEO+COS | demo on **unmanaged/loaner PCs** or isolated switch; allow-listing is a slide |
| 2 | Instant-yield not sub-second / unconvincing | Aditya (T2) | build it Phase 2; close Job Object handle; rehearse the flip |
| 3 | Windows Sandbox / GPU-in-Sandbox unavailable | Aditya (T3) | **Docker default**; GPU host-side; timebox the spike ~3 h |
| 4 | Orchestrator unreachable from workers (topology) | CEO (T1) | orchestrator on a **physical LAN PC**; prove reachability hour 1; short-poll |
| 5 | Verification cost / cheater not caught | Ethan (T4) | one challenge task + blacklist; no blanket replication |
| 6 | Demo loop flat / story unclear | Praj (T5) | ghost-bar throughput hook; tight run-of-show; honest ExaOPS framing |

---

## 7. RACI (quick reference)

| Activity | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| Contracts frozen (P0) | COS | CEO | all leads | team |
| Orchestrator/scheduler | T1 team | CEO | T4 | all |
| Worker + yield | T2 team | Aditya | T3, T1 | all |
| Isolation | T3 team | Aditya | T2 | all |
| Trust + rewards | T4 team | Ethan | T1, T2 | all |
| Dashboard + demo | T5 team | Praj | T1, T2, T4 | all |
| Integration + QA gates | COS | CEO | all leads | CEO |
| Final demo run-of-show | Praj + COS | CEO | all | team |

---

## 8. How orders flow (one example)

> **CEO →** *"Make the end-to-end slice real and keep it green."*
> **COS →** freezes contracts; tasks **T1** (skeleton) and **T2** (agent v0) in parallel via `/fleet`;
> as each unit lands, the lead runs **G1** (code-review + rubber-duck); COS runs **G2** (full slice) and
> reports up: **DONE** (worker registers→runs→returns→credited, demo: `uv run …`) · **DoD** (✅×6) ·
> **RISKS** (LAN reachability untested on real switch) · **NEXT** (Phase 2 yield) · **ASKS** (confirm demo PCs).

---

## 9. Status log (live)

**2026-06-23 — overnight COS run (ReeveOS):**
- **Isolation is now real on the demo path (T3 + Phase D).** `WorkerAgent(isolated=True)` routes fan-out / AI / instant-yield through `run_in_isolation`: Docker-per-job with a unique `--name`, `should_yield` polling, and **sub-second container kill-on-yield** (`docker kill`/`rm -f`); Windows-safe payload staging into `%TEMP%` (no fragile OneDrive-space mount); daemon-real `docker_available()` + honest `active_boundary()` + WARNING-on-fallback. Clean fallback to subprocess + Windows Job Object when Docker is unavailable. The cheater stays in-process so the blacklist beat still fires.
- **Dashboard rebranded to ReeveOS (T5).** Premium, self-contained, offline single-file console; verified via Playwright renders of all four demo states (idle / fan-out / yield / ledger); idle-state polish so registered non-busy workers read as Idle. Live heartbeat pulse in the demo for lively CPU/GPU tiles.
- **Quality:** `uv run pytest` → 62 passed, 2 daemon-guarded skips; `uv run ruff check` clean. `uv run python scripts/demo.py` → all 7 beats green, instant yield ~20–30 ms.
- **RESOLVED — Docker engine + live verification (2026-06-23 ~11:20).** The engine was wedged overnight (HTTP 500 → hung pipe; WSL restart + a forced full restart didn't heal it); the CEO refreshed Docker Desktop (server **29.5.3**). The live container path is now **verified**: `tests/isolation/test_docker_integration.py` passes (real CPU job inside a container + sub-second container kill); `uv run pytest` → **64 passed, 0 skipped**; `uv run python scripts/demo.py` runs on **`isolation: docker`** with a **genuine isolation proof** (a container reading `C:\Users\…` → `FileNotFoundError`, i.e. zero host-filesystem access) and a **619 ms container-kill yield** (sub-second — the subprocess+Job-Object fallback yields in ~20–30 ms). All 7 demo beats green on the real Docker-per-job boundary.
- **CEO 4-task batch (2026-06-23 PM) — all complete.** (1) **Docker money-shot kill optimized** — single `docker rm -f` (was `kill`+`rm`): instant-yield 619→506 ms. (2) **LAN-deployable orchestrator** — `python -m orchestrator` (CLI/env host/port/db, persistent file SQLite, LAN banner with each NIC's dashboard URL + worker command, `/healthz`); G2-verified live with a real `python -m worker` registering→completing→credited across a restart. (3) **GPU host-side path** — `render` jobs (`needs_gpu`) route host-side under a Job Object (never a container) and run real CUDA via `cupy` + `pynvml` util, else an HONEST `cpu-fallback`; clean G1 review. (4) **Rehearsed twice-green** on the Docker boundary (all 7 beats + GPU). `uv run pytest` → **87 passed**; ruff clean.
- **Load-router coverage (for the record):** CPU (`min_cpus`) ✅, RAM (`min_ram_gb` on live free RAM) ✅, GPU (routing + host-side execution + 5× weight) ✅. **NPU is roadmap — not built** (no detection/routing; §13 cuts it).

**2026-06-23 — dashboard-readiness + fleet workloads (branch `katkuri`, 144 tests green):**
- **Device-code dashboard APPROVAL gate (admission/onboarding step).** Orchestrator `--require-approval` / `create_app(require_approval=True)`: a worker registers **PENDING** with a generated **device code** and waits; an admin admits it via **`POST /workers/{id}/approve`** (the dashboard shows the code + an Approve action). `GET /jobs/next` is gated until approved, and heartbeats carry the live `approved` flag so `--once` never hangs. **Additive to the frozen §3 contracts** — un-gated (default) workers auto-approve and carry no device code.
- **FOUR example workloads fanned across the fleet via a hardcoded split (one tile per machine).** `fractal` (Mandelbrot band per machine → reassembled PNG), `optimize` (distributed param-sweep), `ai.batch_infer` (model inference), `ai.synth` (synthetic data). New **stdlib** job kinds + `build_*_jobs` builders; the two AI kinds route **host-side** (real SDK/key; disclosed deterministic fallback without a key). Split lives in `src/workloads/partition.py`. See [`workloads.md`](./workloads.md).
- **Dashboard-readiness API (so a front-end integrates easily).** `GET /jobs/{id}`, `POST /workloads` + `GET /workloads/{id}` (launch a whole workload + read per-tile outputs), `GET /workloads/catalog`; jobs carry a shared `workload_id` for grouping. See [`dashboard-api.md`](./dashboard-api.md).
- **Live per-device usage stream.** Worker pushes cpu/gpu/free-RAM every ~1 s (`--usage-interval`) so `/state` feeds a dashboard usage graph.
- **Demo + docs.** `scripts/demo_fleet.py` (local simulated 3-machine variety demo: approval + 4 beats + instant-yield); `scripts/submit_jobs.py` extended (`--kind fractal|optimize|ai|synth`); [`demo-runbook.md`](./demo-runbook.md), [`workloads.md`](./workloads.md), [`dashboard-api.md`](./dashboard-api.md).
- **Scope note:** the dashboard **front-end UI is owned by the user** (not built here); the **backend is integration-ready** against the APIs above.
