# OneCompute Measurement Pilot Runbook

**Target:** 50-100 voluntary devices for approximately one week
**Phase:** measurement only, no job execution
**Primary platforms:** managed Windows laptops, desktops, and dev boxes

This is the lowest-risk first use of OneCompute in an organization. Each volunteer device learns
how much CPU, GPU, and RAM headroom it has without pulling or running any workload. The pilot is
gated by `docs/pitch/pilot-security-approval.md`, the threat model, Privacy/CELA review, and the
participant notice in `docs/pitch/OneCompute-Measurement-Pilot-Consent.md`.

## 1. Supported scope

| Device class | Current status | Pilot treatment |
|---|---|---|
| Windows laptop | Supported | Standard per-user or Intune deployment |
| Windows desktop | Supported | Standard per-user or Intune deployment |
| Windows dev box | Supported | Per-user or startup task, depending on ownership |
| Retail Xbox | Not supported | Do not deploy the Python or PowerShell observer |
| Xbox dev kit or sanctioned Windows-based Xbox environment | Conditional | Security and platform-owner approval required |

A retail Xbox cannot run the current Python/PowerShell observer. Xbox participation requires a
future signed native collector or a sanctioned dev-kit environment. That collector can implement
the same compact `POST /profile` contract without transmitting an activity timeline.

## 2. Privacy-minimized data flow

### Stored only on the device

`%LOCALAPPDATA%\OneCompute\usage_profile.json` contains:

- Rolling 168-slot hour-of-week CPU, GPU, RAM, AC-power, and idle/away aggregates used by the local
  governor.
- Compact observed and unavailable timing totals used to estimate awake and inferred sleep/offline
  periods.
- No process names, application names, file names, URLs, screen content, keystrokes, input content,
  email, document content, or raw event stream.

Measurement mode does not create `pilot-telemetry.jsonl`. Older installations may have that legacy
per-sample file; the upgraded worker can use it once to bootstrap timing, after which the operator
should purge it.

### Sent to the orchestrator

The worker sends one compact derived summary approximately every five minutes:

- Stable random observer ID, or an IT-supplied pseudonymous fleet alias.
- Coarse device class: laptop, desktop, devbox, xbox, or unknown.
- Coverage count.
- Aggregate CPU and GPU average, peak, and conservatively recoverable range.
- Aggregate RAM average and headroom.
- Aggregate percentage of observed time on AC power.
- Compact observed/unavailable hours per day, timing span, and sample count.

The worker does **not** upload:

- Per-hour buckets or wall-clock timestamps.
- Idle/away or last-input percentages.
- A live CPU/GPU/RAM stream.
- The hostname by default.
- Raw profile files or a per-sample timeline.

Enrollment separately stores the observer ID, approval status, a measurement-only marker, and the
fingerprint derived by the server from the TLS-verified peer certificate. Current measurement
registration does not send actual CPU count, GPU model, total RAM, or live free RAM. Approval
heartbeats carry liveness only.

The orchestrator accepts legacy bucket reports only for rolling upgrades. It collapses them in
memory, discards the hourly and idle pattern, and stores only the compact summary.

## 3. Required security controls

Remote fleet collection requires all of the following:

1. HTTPS with the server CA pinned by every worker.
2. A unique client certificate and private key per device.
3. Mutual TLS on the orchestrator.
4. Device identity binding to the fingerprint derived directly from the verified TLS peer
   certificate. Client-supplied fingerprint headers are ignored.
5. Device-code approval before profile upload.
6. Separate submit and admin/operator tokens.
7. Operator authentication for `/state`, `/measurement`, job/workload detail, and audit APIs.
8. A signed worker package, scoped Defender/WDAC approval, and certificate private-key ACLs.

Plain HTTP is rejected for remote measurement URLs. It is accepted only for explicit loopback
development.

## 4. Stand up the secure orchestrator

Set distinct secrets through the environment rather than placing them in shell history:

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

`--secure-measurement-pilot` fails closed unless server TLS, client CA, and distinct submit/admin
tokens are configured. It enables approval gating and certificate-bound device identity.

Keep the orchestrator on a hardened, access-controlled host. Restrict inbound access to the pilot
network and administrative operators. Back up the database according to the approved retention
plan, not indefinitely.

## 5. Enroll each managed Windows device

Each device needs its own client certificate and private key. Do not reuse one client credential
across the fleet.

```powershell
powershell -ExecutionPolicy Bypass -File C:\OneCompute\scripts\install_observer.ps1 `
  -Url https://onecompute-pilot.contoso.com:8080 `
  -TlsCa C:\ProgramData\OneCompute\pki\server-ca.crt `
  -ClientCert C:\ProgramData\OneCompute\pki\device.crt `
  -ClientKey C:\ProgramData\OneCompute\pki\device.key `
  -DeviceClass laptop
```

The installer creates a resilient Scheduled Task, starts it, runs on battery because measurement
itself imposes no workload, restarts on failure, and relaunches after logon. Use `-AtStartup` only
for sanctioned always-on dev boxes and only from an elevated shell.

For Intune, deploy the signed executable, certificate chain, per-device client credential, and the
same arguments. Store the private key under an ACL limited to the task identity and administrators.
Do not place admin or submit tokens on participant devices.

