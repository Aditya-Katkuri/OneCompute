# OneCompute - Pilot security approval (sanction runbook)

**Engine codename:** NightShift  ·  **Document class:** Microsoft Confidential (draft for security/privacy review)
**Companion to:** `OneCompute-Threat-Model.md` (risk register), `soc2-alignment.md` (control map), `pilot-plan.md` (operations)
**Audience:** CISO / Azure Security, Microsoft Digital (MSD), CELA, Privacy/Purview, HR

> **Purpose.** A single, checkable runbook that gates the contained pilot. It states the exact secure configuration the pilot must run, the per-team approvals required before any device joins, the live monitoring during the pilot, and the reversible shutdown. Nothing in the pilot may exceed what is sanctioned here. This document does not request enterprise rollout.

## 0. Go / no-go gate (all must be YES to start)

| # | Gate | Owner | Evidence | Status |
|---|---|---|---|---|
| G1 | Threat model reviewed; residual risk accepted in writing | Risk-acceptance owner | `OneCompute-Threat-Model.md` section 0 sign-off | |
| G2 | Staged 50-100 device set named and scoped; control ring first | MSD | device inventory and ring plan | |
| G3 | Agent code-signed; publisher + post-sign SHA-256 recorded | Signing owner | signed artifact hash | |
| G4 | Defender custom-indicator allow-list scoped to pilot devices | MSD / CISO | indicator id + scope | |
| G5 | WDAC/AppLocker trust or scoped exception in place | MSD | policy entry | |
| G6 | Purview DLP confirmed for the pilot data class | Privacy/Purview | policy confirmation | |
| G7 | Legal read complete; measurement consent copy approved | CELA / HR | approved `OneCompute-Measurement-Pilot-Consent.md` | |
| G8 | Orchestrator host hardened and access-controlled | CISO / Azure Security | host baseline | |
| G9 | Secure worker configuration enforced (section 1) | Pilot lead | run command + env | |
| G10 | Kill switch tested end to end on a controlled machine | Pilot lead | test log | |
| G11 | Compact central data contract verified; no hourly/idle pattern or live resource stream | Privacy / CISO | tests plus sample captured request | |
| G12 | Retention, disconnect erasure, certificate revocation, and local purge tested | Privacy / CISO / pilot lead | deletion evidence | |
| G13 | Observer runs as the participant at limited privilege with a fixed signed executable or absolute interpreter path | MSD / pilot lead | task principal, Intune user-context setting, and dry-run evidence | |

If any gate is NO, the pilot does not start.

## 1. Required secure configuration (enforced, not optional)

The pilot must run the workers and orchestrator in their hardened modes. These map directly to controls in `soc2-alignment.md`.

**Orchestrator for the measurement pilot:**
```powershell
$env:ONECOMPUTE_SUBMIT_TOKEN = Read-Host "Submit token"
$env:ONECOMPUTE_ADMIN_TOKEN = Read-Host "Admin token"

uv run python -m orchestrator `
  --secure-measurement-pilot `
  --tls-cert C:\OneCompute\pki\server.crt `
  --tls-key C:\OneCompute\pki\server.key `
  --tls-client-ca C:\OneCompute\pki\worker-ca.crt `
  --db C:\OneCompute\data\measurement-pilot.db
```
- The preset fails closed unless HTTPS server identity, a client CA, and distinct submit/admin tokens are present.
- It enables device-code approval and certificate-fingerprint identity binding.
- Missing, malformed, unbound, or mismatched device fingerprints are rejected before token rotation.
- Operator reads and admin mutations require the admin token. Submission requires the separate submit token.
- The default rate limit remains active.

**Measurement-only worker:**
```powershell
uv run python -m worker `
  --url https://onecompute-pilot.contoso.com:8080 `
  --measure-only `
  --no-telemetry `
  --measurement-device-class laptop `
  --tls-ca C:\ProgramData\OneCompute\pki\server-ca.crt `
  --client-cert C:\ProgramData\OneCompute\pki\device.crt `
  --client-key C:\ProgramData\OneCompute\pki\device.key
```
- Measurement mode never pulls or runs a job.
- It saves the durable local profile immediately and about every minute, with an OS-backed
  single-writer lock and atomic validated persistence, but no per-sample measurement timeline.
