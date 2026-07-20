# Device-identity binding

OneCompute authenticates each worker with a per-worker bearer token and can require mutual TLS.
Device-identity binding closes the remaining token-replay gap by tying that bearer token to the
SHA-256 fingerprint of the worker's **TLS-verified peer certificate**. A stolen token is not enough;
the caller must also complete TLS with the same device private key.

Binding is opt-in for local/demo use and mandatory in the secure measurement-pilot preset.

## How the fingerprint is established

Uvicorn verifies the client certificate against `--tls-client-ca` during the TLS handshake.
`orchestrator.mtls_protocol.VerifiedClientCertH11Protocol` then:

1. Reads the verified peer certificate from the connection's TLS object with
   `getpeercert(binary_form=True)`.
2. Computes the lowercase SHA-256 of the certificate DER bytes.
3. Injects that value into the request's ASGI scope.

The FastAPI application reads only that server-injected scope value. It does not trust
`X-Client-Cert-SHA256` or any other client-supplied fingerprint header. The worker does not send a
fingerprint header.

The custom protocol is used whenever the orchestrator is configured with a client CA. The
`--bind-device-identity` flag fails startup unless `--tls-cert`, `--tls-key`, and
`--tls-client-ca` are all present.

## Registration behavior

When binding is enabled:

- Registration without a verified peer fingerprint returns `401`.
- A new worker ID is stored with the verified certificate fingerprint.
- Re-registration from the same certificate may rotate the bearer token.
- Re-registration from a different certificate is rejected before token rotation.
- A legacy worker ID with no stored fingerprint is rejected with `409`; the operator must remove
  and re-enroll it rather than silently claiming it.

On every authenticated worker call, `_require_worker_token` checks both:

1. The bearer token, using constant-time comparison.
2. The verified peer-certificate fingerprint, also using constant-time comparison.

A missing or mismatched peer certificate returns `401` and emits an audited
`device_fingerprint_mismatch`.

## Enabling it

```powershell
uv run python -m orchestrator `
  --tls-cert C:\OneCompute\pki\server.crt `
  --tls-key C:\OneCompute\pki\server.key `
  --tls-client-ca C:\OneCompute\pki\worker-ca.crt `
  --bind-device-identity
```

The worker presents its certificate through the TLS client:

```powershell
uv run python -m worker `
  --url https://onecompute.contoso.com:8080 `
  --tls-ca C:\ProgramData\OneCompute\pki\server-ca.crt `
  --client-cert C:\ProgramData\OneCompute\pki\device.crt `
  --client-key C:\ProgramData\OneCompute\pki\device.key
```

`--secure-measurement-pilot` enables binding and requires the complete TLS/mTLS configuration.

## Reverse proxies and load balancers

The shipped binding is cryptographic when Uvicorn terminates TLS through the custom protocol.
If a reverse proxy or load balancer terminates TLS instead, it must provide an equivalent trusted
peer-certificate signal to the application. Do not reintroduce a raw public header without a
strictly trusted internal hop that strips every inbound copy. That proxy topology needs its own
review and is not the default secure-pilot configuration.

## Certificate lifecycle

- Issue a unique client certificate and private key per device.
- Prefer a TPM-backed key provisioned through Intune SCEP/PKCS.
- Restrict private-key ACLs to the observer identity and administrators.
- Revoke the certificate immediately on device loss, compromise, or pilot withdrawal.
- Certificate rotation changes the fingerprint. The safe current process is operator disconnect,
  certificate replacement, and fresh device-code enrollment.

## STRIDE: Spoofing at boundary B3

The threat is a bearer token copied from one device and replayed from another. Mutual TLS alone
proves only that the caller has some CA-approved certificate. Binding ensures that the token is
accepted only with the exact certificate originally enrolled for that worker ID. An enrolled
attacker using a different valid fleet certificate cannot spoof the victim fingerprint with an
HTTP header.

## Microsoft device identity

The posture mirrors an Intune/Entra managed-device model:

- Intune provisions the device certificate and preferably a hardware-backed key.
- The private key proves device possession during mutual TLS.
- The orchestrator binds the application token to that verified certificate.
- Device compliance and attestation can separately derive the routing trust tier.

Full Entra Conditional Access integration, automated certificate rotation/revocation, and a
managed front-door topology remain production work.

## Verification

The test suite includes:

- Missing and mismatched certificate rejection.
- Collision rejection before token rotation.
- Legacy unbound-ID migration failure.
- Direct rejection of a spoofed client fingerprint header.
- A real Uvicorn mutual-TLS integration test proving that the enrolled certificate succeeds and a
  second CA-valid certificate cannot reuse the token even when it spoofs the victim header.
