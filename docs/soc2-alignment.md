# OneCompute — SOC 2 Trust Services Criteria Alignment

> **Purpose.** A control-mapping self-assessment for the OneCompute proof-of-concept, written for
> the stakeholder deck. It shows how the system aligns with each broad SOC 2 category and is
> **honest about PoC scope vs. production roadmap** — we map controls, we do not claim certification.
>
> **What SOC 2 is.** An AICPA attestation against the **Trust Services Criteria (TSC)**. There are
> five categories: **Security** (the mandatory "Common Criteria," CC1–CC9) plus four optional ones —
> **Availability**, **Processing Integrity**, **Confidentiality**, and **Privacy**. A real audit
> covers a *service organization's* people, process, and technology over a time window; this PoC
> demonstrates the **technical control posture** an audit would build on.

**Brand:** OneCompute (engine codename: NightShift).

## Legend
- ✅ **Implemented** — enforced in the PoC code today, with a test and/or live demo beat.
- 🟡 **Partial** — a real control exists but is narrower than a production deployment needs.
- 🗺️ **Roadmap** — intentionally deferred (documented, not hidden). Many are the §13 cut-list.

## One-slide posture summary
OneCompute is **internal-only and opt-in**, which raises the trust baseline before any control runs.
On top of that the PoC enforces **mutually-authenticated, signed, sandboxed, verified** work:
signed job manifests, per-worker token auth, Docker-per-job isolation with no network and no
persistence, hidden challenge tasks that catch cheaters, and a server-authoritative append-only
rewards ledger. Transport TLS, enterprise SSO, confidential compute (TEE), and formal org-level
process controls are the honest roadmap to audit-readiness.

---

## 1. Security — Common Criteria (CC1–CC9) · *mandatory*

