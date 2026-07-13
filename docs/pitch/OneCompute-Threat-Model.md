# OneCompute - Threat Model and Security/Privacy Risk Assessment

**Engine codename:** NightShift  ·  **Document class:** Microsoft Confidential (draft for security/privacy review)
**Audience:** CISO and Azure Security, Microsoft Digital (MSD), CELA, Privacy/Purview, HR
**Scope of this revision:** the proof-of-concept (PoC) as built, and the proposed contained pilot. It is explicit about what is enforced in code today versus what is honest roadmap. v1.1 synced the document to three merged controls (MXC OS-enforced backend, `--require-isolation` fail-closed switch, `--trusted-key` out-of-band pinned signer) and expanded the side-channel, dependency supply-chain, and data-erasure coverage. v1.2 adds the shipped transport hardening: optional TLS + mutual TLS and per-client rate limiting. v1.3 adds submitter authentication: an optional operator token that gates job/workload submission. v1.4 adds two supply-chain / isolation-assurance controls: a generated CycloneDX SBOM and an MXC launch-path validation harness. v1.5 adds a signed SLSA v1 build-provenance attestation and a hash-chained, tamper-evident, SIEM-exportable audit log. v1.6 adds optional device-identity binding (worker token tied to its TLS cert fingerprint) and a Sigstore cosign signing integration, alongside two capability items (NPU detection/advertisement and capability-weighted dynamic partitioning).

> **One-paragraph summary.** OneCompute runs an opt-in agent on employee machines that harvests spare CPU/GPU headroom for cloud-substitutable batch work and yields the machine back to the employee in under a second. It is internal-only, opt-in, sandboxed, signed, verified, and audited. This document models the threats across every trust boundary, scores the residual risk honestly, maps each control to the teams that must accept it, and proposes a small, reversible, time-boxed pilot as the next safe step. It does not request enterprise rollout.

---

## 0. Document control

| Field | Value |
|---|---|
| Version | 1.6 (draft) |
| Status | For review; not yet socialized with CELA/MSD/CISO |
| Owner | Colin Finney (intern) + sponsor TBD |
| Methodologies | Microsoft SDL threat modeling, STRIDE (per boundary), LINDDUN (privacy), MITRE ATT&CK mapping, qualitative likelihood x impact risk scoring |
| Framework crosswalk | SOC 2 TSC (see `soc2-alignment.md`), NIST CSF, NIST 800-53 control families, CIS Controls, OWASP ASVS (control concepts) |
| Related docs | `idea.md` (concept), `architecture.md` (design), `mxc-sandbox.md` (MXC backend design + preview caveats), `mxc-validation.md` (MXC launch-path harness), `supply-chain.md` (SBOM + signed provenance), `cosign.md` (Sigstore signing), `audit-log.md` (tamper-evident audit + SIEM export), `device-identity.md` (device-bound worker identity), `npu-harvesting.md`, `partitioning.md`, `soc2-alignment.md` (control-to-code mapping), `pilot-security-approval.md` (sanction runbook), `pilot-plan.md` (pilot operations), `OneCompute-Risk-Memo.docx` |

**Revision history**

| Ver | Date | Change |
|---|---|---|
| 0.1 | initial | Trust boundaries + STRIDE summary |
| 1.0 | prior rev | Full enterprise-grade rewrite: data inventory, LINDDUN, ATT&CK, abuse cases, scored risk register, endpoint integration, supply chain, safety, legal/HR, IR, per-team Q&A |
| 1.1 | prior rev | Truth-sync to shipped controls: MXC OS-enforced backend as preferred boundary (fail-closed, inert until a real runtime exists), `--require-isolation` fail-closed switch, `--trusted-key` out-of-band pinned signer. Reconciled risk register (R2/R3/R6 residuals lowered; new R15 for MXC preview immaturity), expanded side-channel (11.1), dependency supply-chain, and ledger-erasure coverage, and authored the three companion docs (`soc2-alignment.md`, `pilot-security-approval.md`, `pilot-plan.md`) |
| 1.2 | prior rev | Transport hardening shipped: optional TLS + mutual TLS (orchestrator `--tls-cert/--tls-key/--tls-client-ca`; worker `--tls-ca/--client-cert/--client-key`) and per-client rate limiting (`--rate-limit`). Updated B3 Tampering/Info-disclosure/DoS rows, the crypto + network sections, R9 (residual lowered), and the CISO Q&A |
| 1.3 | prior rev | Submitter authentication shipped: optional operator `--submit-token` gates job/workload submission (`Authorization: Bearer`, constant-time, audited). Updated B4 Spoofing/Tampering/EoP rows, abuse-case 1 (fleet-as-botnet), and the traceability matrix |
| 1.4 | this rev | Access-control + reward-integrity fixes: the `approve`/`disconnect` admin endpoints now require the operator token (`--submit-token`), closing a B3 self-admit bypass where a pending worker could approve itself and lease real work; credit is now metered on the JOB's GPU requirement from the signed manifest, not the worker's self-reported `has_gpu`, closing a 5x credit-inflation path. Updated the credit-integrity control, the mitigations matrix, and `architecture.md` §4.1/§9 |
| 1.4 | prior rev | Supply-chain + isolation assurance: a generated **CycloneDX SBOM** (`scripts/generate_sbom.py`, `supply-chain.md`) and an **MXC launch-path validation harness** (stub `wxc-exec` driving real `_run_mxc`, `mxc-validation.md`). Updated abuse-case 6, section 11 + section 14, R13 (residual lowered) and R15 (wiring proven), the residual summary, the CISO Q&A, and the traceability matrix; fixed a stale note that still listed the `cryptography` pin as an open gap |
| 1.5 | prior rev | Two more controls: a **signed SLSA v1 build-provenance attestation** (`scripts/generate_provenance.py`, STRIDE Tampering) and a **hash-chained tamper-evident audit log** with verify + JSONL SIEM export (`GET /events/verify`, `GET /events/export`, STRIDE Repudiation, Microsoft Sentinel). Updated the B1/B3 Repudiation rows, sections 14 and 15, R13, and the traceability matrix |
| 1.6 | this rev | Four workstreams: **device-identity binding** (`--bind-device-identity`, B3 Spoofing, Intune/Entra), **Sigstore cosign integration** (`src/trust/cosign.py`, Tampering/SDL, inert-when-absent), plus two capability items -- **NPU detection/advertisement** (Copilot+/DirectML, `docs/npu-harvesting.md`) and **capability-weighted dynamic partitioning** (`docs/partitioning.md`). Updated the B3 Spoofing row, section 14, the CISO Q&A, the traceability matrix, and Related docs |

