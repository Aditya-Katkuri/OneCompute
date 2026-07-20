# OneCompute: Azure Compute + CISO Phase-0 hand-off packet

Audience: Azure Compute (functionality owner), the CISO office (safety owner), and CELA / Privacy (legal + data). This is the executive synthesis for the **Phase-0 co-development decision** in `Azure_Integration_Plan.md`: sanction a bounded pilot to route eligible Azure / Azure AI Foundry workloads onto idle, managed corporate devices. It pulls the measured opportunity and the shipped safety controls into one place and points to the detailed documents behind each claim.

Nothing in the harvest phase runs on a person's machine until Azure Compute and the CISO office co-develop and sanction it. This packet is the starting position for that review.

## 1. The ask

Co-develop and sanction a **bounded, low-sensitivity harvest pilot**: route a small set of delay-tolerant Foundry workloads (evaluation, batch inference, agent runs) onto **already-managed, AC-powered corporate devices**, under the shipped controls in section 4, while a parallel **measurement-only pilot** (already running) quantifies the recoverable capacity. Expand the envelope only as the higher-assurance tiers (section 6) land.

## 2. Why this is worth a Phase-0

- **The compute already exists, powered and managed.** Microsoft already owns, secures, and powers a large endpoint fleet that is ~90% idle. Reclaiming a conservative slice offsets internal cloud/batch demand.
- **The opportunity separates current capacity from wake potential.** `docs/Financial_Impact.md` frames a modeled Year-1 gross of ~$125.6M Azure-equivalent capacity. The pilot replaces utilization, power, and availability assumptions with device data. `scripts/business_case.py` prints a currently executable awake-on-AC value and a separate wake-enabled potential based on inferred sleep/offline gaps. The latter assumes sanctioned wake, power, and a 75% CPU allocation and is not shipped capability. A voluntary measurement pilot is running now (`docs/measurement-pilot.md`); both values and their assumptions belong in the Azure Compute + CISO review.
- **The safety model is built, not promised.** The controls a CISO would ask for are implemented, fail-closed, and inert-by-default (section 4), with an honest line between shipped and roadmap (section 6).

## 3. What OneCompute is (one paragraph)

A worker on each opt-in machine registers with a central orchestrator over an **outbound-only** connection (no inbound ports; works through corporate firewalls), pulls **Ed25519-signed, sandboxed** jobs, runs them only in the machine's **learned spare headroom**, and **yields in milliseconds** when the employee's own CPU/GPU demand rises. It never runs on battery and never wakes a person's laptop. See `idea.md`, `architecture.md`.

## 4. What is shipped today (the enforceable safety core)

Every control below is in code, fail-closed, and (where it touches a real external system) inert until that system is configured, so default behavior is unchanged.

