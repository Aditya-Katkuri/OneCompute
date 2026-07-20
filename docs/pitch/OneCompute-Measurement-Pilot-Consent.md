# OneCompute Voluntary Measurement Pilot Notice and Consent

**Draft for CELA, Privacy, HR, and employee-representation review**

## Purpose

OneCompute is evaluating whether unused CPU, GPU, and memory capacity on existing organization
devices could support future internal computing work. This pilot measures potential capacity only.
It does not send computing jobs to your device and does not run organization workloads.

## Participation

- Participation is voluntary.
- Choosing not to participate has no employment consequence.
- You may stop at any time without giving a reason.
- The pilot is expected to run for approximately one week.
- This notice does not replace any regional consultation or works-council requirement.

## What the observer does

Approximately every 30 seconds, the observer reads device-level CPU, GPU, and memory utilization.
It also records whether the device is on AC power and a local yes/no indication of whether the user
is idle. Those readings are folded into rolling local aggregates. The observer also records compact
totals for time successfully observed and time when the observer was unavailable.

The observer:

- Does not pull or run a OneCompute job.
- Does not read application names, files, email, browser history, URLs, documents, screen content,
  clipboard content, keystrokes, or input content.
- Does not install a kernel driver.
- Does not open an inbound network port.
- Does not write a per-sample activity timeline in current measurement mode.
- Does not stream live CPU, GPU, memory, or idle activity to the central service.

## Data kept on your device

The local profile contains rolling hour-of-week aggregates for CPU, GPU, memory, AC power, and
idle/away state, plus compact observed/unavailable timing totals. It is stored under
`%LOCALAPPDATA%\OneCompute\usage_profile.json`.

A stable random observer ID is stored under `%LOCALAPPDATA%\OneCompute\observer-id`. The random ID
does not contain your hostname. IT may instead assign a pseudonymous fleet alias.

Older pilot builds may have created `%LOCALAPPDATA%\OneCompute\pilot-telemetry.jsonl`. The current
observer does not add measurement samples to that file. The purge command deletes it and any
rotated copies.

## Data sent centrally

The central service receives only a compact derived summary:

- Pseudonymous observer ID.
- Coarse device class.
- Measurement coverage count.
- Aggregate CPU and GPU average, peak, and conservatively recoverable range.
- Aggregate memory average and headroom.
- Aggregate percentage of observed time on AC power.
- Aggregate observed and unavailable hours per day, timing span, and sample count.

The central service does not receive your per-hour profile, idle/away percentage, raw timestamps,
hostname by default, or a per-sample activity log.

Reports are encrypted in transit with HTTPS and mutual TLS. Each approved device uses its own
certificate. The central service stores the verified certificate fingerprint, approval status, and
a measurement-only marker for enrollment. It does not receive actual CPU count, GPU model, total
RAM, or live free RAM from a current measurement observer. Operator views require an administrative
credential.

## How the data will be used

The data will be used to:

- Estimate potential internal compute capacity.
- Evaluate whether a larger technical pilot is worth pursuing.
- Check that the measurement software operates reliably and without noticeable device impact.
- Support Security, Privacy, CELA, HR, Microsoft Digital, and Azure Compute review.

The measurement data will not be used to evaluate individual employee performance, attendance, work
hours, or productivity.

## Retention and deletion

The central service keeps only the latest compact summary for each observer. Disconnecting an
observer deletes that summary. The proposed default is to delete the pilot database and
pseudonymous audit data within 30 days after the pilot closes, unless an approved security-incident
hold applies.

To stop collection but keep your local profile:

```powershell
powershell -ExecutionPolicy Bypass -File C:\OneCompute\scripts\install_observer.ps1 -Uninstall
```

To stop collection and delete the local profile, legacy telemetry, and observer ID:

```powershell
powershell -ExecutionPolicy Bypass -File C:\OneCompute\scripts\install_observer.ps1 -Purge
```

The personal observer uses the same options in `C:\OneCompute\scripts\observe_me.ps1`.

## Known limitations

- Measurements are reported by software on the device and are not hardware-attested measurements.
- An unavailable interval may mean sleep, shutdown, reboot, network loss, or that the observer was
  stopped. The pilot does not collect a more invasive timeline to distinguish those causes.
- A pseudonymous observer may become identifiable to the small enrollment team if that team keeps a
  separate enrollment map.
- The current observer supports Windows laptops, desktops, and dev boxes. It does not run on retail
  Xbox consoles.

## Questions or withdrawal

Pilot lead: ____________________

Privacy contact: ____________________

Security contact: ____________________
Withdrawal channel: ____________________

## Consent

I have read this notice, had an opportunity to ask questions, and voluntarily agree to participate
in the OneCompute measurement-only pilot.

Participant name: ____________________

Date: ____________________
Consent method or signature: ____________________
