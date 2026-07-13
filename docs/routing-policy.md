# Classification-aware, server-assigned device-tier routing (fail-closed)

In the harvest phase a real workload carries a **data classification** (how sensitive its input is)
and every device carries a **trust tier** (how much the fleet operator trusts that machine). A job
is assigned to a device only when the device's tier is high enough for the job's classification, so
**high-sensitivity data never lands on a low-trust device**.

This is a routing/placement control, not an isolation control: it decides *where a job is allowed to
run*, complementing the per-job sandbox (`src/isolation/`) that governs *how* it runs and the signed
manifest (`src/trust/`) that governs *what* runs.

## The two ladders

Data classifications, low to high sensitivity:

| Classification | Meaning (example) |
|---|---|
| `public`       | non-sensitive / already-public inputs |
| `internal`     | ordinary internal data (the conservative default) |
| `confidential` | sensitive business data |
| `restricted`   | the most sensitive data (regulated, secrets-adjacent) |

Device trust tiers, low to high, **assigned server-side**:

| Trust tier             | Meaning (example) |
|---|---|
| `untrusted`            | unmanaged / BYOD / unknown posture (the fail-closed default) |
| `managed`             | enrolled + managed device (e.g. Intune-managed, compliant) |
| `sanctioned`          | managed and explicitly cleared for sensitive internal data |
| `confidential_compute` | hardware-isolated / TEE-backed, cleared for the most restricted data |

## The fail-closed matrix

The four classifications map one-to-one, by rank, onto the four tiers: a job needs a device whose
tier rank is at least the required minimum for its classification.

| Classification \ minimum device tier | required tier |
|---|---|
| `public`       | `untrusted` |
| `internal`     | `managed` |
| `confidential` | `sanctioned` |
| `restricted`   | `confidential_compute` |

Full decision table (`may_route(classification, trust_tier)` in `src/orchestrator/routing_policy.py`):

| classification \ tier | untrusted | managed | sanctioned | confidential_compute |
|---|---|---|---|---|
| **public**       | allow | allow | allow | allow |
| **internal**     | deny  | allow | allow | allow |
| **confidential** | deny  | deny  | allow | allow |
| **restricted**   | deny  | deny  | deny  | allow |

`may_route` **fails closed**: an unknown or misspelled classification, an unknown tier, or any
non-string input returns `deny`. It is a pure, standard-library function that never raises, so it is
safe to call on the scheduler's hot path. A new sensitivity level or a typo can therefore never
silently downgrade to "allow"; it defaults to refusing the placement.

## Why the tier is server-assigned and never self-reported

The device tier lives in the `workers.trust_tier` column (`src/contracts/schema.sql`) and is set by
the control plane. It defaults to the lowest tier (`untrusted`) and is **never read from the worker's
`Capability`**.

The threat this closes: a **rogue worker that claims a high tier to attract confidential data**. If
the orchestrator trusted a self-reported tier, an attacker who could stand up a worker (or tamper
with a compromised one) would simply advertise `confidential_compute` and start receiving
`restricted` jobs on an unmanaged box, i.e. an Information Disclosure across a trust boundary. Making
the tier server-assigned removes that lever entirely. This mirrors the existing OneCompute rule that
**credit is metered on the job's actual GPU requirement from the signed manifest, never on a worker's
self-reported `has_gpu`** (see `src/orchestrator/app.py` results crediting and
`docs/architecture.md`): trust decisions are never delegated to the party being trusted.

A `Capability` may still carry an advisory `attested_tier` string, but the orchestrator ignores it
for routing. It exists only for future attestation tooling and diagnostics; treating it as
authoritative would reintroduce exactly the threat above. The
`test_self_reported_capability_tier_is_ignored` test pins this behavior.

The classification, by contrast, lives inside the **signed** `JobManifest`
(`data_classification`, default `internal`). Because it is covered by the Ed25519 signature the
worker verifies before running, a worker cannot downgrade a job's classification to receive data it
is not cleared for without invalidating the signature.

## How IT elevates a device

A device leaves the fail-closed default only through the admin endpoint:

```
POST /workers/{worker_id}/tier
Authorization: Bearer <operator-token>
{ "trust_tier": "sanctioned" }
```

- It is gated by the operator token via the same `_require_admin_token` helper that protects
  `POST /workers/{id}/approve` and `DELETE /workers/{id}`. Auth is checked **before** the 404, so an
  unknown worker without a token gets `401`, not `404`. When no operator token is configured (the
  local demo) the gate is open, exactly like `approve`.
- An unknown or misspelled tier value is rejected with `400`, so a fat-fingered assignment cannot
  quietly leave a device in an unexpected state; it stays at whatever tier it already had.
- A worker restart (re-register) **preserves** an already-assigned tier, the same way approval is
  preserved on re-register. Registration never lowers or raises the tier; only this endpoint does.

The assigned tier is surfaced per worker in `GET /state` (`WorkerView.trust_tier`) for the dashboard,
and each assignment emits a `tier_assigned` event into the tamper-evident audit feed.

## What is real vs. PoC-scoped

**Real and working here:** the signed `data_classification` on every job, the server-assigned
`trust_tier` column defaulting to `untrusted`, the pure fail-closed `may_route` policy, its
enforcement inside `pick_job_for` (`src/orchestrator/scheduler.py`) so no ineligible job is ever
leased, the operator-token-gated `POST /workers/{id}/tier` endpoint, and the `/state` surfacing.

**Deliberately simplified for the PoC:** tier assignment is **manual/admin today**, a human operator
elevates a device out-of-band via the endpoint above. There is no automated posture check yet, so a
device is exactly as trusted as an admin has declared it to be. The tiers and classifications are a
fixed four-level ladder rather than an org-configurable policy.

**Roadmap:** **attestation-backed tiering**, where a device's tier is derived from verifiable signals
(Intune/Entra device compliance, a TPM-backed device certificate, or a confidential-compute
attestation) instead of an admin's say-so, so `managed` really means "Intune-attested compliant" and
`confidential_compute` really means "hardware-attested TEE". This lines up with the device-identity
binding already prototyped in `docs/device-identity.md` and with **Phase 3 (Intelligent Routing)** and
**Phase 4 (Enterprise Security: Intune deployment, Entra ID identity)** of
`docs/Azure_Integration_Plan.md`. Org-configurable classification/tier ladders and per-workload policy
are also roadmap.

## Example workloads and the demo (PoC scope)

The example/synthetic workloads (fractal, optimize, the AI kinds) carry no sensitive data, so the demo and dev scripts classify them `public` and they route onto any admitted device: `scripts/submit_jobs.py` and `scripts/smoke.py` submit with `classification="public"`, and `scripts/demo_fleet.py` also elevates its three managed corporate machines to the `managed` tier after approval (showcasing the out-of-band IT tiering step). A real, unlabelled workload stays the conservative `internal` default and therefore needs a `managed`-or-higher device, so the fail-closed posture holds for anything that is not explicitly known-public.

## Related

- `docs/azure-routing.md` - the harvest-phase safe-routing architecture spec this control implements (classification model, device tiers, egress, confidential-compute roadmap).
- `docs/pitch/OneCompute-Threat-Model.md` - trust boundaries (notably B4 Submitter/Orchestrator and
  the §3 data inventory + classification) and the Information-Disclosure threat this control mitigates.
- `docs/Azure_Integration_Plan.md` - the Intune/Entra device-identity and intelligent-routing phases
  that attestation-backed tiering builds on.
- `docs/device-identity.md` - the complementary control that binds a worker's token to its device key.