| Control | What it guarantees | Reference |
|---|---|---|
| **Classification-gated routing** | A job carries a `data_classification` in its **signed** manifest; the scheduler assigns it only to a device whose trust tier clears it. Confidential/Restricted data provably cannot be leased to an ordinary laptop. Unknown inputs deny (fail-closed). | `routing-policy.md`, `src/orchestrator/routing_policy.py` |
| **Server-assigned device trust tiers** | A device's tier (`untrusted` < `managed` < `sanctioned` < `confidential_compute`) is assigned server-side and **never read from the worker's self-report**; a new device defaults to `untrusted` (public data only). | `routing-policy.md` |
| **Attestation-derived tiering** | A tier can be derived from a device-posture attestation **verified against a configured authority key** (never the worker's claim), mapping compliant/managed/sanctioned/TEE posture to a tier. Inert until an authority is configured. | `device-attestation.md` |
| **Separation of duties** | A dedicated `--admin-token` gates device administration (approve / disconnect / set-tier) separately from job submission, so a mere submitter cannot elevate a device to receive sensitive data. | Threat model 24.3 |
| **Foundry ingestion gateway (F9)** | `POST /foundry/jobs` authenticates each tenant, enforces a per-tenant classification ceiling and region allow-list (fail-closed), and stamps `{tenant_id, region}` provenance into the **signed** manifest. Inert until tenants are configured. | `foundry-gateway.md`, `azure-routing.md` §9 |
| **No egress by default** | Container jobs run `--network none`; a routed job cannot open outbound connections to exfiltrate data. Disposable sandbox, no persistence. | `architecture.md` §3.3 |
| **Signed, tamper-evident everything** | Manifests are Ed25519-signed and input-hash-bound; a worker verifies before running and can pin an out-of-band trusted signer. Every routing/admin decision is written to a **hash-chained** audit stream (`GET /events/verify`). | `architecture.md` §9, `audit-log.md` |
| **Instant yield + never-on-battery** | The demand-adaptive governor yields the machine back in milliseconds on the employee's own CPU/GPU spike (not on bare input), runs only in learned headroom, and never on battery. | `architecture.md` §3.2 |

Verify it: `uv run pytest -q` (the suite is green), and `uv run python scripts/demo_fleet.py` stands up a real orchestrator and workers, fans classified workloads across managed devices, and shows the instant-yield path, with nothing mocked.

## 5. The safety model, for the CISO office

The harvest phase **inverts the trust model**: a routed workload's data must be protected from the *machine's operator*, because software isolation on hardware an employee controls is not a confidentiality guarantee (threat model 24.1). Everything follows from one rule:

> A machine may only receive data whose sensitivity its established trust level can actually protect. Confidential/Restricted data does not belong on an ordinary laptop; it belongs on a hardware-attested confidential-compute node, or it stays in Azure.

That rule is enforced by the shipped controls in section 4 and is grounded in Microsoft's own model: device trust tiers mirror Entra Conditional Access "require compliant device"; the confidential-compute stance mirrors attestation-gated Secure Key Release; classification mirrors Purview sensitivity labels (`azure-routing.md`, with cited references). The **honest hard limit**: commodity laptops cannot run Azure confidential VMs, so Confidential/Restricted data stays in Azure until an attested TEE tier exists; laptops get device-attested tiers for lower-classification work only.

Residual risks are named and owned jointly (curious-host disclosure on non-TEE tiers; correct upstream classification labeling): threat model sections 19 and 24.6.

## 6. Shipped vs roadmap (honest scope)

**Shipped:** classification-gated routing, server-assigned + attestation-derived tiers, the admin/submit separation, the Foundry ingestion gateway scaffold, no-egress-by-default sandboxing, signed manifests + hash-chained audit, the measurement pilot and the measured business-case pipeline.

**Roadmap (named in `azure-routing.md` §10, requiring this co-development):** wiring the real attestation authority (Azure Attestation / Intune-Entra compliance) behind the shipped tiering scaffold; Purview label propagation; the confidential-compute tier's real TEE backing; region/residency as a first-class constraint; the live Foundry adapter and an Entra-backed tenant registry behind the shipped gateway.

## 7. What each stakeholder is being asked to co-develop

- **Azure Compute (functionality):** the `compute: onecompute` / `compute: auto` Foundry target and the live adapter behind the shipped gateway; which workload classes are eligible; capacity and scheduling expectations. `Azure_Integration_Plan.md` Phases 1-3.
- **CISO office (safety):** validate the section 4 controls; sanction the initial classification/tier envelope (start `public`/`internal` on `managed` devices only); own the residual-risk register with CELA; define the attestation authority and DLP at the gateway boundary. `pilot-security-approval.md`.
- **CELA / Privacy:** confirm data-classification handling, the consent model (the measurement pilot collects only CPU/GPU/RAM plus AC/idle fractions, never activity or files), and the DPIA path for the employee-to-node linkage. `measurement-pilot.md` §1, threat model §17.

## 8. The concrete Phase-0 deliverables

1. Stand up the measurement pilot across a small voluntary group; produce the **measured** headroom + business-case readout (`measure_report.py`, `business_case.py`).
2. CISO sanctions an initial envelope: `public`/`internal` workloads on `managed`, attested devices only; no Confidential/Restricted on endpoints.
3. Azure Compute + OneCompute wire the live Foundry adapter behind the shipped gateway, and the attestation authority (Intune/Entra) behind the shipped tiering scaffold.
4. Joint residual-risk register and a go/no-go on a bounded production harvest pilot.

## 9. References

- Opportunity: `docs/Financial_Impact.md`, `scripts/business_case.py`, `docs/measurement-pilot.md`.
- Plan: `docs/Azure_Integration_Plan.md`.
- Safety design: `docs/azure-routing.md`, `docs/pitch/OneCompute-Threat-Model.md` (§24, v1.6), `docs/routing-policy.md`, `docs/device-attestation.md`, `docs/foundry-gateway.md`, `docs/pitch/pilot-security-approval.md`.
- System: `docs/idea.md`, `docs/architecture.md`, `docs/audit-log.md`, `docs/device-identity.md`.