**Sign-off (to be completed during review)**

| Reviewer / team | Decision | Conditions | Date |
|---|---|---|---|
| MSD (endpoint owner) | | | |
| CISO / Azure Security | | | |
| CELA (legal) | | | |
| Privacy / Purview | | | |
| HR (rewards) | | | |
| Risk acceptance owner | | | |

---

## 1. Purpose and scope

**Purpose.** Give reviewers a complete, honest basis to (a) understand what OneCompute does on an employee machine, (b) see every threat we have considered and how it is mitigated or accepted, and (c) bound a pilot whose blast radius they can accept.

**In scope:** the worker agent, the orchestrator (queue, scheduler, verifier, ledger), job manifests and payloads, the rewards ledger, the on-device usage profile, the control-plane API, and the agent's interaction with the corporate endpoint stack.

**Out of scope (today, with rationale):**
- Live deployment to Intune-managed corp machines. This document is the artifact that requests that review; until sanctioned we run only on controlled/loaner machines.
- Frontier model training (needs co-located datacenter GPUs; not a target workload).
- External or consumer participation (internal-only by design).
- Cross-machine model sharding, NPU harvesting, TEE-backed execution (roadmap).

**Assumptions.** (1) The corporate network, identity provider, and endpoint stack are trustworthy and available. (2) The orchestrator runs on a hardened, access-controlled host. (3) Employees are corporate-identity holders on managed devices. (4) Reviewers will scope a pilot, not approve blanket rollout. Each assumption that fails becomes a risk in section 18.

---

## 2. System description

### 2.1 Components
- **Worker agent** (employee PC): a lightweight user-space Python process (`python -m worker`, no admin install and no kernel driver; a code-signed `.exe` is the packaging roadmap). It registers, advertises spare capability, detects headroom against a learned profile, pulls jobs (outbound-only), runs them sandboxed, returns results, and yields instantly on employee demand.
- **Orchestrator** (hardened host): device-code admission gate, job queue, scheduler, verifier (challenge/replication), append-only ledger and audit event stream, control-plane HTTP API.
- **Submitter**: an internal team that queues a job; the orchestrator binds the input hash and Ed25519-signs the manifest (code is a built-in registered adapter, not shipped code).
- **Rewards/metering**: server-authoritative, append-only, credits only verified work.

### 2.2 Data-flow diagram (numbered flows, each with its control)
```
[Submitter] --(F1 job)--> [Orchestrator]  (binds input hash + limits, Ed25519-signs the manifest)
[Orchestrator] --(F2 device-code admission)--> [Worker]      (PENDING until admin approves)
[Worker] --(F3 register + capability advert; bearer token issued)--> [Orchestrator]
[Worker] --(F4 outbound short-poll: jobs/next)--> [Orchestrator] --(F5 lease + signed manifest)--> [Worker]
[Worker] verify(signature, input hash, expiry; optional out-of-band pinned signer via --trusted-key) -> CPU kinds: MXC container when a real runtime is present, else Docker container (--network none, ephemeral --rm, minimal payload), else Job-Object fallback; AI/GPU kinds: host-side subprocess+JobObject (host network). With --require-isolation the worker fails CLOSED (refuses the job) rather than take any non-OS-enforced fallback.
[Worker] --(F6 result + proof hash, <=8MB)--> [Orchestrator] verify(proof, lease owner, challenge) -> ledger credit
[Worker] --(F7 heartbeat / yield events)--> [Orchestrator] --(F8 audit events, /events)--> [Operator dashboard]
On-device only: usage profile (F0) never leaves; only a derived spare-capacity number rides F3.
```
Per-flow controls: F0 local-only; F1/F5 Ed25519 signature + hash binding + expiry; F2 admin approval; F3/F4/F6/F7 per-worker bearer token (constant-time) + lease ownership; F6 proof hash + payload cap; F8 append-only audit.

### 2.3 Trust boundaries
| ID | Boundary | Why it matters | Primary reviewer |
|---|---|---|---|
| **B1** | Employee device <-> Corp | an internal agent runs on a managed endpoint; must not weaken endpoint posture | MSD, CISO |
| **B2** | Worker host <-> Job code | untrusted submitter code executes on the employee's machine | CISO, employee |
| **B3** | Worker <-> Orchestrator | control plane; auth, integrity, replay | CISO / Azure Security |
| **B4** | Submitter <-> Orchestrator | who may submit, at what privilege, with what data | CISO, CELA |
| **B5** | Employee <-> System (their data) | profiling, monitoring, consent | CELA, Privacy, HR |

---

## 3. Data inventory and classification (for CELA / Privacy)

| Data | Where | Classification | Leaves device? | Retention | Control |
|---|---|---|---|---|---|
| Usage/activity profile (sizes headroom) | on-device only | Personal / sensitive | **No** | rolling window, local | computed and stored locally; never uploaded |
| Derived spare-capacity number | device -> orchestrator | Low | Yes (aggregate only) | transient | only scalar capacity, no raw activity |
| Capability advert (CPU/GPU/RAM, has_gpu) | device -> orchestrator | Low | Yes | transient | no employee identity beyond node id |
| Job code | submitter -> worker | depends on job (up to Confidential) | Yes (to assigned worker) | wiped at job end | signed, hash-bound, sandboxed, no-persistence |
| Job input data | submitter -> worker | depends on job | Yes (data-minimized slice) | wiped at job end | data minimization; per-job sandbox; no network |
| Job result + proof hash | worker -> orchestrator | depends on job | Yes | ledger keeps metadata | <=8MB cap; verified; minimal |
| Identity / node binding | orchestrator | Personal | internal | pilot window | corp-SSO one-identity-per-node (roadmap: device-bound) |
| Rewards ledger | orchestrator | Personal (links employee to credit) | internal | retained | append-only, server-authoritative |
| Audit events | orchestrator | Operational | internal | retained | append-only, queryable |

**Data-residency / sovereignty:** all flows are internal; no third-party or cross-border egress in the pilot. A formal DPIA and records-of-processing are roadmap items we want Privacy/CELA to scope.

---

## 4. Asset register (what we protect, and the cost if it fails)

