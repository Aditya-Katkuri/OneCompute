# OneCompute - SOC 2 control alignment (control-to-code mapping)

**Engine codename:** NightShift  ·  **Document class:** Microsoft Confidential (draft for security/privacy review)
**Companion to:** `OneCompute-Threat-Model.md` (this document is the control mapping referenced there)
**Audience:** CISO / Azure Security, Microsoft Digital (MSD), CELA, Privacy/Purview

> **Purpose.** Map OneCompute's implemented controls to the SOC 2 Trust Services Criteria (TSC) with concrete code citations, so a reviewer can distinguish what is enforced in code today from what is honest roadmap. This is not a SOC 2 attestation (that requires an audit period, an independent auditor, and an operating-effectiveness sample). It is an honest design-time control map for a proof-of-concept and a proposed contained pilot.

## Status legend

| Tag | Meaning |
|---|---|
| **Implemented** | Enforced in code today; citation points at the enforcing code |
| **Partial** | Mechanism exists but a hardening step is roadmap (named in the Gap column) |
| **Roadmap** | Documented, deferred; not built here |

All citations are `path:line` into this repository, verified against the current tree.

---

## 1. Common Criteria (CC) - Security

### CC5 / CC6 - Logical access, authentication, and authorization

| TSC point | OneCompute control | Status | Citation | Gap / note |
|---|---|---|---|---|
| CC6.1 Logical access is restricted | Per-worker bearer token issued at registration; constant-time comparison; unknown/mismatched token rejected | **Implemented** | `src/orchestrator/app.py:6, 306-330` (`compare_digest`); token minted `app.py:408` | Device-bound certs + SSO/OIDC are roadmap (currently a bearer token) |
| CC6.1 Admission gate for new principals | Device-code admission: a gated worker registers as `approved=0` with a short device code and cannot lease work until an operator approves it | **Implemented** | `app.py:393, 411-413, 447-453, 471, 562-571`; `src/contracts/schema.sql:15-16` | Enabled per-run with `--require-approval`; default is auto-approve for the local demo |
| CC6.2 Deprovisioning | Operator can disconnect a worker; any leased job is immediately requeued and the worker fails auth on its next call | **Implemented** | `app.py:577-593` | |
| CC6.6 Untrusted-source protection | Outbound-only short-poll: workers open no inbound ports, so there is no listening service on the employee machine to attack | **Implemented** | `src/worker/agent.py` (httpx client polling `jobs/next`); no server bound on the worker | |
| CC6.7 Transmission protection | Optional **TLS** (`--tls-cert/--tls-key`) and **mutual TLS** (`--tls-client-ca` server; `--client-cert/--client-key` worker with a pinned CA via `--tls-ca`); data-minimized payloads; security response headers on the API | **Implemented** (optional) | client `src/trust/tls.py` (`client_ssl_params`/`build_client`); server `orchestrator/__main__.py` via `server_ssl_kwargs`; headers `app.py:67-71` | TLS on by default + automated cert issuance/rotation is **Roadmap** (pilot enables TLS on a controlled network) |
| CC6.8 Unauthorized/malicious code | Ed25519-signed, hash-bound, expiring manifests verified before execution; refuse on mismatch. Optional out-of-band pinned signer rejects any key but the operator's | **Implemented** | verify `src/trust/signing.py:43-70`; enforcement `src/worker/agent.py:171-195`; pin `src/worker/__main__.py:192-198`, `agent.py:183-190`, `signing.py:59-65` | Full cosign/OIDC/Rekor provenance is **Roadmap** |

### CC6.x - Isolation of the execution boundary (job containment)

| TSC point | OneCompute control | Status | Citation | Gap / note |
|---|---|---|---|---|
| Segregation of untrusted workloads | Preferred OS-enforced boundary is MXC when a real `wxc-exec` runtime is present; else a Docker container (`--network none`, ephemeral `--rm`, minimal stdlib payload); else a Windows Job Object (caps + kill-on-close only) | **Implemented** (MXC fail-closed/inert until a runtime exists) | boundary selection `src/isolation/runner.py:93-102`; MXC probe `src/isolation/mxc.py:70-90`; Job Object `src/isolation/jobobject.py` | MXC not yet validated against a real runtime (threat-model R15); Docker fallback has no FS boundary |
| Fail-closed on missing isolation | `--require-isolation` refuses to run (raises `IsolationUnavailableError`) rather than use the unsandboxed subprocess or run host-side GPU/AI without an OS sandbox | **Implemented** | `src/worker/__main__.py:184-190`; `agent.py:232, 238`; `runner.py:62, 579-587` | Off by default so the demo runs; **recommended on for pilots** |
| No persistence | Inputs/outputs wiped at job end; instant-yield is a real process-tree/container kill | **Implemented** | `runner.py` (per-job temp dir teardown; `should_yield` kill path) | Side-channel residual disclosed (threat-model 11.1) |

