# Safe Azure / Foundry routing architecture (harvest phase)

Status: design spec for the harvest phase. Companion to `Azure_Integration_Plan.md` (the phased plan) and `OneCompute-Threat-Model.md` §24 (the threat model). The concrete, shipped control is documented in `routing-policy.md`.

## 1. Purpose and scope

The measurement pilot and the example-workload PoC run our own signed adapters. The **harvest phase** lets Azure AI Foundry route **real, tenant-owned workloads** (batch inference, evaluation, agent runs) onto the fleet through a `compute: onecompute` target (`Azure_Integration_Plan.md` End State). This document specifies how a routed workload gets from Foundry onto an idle corporate machine **safely**: which data may go where, how device trust is established, how egress is contained, and what must wait for hardware confidential compute.

It is deliberately conservative: the harvest phase starts by routing only low-sensitivity workloads onto ordinary machines, and expands the envelope only as higher-assurance tiers are built and validated.

## 2. The core problem: the trust model inverts

In the PoC the workload is the untrusted party and we protect the employee's machine from submitter code (per-job sandbox, no-persistence, instant yield). Routing real data adds the **opposite** risk: we must protect the *workload's data* from the *machine's operator*.

An employee whose idle laptop runs a routed job owns the hardware, the OS, a debugger, and physical access. Software isolation on hardware the adversary controls is not a confidentiality guarantee. Therefore:

> **A machine may only receive data whose sensitivity its established trust level can actually protect.** Confidential and Restricted data does not belong on an ordinary employee laptop at all; it belongs only on a hardware-attested confidential-compute node, or it stays in Azure.

Everything below follows from that single rule.

## 3. Architecture overview

```
[Azure AI Foundry]                                   Azure control plane: model mgmt, governance
      | F9: routed workload + data classification (per-tenant, per-region)
      v
[OneCompute routing gateway]      authenticates the tenant, stamps the classification into the
      |                           SIGNED manifest, enforces residency, records provenance
      v
[Orchestrator scheduler]          assigns ONLY where routing_policy.may_route(classification, tier)
      |                           holds; every decision is written to the hash-chained audit stream
      v
[Worker on an idle device]        server-assigned trust tier; sandboxed run; no-egress by default;
                                  instant yield; result + proof hash back
```

The gateway is the only submitter of routed workloads and the single point where a tenant's request becomes a signed OneCompute job. The orchestrator never trusts anything the worker self-reports for a routing decision.

## 4. Data classification model

Four ordered levels, intended to map onto Microsoft Purview sensitivity labels so the classification is inherited from the data, not invented by OneCompute:

| OneCompute level | Typical Purview label | Harvest-phase handling |
|---|---|---|
| `public` | Public / Non-business | Routable to any admitted device. |
| `internal` | General | Routable to managed (corporate-enrolled) devices. Default when unlabeled, so unclassified work is treated conservatively. |
| `confidential` | Confidential | Only sanctioned always-on IT machines, or (preferably) confidential compute. Never an ordinary laptop. |
| `restricted` | Highly Confidential | Confidential-compute (attested TEE) only, else stays in Azure. |

The level travels **inside the Ed25519-signed manifest** (`data_classification`), so it is tamper-evident end to end: a worker cannot downgrade it, and a compromised submitter is bounded by submitter authentication (B4) and, with a pinned `--trusted-key`, out-of-band signer provenance. Correct labeling at the Foundry/Purview source is an upstream control and a named CELA/CISO dependency.

## 5. Device trust tiers (server-assigned)

Four ordered tiers describing what a device is trusted to protect:

| Tier | Meaning | How established |
|---|---|---|
| `untrusted` | Default for any newly registered device. | Automatic on registration. Permits `public` only. |
| `managed` | Corporate-enrolled, compliant endpoint (Intune/Entra device compliance, Defender healthy). | IT-assigned; roadmap: derived from Intune/Entra compliance + Conditional Access signals. |
| `sanctioned` | An always-on, IT-controlled machine (locked dev box, lab/SAW, kiosk) where the operator risk is accepted for `confidential` work. | IT-assigned, out of band. |
| `confidential_compute` | A hardware TEE node with valid remote attestation. | Attestation-gated (roadmap; see §8). |

