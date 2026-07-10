# Device-identity binding (worker token bound to TLS client-cert fingerprint)

OneCompute already authenticates each worker with a per-worker bearer token (issued at
`/register`, checked in `_require_worker_token` with a constant-time compare in
`src/orchestrator/app.py`) and already supports mutual TLS (a worker presents a client cert). The
residual gap this feature closes: a **stolen bearer token alone** lets an attacker impersonate a
worker from any machine. Device-identity binding ties the token to the worker's TLS
client-certificate fingerprint, so the token is useless without the device's key.

Binding is **opt-in and default OFF**. With it off, behavior is exactly as before.

## What gets bound

- The **device fingerprint** is the lowercase hex SHA-256 of the client certificate's DER bytes.
- It is delivered in the request header `X-Client-Cert-SHA256`.
- On `/register` (when binding is on and the header is present), the fingerprint is stored in the
  new nullable `workers.cert_fingerprint` column (`src/contracts/schema.sql`; added to the guarded
  `ALTER TABLE` loop in `src/orchestrator/db.py` so a pre-upgrade persistent DB gains it). The
  change is additive and backward-compatible.
- On every authenticated call, when binding is on **and** the worker has a stored fingerprint,
  `_require_worker_token` requires the presented `X-Client-Cert-SHA256` to match the stored value
  (constant-time compare). A mismatch or a missing header returns `401` and emits an audited
  `auth_failed` event with detail `device_fingerprint_mismatch`.

A worker that registered **without** a fingerprint stays token-only even when binding is enabled
globally (there is nothing to enforce against), so a mixed fleet keeps working during rollout.

## Enabling it

- Orchestrator: `uv run python -m orchestrator --bind-device-identity` (threads
  `bind_device_identity=True` into `create_app`).
- Worker: pass `--client-cert` (paired with `--client-key`). The worker computes the cert's
  SHA-256 DER fingerprint once at startup and sends it as `X-Client-Cert-SHA256` on register and
  every authenticated request. It is best-effort: with no client cert (or an unreadable one) the
  worker sends no fingerprint header and behaves exactly as before.

## Honest header-trust framing

**The worker-supplied `X-Client-Cert-SHA256` header is a PoC / proxy-shape stand-in, not a full
cryptographic binding on its own.** In production this header is injected by the
**mTLS-terminating front end / reverse proxy after it verifies the client certificate**, and is
**not** trusted from the open internet: the proxy strips any client-supplied copy and sets it from
the verified peer certificate. In this proof-of-concept (and behind such a proxy) the worker sends
the header itself so a pilot can exercise the exact request shape. Do not read this control as
proof that the caller holds the private key unless the header is set by a trusted mTLS front end.
The real cryptographic guarantee comes from the mutual-TLS handshake terminating at that front
end; this feature binds the application-layer identity (the bearer token) to the fingerprint that
handshake produces.

## STRIDE: Spoofing (B3)

This is the Spoofing control for trust boundary **B3** (worker to control plane). The threat: an
attacker who exfiltrates a worker's bearer token replays it from another host to impersonate the
worker, pull signed jobs, and claim credits. Binding the token to the device's client-cert
fingerprint means a replayed token from a machine that cannot present the same verified
certificate fails closed (`401`, audited), raising the bar from "possess a secret string" to
"possess the device's private key". Because every rejection emits an `auth_failed`
(`device_fingerprint_mismatch`) event into the tamper-evident audit stream, token-replay attempts
are detectable and exportable to a SIEM.

## Microsoft device identity (Intune/Entra)

The posture mirrors Microsoft's device-identity model. In an Entra ID / Intune environment a
managed device holds a certificate provisioned to its hardware (SCEP/PKCS or a TPM-backed key), is
enrolled and attested by Intune, and gains access to resources through Conditional Access policies
that require a **compliant, managed device** rather than a credential alone. Device-identity
binding here is the OneCompute analogue: the worker's client certificate is the device identity,
the fingerprint is the stable handle for it, and the orchestrator enforces "this token only from
this device" the way Conditional Access enforces "this identity only from a compliant device". A
production deployment would source the certificate from the Intune-managed device store and
terminate mutual TLS at an Entra-integrated front end that injects the verified fingerprint.

## Scope and roadmap

- **Real here:** the additive schema column, opt-in binding in `create_app` /
  `_require_worker_token`, the `--bind-device-identity` orchestrator flag, the worker-side
  fingerprint computation and header, constant-time matching, and audited `auth_failed` on
  mismatch or absence.
- **Roadmap:** enforcing that the fingerprint is set only by a trusted mTLS front end (not the
  worker), full Entra Conditional Access / Intune compliance integration, and certificate rotation
  / revocation handling.
