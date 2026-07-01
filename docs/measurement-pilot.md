# OneCompute Measurement Pilot Runbook (2 weeks, voluntary, no job execution)

The lowest-risk first step for OneCompute in a real org: measure how much idle CPU/GPU/RAM headroom actually exists across employee laptops, dev boxes, and Xboxes, before any workload is ever routed onto a device. This pilot runs pure read-only telemetry. It never pulls or runs a job, so there is no chance of a workload landing on someone's machine.

Pairs with `Azure_Integration_Plan.md` (Phase 0) and `Financial_Impact.md` (the measured numbers this pilot produces replace the estimates there).

## What it does and does not do
- Does: on each volunteer device, learn a rolling, on-device usage profile (per hour-of-week min/avg/peak CPU, GPU, RAM), stream live utilization to the operator dashboard, and (opt-in) upload the derived usage envelope to the orchestrator so the fleet-wide number is visible centrally.
- Does not: pull a job, run a job, or place any compute load on the machine. It never consults the admission/yield governor.
- Privacy: the usage profile is stored on the employee's own machine (`%LOCALAPPDATA%\OneCompute\usage_profile.json`). Only derived, aggregated hour-of-week statistics (means/peaks per resource) are ever uploaded, and only a derived spare-capacity number is shown. No keystrokes, files, screen, app content, or wall-clock activity times are collected. Participation is voluntary and stops instantly on Ctrl-C or uninstall.

## 1. Consent and scope
- Volunteers opt in (see `pilot-consent.md` if present). Confirm managers are aware.
- Device classes to cover: assigned laptops, unassigned laptops, idle dev boxes, and Xboxes.

## 2. Stand up the orchestrator (collects profiles centrally)
- Run the orchestrator on a reachable host: `uv run python -m orchestrator` (default `http://<host>:8080`). Confirm reachability from a device: `curl http://<orchestrator>:8080/healthz` returns `{"ok": true}`.
- Devices in `--measure-only` mode upload their on-device usage envelope to the orchestrator automatically (opt-in, derived stats only), so you get a live fleet view without collecting files by hand.
- Fully offline still works: if a device cannot reach the orchestrator the upload is silently skipped and the profile keeps building locally, so you can collect that file later (offline path in step 5).

## 3. Run the measurement worker (each device)
```powershell
uv run python -m worker --url http://<orchestrator-host>:8080 --measure-only
```
- Prints `measure-only: tracking CPU/GPU/RAM, no jobs will run`, then samples on a cadence (default every 30s; tune with `--measure-interval`) and uploads its envelope to the orchestrator on the first sample and about every five minutes after.
- For managed fleets, ship the signed single-exe instead of a Python checkout: build with `scripts/build_worker_exe.ps1` (it prints the SHA-256 for Defender allow-listing; sign it with the corporate cert), then run `onecompute-worker.exe --url http://<host>:8080 --measure-only`.
- Leave it running for the pilot window (about two weeks) so the profile fills its 168 hour-of-week buckets across weekdays and weekends. Stop anytime with Ctrl-C; the learned profile is saved and a final envelope is uploaded on exit.

## 4. Watch the fleet (live, central)
- The operator dashboard (served at the orchestrator root `/`) shows a live "Measured idle headroom" beat: recoverable CPU headroom, contributing device count, average utilization, GPU and RAM.
- Or read it directly: `GET /measurement` returns the fleet-wide measured idle-headroom rollup as JSON (see `dashboard-api.md`).

## 5. Produce the report (the deliverable)
- Central (recommended): the live `GET /measurement` rollup already is the measured number, computed with the same governor-consistent math as the CLI.
- Offline / archival: collect each device's `%LOCALAPPDATA%\OneCompute\usage_profile.json` (one file per device) into a directory and run:
  ```powershell
  uv run python scripts/measure_report.py <dir-of-collected-profiles>
  ```
  Output is a per-device and aggregate summary of average/peak CPU/GPU/RAM plus an **estimated conservatively-recoverable headroom** range (measured spare capacity, with the governor's 25 percent comfort margin, harvested at a conservative 20-40 percent). Both paths share identical math (`measurement.headroom`), so the CLI and the dashboard always agree. Use `--json` for a machine-readable version.
- This aggregate is the honest, measured number that replaces the estimates in `Financial_Impact.md`.

## 6. Interpret and hand off
- The measured recoverable-headroom figure, plus the utilization profiles, are exactly what Azure Compute (functionality) and the CISO office (safety) need to co-design safe routing of Azure/Foundry requests into the pool (Azure_Integration_Plan.md, Phase 0). Nothing is routed onto a device until that co-development is done.

## 7. Rollback
- Fully reversible on the device: volunteers Ctrl-C or uninstall the worker. The only device artifact is the local profile file, which is theirs to keep or delete.
- Central state is minimal: the orchestrator keeps only the latest derived usage envelope per worker (one row, derived stats only). Removing a device from the fleet or tearing down the orchestrator's database drops it; nothing about a device persists once it stops participating.