| # | Asset | Confidentiality | Integrity | Availability | Worst-case impact |
|---|---|---|---|---|---|
| A1 | Employee machine + the employee's own work | - | High | **High** | perceived slowdown kills adoption; device instability |
| A2 | Employee privacy (activity, profile) | **High** | High | - | trust/legal harm if activity data leaked |
| A3 | Job code and data in use | High | High | Med | leak of confidential workload content |
| A4 | Result integrity | - | **High** | Med | wrong outputs trusted; bad downstream decisions |
| A5 | Rewards ledger integrity | Med | **High** | Med | fraud, financial/comp dispute |
| A6 | Corporate endpoint posture | High | High | High | a new agent must not become an attack surface or blind spot |
| A7 | Orchestrator + control plane | High | High | High | compromise = fleet-wide command capability |

---

## 5. Threat actors and personas

| Actor | Motivation | Capability | In-scope concern |
|---|---|---|---|
| Cheating worker | maximize rewards | controls own machine + agent | forge results, inflate credit, Sybil |
| Malicious/buggy job | (attacker-supplied or compromised submitter) | arbitrary code inside sandbox | sandbox escape, host/data read, persistence, C2 beacon |
| Malicious submitter | misuse the fleet | can queue jobs | fleet-as-botnet, target a worker, over-collect data |
| Network/on-path attacker | intercept/alter | sees control traffic | spoof, tamper, replay manifests |
| Curious insider / co-worker | snoop | local/network access | read others' job data, read the profile |
| Compromised orchestrator | full control plane | scheduling + manifests | push malicious manifests, corrupt ledger, fleet command |
| Supply-chain attacker | implant | agent build/update or dependency | ship a backdoored agent to the fleet |
| External APT (proportionate) | espionage | advanced | pivot via the agent into the corp fleet |

---

## 6. STRIDE threat enumeration (per boundary)

Legend for status: [PoC] enforced in code today · [Roadmap] documented, deferred.

### B1 - Employee device <-> Corp
| STRIDE | Threat | Mitigation | Status | Residual |
|---|---|---|---|---|
| Tampering | agent weakens device config | no-admin user-space run; no policy changes; subprocess+Job-Object fallback | [PoC] | low |
| Repudiation | unattributable agent action | append-only, **hash-chained (tamper-evident)** audit incl. approval and auth_failed; `GET /events/verify` re-derives the chain and flags any post-hoc edit at the offending event id | [PoC] | low |
| Info-disclosure | agent reads files/mail/browser | per-job sandbox; agent has no data-collection function beyond local profile | [PoC] | low |
| DoS | agent degrades the machine | instant-yield governor vs learned profile; CPU/mem/timeout caps; never on battery | [PoC] | med (governor mis-sizing) |
| Elevation | agent used to escalate | least privilege; no admin; signed + allow-listed | [PoC]+[Roadmap signing pipeline] | med |
| Endpoint-control evasion (key concern) | looks like cryptojacking; bypasses Defender/WDAC/Purview | code-signed, Defender allow-list by publisher+SHA-256, WDAC-trusted, Purview-sanctioned egress; pass-not-bypass | [Roadmap: corp signing] | **the gating item** |

### B2 - Worker host <-> Job code
| STRIDE | Threat | Mitigation | Status | Residual |
|---|---|---|---|---|
| Tampering (host) | job writes host files | **Preferred OS-enforced boundary: MXC (Microsoft Execution Containers) when a real `wxc-exec` runtime is present** (`active_boundary()=="mxc"`), else container (CPU kinds): only a minimal stdlib payload is mounted (no host files, no repo src), `--network none`, ephemeral `--rm`; the /work mount is read-write because the job writes its output there (isolation-by-minimal-surface, not a read-only mount). Fallback when neither is available (common on managed PCs): subprocess under a Job Object = resource caps + kill-on-close only, NO filesystem/network boundary. **`--require-isolation` makes the worker fail CLOSED (refuse the job) rather than take that unsandboxed fallback** | [PoC] | med (fail-closed pilots) / med-high (default fallback has no FS boundary; software, not TEE) |
| Info-disclosure | job reads host or other jobs; activations leak | one job per sandbox; no shared state; no-persistence; data minimization; schedule only safe job classes | [PoC] + [Roadmap class policy] | med |
| DoS | job exhausts CPU/RAM/disk | per-job memory (`--memory`) + timeout caps (CPU governed by the admission governor; Job Object on the fallback); 8 MB result cap; kill-on-yield | [PoC] | low-med |
| Elevation | sandbox escape to host/kernel | MXC (preferred, kernel-enforced containment when the runtime is present) / container / Job-Object boundary; `--require-isolation` refuses to run when no OS-enforced sandbox is active; roadmap AppContainer/Win32 App Isolation, gVisor, TEE | [PoC]+[Roadmap] | **med-high (named openly; lower under MXC or require-isolation)** |
| Spoofing (code) | run unintended code | Ed25519-signed manifest binds the input hash; worker verifies signature + input hash + expiry before run and refuses on mismatch. **`--trusted-key` / `$ONECOMPUTE_TRUSTED_PUBKEY` pins an out-of-band signer: with it set, unsigned or differently-signed manifests are refused (`unsigned_manifest` / `untrusted_signer`), so a compromised control plane cannot inject a self-signed job.** Code is a built-in adapter (`code_ref=builtin`), so there is no shipped-code hash | [PoC] | low (pinned key) / low-med (trust-on-first-use default) |
| GPU isolation gap | GPU job is weakly isolated (GPU-in-Sandbox unsupported) | GPU jobs run host-side under a Job Object; restrict GPU jobs to lower-sensitivity classes; **`--require-isolation` blocks host-side GPU/AI execution entirely when no OS-enforced sandbox is available** | [PoC, disclosed] | **med-high (open question; eliminated when require-isolation is set)** |

