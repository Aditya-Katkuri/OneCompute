# OneCompute Measurement Pilot Security and Privacy Review

**Review target:** 50-100 voluntary Windows laptops, desktops, and dev boxes

**Mode:** measurement only, no job execution
**Decision requested:** approval for a staged, time-boxed internal pilot

## Executive assessment

The measurement path is designed to be materially lower risk than the OneCompute job-execution
path. Current measurement mode never polls for a job, never executes a job, never opens an inbound
port, never writes a per-sample activity timeline, and never streams live utilization to the
orchestrator.

The remaining central report is deliberately compact. It contains capacity and availability totals,
not an hour-of-week presence heatmap. Remote use fails closed unless HTTPS is used. The sanctioned
fleet configuration adds mutual TLS, per-device certificate identity, device-code approval,
separate operator and submitter credentials, and authenticated operator reads.

The recommended decision is **approve only as a staged measurement pilot**, subject to the
go/no-go gates in `docs/pitch/pilot-security-approval.md`. This review does not approve job
execution or retail Xbox deployment.

## Findings addressed

| Prior concern | Resolution | Evidence |
|---|---|---|
| Central report exposed hour-of-week CPU/GPU/RAM/AC/idle patterns | Current workers send one compact aggregate. Legacy buckets are collapsed in memory and never persisted centrally. Idle/away is discarded. | `src/worker/agent.py`, `src/orchestrator/app.py`, `src/contracts/models.py` |
| Measurement mode streamed live utilization every second | The live usage heartbeat is not started in measurement mode. Periodic compact profile upload provides liveness. | `src/worker/__main__.py`, `tests/worker/test_measure_only.py` |
| Registration and approval heartbeats exposed live free RAM | Measurement registration is normalized to an identity-only capability marker. Approval heartbeats carry no live CPU, GPU, or free-RAM values, and measurement observers cannot lease jobs. | `src/worker/agent.py`, `src/orchestrator/app.py`, `tests/worker/test_measure_only.py` |
| Local JSONL created a timestamped presence timeline | Measurement mode no longer logs `measure` events. Existing JSONL is migration-only and covered by purge. Remaining job diagnostics are size-bounded. | `src/worker/__main__.py`, `src/worker/telemetry.py` |
| Plain HTTP exposed reports and bearer tokens | Non-loopback measurement URLs require HTTPS. TLS material with HTTP is a hard error. Managed installation requires pinned mTLS for remote fleets. | `src/worker/__main__.py`, `scripts/install_observer.ps1` |
| Worker-ID collision could rotate a token and take over an observer | The server derives the fingerprint directly from the TLS-verified peer certificate, ignores spoofed client headers, rejects unbound legacy collisions, and rejects mismatched re-registration before token rotation. | `src/orchestrator/mtls_protocol.py`, `src/orchestrator/app.py`, `tests/trust/test_tls.py` |
| Operator read APIs were unauthenticated | State, measurement, job/workload detail, and audit reads use the admin/operator gate when configured. The dashboard prompts and keeps tokens only in page memory. | `src/orchestrator/app.py`, `src/dashboard/index.html` |
| Opt-out did not stop the observer or erase all local data | `-Uninstall` stops collection and removes persistence while retaining the profile. `-Purge` also removes the profile, legacy telemetry, rotations, and observer ID. | `scripts/observe_me.ps1`, `scripts/install_observer.ps1` |
| Disconnect left a central measurement row behind | Device disconnect now deletes the latest compact central summary. | `src/orchestrator/app.py`, `tests/orchestrator/test_profile_ingest.py` |

## Central data contract

| Field | Purpose | Privacy property |
|---|---|---|
| Observer ID | Stable device correlation | Random and hostname-free by default |
| Device class | Fleet segmentation | Coarse category only |
| Coverage count | Report quality | No wall-clock location |
| CPU/GPU summaries | Capacity estimate | Aggregate average, peak, and conservative range |
| RAM summary | Capacity estimate | Aggregate average and headroom |
| AC percentage | Harvest-window estimate | Aggregate only |
| Availability totals | Awake and unavailable estimate | Daily totals and span, no timestamps |
| Enrollment metadata | Approval and device binding | Observer ID, measurement-only marker, and verified certificate fingerprint; no live free RAM or actual hardware inventory |

The central report has no application data, file data, keystrokes, screen content, URLs, process
names, raw timestamps, hour-of-week slots, or idle/away field.

