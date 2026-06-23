# Signing, roots of trust, and manifest integrity

## PoC root: local Ed25519

Ed25519 is the right hackathon choice because it is small, fast, and directly available in Python. RFC 8032 lists EdDSA advantages: high performance on many platforms, no unique random number requirement per signature, side-channel resilience, and 32-byte Ed25519 public keys with 64-byte signatures [1]. The Python `cryptography` API is direct: generate a key, sign bytes, and call public-key `verify()`, which raises `InvalidSignature` on failure [2].

NightShift's PoC should sign canonical manifest bytes, not a Python object with ambiguous field order. The signature must cover all security-relevant fields: `job_id`, `kind`, `code_sha256`, `input_sha256`, `requires`, `limits`, `sandbox`, `issued_at`, `expires_at`, and verifier policy.

## Production-shaped root: cosign/OIDC/Rekor

Cosign can sign containers, blobs, and standard files. Its blob signing docs say keyless signing associates an ephemeral signing key with an identity from an OpenID Connect provider, and the recommended bundle contains signature, certificate, and transparency-log inclusion proof [7]. Cosign also supports self-managed keys, KMS providers, and Azure Key Vault through its key-management flow [22]. Rekor is Sigstore's immutable, tamper-resistant ledger for supply-chain metadata and supports non-repudiation/inclusion proofs [8].

## Hardware roots: TPM, Pluton, Secure Boot

TPM technology provides hardware-based cryptographic functions: generating/storing/limiting keys, device authentication, and recording boot measurements; TPM keys can be configured so the private key is unavailable outside the TPM [4]. Pluton integrates a secure crypto-processor into the SoC and provides hardware root of trust, secure identity, attestation, and cryptographic services [5]. Secure Boot verifies boot software signatures before OS handoff [6]. These are production trust anchors, not hackathon dependencies.

## Implementation sketch

1. `canonical = json.dumps(manifest_without_signature, sort_keys=True, separators=(",", ":")).encode()`
2. `signature = private_key.sign(canonical)`
3. Worker recomputes canonical bytes and `public_key.verify(signature, canonical)`.
4. Worker recomputes `sha256(code)` and `sha256(input)` and refuses on mismatch.
5. Worker refuses expired manifests and unknown `public_key_id`.

## Decision table

| Capability | PoC | Roadmap |
|---|---|---|
| Manifest signing | Local Ed25519 | cosign keyless OIDC bundle |
| Key custody | Local demo key | Azure Key Vault / hardware-backed key |
| Audit | SQLite event + optional prev-hash | Rekor/transparency style log |
| Worker identity | Server-issued worker ID | Entra + compliant device + TPM/Pluton key |
| Device posture | Not required | Intune compliance + Conditional Access |

## Sources

- [1] RFC 8032. https://datatracker.ietf.org/doc/html/rfc8032
- [2] Python `cryptography` Ed25519 docs. https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/
- [4] Microsoft TPM overview. https://learn.microsoft.com/en-us/windows/security/hardware-security/tpm/trusted-platform-module-overview
- [5] Microsoft Pluton. https://learn.microsoft.com/en-us/windows/security/hardware-security/pluton/microsoft-pluton-security-processor
- [6] Microsoft Secure Boot. https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/oem-secure-boot
- [7] Sigstore cosign signing blobs/files. https://docs.sigstore.dev/cosign/signing/signing_with_blobs/
- [8] Sigstore Rekor. https://docs.sigstore.dev/logging/overview/
- [22] Sigstore cosign key management. https://docs.sigstore.dev/cosign/key_management/overview/