### B3 - Worker <-> Orchestrator
| STRIDE | Threat | Mitigation | Status | Residual |
|---|---|---|---|---|
| Spoofing | fake worker | per-worker bearer token (issued at register, constant-time check); **optional device-identity binding** (`--bind-device-identity`) ties the token to the worker's TLS client-cert SHA-256 fingerprint, so a token replayed from another host fails closed (401 + audited `device_fingerprint_mismatch`) | [PoC] | low-med (token + device binding; Intune/Entra-managed device is the upgrade) |
| Tampering | alter control messages | Ed25519-signed manifests; typed/validated inputs. By default the public key travels with the manifest (proves integrity, not provenance); **an out-of-band pinned signer is now shipped (`--trusted-key`), so a worker can reject any key but the operator-provisioned one.** **Optional TLS and mutual TLS on the transport are now shipped** (`--tls-cert/--tls-key`, `--tls-client-ca`; worker `--client-cert/--client-key`) | [PoC sign + pinned key + optional TLS/mTLS] | low-med (mTLS closes on-path tampering when enabled) |
| Repudiation | deny actions | append-only audit (register/assign/complete/yield/fail/auth_failed), now **hash-chained and tamper-evident** with a verify endpoint (`GET /events/verify`) and a JSONL SIEM export (`GET /events/export`) | [PoC] | low |
| Info-disclosure | sniff control plane | **optional TLS in transit now shipped** (`--tls-cert/--tls-key`); data-minimized payloads; internal LAN as a floor | [PoC optional TLS] | low-med (encrypted when TLS enabled) |
| DoS | flood orchestrator | lease timeouts + requeue; payload cap; per-job limits; **per-client rate limiting now shipped** (`--rate-limit`, default 600/min, keyed by worker token or IP, returns 429 + Retry-After) | [PoC] | low-med |
| Replay | reuse old manifest/lease | manifest expiry; lease ownership; nonce/short TTL roadmap | [PoC expiry]+[Roadmap nonce] | low-med |

### B4 - Submitter <-> Orchestrator
| STRIDE | Threat | Mitigation | Status | Residual |
|---|---|---|---|---|
| Spoofing | unauthorized submitter | **optional operator submit token now shipped** (`--submit-token`; `Authorization: Bearer`, constant-time, audited on failure) gates job/workload submission; full SSO/OIDC is the upgrade | [PoC token]+[Roadmap SSO/OIDC] | low-med (unauthorized submission blocked when token set) |
| Tampering | inject malicious job | signed manifest; review of job classes; allow-listed adapters; **submit-token gate** on the submission endpoints | [PoC sign + submit token]+[Roadmap submitter SSO] | low-med |
| Info-disclosure | over-collect employee/job data | data minimization; class policy; CELA review of data scope | [PoC]+[Roadmap DPIA] | med |
| EoP / abuse | use fleet as botnet | internal-only; audited; class allow-list; **submit-token gate on submission**; rate/scope limits (rate limiting shipped) | [PoC audit + submit token + rate limit]+[Roadmap quotas] | low-med |

### B5 - Employee <-> System (privacy)
Covered in depth by the LINDDUN analysis (section 7).

---

## 7. Privacy threat model (LINDDUN) - for CELA / Privacy

| LINDDUN category | Threat | Mitigation | Residual |
|---|---|---|---|
| **Linkability** | link compute behavior to an individual over time | only derived spare-capacity leaves device; no raw activity stream; minimal ledger linkage | low-med |
| **Identifiability** | identify the employee from telemetry | profile stays on-device; advert carries node id, not activity | low |
| **Non-repudiation (privacy sense)** | employee cannot deny participation | participation is voluntary/opt-in and logged transparently; this is desired for audit, disclosed to employee | n/a (by design, disclosed) |
| **Detectability** | infer activity from job scheduling patterns | governor decisions are local; scheduling sees capacity, not activity | low |
| **Disclosure of information** | activity/keystroke/file data exposed | none is collected or transmitted; on-device-only profiling; no-persistence | low |
| **Unawareness** | employee unaware of what runs/why | one-page consent: what it does, headroom-only, never on battery, instant opt-out, caps | low |
| **Non-compliance** | violates privacy law/policy | request DPIA + CELA legal review; data minimization; lawful basis = voluntary consent; works-council consideration in applicable regions | **open: needs CELA/Privacy** |

**Employee-monitoring posture:** OneCompute is explicitly designed **not** to be employee monitoring. It measures spare hardware capacity locally to avoid disturbing the user, and transmits only a capacity scalar. We will state this plainly to CELA and in consent, and we will honor regional employee-representation requirements (e.g., works councils) before any non-pilot expansion.

---

## 8. Abuse cases and attack trees

1. **Fleet-as-botnet.** Goal: run attacker code at scale. Branches: compromise submitter (mitigate: the shipped `--submit-token` operator gate on job/workload submission, plus signed manifests + class allow-list + audit; SSO/OIDC is the upgrade); compromise orchestrator (mitigate: hardened host, out-of-band pinned worker signer, append-only audit, least-privilege, roadmap signing transparency).
2. **Data exfiltration via job.** Goal: read host/other-job data. Branches: sandbox escape (mitigate: MXC/container isolation + class policy + no-network; `--require-isolation` fails closed when no OS-enforced sandbox is present); intermediate-state leakage like activations (mitigate: restrict sensitive classes; disclosed residual).
3. **Reward fraud at scale (Sybil / benchmark inflation).** Reference incident: io.net saw ~1.8M fake GPUs. Mitigate: corp-SSO one-identity-per-node, validated-output-only metering, server-assigned credit, challenge/ringer, capped multipliers.
4. **Cryptojacking of our own fleet.** An attacker abuses the agent to mine. Mitigate: only signed, hash-bound manifests run; server-authoritative job source; audit; allow-listed adapters.
5. **Malicious or compromised orchestrator.** By default (trust-on-first-use) the worker trusts the public key embedded in the manifest, so a compromised orchestrator (or an active MITM that swaps key and signature on the unencrypted channel) could issue runnable jobs. **This is now closable in code: pinning an out-of-band signer with `--trusted-key` / `$ONECOMPUTE_TRUSTED_PUBKEY` makes the worker accept only manifests signed by the operator-provisioned key and refuse a self-signed job from a compromised control plane.** Additional mitigations: harden and restrict the orchestrator host, least privilege, append-only audit. Remaining roadmap: TLS/mTLS on the channel, and moving the signing authority to an HSM/corp service separate from the orchestrator.
6. **Supply-chain compromise of the agent.** Mitigate: dependency pinning (`uv.lock` + the pinned `cryptography` trust root), a generated **CycloneDX SBOM** (`scripts/generate_sbom.py`; see `supply-chain.md`) for CVE scanning, and Ed25519 manifest signing; roadmap: cosign/OIDC/Rekor build signing + SLSA provenance.

---

## 9. Cryptography and key management

