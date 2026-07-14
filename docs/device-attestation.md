# Attestation-derived device trust tiers (fail-closed, inert until configured)

Classification-aware routing (`docs/routing-policy.md`) only lets a job land on a device whose
**trust tier** is high enough for the job's **data classification**. Until now a device left the
fail-closed `untrusted` default only when an admin set its tier by hand via
`POST /workers/{id}/tier`. This control lets a device's tier be **derived automatically from a
verified device-posture attestation**, so a tier can come from verifiable posture (mirroring
Intune/Entra device compliance plus an Azure Attestation / MAA TEE claim) instead of only an
operator's say-so.

It is deliberately conservative. Like the MXC OS-sandbox backend (`docs/mxc-sandbox.md`), it is
**fail-closed and inert until configured**: with no attestation authority key set, the feature does
nothing and every worker keeps today's `untrusted` default. Ed25519 stands in for the real
attestation authority in this PoC; production MAA/Intune integration is roadmap.

## The attestation

A worker may present a `DeviceAttestation` (`src/contracts/models.py`) at registration: a signed,
time-boxed device-posture claim.

| Field | Meaning |
|---|---|
| `worker_id` | The device this claim is bound to. Must equal the registering worker. |
| `compliant` | Device-compliance signal (stands in for Intune/Entra compliance). |
| `managed` | Device is enrolled / corporate-managed. |
| `sanctioned` | Device is explicitly cleared for sensitive internal data. |
| `tee` | Device presents a valid hardware-TEE attestation (stands in for MAA). |
| `issued_at` | When the authority issued the claim. |
| `expires_at` | Optional expiry; `None` means no expiry. |
| `signature` | Hex Ed25519 signature over the canonical claim bytes. |
| `signer_pubkey` | Hex public key of the signer. **Advisory only** (diagnostics); never trusted for the decision. |

The **signed bytes** (`canonical_claims_bytes`, `src/trust/attestation.py`) cover the posture flags
bound to `worker_id` and the `issued_at`/`expires_at` window, and **exclude** `signature` and
`signer_pubkey`. Serialization is pydantic JSON mode (datetimes as ISO-8601) fed through the shared
canonical JSON encoder, so the authority and the orchestrator compute byte-identical input even
across the HTTP round trip.

The attestation rides as an optional `attestation` field on the registration `Capability`. A
Capability-only registration (no `attestation`) is unchanged, and any presented attestation is
**ignored unless it verifies**.

## Verification (`verify_attestation`)

`verify_attestation(att, trusted_authority_pubkey_hex, worker_id, now)` returns `True` only if
**every** guard holds, and is a pure function that **never raises** (any malformed input fails
closed):

1. **Configured.** A trusted authority key is set. With none, the feature is **inert** and returns
   `False`.
2. **Verified.** The signature verifies against **that trusted authority key** over the canonical
   claim bytes, **never** against `att.signer_pubkey` and **never** against the worker's self-report.
3. **Bound.** `att.worker_id` equals the registering `worker_id`, so one device's attestation cannot
   be **replayed** to elevate another.
4. **Unexpired.** `att.expires_at` is `None` or strictly after `now`.

### Why the tier is derived from a verified authority signature, never the self-report

This is the same principle that makes the existing tier server-assigned: **trust decisions are never
delegated to the party being trusted.** A worker signs nothing that the orchestrator will believe;
only the configured **authority** key can produce a claim the orchestrator acts on. The
`signer_pubkey` field is carried for diagnostics but is explicitly **not** consulted, because
verifying against a key the claim carries would let a worker mint its own "attestation," sign it with
its own key, set `signer_pubkey` to match, and self-elevate. That is exactly the rogue-worker threat
`docs/routing-policy.md` closes for the self-reported `attested_tier`; deriving the tier from a
verified authority signature preserves it. Binding to `worker_id` and time-boxing close attestation
replay and stale-posture reuse.

## Derivation (`derive_tier`)

Called **only on a verified claim**, mapped highest-first and fail-closed:

| Verified posture | Derived tier |
|---|---|
| `tee` | `confidential_compute` |
| `managed and compliant and sanctioned` | `sanctioned` |
| `managed and compliant` | `managed` |
| anything less / malformed | `untrusted` |

