# OneCompute - Pilot security approval (sanction runbook)

**Engine codename:** NightShift  ·  **Document class:** Microsoft Confidential (draft for security/privacy review)
**Companion to:** `OneCompute-Threat-Model.md` (risk register), `soc2-alignment.md` (control map), `pilot-plan.md` (operations)
**Audience:** CISO / Azure Security, Microsoft Digital (MSD), CELA, Privacy/Purview, HR

> **Purpose.** A single, checkable runbook that gates the contained pilot. It states the exact secure configuration the pilot must run, the per-team approvals required before any device joins, the live monitoring during the pilot, and the reversible shutdown. Nothing in the pilot may exceed what is sanctioned here. This document does not request enterprise rollout.

## 0. Go / no-go gate (all must be YES to start)

| # | Gate | Owner | Evidence | Status |
|---|---|---|---|---|
| G1 | Threat model reviewed; residual risk accepted in writing | Risk-acceptance owner | `OneCompute-Threat-Model.md` section 0 sign-off | |
| G2 | Pilot device set named and scoped (loaner/controlled first) | MSD | device inventory list | |
| G3 | Agent code-signed; publisher + post-sign SHA-256 recorded | Signing owner | signed artifact hash | |
| G4 | Defender custom-indicator allow-list scoped to pilot devices | MSD / CISO | indicator id + scope | |
| G5 | WDAC/AppLocker trust or scoped exception in place | MSD | policy entry | |
| G6 | Purview DLP confirmed for the pilot data class | Privacy/Purview | policy confirmation | |
| G7 | Legal read complete; consent copy approved | CELA / HR | approved `OneCompute-Pilot-Consent.md` | |
| G8 | Orchestrator host hardened and access-controlled | CISO / Azure Security | host baseline | |
| G9 | Secure worker configuration enforced (section 1) | Pilot lead | run command + env | |
| G10 | Kill switch tested end to end on a controlled machine | Pilot lead | test log | |

If any gate is NO, the pilot does not start.

## 1. Required secure configuration (enforced, not optional)

The pilot must run the workers and orchestrator in their hardened modes. These map directly to controls in `soc2-alignment.md`.

**Orchestrator (admission-gated, TLS + mutual TLS + rate limited):**
```
uv run python -m orchestrator --require-approval \
  --tls-cert server.crt --tls-key server.key --tls-client-ca worker-ca.crt \
  --rate-limit 600
```
- `--require-approval` holds every new worker as `approved=0` behind a device code until an operator approves it (`src/orchestrator/app.py:562-571`; `src/contracts/schema.sql:15-16`). This is control CC6.1.
- `--tls-cert/--tls-key` serve HTTPS; `--tls-client-ca` **requires mutual TLS** so only workers presenting a cert signed by that CA can reach the control plane (`src/orchestrator/__main__.py` via `src/trust/tls.py:server_ssl_kwargs`). This is control CC6.7 and threat-model R9.
- `--rate-limit` caps requests per minute per client (worker token or IP), returning 429 + Retry-After (`src/orchestrator/ratelimit.py`). This is control A1.2 and threat-model B3 DoS.

**Worker (fail-closed isolation + pinned signer + TLS client):**
```
setx ONECOMPUTE_TRUSTED_PUBKEY <hex-of-operator-signing-pubkey>
uv run python -m worker --url https://<orchestrator-host>:8080 --require-isolation --trusted-key <hex> \
  --tls-ca server-ca.crt --client-cert worker.crt --client-key worker.key
```
- `--require-isolation` makes the worker refuse any job when no OS-enforced sandbox (MXC or Docker) is available, instead of falling back to the unsandboxed subprocess or running host-side GPU/AI unsandboxed (`src/worker/__main__.py:184-190`; `src/isolation/runner.py:579-587`). This is the fail-closed control for threat-model R2/R3.
- `--trusted-key` / `$ONECOMPUTE_TRUSTED_PUBKEY` pins the operator's out-of-band signer so a compromised control plane cannot inject a self-signed job (`src/worker/__main__.py:192-198`; `src/trust/signing.py:59-65`). This is the control for threat-model R6.
- `--tls-ca` pins the orchestrator's CA and `--client-cert/--client-key` present the worker's mutual-TLS certificate (`src/trust/tls.py:client_ssl_params`). This is control CC6.7.

**Measurement-only variant (phase 1 of the pilot, see `pilot-plan.md`):**
```
uv run python -m worker --url http://<orchestrator-host>:8080 --measure-only
```
- Tracks CPU/GPU/RAM only; never pulls or runs a job. Lowest-risk entry point.

**Pre-flight verification (run on each pilot device before it joins):**
```
uv run python -c "from isolation.runner import active_boundary; print(active_boundary())"
```
- With `--require-isolation` set, a worker on a machine reporting `subprocess+jobobject` will refuse jobs by design. Confirm the intended boundary (`mxc` or `docker`) before enabling job execution, or keep the device in `--measure-only`.

## 2. Job-class policy for the pilot

- Start with **CPU, non-sensitive** job classes only (for example `fractal`, `optimize`, `challenge`).
- **No sensitive-data classes** and **no GPU/AI classes** in phase 1, given the disclosed GPU-in-Sandbox gap (R3) and host-side execution of AI/GPU kinds.
- Submitters are **controlled/internal** only; submitter SSO/OIDC is roadmap, so submission is restricted to the pilot team in the interim.

## 3. Live monitoring during the pilot

| Signal | Source | Action on trigger |
|---|---|---|
| Any Defender / Purview alert on a pilot device | MDE / Purview console | Stop the affected worker; notify security contact; pause pilot; investigate; do not resume until cleared |
| `auth_failed` spikes | `GET /events` (`app.py:330`) | Investigate token/enrollment; rotate token; check for a spoofed worker |
| `blacklisted` events | `GET /events` (`app.py:661`) | Review the failed challenge; confirm anti-cheat working as intended |
| Perceived slowdown report | Employee channel | Stop worker; re-tune governor margin; re-test on a controlled machine before resuming |
| Boundary downgrade to `subprocess+jobobject` while running jobs | worker logs (WARNING) | With `--require-isolation` the job is refused; otherwise stop the worker |

## 4. Kill switch (reversible by design)

1. **Stop the orchestrator.** All workers idle within one poll (no new leases issued).
2. **Operator disconnect** of any worker requeues its held job immediately (`app.py:577-593`).
3. **Employee** stops with Ctrl-C or uninstalls the user-space agent (no admin, no kernel driver, no service).
4. **Remove the Defender allow-list entry** at pilot end.
5. The only residual artifact on the device is a **local profile file**, deletable on opt-out.

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
- Kill switch executed; allow-list entry removed; devices returned to baseline.
- Findings written up against the threat-model risk register; go/no-go recommendation for a next phase.