The worker registers with its random observer ID and certificate fingerprint. The pilot operator
then approves the pending device code in the dashboard. The dashboard asks for the admin token and
keeps it only in page memory.

## 6. Solo local observer

For one-device testing with no orchestrator or network:

```powershell
powershell -ExecutionPolicy Bypass -File C:\OneCompute\scripts\observe_me.ps1 `
  -Install `
  -DeviceClass laptop
```

Check it at any time:

```powershell
powershell -ExecutionPolicy Bypass -File C:\OneCompute\scripts\observe_me.ps1 -Status
```

The status command reports verified observer PIDs, durable profile freshness, sample count,
autostart state, and whether a legacy timeline still exists. The observer appears in Task Manager
as `OneCompute Observer`.

## 7. Pilot rollout

Use staged enrollment instead of connecting 100 devices at once:

1. **Control ring:** 3-5 pilot-team devices for at least 24 hours.
2. **Early ring:** 10-15 diverse laptops and dev boxes for at least 24 hours.
3. **Fleet ring:** expand to 50-100 volunteers only after Security, Privacy, and the pilot lead
   review the first two rings.

At each gate, confirm:

- No Defender, WDAC, Purview, or certificate alerts.
- No unexpected CPU, GPU, memory, battery, or network impact.
- Profile freshness and upload cadence are healthy.
- Device counts and classes match the approved inventory.
- No duplicate observer IDs or certificate fingerprints.
- No anomalous or implausible self-reported measurements.

## 8. Monitor the pilot

Read the central summary with the admin token:

```powershell
$scheme = "Bear" + "er"
$headers = @{ Authorization = "$scheme $env:ONECOMPUTE_ADMIN_TOKEN" }
Invoke-RestMethod https://onecompute-pilot.contoso.com:8080/measurement `
  -Headers $headers `
  -Certificate (Get-PfxCertificate C:\OneCompute\pki\operator.pfx)
```

`GET /measurement` returns fleet averages, conservative recoverable ranges, timing coverage, and
device-class counts. It does not return per-device hour patterns or idle/away data.

The measurements are self-reported by the endpoint process. Mutual TLS proves which enrolled device
sent a report, but it does not prove the report is physically accurate. Review outliers, duplicate
patterns, impossible percentages, abrupt discontinuities, and unexpected class changes. For a
sample of devices, compare results with approved Intune or endpoint-management inventory.

## 9. Reporting

Use the central compact rollup for the 50-100 device readout. Do not collect raw
`usage_profile.json` files into a central share unless Privacy explicitly approves that richer
hour-of-week pattern.

For a local-only participant, run reports on that device:

```powershell
uv run python C:\OneCompute\scripts\measure_report.py `
  "$env:LOCALAPPDATA\OneCompute\usage_profile.json"

uv run python C:\OneCompute\scripts\business_case.py `
  "$env:LOCALAPPDATA\OneCompute\usage_profile.json"
```

The business-case report separates currently executable awake-only capacity from modeled
wake-enabled potential. OneCompute does not currently wake or power on sleeping devices.

## 10. Opt-out, erasure, and retention

Stop collection but retain the local profile:

```powershell
powershell -ExecutionPolicy Bypass -File C:\OneCompute\scripts\install_observer.ps1 -Uninstall
```

Stop collection and delete all known local pilot artifacts:

```powershell
powershell -ExecutionPolicy Bypass -File C:\OneCompute\scripts\install_observer.ps1 -Purge
```

The personal Startup-folder observer supports the same `-Uninstall` and `-Purge` choices.

`-Purge` removes:

- `usage_profile.json`
- `pilot-telemetry.jsonl` and rotated legacy copies
- the persistent random `observer-id`
- the scheduled task or Startup launcher

When the operator disconnects a worker, the orchestrator immediately deletes that worker's latest
central measurement summary. Audit records retain a pseudonymous observer ID for security
accountability. The pilot approval should set a concrete retention window for the database and
audit log. The proposed default is deletion within 30 days after the pilot closes, unless an
approved incident hold applies.

## 11. Incident response and kill switch

Pause the pilot for any security alert, suspected certificate misuse, unexplained data anomaly,
participant complaint, or material endpoint impact:

1. Stop the orchestrator or block its network listener.
2. Disconnect affected observer IDs through the authenticated admin endpoint.
3. Stop or purge the observer on affected devices.
4. Revoke affected client certificates.
5. Preserve only the evidence required by the approved incident process.
6. Do not resume until the Security and Privacy contacts approve.

## 12. Residual risks reviewers must accept

- Endpoint measurements are self-reported and can be falsified by a local administrator or
  compromised device.
- A pseudonymous observer can still become identifiable if the operator keeps an external
  observer-to-employee enrollment map.
- Compact observed/unavailable totals can reveal broad device-availability behavior, although they
  do not reveal wall-clock times or an idle/presence heatmap.
- Certificate issuance, private-key protection, revocation, and operator-token custody are
  deployment responsibilities.
- Unavailable gaps cannot distinguish sleep, shutdown, reboot, network loss, or a stopped observer
  without collecting more invasive telemetry.
- Retail Xbox collection is not implemented.