- It does not start the one-second live resource heartbeat.
- It uploads one compact aggregate approximately every five minutes.
- It continues local collection during registration outages or pending approval; central profile
  upload remains blocked until approval.
- CPU-only devices do not contribute GPU headroom.
- Each device uses a unique client certificate. No operator token is deployed to the endpoint.
- The Windows installer runs in the participant's context at `RunLevel Limited`, rejects
  SYSTEM, LOCAL SERVICE, and NETWORK SERVICE, and uses a fixed signed executable or absolute
  virtual-environment interpreter. Intune must use the logged-on credentials.

**Contained-execution worker, only after a separate Phase 2 approval:**
```powershell
setx ONECOMPUTE_TRUSTED_PUBKEY <hex-of-operator-signing-pubkey>
uv run python -m worker `
  --url https://onecompute-pilot.contoso.com:8080 `
  --require-isolation `
  --trusted-key <hex> `
  --tls-ca C:\ProgramData\OneCompute\pki\server-ca.crt `
  --client-cert C:\ProgramData\OneCompute\pki\device.crt `
  --client-key C:\ProgramData\OneCompute\pki\device.key
```

**Pre-flight verification (run on each pilot device before it joins):**
```
uv run python -c "from isolation.runner import active_boundary; print(active_boundary())"
```
- With `--require-isolation` set, a worker on a machine reporting `subprocess+jobobject` will refuse jobs by design. Confirm the intended boundary (`mxc` or `docker`) before enabling job execution, or keep the device in `--measure-only`.

## 2. Job-class policy for the pilot

- Start with **CPU, non-sensitive** job classes only (for example `fractal`, `optimize`, `challenge`).
- **No sensitive-data classes** and **no GPU/AI classes** in phase 1, given the disclosed GPU-in-Sandbox gap (R3) and host-side execution of AI/GPU kinds.
- Submitters are **controlled/internal** only; an optional operator **`--submit-token`** now gates submission (full submitter SSO/OIDC is the upgrade), so submission is restricted to the pilot team in the interim.

## 3. Live monitoring during the pilot

| Signal | Source | Action on trigger |
|---|---|---|
| Any Defender / Purview alert on a pilot device | MDE / Purview console | Stop the affected worker; notify security contact; pause pilot; investigate; do not resume until cleared |
| `auth_failed` spikes | authenticated `GET /events` | Investigate token/enrollment; revoke certificate; check for spoofing |
| Unexpected report values or class changes | `GET /measurement`, inventory, outlier review | Pause expansion; compare with approved endpoint-management data |
| GPU contributor count is unexpected or profile freshness stalls | `GET /measurement`, observer `-Status` | Pause expansion; inspect sensor support, profile write errors, singleton lock, and enrollment state |
| Perceived slowdown report | Employee channel | Stop worker; re-tune governor margin; re-test on a controlled machine before resuming |
| Boundary downgrade to `subprocess+jobobject` while running jobs | worker logs (WARNING) | With `--require-isolation` the job is refused; otherwise stop the worker |

## 4. Kill switch (reversible by design)

1. **Stop the orchestrator** or block its listener.
2. **Operator disconnect** deletes the device's latest central measurement summary.
3. **Revoke the client certificate** for any affected observer.
4. **Uninstall** to stop both persistence mechanisms and retain the local profile, or **purge** to
   also delete the profile, lock, temporary and recovery files, legacy telemetry and rotations,
   observer ID, and configuration.
5. **Remove the Defender allow-list entry** at pilot end.

Tested end-to-end as gate G10 before the pilot starts.

## 5. Per-team sign-off (mirrors the threat-model section 0 table)

| Reviewer / team | Decision | Conditions | Date |
|---|---|---|---|
| MSD (endpoint owner) | | | |
| CISO / Azure Security | | | |
| CELA (legal) | | | |
| Privacy / Purview | | | |
| HR (rewards) | | | |
| Risk acceptance owner | | | |

## 6. Exit criteria (when the pilot ends)

- Time box reached (see `pilot-plan.md`), or any unresolved security/privacy trigger.
- Kill switch executed; certificates revoked; local purge and central deletion evidenced; allow-list entry removed; devices returned to baseline.
- Pilot database and pseudonymous audit data deleted within the approved window, proposed as 30 days unless an incident hold applies.
- Findings written up against the threat-model risk register; go/no-go recommendation for a next phase.
