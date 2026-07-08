# OneCompute - Pilot plan (operations)

**Engine codename:** NightShift  ·  **Document class:** Microsoft Confidential (draft for security/privacy review)
**Companion to:** `OneCompute-Threat-Model.md`, `soc2-alignment.md`, `pilot-security-approval.md`
**Related:** `docs/measurement-pilot.md` (measurement mechanics), `docs/product/Measurement-Pilot-Readout-Template.md` (readout)
**Audience:** sponsor, Azure Compute, CISO / Azure Security, MSD, CELA, Privacy, HR

> **Purpose.** Describe how the contained pilot actually runs: scope, phases, device set, roles, metrics, and exit criteria. The pilot is deliberately small, reversible, time-boxed, internal-only, and gated on `pilot-security-approval.md`. It is a measurement-first pilot, not a rollout.

## 1. Objectives

1. **Measure real idle headroom** across a small, consenting fleet (laptops, dev boxes, and where applicable Xboxes) without disturbing anyone's work.
2. **Validate the safety story in the field**: instant-yield governor behavior, endpoint-control coexistence (Defender/WDAC/Purview), and the fail-closed isolation posture.
3. **Produce an honest go/no-go**: a readout tying measured headroom and any incidents back to the threat-model risk register.

Explicit non-objectives: enterprise rollout, sensitive-data workloads, GPU/AI workloads, external participation.

## 2. Phases

### Phase 0 - Sanction (blocking)
- Complete every gate in `pilot-security-approval.md` section 0. No device joins until G1-G10 are YES.
- In parallel (partnership track): engage **Azure Compute** and the **CISO office** on the design for safely routing cloud-substitutable requests into the fleet pool. This is a design conversation during the pilot, not a pilot dependency.

### Phase 1 - Measurement only (approx. 2 weeks)
- Consenting devices run the worker in `--measure-only` mode. It tracks CPU/GPU/RAM and joins the fleet view, but **never pulls or runs a job** (`src/worker/__main__.py` measure-only path).
- Each device uploads only a derived hour-of-week usage envelope via `POST /profile`; raw activity never leaves the device (`src/measurement/headroom.py`, `src/contracts/schema.sql:60-68`).
- Fleet-wide measured idle headroom is rolled up at `GET /measurement` and on the dashboard's "Measured idle headroom" beat.
- Deliverable: a measurement readout using `docs/product/Measurement-Pilot-Readout-Template.md`, reporting measured (not theoretical) harvestable headroom, with the conservative harvest target (roughly 20-40% of idle, staying well below the level users perceive as slow).

### Phase 2 - Contained execution (only if Phase 1 is clean and re-sanctioned)
- A subset of Phase 1 devices runs real CPU, non-sensitive jobs under the **required secure configuration** (`--require-approval` orchestrator; `--require-isolation --trusted-key` workers) from `pilot-security-approval.md` section 1.
- Job classes restricted to CPU/non-sensitive (for example `fractal`, `optimize`, `challenge`). No GPU/AI, no sensitive data.
- Submitters are controlled/internal only (submitter SSO is roadmap).

## 3. Device set and consent

- **Start with loaner/controlled devices**, then a handful of named, consenting employee devices.
- Consent is voluntary, informed, opt-in, with instant withdrawal, per `OneCompute-Pilot-Consent.md`.
- Regional handling: engage employee representation (for example works councils) before any expansion beyond the sanctioned pilot.

## 4. Roles

| Role | Responsibility |
|---|---|
| Sponsor | Owns go/no-go; receives daily status during active phases |
| Pilot lead | Runs the orchestrator, approves devices, enforces secure config, executes the kill switch |
| Security contact (CISO/Azure Security) | Receives alerts; sets the required isolation bar; reviews incidents |
| Endpoint owner (MSD) | Owns the Defender allow-list, device set, and device-wear stance |
| Privacy/CELA | Owns DPIA scope and the legal/consent read |
| Participants | Consenting device owners; can withdraw instantly |

## 5. Metrics and success criteria

| Metric | Source | Success signal |
|---|---|---|
| Measured idle headroom (fleet) | `GET /measurement` | A credible, conservative harvestable envelope emerges |
| Perceived slowdown reports | Participant channel | Near zero; any report is investigated and the governor re-tuned |
| Yield responsiveness | worker logs / governor decisions | Sub-second yield when the employee's demand spikes |
| Endpoint alerts | Defender/Purview | Zero unexpected alerts on allow-listed devices |
| Auth/anti-cheat health | `GET /events` (`auth_failed`, `blacklisted`) | No anomalies; controls behave as designed |
| Work correctness (Phase 2) | proof-hash + challenge results | No accepted incorrect results; cheaters blacklisted |

The pilot **succeeds** if it measures real headroom, generates clean attributable telemetry, produces no unresolved security/privacy incident, and gives reviewers a sound basis for a next-phase decision. A null or negative result (for example headroom too small, or governor tuning harder than expected) is a valid, honest outcome.

## 6. Risk linkage

Each active-phase risk maps to the threat-model risk register and its treatment:
- R1 endpoint collision -> Defender allow-list + monitoring (Phase 0/live monitoring).
- R2/R3 isolation -> `--require-isolation`; CPU-only, no GPU/AI in the pilot.
- R6 orchestrator compromise -> `--trusted-key` pinned signer.
- R8 governor mis-sizing -> Phase 1 measurement + conservative defaults; slowdown playbook.
- R4/R12 privacy/legal -> measurement-only first; DPIA and CELA/HR review.
- R15 MXC preview -> not relied on; Docker/Job-Object + fail-closed is the enforced path.

## 7. Reporting and exit

- **Daily** status to the sponsor during active phases; **immediate** escalation on any security/privacy trigger.
- **End of Phase 1:** measurement readout + recommendation on whether to enter Phase 2.
- **End of pilot:** execute the kill switch (`pilot-security-approval.md` section 4), remove the allow-list entry, return devices to baseline, and write findings against the risk register.