| Aspect | PoC | Roadmap |
|---|---|---|
| Manifest signing | Ed25519 (`cryptography`), on by default; worker verifies signature + input hash + expiry, refuses on mismatch (no code-hash check; code is a built-in adapter). **Out-of-band pinned signer shipped (`--trusted-key` / `$ONECOMPUTE_TRUSTED_PUBKEY`): strict mode rejects unsigned or differently-signed manifests.** Default (no pin) is trust-on-first-use against the key carried in the manifest | cosign/Sigstore + OIDC + Rekor transparency; SLSA provenance; HSM-custodied signing key |
| Signing-key custody | private key on the orchestrator/signing host; this is the highest-value secret | HSM / corp signing service; key rotation policy; separation from orchestrator |
| Worker auth | per-worker bearer token, constant-time comparison | device-bound certificates + SSO/OIDC |
| Transport | **optional TLS + mutual TLS shipped** (uvicorn `ssl_*` server side, pinned-CA + client-cert httpx client side); internal LAN as a floor when TLS is off | TLS on by default; automated cert issuance/rotation; WAF |
| Token/secret handling | tokens not logged; auth failures audited | secret store integration, short-lived tokens, rotation |

**Key risk:** compromise of the manifest signing key allows arbitrary code on the fleet. Treatment: restrict custody, move to a corporate signing service/HSM, rotate, and separate the signer from the orchestrator before any expansion.

---

## 10. Network and transport security
- **Outbound-only short-poll**: workers open no inbound ports (NAT/firewall-proof, no listening service to attack on the employee machine).
- Orchestrator exposes a minimal HTTP API behind security response headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, strict `Content-Security-Policy`).
- Pilot: confirm proxy/VPN allows worker outbound HTTPS to the orchestrator FQDN; Purview DLP permits the data class.
- **Optional TLS + mutual TLS shipped:** the orchestrator serves HTTPS (`--tls-cert/--tls-key`) and can require client certificates (`--tls-client-ca`); the worker pins a private CA (`--tls-ca`) and presents a client cert (`--client-cert/--client-key`). Roadmap: TLS on by default, automated cert issuance/rotation, network segmentation for the orchestrator, WAF.
- **Per-client rate limiting shipped:** `--rate-limit` (default 600/min, keyed by worker token or source IP) returns 429 + Retry-After when exceeded.

---

## 11. Isolation and sandbox deep-dive
- **Preferred OS-enforced boundary - MXC (Microsoft Execution Containers)**: when a real `wxc-exec` runtime is present and its probe passes, `active_boundary()` returns `"mxc"` and CPU jobs run under a kernel-enforced, policy-driven container (writable surface restricted to the per-job work dir; default-deny filesystem/network/UI). The backend is merged but **fail-closed and inert until a real runtime exists**: the probe never raises and reports unavailable on absent binaries, probe errors/timeouts, malformed probe JSON, or "host prep still required" warnings, and `_run_mxc` raises an infra error (falling back) rather than run a job it cannot contain. On current managed machines MXC is not yet present, so `active_boundary()` is `subprocess+jobobject`.
- **Per-job container (CPU kinds, when MXC absent)**: only a minimal stdlib payload is mounted (no host files, no repo `src`), `--network none`, ephemeral `--rm`. The `/work` mount is read-write because the job writes its output there, so it is isolation-by-minimal-surface, not a read-only mount. **AI and GPU kinds run host-side** as a subprocess (AI needs the real SDK plus API key; GPU needs CUDA), so they are not network-isolated. **Fallback when neither MXC nor the Docker daemon is available (common on managed PCs)**: a subprocess under a **Windows Job Object** (no admin) gives resource caps plus kill-on-close only, with **no filesystem or network boundary**, reported honestly by `active_boundary()` and logged at WARNING. **`--require-isolation` makes the worker fail closed - refuse the job with `IsolationUnavailableError` - rather than take this unsandboxed fallback or run host-side GPU/AI without an OS sandbox.** Windows Sandbox/Hyper-V and AppContainer/Win32 App Isolation are roadmap.
- **No persistence**: inputs/outputs wiped at job end; instant-yield is a real process-tree/container kill, not a pause.
- **Known gaps (disclosed):** software isolation is not a hardware TEE; sandbox escape and side-channels are reduced, not eliminated (see section 11.1); **GPU-in-Sandbox is unsupported**, so GPU jobs run host-side under a Job Object with weaker isolation. **MXC preview caveats (from the public MXC repo, tracked as R15):** current SDK-generated policies can be overly permissive, denied-paths are not yet enforced on Windows, and no MXC profile should be treated as a security boundary yet; our integration therefore fails closed on any policy it cannot verify, and is not yet validated against a real runtime. **A validation harness with a stub `wxc-exec` now exercises the real `_run_mxc` launch path end-to-end (see `mxc-validation.md`), proving OneCompute's side of the probe/policy/launch contract; the real-runtime validation remains pending.** Treatment: restrict GPU and sensitive-class jobs; validate MXC against a real `wxc-exec` before relying on it; TEE is roadmap as datacenter-class GPU TEEs reach the desk.

### 11.1 Side-channel and shared-hardware exposure (expanded)
Because a job shares physical CPU, cache, and memory (and, for GPU kinds, the GPU) with the employee's own work, microarchitectural side channels (cache-timing such as Spectre-class, and resource-contention channels) are a residual we do not claim to fully eliminate. Reductions in place: one job per sandbox with no shared state and no persistence; short-lived jobs; `--network none` for CPU container/MXC kinds, which removes the C2 path needed to exfiltrate any inferred secret; and (when validated) MXC session isolation that separates the sandboxed process from the human desktop, clipboard, and input devices. Not mitigated in software: same-core timing inference against co-resident work. Treatment: restrict sensitive job classes; prefer MXC's session isolation once validated; hardware-TEE execution is the roadmap answer for confidential classes. This is disclosed residual (feeds R2/R10), not a solved problem.

---

## 12. Endpoint and enterprise-control integration (for MSD / CISO)

| Control | Collision | Our design (pass, not bypass) |
|---|---|---|
| **Defender for Endpoint** | sustained CPU/GPU == cryptojacking signature; risk of quarantine | code-signed agent, **allow-list custom indicator by publisher + SHA-256**, scoped to named pilot devices, monitored live |
| **WDAC / AppLocker** | unsigned agent blocked (verified) | corporate code-signing trust, or scoped exception |
| **Purview DLP** | may block job data egress | confirm policy for the pilot data class; sanctioned channel |
| **Intune compliance** | agent could trip compliance | confirm no compliance impact; user-space delivery for pilot |
| **ASR / EDR telemetry** | unusual process behavior | we **generate** clean, attributable telemetry (signed process, known publisher); share IOCs with the endpoint team up front |

**Doctrine:** internal scope shrinks attack surface and makes every action attributable, but it does **not** let us skip controls. We enforce signing, sandboxing, and audit at the boundary and ask to be allow-listed, not exempted.

---