**The tier is assigned server-side and is never read from the worker's self-report.** This mirrors the reward-integrity fix (credit is metered on the job, not the worker's claim): a device that could self-declare `confidential_compute` would simply request Confidential data. A new device defaults to `untrusted`; IT elevates a specific device out of band via the operator-token-gated `POST /workers/{id}/tier`. Fail-closed by construction.

This maps directly onto Microsoft Zero Trust, which already tiers devices (compliant-managed > known-registered > unmanaged) and gates sensitive access on that tier via Entra Conditional Access ("require device to be marked as compliant"). OneCompute ships the mechanism for this: a device's tier can be **derived from a verified device-posture attestation** (`src/trust/attestation.py`, §10), fail-closed and inert until an attestation authority is configured. Wiring the real authority, **Intune device-compliance + Entra device state** and hardware **device attestation** (TPM/VBS via Azure Attestation), is the remaining roadmap step, so the tier a device carries is earned from verifiable posture, not asserted, exactly the control a CISO already recognizes.

## 6. The routing decision

`routing_policy.may_route(classification, tier)` permits an assignment only when the device's tier rank meets the minimum tier required for the data's classification:

| Classification | Minimum device tier |
|---|---|
| public | untrusted |
| internal | managed |
| confidential | sanctioned |
| restricted | confidential_compute |

Unknown classification or unknown tier -> **deny** (fail closed). The scheduler consults this on every assignment, so Confidential/Restricted data provably cannot be leased to an ordinary laptop, regardless of what the device advertises. See `routing-policy.md` for the shipped implementation and tests.

## 7. Egress and data handling

- **No egress by default.** Container kinds run `--network none`; a routed job cannot open outbound connections to exfiltrate data. Egress is declared and enforced, never implicit.
- **Data minimization.** The gateway sends the smallest input slice a job needs, and results are capped and proof-hashed. No raw activity, files, or wall-clock data ride the control plane.
- **No persistence.** The sandbox is disposable (`--rm` / kill-on-yield), so job data does not linger on the device after the run.
- **DLP + residency at the gateway (B6).** Data-loss-prevention and region/residency checks belong at the Foundry-facing boundary, before a workload becomes a job. Per-tenant provenance is stamped there and every routing decision is written to the hash-chained audit stream.
- **Host-side AI/GPU exception.** AI/GPU kinds that need the provider SDK run host-side with host network (a disclosed exception). Those are the first workloads to require a confidential-compute tier before they carry sensitive data.

## 8. Confidential compute for high classifications (roadmap)

Running Confidential/Restricted data on a machine an employee controls is only safe inside a hardware **TEE** (AMD SEV-SNP confidential VMs, Intel SGX/TDX) with **remote attestation** (Microsoft Azure Attestation, MAA). The correct enforcement primitive is not "tell the scheduler to pick an attested node," it is **attestation-gated key release**: the data (or the key that decrypts it) is bound by an attestation policy in Azure Key Vault / Managed HSM (Secure Key Release), and the key is released only to a workload that presents a valid MAA token whose claims prove it is a genuine, correctly-configured TEE. Plaintext is therefore unrecoverable outside an attested boundary, regardless of what the scheduler or the host claims.

**Hard limit for employee laptops (grounded in the research packet).** Commodity laptops **cannot** provide this. Azure confidential VMs require specific datacenter CPU SKUs (AMD EPYC with SEV-SNP, Intel TDX/SGX server parts); a laptop cannot run one. Intel SGX was removed from mainstream client CPUs. Client-side hardware trust on Windows (TPM 2.0, Virtualization-Based Security / VBS enclaves, Credential Guard, Pluton) can attest **device and boot integrity**, which is exactly what grounds the `managed` and `sanctioned` tiers, but it is **not** whole-workload, memory-encrypted TEE isolation against a privileged local admin. So the defensible split is:

