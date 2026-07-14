# Foundry routing gateway (flow F9 / boundary B6)

The harvest-phase, Foundry-facing routing gateway: the single ingestion point where an Azure AI
Foundry / tenant request becomes a signed, classified OneCompute job. It authenticates the calling
tenant, enforces a per-tenant classification and region policy, stamps tenant/region **provenance**
into the Ed25519-signed manifest, and enqueues the job through the existing signed-submit path.

This is the enforceable scaffold for boundary **B6** and flow **F9** described in
`pitch/OneCompute-Threat-Model.md` section 24 and `azure-routing.md` sections 3, 9, and 10. The live
Foundry connection is stubbed (we have no live Foundry): a real Foundry adapter and an Entra-backed
tenant registry plug into this scaffold and remain roadmap (see "PoC scope" below).

## Why a gateway at all

In the harvest phase the trust model inverts (`azure-routing.md` section 2): a routed job carries
**real, tenant-owned data**, and the machine that runs it is a potentially curious host. Two questions
have to be answered before any tenant data touches the fleet, and they must be answered in ONE
authoritative place rather than scattered across callers:

- **Who may inject a routed workload, at what data classification, for which tenant/region?**
- **What is the authenticity/provenance of a routed job once it is on the fleet?**

The gateway is that one place. It is the only new way to submit a job that carries provenance, and it
does not change or weaken the ordinary `POST /jobs` path.

## Endpoint

`POST /foundry/jobs` (`src/orchestrator/app.py`). Body is a `FoundryRoutingRequest`
(`src/contracts/models.py`):

```
tenant_id: str
region: str
kind: JobKind
input: dict
requires: Requires | None = None
units: int = 1
data_classification: DataClassification   # authoritative, set from the request (bounded below)
```

The response is the same `SubmitResponse` (`{job_id}`) that `POST /jobs` returns.

## Inert by default

The gateway is **off unless it is configured**. `create_app` gains a
`foundry_tenants: dict[str, FoundryTenant] | None = None` parameter. With `None` or an empty registry
(the default), `POST /foundry/jobs` accepts nothing: every request is refused (401) and nothing is
enqueued, and all existing behavior is unchanged. A tenant registry is what turns the feature on.

Tenant registry entries are `FoundryTenant` (`src/contracts/models.py`):

```
tenant_id: str
token: str                               # shared secret, compared in constant time
max_classification: DataClassification   # highest classification this tenant may route
allowed_regions: list[str]               # region allow-list; EMPTY = deny all (fail-closed)
```

The registry is in-memory config for the PoC. A real deployment backs it with Entra tenants rather
than a static dict; that adapter is roadmap.

## Tenant authentication

For a configured registry, the claimed `tenant_id` is looked up and the caller must present
`Authorization: <scheme> <tenant.token>` where the scheme is `Bearer`. The token is compared with
`hmac.compare_digest` (constant time). All of the following fail closed with **401** and an audited
`auth_failed` event whose detail is prefixed `foundry:`:

- the registry is empty/None (feature off),
- the claimed tenant is unknown,
- the token is missing, wrong, or presented under the wrong scheme.

Because the claimed `tenant_id` is bound to that tenant's own secret, the `tenant_id` later stamped
into the manifest provenance is the authenticated one: a caller cannot claim to be tenant A while
holding tenant B's credential.

## Per-tenant policy (fail-closed)

Only after authentication does the gateway enforce policy. A violation returns **403** and is audited
as a `foundry_denied` event.

1. **Classification clearance.** The requested `data_classification` must be `<=` the tenant's
   `max_classification` by the `routing_policy.CLASSIFICATIONS` rank
   (`public < internal < confidential < restricted`). A tenant cleared only to `internal` cannot
   route `confidential` data. The comparison is `routing_policy.classification_cleared`, which is
   pure and **fails closed**: an unknown or misspelled classification on either side denies.
2. **Region policy.** The requested `region` must be in the tenant's `allowed_regions`. An empty
   allow-list denies every region (fail-closed), so a tenant with no regions configured can route
   nowhere.