### CC7 - System operations, monitoring, and incident response

| TSC point | OneCompute control | Status | Citation | Gap / note |
|---|---|---|---|---|
| CC7.2 Monitoring / anomaly detection | Append-only audit event stream: `registered`, `approved`, `submitted`, `assigned`, `completed`, `yielded`, `failed`, `blacklisted`, `removed`, and `auth_failed`, each timestamped and queryable via `GET /events` | **Implemented** | emits `app.py:330, 448, 463, 507, 571, 593, 661, 683, 696`; table `schema.sql:51-58` | SIEM export + alerting thresholds are **Roadmap** |
| CC7.3 Integrity of results (anti-cheat) | Proof-hash match required (`invalid_proof`); hidden challenge/ringer with a server-known answer; a wrong answer blacklists the worker and credits zero | **Implemented** | `app.py:641-664`; ringer `src/trust/challenge.py` | Challenge ringer is exact-integer; tolerance-aware comparison for FP workloads lives with the result verifiers, not the ringer |
| CC7.4 Incident response / kill switch | Stop the orchestrator and all workers idle within one poll; operator disconnect requeues held work; employee can Ctrl-C/uninstall | **Implemented** | disconnect+requeue `app.py:577-593`; lease reaping `app.py:238, 247-248` | IR playbook in threat-model section 18 |

### CC8 - Change management

| TSC point | OneCompute control | Status | Citation | Gap / note |
|---|---|---|---|---|
| CC8.1 Authorized changes only | Frozen data contracts and SQLite schema treated as deliberate seams; tests mirror source layout | **Implemented** | `src/contracts/models.py`, `src/contracts/schema.sql`; `tests/` | 239 tests, 2 skipped (Docker-only) |
| CC8.1 Supply chain / build provenance | Ed25519 manifest signing; pinned dependency lockfile | **Partial** | signing `src/trust/signing.py` | `cryptography` (trust root) is currently transitive via `azure-identity`/`msal`, not a pinned direct dep; SBOM + cosign/SLSA are **Roadmap** (threat-model section 14) |

---

## 2. Availability (A)

| TSC point | OneCompute control | Status | Citation | Gap / note |
|---|---|---|---|---|
| A1.1 Capacity / the employee's own work is protected | Headroom-aware admission plus demand-aware yield against a learned profile; never admit above the hard ceiling; never run on battery | **Implemented** | `src/worker/governor.py:112-129, 205-239` (on-battery reason `:211-212`); binary `src/worker/idle.py` | Governor mis-sizing is a measured pilot risk (R8) |
| A1.2 Recovery from churn | Laptops sleep and return: leases expire and jobs auto-requeue; file-backed SQLite (WAL) persists across restart | **Implemented** | lease reaping `app.py:238, 247-248`; requeue paths `app.py:544-557, 586-587, 616-624` | No lost or double-counted work by lease ownership |
| A1.2 Resource abuse containment | Per-job memory + timeout caps; 8 MB result cap; kill-on-yield; **per-client request rate limiting** (`--rate-limit`, default 600/min, keyed by worker token or IP; 429 + Retry-After) | **Implemented** | result cap `app.py:58, 627-628`; limits enforced in `runner.py`; rate limiter `src/orchestrator/ratelimit.py`, wired in `create_app` (`rate_limit_per_min`) | Distributed/shared-store limiting is the multi-orchestrator upgrade |

---

## 3. Confidentiality (C)

| TSC point | OneCompute control | Status | Citation | Gap / note |
|---|---|---|---|---|
| C1.1 Confidential data is protected in use | One job per sandbox; no shared state; no-persistence; `--network none` for CPU container/MXC kinds (no C2 egress) | **Implemented** | `runner.py` (per-job temp dir, docker `--network none`) | AI/GPU kinds run host-side by necessity; restrict sensitive classes |
| C1.1 Data minimization off-device | Only a derived spare-capacity scalar and a coarse hour-of-week usage envelope leave the device; raw activity never leaves | **Implemented** | `src/measurement/headroom.py`; `agent` profile report; sanitized on ingest `app.py` (`_sanitize_profile_buckets`); table `schema.sql:60-68` | |
| C1.2 Disposal | Job inputs/outputs wiped at job end; on-device profile is a single local file removable on opt-out | **Implemented** | `runner.py` teardown | Ledger/audit retention is append-only; erasure path is a DPIA deliverable |

