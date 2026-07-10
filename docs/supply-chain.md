# Supply-chain security and SBOM

This document covers how OneCompute generates a Software Bill of Materials (SBOM),
what that SBOM does and does not cover, the software provenance posture today versus
the roadmap, and the dependency-pinning policy. It closes the SBOM roadmap item called
out in the threat model (`docs/pitch/OneCompute-Threat-Model.md`, section 14, risk R13).

## Generating the SBOM

The generator is pure standard library (`tomllib` + `json`); it reads the resolved
dependency graph from `uv.lock` and emits a CycloneDX 1.5 JSON document.

```powershell
# From the repository root. Writes sbom.cyclonedx.json at the repo root.
uv run python scripts/generate_sbom.py

# Alternatives:
uv run python scripts/generate_sbom.py --output build/sbom.cyclonedx.json
uv run python scripts/generate_sbom.py --stdout
```

Source of truth:

- Generator: `scripts/generate_sbom.py`
- Tests: `tests/test_sbom.py`
- Resolved dependency graph it reads: `uv.lock`
- Project name/version recorded as `metadata.component`: `pyproject.toml`

The output validates against the CycloneDX 1.5 schema. Each locked third-party package
becomes a `type: library` component with a `name`, a `version`, and a
`pkg:pypi/<name>@<version>` Package URL (purl). Components are sorted by name so the
document is deterministic: two runs against the same `uv.lock` produce an identical
component list (only the `serialNumber` and `timestamp` differ, by design). The
OneCompute project itself is recorded once as the SBOM's `metadata.component` of type
`application`, not as a library component.

## What the SBOM covers, and what it does not

Covered:

- Every Python package `uv` resolves for the project, at the exact pinned version, as
  recorded in `uv.lock`. This includes direct and transitive dependencies.
- A stable purl per component, suitable for feeding a vulnerability scanner or a
  policy gate (for example, matching against advisories by purl).

Not covered (honest scope):

- This SBOM is derived from `uv.lock`, the resolved dependency graph. It is not a full
  build or runtime attestation. It describes which package versions the project resolves
  to, not the provenance of a built artifact or of the machine that built it.
- It does not enumerate operating-system packages, the Python interpreter itself, or
  non-Python assets bundled at build time.
- It does not by itself prove that the installed bytes match the locked hashes. `uv.lock`
  records per-artifact hashes; enforcing them at install time is `uv`'s job, and hash and
  signature verification of the built agent is part of the provenance roadmap below.

## Provenance posture

Today:

- **Job manifests are Ed25519-signed** and verified by every worker before a job runs
  (`src/trust/signing.py`). This is the runtime trust root: a worker will not execute a
  workload whose manifest signature does not verify. A worker can additionally pin an
  out-of-band trusted signer (`--trusted-key` / `$ONECOMPUTE_TRUSTED_PUBKEY`) so a
  compromised control plane cannot inject a self-signed job.
- **Dependencies are pinned** through `uv.lock`, and `cryptography` (the Ed25519 trust
  root) is promoted to a pinned, CVE-watched *direct* dependency in `pyproject.toml`
  rather than being pulled in transitively.
- **This SBOM** gives an auditable, machine-readable inventory of the resolved
  dependency set.

Roadmap (not built here, tracked in `docs/pitch/OneCompute-Threat-Model.md` section 14):

- cosign / OIDC keyless signing of the agent build, with a Rekor transparency-log entry.
- SLSA-style build provenance attestations tying the built agent back to its source and
  builder.
- Signed update channel with mandatory signature verification (no silent auto-update).

## Signed provenance attestation