- **Endpoints** (laptops, dev boxes) get **device-attested** tiers (Intune/Entra compliance + TPM/VBS) and run only `public`/`internal`, and `confidential` only on `sanctioned` always-on IT machines where the operator risk is explicitly accepted.
- **Truly confidential compute** (`restricted`, and `confidential` when a TEE is required) stays on **Azure datacenter confidential VMs**; on the fleet the `confidential_compute` tier is reachable only if and when an actual attested TEE node exists, and until then such data stays in Azure.

This mirrors how the MXC OS-sandbox backend is fail-closed and inert until a real `wxc-exec` runtime is present. TEE side channels (cache-timing, speculative-execution, page-fault) remain an acknowledged residual (threat model §11.1): confidential compute reduces, it does not eliminate, host-operator risk, so it is layered with data minimization, ephemeral handling, and DLP.

## 9. Foundry integration

Foundry stays responsible for model management, governance, and orchestration (`Azure_Integration_Plan.md` End State). OneCompute is the elastic execution layer for eligible, delay-tolerant workloads (evaluation, batch inference, agent runs), selected by the `compute: onecompute` / `compute: auto` target. The gateway authenticates the calling tenant, carries the tenant/region context as routing constraints alongside classification, and returns results through Foundry's existing result and governance paths so developer workflow and compliance are preserved. The gateway is shipped as an enforceable scaffold (`POST /foundry/jobs`, §10, `docs/foundry-gateway.md`); the live Foundry adapter and an Entra-backed tenant registry are the roadmap step behind it.

## 10. What is shipped now vs roadmap