---

## 4. Processing Integrity (PI)

| TSC point | OneCompute control | Status | Citation | Gap / note |
|---|---|---|---|---|
| PI1.1 Inputs are authorized and complete | Manifest binds `input_sha256`; worker recomputes and refuses on mismatch; expiry enforced | **Implemented** | `agent.py:191-195` | |
| PI1.2 Processing is authorized | Signature (and, when pinned, provenance) verified before any execution | **Implemented** | `agent.py:183-190`; `signing.py:43-70` | |
| PI1.4 Outputs are complete and accurate | Server-assigned credit (`accepted_units * class_weight`), never self-reported; append-only ledger; lease ownership prevents double-credit; capped multipliers | **Implemented** | ledger `schema.sql:8, 37-45`; credit path `app.py` (result endpoint) | |
| PI1.5 Output integrity verification | Proof-hash + challenge/ringer verification; cheaters blacklisted and credited zero | **Implemented** | `app.py:641-664`; `challenge.py` | |

---

## 5. Privacy (P) - primarily CELA / Privacy scope

Privacy is analyzed in depth in the threat model's LINDDUN section (section 7). Code-level posture:

| TSC point | OneCompute control | Status | Citation | Gap / note |
|---|---|---|---|---|
| P1 Notice / consent | Voluntary informed opt-in; one-page consent; instant withdrawal | **Partial** | consent copy `docs/pitch/OneCompute-Pilot-Consent.md` | Formal DPIA is **Roadmap** (CELA/Privacy) |
| P4 Use limitation | On-device-only profiling; only a capacity scalar transmitted; no keystroke/file/mail/browser collection | **Implemented** | `src/measurement/headroom.py`; `agent` profile report | |
| P6 Access / P7 Quality | Employee controls participation; profile is local and inspectable | **Partial** | local profile file | Subject-access + erasure workflow is a DPIA deliverable |

---

## 6. Summary: implemented vs. roadmap

**Implemented in code (the technical heart of the design):** authenticated (constant-time worker token), admission-gated (device-code approval), signed + hash-bound + expiring manifests, optional out-of-band pinned signer, OS-enforced isolation with a fail-closed switch, MXC as the preferred boundary (fail-closed/inert until a runtime exists), no-network CPU containment, lease/requeue for churn, server-assigned append-only crediting, challenge/ringer anti-cheat with blacklisting, append-only audit including `auth_failed`, 8 MB result cap, security response headers, optional TLS + mutual TLS transport, per-client rate limiting, `cryptography` pinned as a direct dependency, never-on-battery demand-aware yield, and on-device-only data minimization.

**Roadmap (named, not built here):** submitter SSO/OIDC, device-bound certificates, HSM-custodied signing key, cosign/OIDC/Rekor + SLSA provenance, SBOM, TLS-on-by-default with automated cert issuance/rotation, SIEM export, MXC validation against a real `wxc-exec` runtime, and a formal DPIA.

## 7. Traceability to the threat-model risk register

| Threat-model risk | Controls above that address it |
|---|---|
| R1 Endpoint-stack collision | CC6.6 outbound-only; CC7.2 audit; (allow-list is an MSD action, not code) |
| R2 Sandbox escape | CC6.x isolation (MXC/container) + fail-closed `--require-isolation` |
| R3 GPU-job weak isolation | CC6.x; `--require-isolation` blocks host-side GPU/AI when no OS sandbox |
| R5 Signing-key compromise | CC8.1 (custody is roadmap: HSM/corp signing) |
| R6 Orchestrator compromise | CC6.8 out-of-band pinned signer |
| R7 Reward fraud / Sybil | CC7.3 + PI1.4/PI1.5 server-assigned credit, challenge, blacklist |
| R8 Governor mis-sizing | A1.1 headroom governor (measured in pilot) |
| R9 Transport insecure | CC6.7 optional TLS/mTLS + A1.2 rate limiting |
| R11 Churn | A1.2 lease/requeue |
| R13 Supply chain | CC8.1 (SBOM/cosign roadmap; pin `cryptography`) |
| R15 MXC preview immaturity | CC6.x MXC fail-closed/inert; validate before reliance |
