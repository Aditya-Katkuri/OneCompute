# OneCompute Pilot — Phase 0 Runbook (first controlled run)

> **Goal:** prove the whole slice on a machine **you control** — orchestrator + worker + the
> demand-adaptive governor + telemetry — with **zero Defender alerts** and a **verified sub-second
> yield**. Passing this gates Phase 1 (a real employee). See [`pilot-plan.md`](./pilot-plan.md) §5/§11.
>
> **Prereqs:** the repo + `uv` on the machine. Phase 0 runs on **your own** machine (loopback), so it
> needs no sanction; if your machine is managed, use the from-source path (it runs — verified).

Open **three terminals** in the repo root. (PowerShell shown; `export PYTHONPATH=src` on Linux/mac.)

## 1. Terminal A — start the orchestrator (persistent)
```powershell
$env:PYTHONPATH = "src"
uv run python -m orchestrator --host 127.0.0.1 --port 8080 --db .\pilot-fleet.db
```
Note the **Dashboard** URL it prints (`http://127.0.0.1:8080/`). Leave it running.

## 2. Browser — open the dashboard
Open `http://127.0.0.1:8080/` — the OneCompute console (idle fleet, ready to fill in).

## 3. Terminal B — start a worker (adaptive governor + telemetry)
```powershell
$env:PYTHONPATH = "src"
uv run python -m worker --url http://127.0.0.1:8080 --telemetry
```
It prints the **telemetry path** (`…\OneCompute\pilot-telemetry.jsonl`). It registers and starts
polling. *(Tip: the governor is conservative — on a busy machine it may print `skip: outside headroom`,
which is correct. Run when the machine is lightly loaded to see it harvest, and let it learn the
envelope for a bit.)*

## 4. Terminal C — submit work and watch the dashboard
```powershell
$env:PYTHONPATH = "src"
uv run python scripts/submit_jobs.py --url http://127.0.0.1:8080 --kind fanout --n 8     # CPU fan-out
uv run python scripts/submit_jobs.py --url http://127.0.0.1:8080 --kind challenge          # integrity ringer
uv run python scripts/submit_jobs.py --url http://127.0.0.1:8080 --kind ai                 # AI (disclosed fallback w/o keys)
# GPU (only on an NVIDIA machine; otherwise it honestly reports cpu-fallback):
uv run python scripts/submit_jobs.py --url http://127.0.0.1:8080 --kind gpu --n 2
```
Watch tiles flip **busy → idle**, **credits tick**, and jobs complete on the dashboard.

## 5. The make-or-break: it backs off when you get busy

**5a. Admission (rock-solid).** Pause submitting, then simulate the employee getting busy *before*
there's a harvested job running:
```powershell
uv run python scripts/cpu_spike.py 15
```
While the spike runs, watch Terminal B — the worker prints **`skip: outside headroom`** (the governor
won't admit work while *you* need the CPU). When the spike ends, it resumes admitting. This proves the
headroom-aware admission directly.

**5b. Mid-job yield.** Queue a long **sandboxed** job, then spike during it:
```powershell
uv run python scripts/submit_jobs.py --url http://127.0.0.1:8080 --kind fanout --n 1 --items 400000 --op sha256
uv run python scripts/cpu_spike.py 15
```
**Expect:** the worker **yields** the running job and requeues it (Terminal B shows `yielded …`), then
resumes after the spike. The job runs sandboxed (a child process), so the governor subtracts *its* CPU
and attributes the spike to **your** demand.

> **Attribution note:** this is accurate for host-side/subprocess execution — the path managed machines
> use (no Docker). If **Docker is active** on your Phase-0 machine, a containerized job's CPU runs in the
> WSL VM (not a child), so **5a (admission)** is the cleaner demonstration there; the disclosed
> `docker stats` refinement closes the gap (`architecture.md` §3.2).

## 6. Watch Defender throughout
Keep an eye on Microsoft Defender. **Any alert = stop and record it** (on a managed machine this is the
whole point of the sanction; on your own machine you're confirming the behaviour profile).

## 7. Summarize the run
```powershell
$env:PYTHONPATH = "src"
uv run python scripts/pilot_report.py
```
Expect a summary: ticks (admitted vs held, **% harvesting**), **user-CPU** min/avg/max, jobs
completed/yielded, units. Save it.

## 8. Opt-out + resume test
In Terminal B press **Ctrl-C** — the worker stops immediately (instant opt-out). Restart the same
command — it re-registers and resumes (state persisted in `pilot-fleet.db`).

## 9. Teardown
Ctrl-C the worker (B) and the orchestrator (A).

---

## ✅ Phase-0 exit criteria (the gate to Phase 1)
- ☐ **Zero** Defender/Purview alerts.
- ☐ Governor **admitted** work in headroom **and yielded** on the CPU spike (felt sub-second).
- ☐ Results verified — fan-out completes; the **challenge** job is accepted (honest worker).
- ☐ `pilot_report` is sane (it harvested, saw your CPU, completed jobs).
- ☐ **Clean opt-out** (Ctrl-C stops instantly) and resume after restart.

Record the numbers (throughput, % harvesting, yield feel, any alerts) — they're your evidence for the
go/no-go and the security readout.
