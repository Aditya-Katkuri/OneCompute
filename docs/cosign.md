# cosign / Sigstore signing integration

Audience: OneCompute engineering and the Chief-of-Staff threat-model owner.
Status: integration + test harness. Fail-closed and inert until a real `cosign`
binary is present. No production behavior changes for a machine without cosign.

This document describes how OneCompute integrates [Sigstore
cosign](https://docs.sigstore.dev/) to sign its supply-chain artifacts, exactly what
is proven in this offline environment, and what is explicitly **not** proven (no real
keyless signing, no Rekor transparency-log entry). It follows the same honesty
contract as [`docs/mxc-validation.md`](mxc-validation.md): detect the runtime, use it
when present, stay inert and honest when absent, and never fabricate a result.

## 1. What this integrates

OneCompute already produces two supply-chain artifacts (see
[`docs/supply-chain.md`](supply-chain.md)):

- `sbom.cyclonedx.json` : a CycloneDX 1.5 SBOM from `scripts/generate_sbom.py`.
- `attestation.intoto.jsonl` : an offline Ed25519-signed SLSA v1 provenance
  attestation from `scripts/generate_provenance.py`.

The cosign integration adds a Sigstore signing layer over those artifacts:

- `src/trust/cosign.py` : a thin, well-documented integration layer. It detects a real
  `cosign` binary, builds the `cosign sign-blob` / `cosign verify-blob` argv, runs it
  safely (list-argv subprocess, timeouts, Windows `cosign.exe`), and returns a
  structured `CosignResult`. When cosign is absent it returns an honest `available=False`
  result and produces **no** signature.
- `scripts/cosign_attest.py` : a CLI (`status`, `sign`, `verify`) that reuses the two
  existing generators by file-location import to produce the artifact to sign, then
  signs it (or reports cleanly that cosign is unavailable).
- `tests/test_cosign.py` + `tests/fake_cosign.py` : a conforming **stub** cosign so the
  real OneCompute code path (argv + subprocess) is exercised without requiring a genuine
  cosign binary in CI.

This mirrors the MXC seam precedent (`src/isolation/mxc.py`): availability means a
resolvable executable that answers a probe (`cosign version`, exit 0), not merely that a
name exists on PATH.

## 2. The exact commands

### 2.1 Key-based (offline, actually run here)

A local cosign key pair signs the blob and, with `--tlog-upload=false`, never touches
the network:

```powershell
# Generate a local key pair once (interactive; sets a password).
cosign generate-key-pair

# Sign an artifact; writes <artifact>.sig next to it. Offline: no Rekor upload.
cosign sign-blob --yes --key cosign.key --output-signature sbom.cyclonedx.json.sig --tlog-upload false sbom.cyclonedx.json

# Verify with the public key, skipping the transparency log (offline).
cosign verify-blob --key cosign.pub --signature sbom.cyclonedx.json.sig --insecure-ignore-tlog true sbom.cyclonedx.json
```

Through the OneCompute CLI:

```powershell
uv run python scripts/cosign_attest.py status
uv run python scripts/cosign_attest.py sign --sbom --key cosign.key
uv run python scripts/cosign_attest.py sign --attestation --key cosign.key --bundle
uv run python scripts/cosign_attest.py verify --blob sbom.cyclonedx.json --key cosign.pub
```

`sign` with neither `--sbom` nor `--attestation` signs both. The artifact is
regenerated in-process (from `scripts/generate_sbom.py` /
`scripts/generate_provenance.py`) so the signed bytes match the current source and lock.

### 2.2 Keyless (production / CI, documented, NOT run here)

The production path is Sigstore keyless signing: an ambient OIDC identity (GitHub
Actions, Entra, etc.) is exchanged for a short-lived Fulcio certificate, the blob is
signed, and an inclusion proof is recorded in the Rekor transparency log:

```powershell
# Needs network + an OIDC identity provider. NOT executed in this offline environment.
cosign sign-blob --yes --output-signature sbom.cyclonedx.json.sig --bundle sbom.cyclonedx.json.cosign.bundle sbom.cyclonedx.json

cosign verify-blob --bundle sbom.cyclonedx.json.cosign.bundle \
  --certificate-identity <workflow-identity> \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com sbom.cyclonedx.json
```

To prevent an offline environment from accidentally starting a network OIDC flow,
`cosign.sign_blob(...)` **refuses** the keyless path (no `key=`) unless
`allow_keyless=True` is passed explicitly. `scripts/cosign_attest.py` surfaces this as
`--allow-keyless`. The default, safe behavior offline is key-based signing.

## 3. What this PROVES and does NOT prove

### Proves (OneCompute-side integration)

- **Runtime resolution.** `find_cosign_exe()` resolves cosign from `$COSIGN` (an explicit
  path/shim override) or from PATH (`cosign.exe` / `cosign`), the same override pattern as
  `ONECOMPUTE_MXC_EXE`.
- **Availability probe is fail-closed.** `cosign_available()` returns `True` only when the
  executable resolves and answers `cosign version` with exit 0, and `False` (never an
  exception) on absent binaries, non-zero probes, and timeouts.
- **Correct argv.** The `sign-blob` / `verify-blob` command lines are built exactly as
  documented (`build_sign_argv` / `build_verify_argv`) and asserted against a stub, so the
  invocation is validated without a real binary.
- **Real launch + readback.** Against the conforming stub, `sign_blob` runs a real
  subprocess, writes a real `<artifact>.sig` (and bundle when requested), and returns the
  signature; `verify_blob` maps the stub exit code (0 / non-zero) to `True` / `False`.
- **Graceful degradation.** With no cosign present, `sign_blob` returns
  `available=False, ok=False` with an honest message and writes **no** signature file,
  and the CLI reports the artifacts as generated-but-unsigned (inert), never fabricating
  a signature.

### Does NOT prove (out of scope offline)

- **No real keyless signing.** No OIDC identity was exchanged for a Fulcio certificate;
  the keyless path is documented and code-gated, not executed.
- **No Rekor entry.** No transparency-log inclusion proof was produced or verified. There
  is nothing a third party can audit in a public log from this environment.
- **No real cosign binary was exercised.** The tests use a stub that emulates the three
  invocations OneCompute makes; they validate OneCompute's wiring, not Sigstore's
  cryptography or its Fulcio/Rekor services. This is the same limitation, and the same
  honesty, as the MXC stub in [`docs/mxc-validation.md`](mxc-validation.md).

## 4. Swapping in a real cosign

When a genuine cosign binary is available, point `$COSIGN` at it (or put it on PATH) and
drop the stub. No OneCompute code changes:

```powershell
$env:COSIGN = 'C:\Program Files\Sigstore\cosign.exe'
uv run python -c "from trust.cosign import cosign_available; print(cosign_available())"
uv run python scripts/cosign_attest.py sign --key cosign.key
```

If the binary conforms (answers `cosign version` and accepts the documented
`sign-blob` / `verify-blob` flags) it signs for real. For the keyless production/CI
path, run in an environment with an OIDC identity and network access and pass
`--allow-keyless`; only then are a Fulcio certificate and a Rekor entry produced.

## 5. Trust comparison with the Ed25519 attestation

| Property | Ed25519 SLSA attestation (`generate_provenance.py`) | cosign key-based (offline, here) | cosign keyless (production, roadmap) |
|---|---|---|---|
| Signer identity | key carried in the DSSE-lite envelope (or locally pinned) | local cosign key pair | short-lived Fulcio cert bound to an OIDC identity |
| Network required | no | no (`--tlog-upload false`) | yes (Fulcio + Rekor) |
| Transparency log | none | none | Rekor inclusion proof, third-party auditable |
| What is bound | SBOM digest + `src/**/*.py` digest under one signature | any blob (the SBOM or the attestation file) | same, plus an auditable identity + log entry |
| Runs in this env | yes | only with a real cosign binary | no (documented only) |

The two are complementary. The Ed25519 attestation proves the SBOM and source were
attested together offline; cosign key-based signing puts a Sigstore-format signature over
those same artifacts; cosign keyless closes the remaining gap with an identity-bound,
transparency-logged proof. cosign does **not** replace the Ed25519 manifest trust root
(`src/trust/signing.py`), which remains the runtime job-execution trust root.

## 6. STRIDE: Tampering (build provenance / transparency)

**Threat.** An attacker alters a supply-chain artifact (the SBOM or the provenance
attestation) between build and deployment, or substitutes a different one, so that what
ships does not match what was reviewed, and does so without leaving an auditable trace.

**Mitigation added here.** A Sigstore `cosign sign-blob` signature can be produced over
each artifact and verified with `cosign verify-blob`; `verify_blob` maps a failed
verification to `False`, so a post-signing modification is detected. The integration is
fail-closed: with no cosign present nothing is silently trusted, and no fake signature is
emitted, so an unsigned artifact is never mistaken for a signed one.

**Residual risk / roadmap.** In this offline environment signing is key-based only:
trust rests on a local key and there is **no Rekor transparency-log entry**, so a fully
compromised signer could still produce a valid-looking signature and no public log would
record it. Closing this means the keyless path: an OIDC-issued, short-lived Fulcio
identity plus a Rekor inclusion proof that a third party can audit, exactly the roadmap
item named in [`docs/supply-chain.md`](supply-chain.md). Until then the honest posture is:
supply-chain artifacts can be cosign-signed and verified offline with a local key, while
identity-bound, transparency-logged signing remains on the roadmap.

## 7. Microsoft SDL / Sigstore alignment

This integration advances the Microsoft Security Development Lifecycle (SDL)
supply-chain practices and adopts the Sigstore ecosystem as the signing format:

- **Secure the build and establish provenance.** cosign signatures over the SBOM and the
  SLSA provenance attestation add a standard, tool-verifiable signature layer on top of
  the existing Ed25519 attestation, moving OneCompute toward SLSA build-level provenance.
- **Reuse approved, standard tooling.** Sigstore cosign is the industry-standard signing
  tool; the integration wraps it rather than inventing a new signing primitive, and reuses
  the existing pinned, CVE-watched dependency posture.
- **Fail closed, degrade honestly.** Consistent with the SDL principle of secure defaults,
  the seam is inert when cosign is absent and refuses the network keyless path offline, so
  it can never silently downgrade or fabricate trust.
- **Verification is enforceable and honest about scope.** `verify-blob` can gate a release
  or pilot hand-off, and the offline limitation (no Rekor entry) is documented explicitly
  so reviewers judge the posture on what it actually does, not on an implied guarantee.