These are the same four tiers as `src/orchestrator/routing_policy.py` (`untrusted < managed <
sanctioned < confidential_compute`), so a derived tier feeds `may_route` directly. Anything short of
a sufficient posture (for example `sanctioned` without `managed`+`compliant`, or `managed` without
`compliant`) collapses to `untrusted`.

## Registration wiring and admin-pin precedence

`create_app(attestation_pubkey=...)` configures the trusted authority key (`None` = inert). In
`POST /register`, when the key is configured **and** the worker presented an attestation **and**
`verify_attestation` passes, the orchestrator derives the tier and stores it for a **not-pinned**
worker; a re-register **re-derives**. If verification fails, or no key / no attestation is present,
the stored tier is left untouched (a new worker keeps the `untrusted` default). Registration never
raises on an attestation, and never trusts `signer_pubkey`. Each derivation emits a `tier_derived`
event into the tamper-evident audit stream.

**Admin pin wins.** When an admin sets a tier via `POST /workers/{id}/tier`, the worker row is marked
`tier_pinned = 1` (`src/contracts/schema.sql`, with a back-compat `ALTER TABLE` in
`src/orchestrator/db.py`). Attestation-derived tiering only updates **un-pinned** workers, so IT's
explicit decision is sticky: a later re-register whose attestation would derive a different (even
higher) tier does **not** override a pinned tier. This keeps a human operator's override authoritative
over automated posture.

## Running it

```
uv run python -m orchestrator --attestation-key <hex-ed25519-public-key>
```

`--attestation-key` defaults to `$ONECOMPUTE_ATTESTATION_KEY`. When set, the startup banner prints an
"Attestation tiering: ON" line. Unset leaves the feature inert, so default behavior is identical to
today and every existing flow is unchanged.

## Security invariants (pinned by tests)

`tests/trust/test_attestation.py` and `tests/orchestrator/test_attested_tier.py` prove:

- **Inert by default:** with no authority key, a worker presenting any attestation (even a valid
  self-signed one) registers `untrusted`.
- **Verified-only:** an authority-signed attestation yields the derived tier; one signed by a
  different key, a tampered claim, or a claim whose `signer_pubkey` is the worker's own key yields
  `untrusted`.
- **Bound:** an attestation whose `worker_id` differs from the registering worker is rejected (no
  cross-device replay).
- **Expiry:** an expired attestation is rejected.
- **Derivation matrix:** `tee -> confidential_compute`, `managed+compliant+sanctioned -> sanctioned`,
  `managed+compliant -> managed`, anything less / malformed -> `untrusted`.
- **Admin-pin precedence:** after an admin pins `sanctioned`, a re-register that would derive
  `managed` does not downgrade it (and a pin holds even against a stronger `tee` attestation).
- **End-to-end:** a worker attesting as TEE-capable can be assigned a `restricted`-classified job by
  the scheduler; the same worker without a valid attestation stays `untrusted` and is denied.

## What is real vs. roadmap

**Real here:** the signed `DeviceAttestation` contract; fail-closed, inert-until-configured
`verify_attestation` (authority-key-only, device-bound, time-boxed); the fail-closed `derive_tier`
matrix; `/register` wiring that derives an un-pinned worker's tier and audits it; admin-pin
precedence; the `--attestation-key` switch.

**Roadmap:** replacing the Ed25519 authority stand-in with real **Microsoft Azure Attestation (MAA)**
tokens for the `tee` claim and **Intune/Entra device-compliance + Conditional Access** signals for
`managed`/`sanctioned`, so each flag is earned from verifiable Microsoft posture rather than an
authority's assertion. This is the attestation-backed tiering named as roadmap in
`docs/routing-policy.md` and `docs/azure-routing.md` §5/§8, and lines up with the device-identity
binding in `docs/device-identity.md`.

## Related

- `docs/routing-policy.md` - the shipped classification-aware routing this tiering feeds; the
  server-assigned tier and the rogue-worker threat it closes. (Do not edit here.)
- `docs/azure-routing.md` - the harvest-phase safe-routing spec: device tiers (§5) and the
  attestation-gated confidential-compute roadmap (§8). (Do not edit here.)
- `docs/mxc-sandbox.md` - the fail-closed, inert-until-a-real-backend-exists pattern this mirrors.
- `docs/device-identity.md` - the complementary control binding a worker's token to its device key.
