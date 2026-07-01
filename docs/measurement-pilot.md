# OneCompute Measurement Pilot Runbook (2 weeks, voluntary, no job execution)

The lowest-risk first step for OneCompute in a real org: measure how much idle CPU/GPU/RAM headroom actually exists across employee laptops, dev boxes, and Xboxes, before any workload is ever routed onto a device. This pilot runs pure read-only telemetry. It never pulls or runs a job, so there is no chance of a workload landing on someone's machine.

Pairs with `Azure_Integration_Plan.md` (Phase 0) and `Financial_Impact.md` (the measured numbers this pilot produces replace the estimates there).

## What it does and does not do
- Does: on each volunteer device, learn a rolling, on-device usage profile (per hour-of-week min/avg/peak CPU, GPU, RAM) and stream live utilization to the operator dashboard.
- Does not: pull a job, run a job, or place any compute load on the machine. It never consults the admission/yield governor.
- Privacy: the usage profile is stored only on the employee's own machine (`%LOCALAPPDATA%\OneCompute\usage_profile.json`); only a derived spare-capacity number is streamed. No keystrokes, files, screen, or app content are collected. Participation is voluntary and stops instantly on Ctrl-C or uninstall.

## 1. Consent and scope
- Volunteers opt in (see `pilot-consent.md` if present). Confirm managers are aware.
- Device classes to cover: assigned laptops, unassigned laptops, idle dev boxes, and Xboxes.

## 2. Reachability (optional, for the live dashboard)
- If you want the live fleet view, stand up the orchestrator on a reachable host and confirm each device can reach it: `curl https://<orchestrator>/healthz` returns `{"ok": true}`.
- The pilot also works fully offline: the on-device profile is learned locally even with no orchestrator, so a machine that cannot reach the host still contributes a profile you can collect later.

## 3. Run the measurement worker (each device)
```powershell
uv run python -m worker --url http://<orchestrator-host>:8080 --measure-only
```
- It prints `measure-only: tracking CPU/GPU/RAM, no jobs will run` and then samples on a cadence (default every 30s; tune with `--measure-interval`).
- Leave it running for the pilot window (about two weeks) so the profile fills its 168 hour-of-week buckets across weekdays and weekends.
- Stop anytime with Ctrl-C; the learned profile is saved on exit.

## 4. Collect the profiles (end of pilot)
- With each volunteer's consent, gather their `%LOCALAPPDATA%\OneCompute\usage_profile.json` into a single directory, one file per device (rename per device or asset tag).

## 5. Produce the report (the deliverable)
```powershell
uv run python scripts/measure_report.py <dir-of-collected-profiles>
```
- Output is a per-device and aggregate summary of average/peak CPU/GPU/RAM utilization plus an **estimated conservatively-recoverable headroom** range (measured spare capacity, with the governor's 25 percent comfort margin, harvested at a conservative 20-40 percent).
- This aggregate is the honest, measured number that replaces the estimates in `Financial_Impact.md`. Use `--json` for a machine-readable version.

## 6. Interpret and hand off
- The measured recoverable-headroom figure, plus the utilization profiles, are exactly what Azure Compute (functionality) and the CISO office (safety) need to co-design safe routing of Azure/Foundry requests into the pool (Azure_Integration_Plan.md, Phase 0). Nothing is routed onto a device until that co-development is done.

## 7. Rollback
- Fully reversible: volunteers Ctrl-C or uninstall the worker. The only artifact is the local profile file, which is theirs to keep or delete. No orchestrator-side state is required for the measurement pilot.
