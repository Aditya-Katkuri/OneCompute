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
| A modified observer could send live metrics or a job ID in its heartbeat | The server independently replaces measurement heartbeat values with privacy-safe sentinels and never renews a job lease for a measurement-only identity. | `src/orchestrator/app.py`, `tests/orchestrator/test_profile_ingest.py` |
| Local JSONL created a timestamped presence timeline | Measurement mode no longer logs `measure` events. Existing JSONL is migration-only and covered by purge. Remaining job diagnostics are size-bounded. | `src/worker/__main__.py`, `src/worker/telemetry.py` |
| Disk errors could make a long observer run look healthy while no profile persisted | The worker probes writeability before collection, saves immediately and about every minute, reports save failures, flushes and syncs files, and publishes by atomic replace. | `src/worker/__main__.py`, `src/worker/profiler.py`, `tests/worker/test_profiler.py` |
| Corrupt or hostile local profile values could be loaded or silently overwritten | Loads are size-bounded, schema-checked, finite, and clamped. Malformed profiles are preserved under a unique recovery name before a clean profile starts; failure to preserve blocks collection and overwrite. | `src/worker/profiler.py`, `tests/worker/test_profiler.py` |
| Startup, Scheduled Task, and foreground launches could corrupt one shared profile | An OS-backed per-profile singleton lock allows only one observer writer. Unique temporary files remove fixed-name races. | `src/worker/profile_lock.py`, `tests/worker/test_profile_lock.py` |
| CPU, RAM, or GPU sensor failure could be misreported as 0% load | Invalid CPU or RAM readings skip the sample. A missing GPU reading excludes only GPU while retaining CPU/RAM. Unknown AC/idle readings are excluded from their means. CPU-only devices carry `gpu_sampled=false` and cannot inflate fleet GPU headroom. | `src/worker/__main__.py`, `src/worker/governor.py`, `src/worker/profiler.py`, `src/measurement/headroom.py` |
| A startup outage or pending approval stopped useful local collection | Local collection now continues while enrollment is unavailable or pending, retries periodically, and keeps central upload blocked until approval. | `src/worker/__main__.py`, `src/worker/agent.py`, `src/orchestrator/app.py` |
| Plain HTTP exposed reports and bearer tokens | Non-loopback measurement URLs require HTTPS. TLS material with HTTP is a hard error. Managed installation requires pinned mTLS for remote fleets. | `src/worker/__main__.py`, `scripts/install_observer.ps1` |
| Worker-ID collision could rotate a token and take over an observer | The server derives the fingerprint directly from the TLS-verified peer certificate, ignores spoofed client headers, rejects unbound legacy collisions, and rejects mismatched re-registration before token rotation. | `src/orchestrator/mtls_protocol.py`, `src/orchestrator/app.py`, `tests/trust/test_tls.py` |
| Operator read APIs were unauthenticated | State, measurement, job/workload detail, and audit reads use the admin/operator gate when configured. The dashboard prompts and keeps tokens only in page memory. | `src/orchestrator/app.py`, `src/dashboard/index.html` |
| Installers could run elevated or as Intune SYSTEM, use a PATH-resolved Python, or leave another persistence mechanism active | The personal installer rejects elevated tokens. Both installers reject SYSTEM, LOCAL SERVICE, and NETWORK SERVICE, use limited privilege, require a fixed executable or absolute virtual-environment interpreter, remove Scheduled Task and Startup persistence, and stop only command lines bound to the exact profile. | `scripts/observe_me.ps1`, `scripts/install_observer.ps1`, `tests/test_observer_scripts.py` |
| Opt-out did not stop the observer or erase all local data | `-Uninstall` stops collection and removes both persistence mechanisms while retaining the profile. `-Purge` also removes locks, temporary and recovery files, configuration, legacy telemetry and rotations, and the observer ID. | `scripts/observe_me.ps1`, `scripts/install_observer.ps1` |
| Disconnect left a central measurement row behind | Device disconnect now deletes the latest compact central summary. | `src/orchestrator/app.py`, `tests/orchestrator/test_profile_ingest.py` |

## Central data contract

| Field | Purpose | Privacy property |
|---|---|---|
| Observer ID | Stable device correlation | Random and hostname-free by default |
| Device class | Fleet segmentation | Coarse category only |
| Coverage count | Report quality | No wall-clock location |
| CPU summary | Capacity estimate | Aggregate average, peak, and conservative range |
| GPU sampling status and summary | Capacity estimate | Only valid GPU-sampled devices contribute; CPU-only devices cannot appear as idle GPUs |
| RAM summary | Capacity estimate | Aggregate average and headroom |
| AC percentage | Harvest-window estimate | Aggregate only |
| Availability totals | Awake and unavailable estimate | Daily totals and span, no timestamps |
| Report receipt time | Freshness and operations | Server-generated latest receipt time only, not a device sample timeline |
| Enrollment metadata | Approval and device binding | Observer ID, measurement-only marker, and verified certificate fingerprint; no live free RAM or actual hardware inventory |

The central report has no application data, file data, keystrokes, screen content, URLs, process
names, device-generated sample timestamps, hour-of-week slots, or idle/away field.

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
installer in the participant's user context at limited privilege. In Intune, enable **Run this
script using the logged-on credentials**. Both installers reject SYSTEM, LOCAL SERVICE, and NETWORK
SERVICE.
Restrict the client private key to the task identity and administrators. Do not deploy operator or
submitter tokens to participant endpoints.

## Residual risk

| Residual | Rating | Treatment |
|---|---|---|
| A local administrator or compromised endpoint can falsify measurements | Medium | Outlier review, compare a sample against approved endpoint-management data, do not treat results as attested billing evidence |
| Enrollment records can re-identify a pseudonymous observer | Medium | Minimize the external enrollment map, restrict access, set a deletion date |
| Availability totals reveal broad device availability | Low-Medium | No timestamps or presence buckets; disclose in consent; aggregate centrally |
| Certificate or token custody failure | Medium | Per-device credentials, ACLs, revocation process, distinct admin/submit tokens, short pilot |
| Local profile is richer than the central report | Medium | Keep local, do not centrally collect raw profiles, provide explicit purge |
| A sensor outage reduces metric-specific coverage | Low | Retain valid metrics, exclude unavailable readings, surface profile freshness and sample count, never substitute optimistic 0% utilization |
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

- Full repository suite: 575 passed, 2 Docker-only tests skipped because the daemon was unavailable.
- Focused observer lifecycle suite: 153 passed.
- Ruff: all source, tests, and scripts clean.
- Both PowerShell observer scripts parse successfully.
- Managed and personal installer dry-runs produce the privacy-minimized measure-only command,
  including Unicode paths and an exact profile path.
- Behavioral tests cover limited privilege, no PATH fallback, singleton operation, resilient purge,
  enrollment retry, pending local collection, and GPU-contributor accuracy.
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