**Shipped (this design's enforceable core):**
- `data_classification` in the signed manifest; server-assigned `trust_tier` per worker (default `untrusted`, IT-elevated via an operator-token-gated endpoint); fail-closed `routing_policy.may_route`; scheduler enforcement so sensitive data cannot land on a low-trust device; per-decision audit; no-egress-by-default sandboxing (`routing-policy.md`).
- **Attestation-derived tiering** (`src/trust/attestation.py`, `docs/device-attestation.md`): a device's tier can be derived automatically from a device-posture attestation (compliant/managed/sanctioned/TEE) that is verified against a configured authority key, fail-closed and inert until that key is set (Ed25519 stands in for Azure Attestation / Intune). Admin-pinned tiers stay authoritative over attestation.
- **Foundry routing gateway** (`POST /foundry/jobs`, `docs/foundry-gateway.md`): the F9/B6 ingestion point where a tenant request becomes a signed, classified job. Authenticates the tenant, enforces a per-tenant classification ceiling and region allow-list (fail-closed), stamps `{tenant_id, region}` provenance into the signed manifest, and enqueues through the same signed-submit path. Inert until tenants are configured.

**Roadmap (named, not built here):**
- Wiring the REAL attestation authority (Azure Attestation / Intune-Entra device compliance + Conditional Access) into the shipped attestation-derived tiering scaffold, so posture claims come from live compliance/MAA signals rather than a PoC Ed25519 authority.
- Purview label propagation so `data_classification` is inherited automatically from the data.
- The confidential-compute tier's real TEE + Azure Attestation backing (§8).
- Region/residency as a first-class routing constraint next to classification.
- The live Foundry adapter and an Entra-backed tenant registry behind the shipped gateway scaffold, plus DLP at the boundary.

These are honest scope choices: the enforceable fail-closed gate ships first so the harvest phase can start with low-sensitivity workloads on ordinary machines, and the envelope only widens as the higher-assurance pieces land.

## 11. CISO / CELA acceptance

The harvest phase does not ship until Azure Compute (functionality) and the CISO office (safety) co-develop and sanction routing per `Azure_Integration_Plan.md` Phase 0. This spec is the starting position: classification in the signed manifest, server-assigned fail-closed device tiers, no-egress-by-default, per-decision audit, and a hard rule that Confidential/Restricted data waits for attested confidential compute. New harvest-phase residual risks (curious-host disclosure on non-TEE tiers; correct upstream labeling) are owned jointly by CISO and CELA.

## 12. Mapping to code

| Design element | Code |
|---|---|
| Data classification (signed) | `data_classification` on `JobManifest` (`src/contracts/models.py`) |
| Device trust tier (server-assigned) | `trust_tier` column on `workers` (`src/contracts/schema.sql`); set at `/register`, elevated by `POST /workers/{id}/tier` (`src/orchestrator/app.py`) |
| Attestation-derived tiering | `src/trust/attestation.py` (verify + derive); `create_app(attestation_pubkey=...)` and the `/register` derivation (`src/orchestrator/app.py`); `--attestation-key` CLI; `tier_pinned` admin precedence |
| Routing decision (fail-closed) | `routing_policy.may_route` (`src/orchestrator/routing_policy.py`) |
| Enforcement | `pick_job_for` in `src/orchestrator/scheduler.py` |
| Egress containment | `--network none` container sandbox (`src/isolation/`) |
| Audit of decisions | hash-chained event stream (`GET /events/verify`) |
| Foundry ingestion (F9/B6) | `POST /foundry/jobs` + `_authenticate_foundry_tenant` (`src/orchestrator/app.py`); `classification_cleared` (`routing_policy.py`); signed `JobManifest.provenance` via `submit_job(..., provenance=...)` (`src/orchestrator/submit.py`) |

## 13. References

Authoritative sources grounding this spec (Microsoft Learn and standards), compiled for the CISO hand-off:

- **Azure Confidential Computing (data-in-use, TEEs):** https://learn.microsoft.com/en-us/azure/confidential-computing/overview
- **Confidential VMs (AMD SEV-SNP; attestation-gated boot + secure key release):** https://learn.microsoft.com/en-us/azure/confidential-computing/confidential-vm-overview
- **Microsoft Azure Attestation (MAA):** https://learn.microsoft.com/en-us/azure/attestation/overview
- **Azure Key Vault keys (key model; Secure Key Release backing):** https://learn.microsoft.com/en-us/azure/key-vault/keys/about-keys
- **Microsoft Purview sensitivity labels (classification that travels with the data):** https://learn.microsoft.com/en-us/purview/sensitivity-labels
- **Purview DLP (label-conditioned egress/endpoint controls):** https://learn.microsoft.com/en-us/purview/dlp-learn-about-dlp
- **Azure AI Foundry managed network / private link (workload isolation):** https://learn.microsoft.com/en-us/azure/ai-foundry/how-to/configure-managed-network and https://learn.microsoft.com/en-us/azure/ai-foundry/how-to/configure-private-link
- **Azure ML data-exfiltration prevention (egress patterns, nearest analog):** https://learn.microsoft.com/en-us/azure/machine-learning/concept-data-exfiltration-prevention
- **Zero Trust + Entra Conditional Access device compliance ("require compliant device"):** https://learn.microsoft.com/en-us/entra/identity/conditional-access/policy-all-users-device-compliance
- **Microsoft SDL + STRIDE threat modeling:** https://learn.microsoft.com/en-us/azure/security/develop/threat-modeling-tool-threats

Two caveats carried from the research into this design: (1) commodity **laptops cannot run Azure confidential VMs** and SGX is gone from client CPUs, so on-endpoint confidential compute is not realistic today (§8); and (2) Foundry/Azure network isolation constrains the **workload's** egress but does **not** defend against a **local admin / host owner**, so an untrusted host must be treated as fully adversarial and simply kept away from high-classification plaintext (§7, §8).