## 13. Trust, verification, and anti-abuse
- **Result integrity:** proof-hash match required (`invalid_proof` rejection); hidden **challenge/ringer** tasks with server-known answers; wrong answer -> blacklist + zero credit; comparators tolerance-aware (heterogeneous FP), not bitwise.
- **Job provenance (anti-injection):** with `--trusted-key` / `$ONECOMPUTE_TRUSTED_PUBKEY` the worker accepts only manifests signed by the operator's out-of-band key, so a compromised or spoofed orchestrator cannot inject a self-signed job; default (no pin) is trust-on-first-use against the key carried in the manifest.
- **Credit integrity:** credit is computed **server-side from the JOB's actual GPU requirement** in the signed manifest (accepted_units x 5 for a GPU job, else x1), never from the worker's self-reported `has_gpu`/TOPS, so a worker cannot inflate credit 5x by claiming a GPU it lacks and running CPU jobs; append-only ledger; lease ownership prevents double-credit; capped multiplier.
- **Sybil resistance:** corp-SSO one-identity-per-node (roadmap: device-bound certs).
- Roadmap: reputation-weighted adaptive replication; formal verifiable compute.

---

## 14. Supply-chain security
- **Agent build:** reproducible build script; record publisher + post-sign SHA-256.
- **Dependencies:** the sandboxed job payload is **pure-stdlib** by design (`jobkit` uses only `urllib`), so no third-party code runs inside the container - a real strength. The control plane and worker use `fastapi`/`uvicorn` (API), `pydantic` (frozen contracts), `httpx` (worker outbound client), `psutil` (headroom/idle governor), and `cryptography` (Ed25519 sign/verify - the trust root). **Status:** (1) `cryptography` (the trust root) is now a **pinned, CVE-watched direct dependency** in `pyproject.toml` (gap closed); (2) a **CycloneDX SBOM is now generated** from `uv.lock` (`scripts/generate_sbom.py`; see `supply-chain.md`) for automated CVE scanning. Remaining hardening: the declared set is still broad (borrowed from the CoworkBench toolbox: `anthropic`, `openai`, `pandas`, `playwright`, `matplotlib`, `pymupdf`, ...), so a hardened agent build should trim to the actual worker/orchestrator import surface, and a signed **build-provenance attestation** is now produced offline (a signed SLSA v1 in-toto statement over the SBOM + source tree via `scripts/generate_provenance.py`; see `supply-chain.md`), while transparency-logged, hardware-rooted signing (cosign/OIDC keyless + Rekor + full SLSA build levels) remains roadmap. The **Sigstore cosign integration is now wired in** (`src/trust/cosign.py`; see `cosign.md`): it signs the SBOM / attestation with `cosign sign-blob` when a cosign binary is present and stays fail-closed and inert (no fabricated signature) when absent, so the industry-standard tooling is adopted while the keyless OIDC + Rekor path (which needs network + an identity provider) is the honest production roadmap.
- **Update channel:** signed updates; no silent auto-update without signature verification (roadmap formalization).
- **Provenance:** Ed25519 now; cosign/OIDC/Rekor transparency log on the roadmap (SLSA-style).

---

## 15. Logging, audit, monitoring, detection
- Append-only **audit event stream**: register, submit, assign, complete, yield, fail, blacklist, approved, **auth_failed**, each timestamped, queryable via `GET /events`. The stream is **hash-chained (tamper-evident)**: each event carries `prev_hash`/`hash`, `GET /events/verify` re-derives the chain and reports the first broken link, and `GET /events/export` emits JSONL for a SIEM (Microsoft Sentinel).
- Live operator dashboard surfaces fleet, ledger, and the yield beat.
- Pilot adds per-machine telemetry (CPU impact, yield rate, governor decisions) and **any AV alerts**.
- **Shipped:** the audit stream is now **hash-chained and tamper-evident** (`verify_audit_chain` / `GET /events/verify`) with a **JSONL export** (`GET /events/export`) for SIEM ingestion (Microsoft Sentinel). Roadmap: SIEM alerting thresholds, and external anchoring / WORM / Rekor-style transparency to defeat a live-write attacker.

---

## 16. Safety, reliability, and hardware/physical considerations
*(A thorough reviewer will ask; we address proactively.)*
- **Thermal/performance:** governor caps utilization and yields sub-second; **never runs on battery**; conservative default budget to avoid heat/fan impact.
- **Hardware wear / warranty:** sustained load is bounded and opt-in; document expected duty cycle; respect device warranty/AUP. Open item to confirm with MSD.
- **Power cost / sustainability:** marginal electricity (~$10-20/mo/machine est.); net-positive only for substitutable work; honest accounting; e-waste reduced by using existing hardware longer.
- **Reliability/churn:** laptops sleep and return -> lease timeout + auto-requeue; no lost or double-counted work; file-backed SQLite (WAL) persists across restart.
- **Availability of the employee's own work is the top priority** (A1): the system degrades itself, never the user.

---

## 17. Legal, HR, and compliance (for CELA / HR)
- **Lawful basis:** voluntary, informed **opt-in consent**; clear notice; instant withdrawal.
- **Employee monitoring law / works councils:** treat as potentially in-scope in some regions; engage employee representation before any non-pilot expansion; emphasize no activity surveillance.
- **Compensation / tax / off-the-clock (e.g., FLSA-style):** rewards are for passive use of an already-issued device, not labor; keep voluntary and capped; HR to confirm the incentive structure and any tax treatment.
- **Acceptable Use Policy alignment:** confirm the agent and rewards comply with corporate AUP and device-use policy.
- **Retention & erasure on opt-out:** on withdrawal the on-device profile file is deleted locally; the append-only ledger and audit stream retain only pseudonymous node-id plus credit/operational metadata (no activity data). Because those stores are append-only, honoring an erasure request means a documented retention window plus a pseudonymization/tombstoning path for the employee<->node linkage rather than in-place deletion. Defining that path is a named DPIA deliverable for CELA/Privacy.
- **Compliance crosswalk:** SOC 2 TSC control-to-code mapping in `soc2-alignment.md`; NIST CSF / 800-53 and ISO 27001 mapping is roadmap; a **DPIA** is the named privacy deliverable.

---