## Enrollment and transport controls

The sanctioned orchestrator command uses `--secure-measurement-pilot`. That preset requires:

- HTTPS server certificate and key.
- A client-certificate CA, which enables mutual TLS.
- Distinct submit and admin tokens.
- Device-code approval.
- Certificate-fingerprint device binding.

Each device receives a unique client credential. The custom Uvicorn protocol reads the certificate
from the verified TLS connection and injects its fingerprint into the ASGI scope. The application
does not trust a client fingerprint header. A registration without a verified fingerprint is
rejected. A collision with an existing unbound ID requires operator removal, and a collision with a
different certificate is rejected before any token changes.

## Operator access

When an admin token is configured, the following require it:

- `GET /state`
- `GET /measurement`
- `GET /jobs/{id}`
- `GET /workloads/{id}`
- `GET /events`
- `GET /events/verify`
- `GET /events/export`

The bundled dashboard uses separate in-memory credentials for admin reads/mutations and workload
submission. It does not write either token to browser storage.

## Device and deployment boundary

The current implementation supports Windows laptops, desktops, and dev boxes. Retail Xbox consoles
are not supported. A future Xbox collector requires a signed native application or sanctioned
dev-kit environment, platform-owner review, equivalent certificate enrollment, and the same compact
profile contract.

For the Windows fleet, deploy a signed executable through Intune or the provided Scheduled Task
installer. Restrict the client private key to the task identity and administrators. Do not deploy
operator or submitter tokens to participant endpoints.

## Residual risk

| Residual | Rating | Treatment |
|---|---|---|
| A local administrator or compromised endpoint can falsify measurements | Medium | Outlier review, compare a sample against approved endpoint-management data, do not treat results as attested billing evidence |
| Enrollment records can re-identify a pseudonymous observer | Medium | Minimize the external enrollment map, restrict access, set a deletion date |
| Availability totals reveal broad device availability | Low-Medium | No timestamps or presence buckets; disclose in consent; aggregate centrally |
| Certificate or token custody failure | Medium | Per-device credentials, ACLs, revocation process, distinct admin/submit tokens, short pilot |
| Local profile is richer than the central report | Medium | Keep local, do not centrally collect raw profiles, provide explicit purge |
| Observer absence has ambiguous cause | Low | Report as unavailable, not as proven sleep or shutdown |
| Retail Xbox support is absent | Not accepted for current pilot | Exclude retail Xbox until a native collector is reviewed |

## Staged go/no-go

1. Run 3-5 controlled devices for 24 hours.
2. Review endpoint alerts, report contents, certificate logs, device counts, and participant impact.
3. Expand to 10-15 diverse devices for another 24 hours.
4. Expand to 50-100 only after Security, Privacy, and the pilot lead approve both prior rings.

Any participant complaint, unexplained endpoint alert, certificate anomaly, data-contract
regression, or material performance impact pauses enrollment.

## Engineering verification

- Full repository suite: 528 passed, 2 Docker-only tests skipped because the daemon was unavailable.
- Ruff: all source, tests, and scripts clean.
- Both PowerShell observer scripts parse successfully.
- Managed installer dry-run produces the privacy-minimized measure-only command.
- Real Uvicorn mutual-TLS integration proves that the enrolled device certificate succeeds and a
  second CA-valid certificate cannot reuse the bearer token, even with a spoofed fingerprint header.

## Required reviewer decisions

- **CISO / Azure Security:** approve the mTLS topology, certificate lifecycle, orchestrator baseline,
  network allow-list, monitoring, and incident process.
- **Microsoft Digital:** approve signed package deployment, Defender/WDAC posture, private-key ACL,
  and device inventory.
- **Privacy / CELA:** approve the compact data contract, consent text, regional consultation, and
  30-day proposed post-pilot deletion window.
- **HR:** confirm voluntary participation language and that measurement is not used for performance
  management.
- **Pilot owner:** own the enrollment map, daily checks, outlier review, kill switch, and deletion
  evidence.

## Approval boundary

Approval of this packet permits only the measurement behavior described here. It does not permit:

- OneCompute job execution.
- Sensitive data processing.
- GPU or AI workload execution.
- Retail Xbox collection.
- Collection of raw local profiles into a central share.
- Use of the data for employee performance, attendance, or productivity decisions.