`scripts/generate_provenance.py` takes a concrete, offline step toward the build-provenance
roadmap item above. It builds an [in-toto Statement](https://in-toto.io/Statement/v1) whose
`predicateType` is [SLSA Provenance v1](https://slsa.dev/provenance/v1) and Ed25519-signs it
by reusing the project's manifest trust root (`src/trust/signing.Signer`, the same primitive
that signs job manifests). It is pure standard library (`json` + `hashlib` + `tomllib`) plus
`src/trust` for signing.

### Statement and predicate shape

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {"name": "sbom.cyclonedx.json", "digest": {"sha256": "<hex>"}},
    {"name": "src-tree",            "digest": {"sha256": "<hex>"}}
  ],
  "predicateType": "https://slsa.dev/provenance/v1",
  "predicate": {
    "buildDefinition": {
      "buildType": "https://onecompute.dev/provenance/buildtypes/offline-attestation/v1",
      "externalParameters": {"project": "onecompute", "entryPoint": "scripts/generate_provenance.py", "subjects": ["sbom.cyclonedx.json", "src-tree"]},
      "internalParameters": {},
      "resolvedDependencies": [{"uri": "pkg:pypi/<name>@<version>", "digest": {"sha256": "<hex>"}}]
    },
    "runDetails": {"builder": {"id": "https://onecompute.dev/provenance/builders/offline-ed25519/v1"}}
  }
}
```

Two **subjects** are attested, both deterministic so the digests are reproducible:

- `sbom.cyclonedx.json`: the CycloneDX SBOM regenerated in-process by the existing
  `scripts/generate_sbom.py` generator (pinned to a fixed timestamp and serial number so the
  digest is stable), hashed over its canonical bytes (`contracts.hashing.canonical_bytes` /
  `sha256_hex`).
- `src-tree`: a digest of the tracked sources, defined as the sha256 of the sorted list of
  `[posix-relative-path, sha256-of-file-content]` pairs over `src/**/*.py`.

The **predicate** is a faithful, minimal SLSA v1 shape. `resolvedDependencies` is derived
from `uv.lock`: one entry per locked registry package as `{uri: pkg:pypi/<name>@<version>,
digest: {sha256: ...}}`, with the sha256 taken from the package's sdist hash (falling back to
the first wheel hash) and omitted only when the lock records none. `builder.id` identifies the
offline Ed25519 attestor.

Stubbed / deliberately empty fields (honest scope): `internalParameters` is empty (this
offline PoC has no build-system internal configuration to record), and `runDetails.metadata`
(invocationId, `startedOn` / `finishedOn` timestamps) is omitted so the statement is
byte-reproducible. Recording real, signed build metadata is part of the roadmap below.

### Signing envelope (DSSE-lite)

The statement's canonical bytes are Ed25519-signed and emitted as a DSSE-lite JSON envelope:

```json
{
  "payloadType": "application/vnd.in-toto+json",
  "payload": { "...the in-toto Statement..." },
  "signatures": [{"sig": "<hex>", "keyid": "<pubkey-hex>", "publicKey": "<pubkey-hex>"}]
}
```

The public key is recorded in the envelope so verification is self-contained for the PoC. This
mirrors the manifest signer: a key can be pinned out of band, but by default the carried key is
used. Full [DSSE](https://github.com/secure-systems-lab/dsse) PAE framing, cosign/OIDC keyless
signing, and a Rekor transparency-log entry are the production upgrade.

### Generate and verify

```powershell
# From the repository root. Writes attestation.intoto.jsonl at the repo root.
uv run python scripts/generate_provenance.py generate

# Alternatives:
uv run python scripts/generate_provenance.py generate --output build/attestation.intoto.jsonl
uv run python scripts/generate_provenance.py generate --stdout