## 18. Incident response and kill switch
- **Detection:** live Defender/Purview monitoring during pilot; audit stream; operator on call.
- **Kill switch (reversible by design):** stop the orchestrator -> all workers idle within one poll; employees Ctrl-C/uninstall; remove the Defender allow-list entry at pilot end; the only artifact is a local profile file.
- **Playbook:** Defender/Purview alert -> stop affected worker, notify security contact, pause pilot, investigate, do not resume until cleared. Slowdown report -> stop worker, re-tune governor margin, re-test on a controlled machine first.
- **Forensics/comms/breach:** preserve audit + telemetry; follow corporate IR and breach-notification process; sponsor gets daily status + go/no-go.

---

## 19. Risk register (scored)

Likelihood/Impact: L/M/H. Severity = combined. Owner/treatment shown; residual after mitigation.

| # | Risk | Like. | Impact | Severity | Treatment | Residual |
|---|---|---|---|---|---|---|
| R1 | Endpoint-stack collision (cryptojacking false-positive, WDAC, Purview) | H | H | **Critical** | code-sign + Defender allow-list + WDAC trust + Purview confirm; pilot only after sanction | Med (managed by allow-list) |
| R2 | Sandbox escape / host compromise via job | L-M | H | **High** | MXC (preferred, kernel-enforced) when a runtime is present, else container isolation for CPU kinds (no-network, --rm, minimal payload); the Job-Object fallback is caps+kill only with no FS boundary; `--require-isolation` fails closed rather than use it; class policy; no-persistence; TEE roadmap | Med (fail-closed / MXC) / Med-High (default fallback) |
| R3 | GPU-job weak isolation (GPU-in-Sandbox unsupported) | M | H | **High** | host-side Job Object; restrict GPU job sensitivity; `--require-isolation` blocks host-side GPU/AI when no OS sandbox is present | Med-High (open; eliminated when require-isolation set) |
| R4 | Privacy harm / employee-monitoring perception | M | H | **High** | on-device-only profiling; consent; data minimization; DPIA + CELA | Med (pending CELA) |
| R5 | Signing-key compromise -> arbitrary fleet code | L | H | **High** | restrict custody; HSM/corp signing; rotation; separate from orchestrator | Med |
| R6 | Orchestrator compromise -> fleet command | L | H | **High** | hardened host; **out-of-band pinned signer (`--trusted-key`) so a compromised orchestrator cannot inject a self-signed job**; signature gate at worker; audit; least privilege | Low-Med (pinned) / Med (trust-on-first-use default) |
| R7 | Reward fraud / Sybil / benchmark inflation | M | M | **Med** | SSO one-identity-per-node; server metering; challenge; caps | Low-Med |
| R8 | Governor mis-sizing -> perceptible slowdown (adoption killer) | M | M-H | **High** | conservative default; learned profile; sub-second yield; pilot measures it | Med |
| R9 | Transport blocked/insecure (proxy issues) | M | M | **Med** | validate hour-1 reachability; **optional TLS/mTLS shipped**; rate limiting shipped | Low-Med (encrypted + mutually authenticated when TLS/mTLS enabled) |
| R10 | Data leakage via intermediate state (activations) | L-M | M-H | **Med-High** | restrict sensitive job classes; disclosed; class policy roadmap | Med |
| R11 | Churn -> lost/double work | M | L-M | **Low-Med** | lease timeout + requeue; idempotent crediting | Low |
| R12 | Legal/works-council/tax non-compliance | L-M | M-H | **Med-High** | CELA/HR review; voluntary capped rewards; regional handling | Med (pending legal) |
| R13 | Supply-chain compromise of agent | L | H | **Med-High** | Ed25519 signing; dependency pinning (pinned `cryptography` trust root); **CycloneDX SBOM** + **signed SLSA v1 provenance attestation** shipped (`scripts/generate_sbom.py`, `scripts/generate_provenance.py`); transparency-logged cosign/OIDC/Rekor signing roadmap | Low-Med (inventory + pinning + signing + offline attestation in place; transparency-logged build proof roadmap) |
| R14 | Hardware wear / thermal / battery | L-M | M | **Med** | never on battery; bounded duty cycle; MSD confirm | Low-Med |
| R15 | MXC preview immaturity (overly-permissive default policies; denied-paths unsupported on Windows; not a security boundary yet; unvalidated against a real runtime) | M | M-H | **Med-High** | fail closed on any policy we cannot verify; inert until a real `wxc-exec` runtime is present; **a validation harness (stub `wxc-exec`) now proves the launch/policy/probe wiring end-to-end** (`mxc-validation.md`), so a real preview can be dropped in without a OneCompute code change; keep Docker/Job-Object + `--require-isolation` enforced meanwhile | Med (contained by fail-closed; OneCompute-side wiring proven, real-runtime validation still pending) |

---

## 20. Residual risk and acceptance
The PoC enforces the technical heart of a secure design (authenticated, signed, sandboxed, verified, audited, opt-in, instant-yield), and v1.1 adds three hardening controls in code: a **fail-closed isolation switch** (`--require-isolation`), an **out-of-band pinned signer** (`--trusted-key`) that closes the compromised-orchestrator injection path, and a **merged, fail-closed, inert-until-runtime MXC OS-enforced backend** as the preferred boundary. The **honest residual** now concentrates in: software (not hardware-TEE) isolation and side channels, the GPU-isolation gap, MXC not yet validated against a real runtime (its launch-path wiring is now proven via a stub harness; R15), TLS/SSO/build-signing being roadmap, and unresolved legal/privacy review. **Therefore the ask is a contained pilot, not rollout**: opt-in, a handful of named devices, time-boxed, internal-only, reversible, watched, and gated on written risk acceptance. Residual acceptance is recorded in the section 0 sign-off table.

---

## 21. Open questions / decisions needed
1. MSD: acceptable duty cycle / device-wear stance; allow-list path; pilot device set.
2. CISO/Azure Security: required isolation bar for GPU and sensitive classes; TLS/mTLS timing; orchestrator hardening baseline.
3. CELA/Privacy: is on-device-only profiling + capacity scalar in-scope for monitoring law? DPIA scope? works-council regions?
4. HR: rewards structure, tax treatment, off-the-clock interpretation.
5. Signing: corporate signing service vs interim Ed25519; key custody.

---

## 22. Control-to-threat traceability (summary matrix)