The gateway sets the job's classification **authoritatively** from the (now bounded) tenant request.
It never trusts a device's self-report; the device trust tier remains server-assigned
(`docs/routing-policy.md`).

## Signed provenance

On success the gateway builds the job through the **same signed-submit path** as `POST /jobs`
(`src/orchestrator/submit.py:submit_job`), passing a `RoutingProvenance` that `submit_job` stamps into
the manifest:

```
RoutingProvenance = { tenant_id: str, region: str }
JobManifest.provenance: RoutingProvenance | None = None   # default None keeps every other job unchanged
```

Because provenance lives INSIDE the Ed25519-signed `JobManifest`, it is tamper-evident: a relay or a
curious worker cannot rewrite which tenant/region a job was routed for without breaking the signature
that the worker verifies before running (`src/trust/signing.py`, `src/worker/agent.py`). A routing
decision is also written to the hash-chained audit stream as a `foundry_routed` event carrying the
tenant, region, classification, and job id (`GET /events`, verifiable via `GET /events/verify`).

## It reuses, it does not weaken

- The gateway is the ONLY new way to submit a job that carries provenance.
- It does not change `POST /jobs`; an ordinary submission still has `provenance = None`.
- It does not touch the scheduler's routing decision. The existing device-tier gate
  (`routing_policy.may_route`, enforced in `GET /jobs/next`) still decides which device a job of a
  given classification may land on. The gateway's job is only to set that classification
  authoritatively and to attach provenance. A `confidential` routed job is therefore still withheld
  from an `untrusted` worker (204) and only assigned once IT elevates that device to `sanctioned`; a
  `public` routed job reaches an untrusted worker as before.

## PoC scope (what is real vs roadmap)

**Real and enforceable here:** the inert-by-default endpoint, constant-time per-tenant Bearer
authentication, fail-closed classification + region policy, signed manifest provenance, reuse of the
signed-submit path, and end-to-end pass-through of the existing device-tier gate. Covered by
`tests/orchestrator/test_foundry_gateway.py`.

**Deliberately stubbed / roadmap** (tracked in `azure-routing.md` section 10 and the threat model
section 24):

- The live Azure AI Foundry adapter (the `compute: onecompute` target and Foundry's result/governance
  return path). This gateway is the seam it plugs into.
- An **Entra-backed tenant registry** instead of the in-memory config dict.
- DLP / residency enforcement and Purview label propagation at the boundary.
- Region/residency as a first-class routing constraint next to classification (today it is an
  allow-list check at ingestion).

## References (do not edit here; the Chief of Staff integrates these)

- `azure-routing.md` sections 3 (architecture overview), 9 (Foundry integration), 10 (shipped vs
  roadmap).
- `pitch/OneCompute-Threat-Model.md` section 24: boundary **B6** (Foundry / routing gateway) and flow
  **F9** (routed workload + data classification, per-tenant).
- `routing-policy.md`: the classification-vs-tier gate the gateway feeds.
- `device-attestation.md`: how a device earns a higher trust tier that the gate then honors.

## Code map

| Path | Role |
|---|---|
| `src/orchestrator/app.py` (`POST /foundry/jobs`, `_authenticate_foundry_tenant`) | The gateway endpoint, tenant auth, and policy enforcement. |
| `src/contracts/models.py` (`FoundryTenant`, `FoundryRoutingRequest`, `RoutingProvenance`, `JobManifest.provenance`) | The request/registry contracts and the signed provenance field. |
| `src/orchestrator/routing_policy.py` (`classification_cleared`) | Fail-closed classification-clearance rank check. |
| `src/orchestrator/submit.py` (`submit_job(..., provenance=...)`) | The shared signed-submit path, now stamping provenance. |
| `tests/orchestrator/test_foundry_gateway.py` | Inert-by-default, tenant auth, classification/region policy, signed provenance, and tier-gate end-to-end. |