| Criterion (intent) | OneCompute control | Status | Evidence |
|---|---|---|---|
| **CC1** Control environment (governance, roles) | Documented chain of command, ownership, and a Definition of Done with mandatory review gates (G0–G3). Org-level HR/policy controls are out of scope for a PoC. | 🟡 | `.github/copilot-instructions.md`, `docs/execution-plan.md` |
| **CC2** Communication & information | Architecture, threat model, and risk register are written down and versioned. | ✅ | `docs/idea.md` §8/§10, `docs/architecture.md` |
| **CC3** Risk assessment | Ranked risk register with owners and mitigations (cryptojacking false-positive, verification cost, transport, Sybil/inflation, GPU isolation gap). | ✅ | `docs/idea.md` §10, `docs/execution-plan.md` §6 |
| **CC4** Monitoring | Append-only **audit event stream** (`events` table) records register/submit/assign/complete/yield/fail/blacklist/**auth_failed**, each timestamped and queryable via `GET /events`. Live dashboard surfaces fleet + ledger state. | ✅ | `src/contracts/schema.sql` (`events`), `src/orchestrator/app.py` (`_emit`, `/events`) |
| **CC5** Control activities | Controls are enforced in code on the request path (auth, signature verify, lease ownership, payload limits), not by convention. | ✅ | `src/orchestrator/app.py`, `src/worker/agent.py` |
| **CC6** Logical & physical access | **Per-worker bearer-token auth** (issued at `/register`, required + constant-time-checked on `/jobs/next`, `/heartbeat`, `/results`); **lease-owner authorization** (only the assigned worker can return a job); **Ed25519-signed manifests** verified by the worker before execution; **least-privilege transport** (workers pull outbound-only, no inbound ports). Physical access = the employee's own opt-in device. | ✅ | `app.py` (token check, `not_leased`), `src/trust/signing.py`, `src/worker/agent.py` (`_verify_assignment`) |
| **CC6.7** Transmission protection | Security response headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, strict `Content-Security-Policy`). **TLS in transit is roadmap** (PoC runs on a trusted isolated LAN). | 🟡 | `app.py` security-headers middleware |
| **CC6.8** Unauthorized change prevention | Worker verifies **code hash + input hash + signature** and **refuses on a flipped byte**; **manifest expiry** enforced (expired manifest refused). | ✅ | `src/worker/agent.py` `_verify_assignment`, `src/contracts/models.py` (`code_sha256`/`input_sha256`/`expires_at`) |
| **CC7** System operations (detect/respond) | Health probe (`/healthz`); lease timeouts auto-requeue stalled work; **challenge/ringer tasks** detect bad results → **blacklist + points forfeited**; auth failures audited. | ✅ | `app.py` (`/healthz`, `reap_expired`, challenge path), `src/trust/challenge.py` |
| **CC8** Change management | Frozen, typed contracts gate cross-team changes; tests + lint required green before merge. Formal ticketed change-control is an org roadmap item. | 🟡 | `src/contracts/models.py` (FROZEN), `uv run pytest` / `uv run ruff check` |
| **CC9** Risk mitigation (incl. vendors) | Server-authoritative metering and capped multipliers blunt reward-gaming; isolation blunts a malicious job. Vendor/insurance controls are org-level roadmap. | 🟡 | `src/trust/metering.py`, `src/isolation/` |

---

## 2. Availability (A1)

| Criterion | OneCompute control | Status | Evidence |
|---|---|---|---|
| A1.1 Capacity & resilience | Naturally geo-distributed fleet; **lease + timeout + auto-requeue** so a dropped/slept worker's job re-runs elsewhere; per-job resource limits (`cpu_pct`, `mem_gb`, `timeout_s`). | ✅ | `app.py` `reap_expired`, `src/contracts/models.py` `Limits` |
| A1.1 Abuse / exhaustion guard | **Result payload size cap (256 KB)** rejects oversized output (`payload_too_large`). | ✅ | `app.py` `/results` |
| A1.2 Monitoring & durability | `/healthz` reachability probe; **file-backed SQLite (WAL)** persists queue/ledger across restart (verified live). | ✅ | `src/orchestrator/db.py`, `tests/orchestrator/test_persistence.py` |
| A1.2 Employee-machine availability | **Instant-yield / demand-adaptive governor** — OneCompute backs off sub-second so it never degrades the employee's own work. | ✅ | `src/worker/` governor + yield; demo beat #4 |
| A1.3 BCP / DR, multi-orchestrator | Single-orchestrator PoC. HA orchestrator + formal DR are roadmap. | 🗺️ | — |

---

## 3. Processing Integrity (PI1)

| Criterion | OneCompute control | Status | Evidence |
|---|---|---|---|
| PI1.1 Inputs valid & authorized | All API inputs are **typed/validated pydantic models**; signed manifests bind the exact code + input. | ✅ | `src/contracts/models.py`, `src/trust/signing.py` |
| PI1.2 Processing complete & accurate | **Result proof hash** must match the output (`invalid_proof` rejection); **lease ownership** prevents double-credit; **append-only ledger**. | ✅ | `app.py` `/results`, `src/contracts/schema.sql` (`ledger`) |
| PI1.3 Output verified, not trusted | **Hidden challenge/ringer tasks** with server-known answers spot-check results; a wrong answer ⇒ **blacklist + zero credit**. Credit is **server-assigned** (`accepted_units × class_weight`), never the worker's self-reported TOPS. | ✅ | `src/trust/challenge.py`, `src/trust/metering.py`, `app.py` |
| PI1 Tolerance-aware / replicated verification | PoC uses one deterministic challenge task. Adaptive replication / tolerance-aware comparators are roadmap. | 🗺️ | `docs/idea.md` §8 |

---

## 4. Confidentiality (C1)

| Criterion | OneCompute control | Status | Evidence |
|---|---|---|---|
| C1.1 Protect data in use (job ↔ worker) | **Docker-per-job isolation**: `--network none`, **read-only** staged payload, `--rm`; clean fallback to a restricted subprocess under a **Windows Job Object**. The worker cannot read the job's internals; the job cannot read the host. | ✅ | `src/isolation/docker.py`, `src/isolation/runner.py`, `src/isolation/jobobject.py`; demo beat #6 |
| C1.1 Data minimization | Only what a shard needs is sent to a worker; capability advertisements carry no employee data. | ✅ | `src/contracts/models.py` `Capability` |
| C1.2 Disposal / no residue | **No persistence** — container + staged inputs/outputs are destroyed when the job ends; instant-yield is a real process-tree/container **kill**, not a pause. | ✅ | `src/isolation/` (`docker rm -f`, Job-Object close) |
| C1 Confidential compute (TEE) | Hardware TEE for the most sensitive workloads needs datacenter GPUs; consumer/Copilot+ silicon has none. Roadmap. | 🗺️ | `docs/idea.md` §8 |

---

## 5. Privacy (P1–P8)

| Criterion | OneCompute control | Status | Evidence |
|---|---|---|---|
| P1 Notice & consent | **Opt-in by design**; the employee keeps caps, schedules, "never on battery," and instant opt-out. | ✅ | `docs/idea.md` §5/§7 |
| P3/P4 Collection & use limitation | **On-device profiling only** — the usage profile that sizes spare-capacity is computed and stored **locally**; only a derived spare-capacity number ever leaves the machine. No raw keystroke/activity telemetry. | ✅ | `docs/idea.md` §5/§8; `src/worker/profiler.py` (local store) |
| P5 Access / individual control | Employee controls participation and can withdraw instantly; rewards are transparent and metered on verified work. | ✅ | `docs/idea.md` §7 |
| P6 Disclosure to third parties | Internal-only by design; no job or worker data leaves the company boundary. | ✅ | architecture (internal LAN topology) |
| P7/P8 Quality, monitoring, enforcement | Audit event stream + honest reward metering; formal DPIA / privacy-program governance is org roadmap. | 🟡 | `events` table |

---

## Scorecard (for the slide)

| Category | Implemented | Partial | Roadmap |
|---|---|---|---|
| **Security (CC1–CC9)** | CC2, CC3, CC4, CC5, CC6, CC6.8, CC7 | CC1, CC6.7, CC8, CC9 | TLS, SSO/OIDC, cosign |
| **Availability** | leases/requeue, payload cap, persistence, instant-yield | — | HA orchestrator, DR |
| **Processing Integrity** | proof hash, challenge, server metering, ledger | — | adaptive replication |
| **Confidentiality** | sandbox, no-network, no-persistence, minimization | — | TEE |
| **Privacy** | opt-in, on-device profiling, individual control | governance | DPIA/program |

## Honest roadmap to audit-readiness (the credible "next")
1. **TLS everywhere** + mTLS between worker and orchestrator (CC6.7).
2. **Enterprise identity**: device-bound registration + SSO/OIDC for workers *and* submitters (CC6.1/CC6.2).
3. **Supply-chain signing**: cosign/OIDC/Rekor in place of local Ed25519 (CC8/CC6.8).
4. **Enterprise endpoint posture**: signed agent, Intune deployment, Defender/Purview allow-listing (CC6/CC9) — the real enterprise-acceptance gate.
5. **Confidential compute (TEE)** for the most sensitive workloads (C1).
6. **Org program**: formal policies, risk assessments, vendor management, BCP/DR, ticketed change control, and continuous-monitoring evidence over a 6–12 month window (CC1/CC3/CC8/A1) — what an actual Type II audit attests.

> **Bottom line for the deck:** the PoC already demonstrates the *technical* heart of SOC 2 —
> authenticated, signed, sandboxed, verified, audited compute with server-authoritative rewards —
> and we are transparent that transport security, enterprise identity, and organizational process
> controls are the roadmap to a formal attestation.