| Control | Mitigates |
|---|---|
| Ed25519 signed, hash-bound, expiring manifest; refuse on flip | B2 spoof-code, B3 tamper/replay, abuse-case 4/5 |
| Per-worker bearer token (constant-time) + lease ownership | B3 spoof, double-credit, R7 |
| Device-identity binding (`--bind-device-identity`, TLS cert fingerprint) | B3 spoof (stolen token), Intune/Entra |
| Sigstore cosign integration (`cosign sign-blob`, inert-when-absent) | Tampering (build), abuse-case 6, R13 |
| Device-code admission gate; `approve`/`disconnect` require the operator token | B1/B3 unauthorized join, B3 self-admit |
| Container (CPU kinds) `--network none` / ephemeral `--rm` / minimal payload; Job-Object fallback (caps + kill only) | B2 host read/escape, no-persistence, A3 |
| MXC (preferred OS-enforced boundary; fail-closed/inert without a real runtime) | B2 host read/escape, R2, R15 |
| MXC launch-path validation harness (stub `wxc-exec` driving real `_run_mxc`) | B2 isolation wiring, R15 |
| CycloneDX SBOM (`scripts/generate_sbom.py`) + dependency pinning | abuse-case 6, R13 |
| `--require-isolation` fail-closed switch (refuse when no OS sandbox; blocks unsandboxed fallback + host-side GPU/AI) | B2 escape, R2/R3 |
| `--trusted-key` out-of-band pinned signer (strict provenance) | B3 tamper, abuse-case 5, R6 |
| Optional TLS + mutual TLS (server `--tls-*`, worker `--tls-ca`/`--client-cert`) | B3 tamper/info-disclosure, R9 |
| Per-client rate limiting (`--rate-limit`, 429 + Retry-After) | B3 DoS, B4 fleet-abuse, R9 |
| Operator submit token (`--submit-token`, constant-time, audited) | B4 spoofing/tampering/EoP, abuse-case 1 |
| Signed SLSA v1 provenance attestation (`scripts/generate_provenance.py`) | Tampering (build), abuse-case 6, R13 |
| Hash-chained tamper-evident audit + verify/export (`GET /events/verify`, `/events/export`) | B1/B3 Repudiation, SIEM/Sentinel |
| Instant-yield governor vs learned profile; never on battery | A1, R8, R14 |
| Challenge/ringer + server-assigned credit + append-only ledger | A4/A5, R7 |
| On-device-only profiling; data minimization; no-persistence | A2, B5/LINDDUN, R4/R10 |
| Append-only audit + monitoring | repudiation, detection, IR |
| Code-sign + Defender allow-list + WDAC + Purview | B1 endpoint-control, R1 |
| Outbound-only, no inbound ports; security headers | B3 network exposure |

---

## 23. Anticipated reviewer questions (and our answers)

**CISO / Azure Security**
- *Isolation strength?* MXC (merged, preferred, kernel-enforced) when a real runtime is present, else container + Job Object; `--require-isolation` lets a pilot fail closed rather than run unsandboxed; TEE roadmap; GPU gap disclosed. **The MXC launch-path wiring is now validated end-to-end against a stub `wxc-exec` runtime** (`mxc-validation.md`); validation against a real preview runtime remains pending (R15). We restrict sensitive/GPU classes and want your required bar for the pilot.
- *What if the orchestrator is compromised?* A worker run with `--trusted-key` / `$ONECOMPUTE_TRUSTED_PUBKEY` accepts only manifests signed by the operator's out-of-band key, so a compromised orchestrator cannot inject a self-signed job. Without a pinned key (trust-on-first-use default) it could. Remaining roadmap: TLS/mTLS on the channel and moving the signing key to an HSM/corp service separate from the orchestrator.
- *Auth/transport?* Per-worker tokens + lease ownership now; **optional TLS and mutual TLS are now shipped** (`--tls-cert/--tls-key`, `--tls-client-ca`; worker `--client-cert/--client-key`), plus per-client **rate limiting** (`--rate-limit`) and **optional device-identity binding** (`--bind-device-identity`, ties the token to the worker's TLS client-cert fingerprint); SSO/OIDC remains roadmap. A pilot can run TLS-everywhere on a controlled network.

**Microsoft Digital (MSD)**
- *Will it trip Defender/WDAC/Purview/Intune?* Yes if unsanctioned; we ask to be **allow-listed** (signed publisher + SHA-256), not exempted; we share IOCs and monitor live.
- *Device wear/thermal/battery?* Bounded duty cycle, never on battery, conservative governor; we want your acceptable-use stance.
- *Rollback?* Stop orchestrator -> idle in one poll; uninstall; remove allow-list entry.

**CELA / Privacy**
- *Is this employee monitoring?* No: profiling is on-device only and only a capacity scalar leaves; no activity/keystroke/file/mail/browser data. We request a DPIA and a scoped legal read, and will handle works-council regions.
- *Lawful basis?* Voluntary informed opt-in with instant withdrawal.

**HR**
- *Rewards / off-the-clock / tax?* Voluntary, capped rewards for passive device use, not labor; we want HR to confirm structure and tax treatment.

---

## Appendix A - Glossary
TEE (trusted execution environment), WDAC (Windows Defender Application Control), DLP (data loss prevention), MDE (Microsoft Defender for Endpoint), DPIA (data protection impact assessment), Sybil (many fake identities), IOC (indicator of compromise), governor (the demand-adaptive admission/yield loop).

## Appendix B - References
Internal: `idea.md` (§8 trust, §10 risks), `architecture.md` (§3.2 governor, §3.3 sandbox, §9 security), `mxc-sandbox.md` (MXC backend design + preview caveats), `mxc-validation.md` (MXC launch-path validation harness), `supply-chain.md` (SBOM + signed provenance), `cosign.md` (Sigstore signing), `audit-log.md` (tamper-evident audit + SIEM export), `device-identity.md` (device-bound worker identity), `npu-harvesting.md`, `partitioning.md`, `soc2-alignment.md` (control-to-code map), `pilot-security-approval.md` (sanction runbook), `pilot-plan.md` (pilot operations), `OneCompute-Risk-Memo.docx`. External concepts: Microsoft SDL threat modeling, STRIDE, LINDDUN, MITRE ATT&CK, NIST CSF/800-53, CISA cryptojacking guidance, Golle and Mironov (result verification), BOINC security/code-signing.

## Appendix C - MITRE ATT&CK touchpoints (illustrative)
Execution (T1059) and Resource Hijacking (T1496) are the behaviors our agent **resembles** and must be allow-listed against; Exfiltration over C2 (T1041) and Ingress Tool Transfer (T1105) are blocked for CPU container jobs by `--network none`, while AI and GPU jobs run host-side with network by necessity and so rely on signed-only job code, class policy, and audit instead; Valid Accounts/Sybil map to our SSO + one-identity-per-node metering. We will provide a full technique-by-technique sheet to the endpoint team on request.