# Verify (recomputes the statement bytes and checks the Ed25519 signature):
uv run python scripts/generate_provenance.py verify attestation.intoto.jsonl
```

Signing uses a hex Ed25519 private key from `--private-key` or `$ONECOMPUTE_PROVENANCE_KEY`;
if neither is set an ephemeral key is generated (like `Signer`'s default). `verify` returns a
non-zero exit (and the `verify_envelope` function returns `False`) if the payload or the
signature is tampered by even one byte.

Source of truth:

- Generator / verifier: `scripts/generate_provenance.py`
- Tests: `tests/test_provenance.py`
- Trust root reused for signing: `src/trust/signing.py`
- Subjects hashed: the in-process SBOM (`scripts/generate_sbom.py`) and `src/**/*.py`

### Honest scope

This is a **lock-and-source attestation, signed offline**. It cryptographically binds a
specific SBOM and source tree together and proves they were attested by the holder of an
Ed25519 key. It is **not** a hardware-rooted or transparency-logged build proof: the signature
is checked against a key carried in the envelope (or a locally supplied one), there is no
independent builder identity, no OIDC-issued short-lived certificate, and no Rekor entry that a
third party can audit. It attests the resolved dependency set and source, not the bytes of a
built and shipped artifact.

Roadmap (closing the remaining gap): cosign keyless OIDC signing (a short-lived,
identity-bound certificate instead of a self-carried key) + Rekor transparency-log inclusion +
full DSSE PAE framing + progression up the SLSA build levels (a hosted, isolated builder that
generates the provenance, rather than an offline script the operator runs).

### Microsoft SDL alignment

This attestation extends the Microsoft Security Development Lifecycle (SDL) supply-chain
practices already covered by the SBOM:

- **Secure the build and establish provenance:** a signed SLSA v1 statement binds the resolved
  dependency graph and source tree under one Ed25519 signature, giving a verifiable,
  machine-readable provenance record where before there was only an unsigned SBOM.
- **Reuse an approved cryptographic trust root:** signing reuses the vetted Ed25519 manifest
  signer (`src/trust/signing.py`) and the CVE-watched, pinned `cryptography` dependency, rather
  than introducing a new signing primitive.
- **Verification is enforceable:** `verify` fails closed on any tampering, so the attestation
  can gate a release or pilot hand-off.
- **Roadmap tracked honestly:** cosign/OIDC/Rekor and higher SLSA build levels are named as the
  path to a hardware-rooted, transparency-logged proof.

### STRIDE: Tampering (build provenance)

This work addresses the **Tampering** category of STRIDE for the build/release step,
complementing the supply-chain Tampering mitigations above (risk R13, section 14 of
`docs/pitch/OneCompute-Threat-Model.md`).

- **Threat:** an attacker alters the resolved dependency set or the source tree between build
  and deployment, or swaps in a different SBOM, so that what ships does not match what was
  reviewed.
- **Mitigation in place:** an Ed25519-signed SLSA v1 statement binds the SBOM digest and the
  `src/**/*.py` source digest together; `verify` recomputes the statement bytes and rejects any
  single-byte change to the payload or signature, so post-attestation tampering is detected.
- **Residual risk / roadmap:** trust rests on a self-carried (or locally pinned) key rather than
  an OIDC-issued builder identity, and there is no transparency log, so a fully compromised
  signer could still produce a valid-looking attestation. Closing this means cosign keyless
  signing + Rekor inclusion + full DSSE and SLSA build-level progression, as above. Until then
  the honest posture is: dependencies and source cryptographically bound and offline-signed,
  hardware-rooted and transparency-logged build proof on the roadmap.

## Dependency-pinning policy

- `uv.lock` is the single resolved lock for the project and is committed to the
  repository. All environments (dev, CI, pilot) resolve from it.
- `cryptography` is pinned as a direct dependency in `pyproject.toml` because it is the
  Ed25519 trust root; it is watched for CVEs rather than inherited transitively.
- The declared dependency set is intentionally broad today (borrowed from the shared
  toolbox); trimming it to the actual worker/orchestrator import surface is a hardening
  step tracked in the threat model. Regenerate this SBOM whenever `uv.lock` changes so
  the inventory stays in sync with the resolved graph.

## Microsoft SDL alignment

Generating and publishing an SBOM maps directly to the Microsoft Security Development
Lifecycle (SDL) practices for managing the software supply chain:

- **Inventory of components (SDL supply-chain practice):** the CycloneDX SBOM is a
  complete, versioned inventory of third-party components, the prerequisite for
  vulnerability management and license review.
- **Use approved and pinned components:** `uv.lock` plus the pinned direct
  `cryptography` dependency give a deterministic, reviewable component set.
- **Secure the build and provenance:** Ed25519 manifest signing is enforced today; cosign
  / OIDC / Rekor and SLSA build provenance are the tracked roadmap to close the remaining
  build-provenance gap.
- **Continuous monitoring:** the purl-keyed SBOM feeds automated CVE scanning so newly
  disclosed advisories can be matched against the resolved graph.

## STRIDE: Tampering (supply chain)

This work addresses the **Tampering** category of STRIDE as it applies to the software
supply chain, corresponding to **risk R13 (supply-chain compromise of the agent)** and
**section 14 (supply-chain security)** of
`docs/pitch/OneCompute-Threat-Model.md`.

- **Threat:** an attacker tampers with a dependency, the resolved dependency graph, or
  the built agent, so that malicious code ships inside OneCompute.
- **Mitigations in place:** pinned dependencies (`uv.lock`), a pinned CVE-watched
  `cryptography` trust root, Ed25519 signature verification of every job manifest before
  execution (`src/trust/signing.py`), optional out-of-band signer pinning, and this
  auditable SBOM (`scripts/generate_sbom.py`) that makes the resolved component set
  reviewable and scannable.
- **Residual risk / roadmap:** the SBOM is derived from the resolved lock, not from a
  signed build attestation. Closing the remaining gap means cosign/OIDC/Rekor signing of
  the build and SLSA provenance, as tracked in the threat model. Until then, the honest
  posture is: dependencies pinned and inventoried, runtime manifests signed, build
  provenance on the roadmap.
