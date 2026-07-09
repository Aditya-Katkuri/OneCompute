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
