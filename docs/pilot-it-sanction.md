# OneCompute Pilot — IT / Security Sanction Request

> **Ask:** approve a **time-boxed, opt-in 5-machine pilot** of the OneCompute worker agent and
> **allow-list** it in Defender for Endpoint so it isn't quarantined as cryptojacking. We are
> designing to **pass, not bypass** your controls (ref: idea.md §8 / §10).

## 1. What the agent is
A user-space agent that runs **company batch jobs** (test/eval/data) on a machine's **spare CPU/GPU
headroom**, paying employees points for verified work. Internal-only; no external compute marketplace.

- **Runs as:** a normal **user process** in the interactive session (no service, **no admin/elevation**).
- **Form:** a **code-signed `onecompute-worker.exe`** (PyInstaller). SHA-256 + Authenticode publisher
  provided below for allow-listing.
- **Footprint:** outbound HTTPS only; one local profile file under `%LOCALAPPDATA%\OneCompute`.

## 2. Why it can look like cryptojacking (and why this pilot is safe)
Sustained CPU/GPU is the cryptojacking signature, so Defender may flag it. Mitigations:
- **Opt-in only**, on **5 named machines**, **time-boxed**, supervised, with a kill switch.
- A **demand-adaptive governor** keeps usage in the machine's *learned spare headroom* and **yields
  sub-second** when the employee's own demand rises — so it backs off under real load by design.
- **Never on battery**; honors caps/schedule.
- We are **requesting allow-listing**, not evading detection.

## 3. Behavior — what it does / does NOT do
| Does | Does NOT |
|---|---|
| Run sandboxed jobs in spare headroom | Read user files, keystrokes, screen, or browser |
| Verify a **signed manifest** (code+data hash) before running any job | Run code that isn't signed/verified |
| Wipe job inputs/outputs on completion (no-persistence) | Persist job data or user data |
| Keep a usage profile **locally** (never uploaded) | Exfiltrate activity/telemetry off the machine |
| Poll the orchestrator **outbound** over HTTPS | Open any inbound port / accept inbound connections |

## 4. Network & data scope
- **Egress:** HTTPS to a single orchestrator endpoint (`https://<orchestrator-host>:<port>`) — short-poll
  (`GET /jobs/next`), `POST /results`, `POST /heartbeat`, `GET /healthz`. **No inbound ports.**
- **Data handled:** only the **job slice** sent to it (data-minimized) + the result it returns. No access
  to corporate file shares, mailboxes, or browser data. Job data is wiped on completion.
- **Identity:** one identity per node (corp SSO recommended) so all actions are attributable.

## 5. What we need from you (the sanction)
1. **Defender for Endpoint allow-list** the signed exe (by **publisher cert** and **SHA-256** below) on the
   5 pilot machines, so sustained CPU from this specific, signed binary isn't quarantined.
2. Confirm **WDAC/AppLocker** permits the signed exe to execute (or advise the approved path).
3. Confirm **Purview DLP** won't block the orchestrator egress for the pilot data class.
4. A point of contact to **monitor** for alerts during the pilot.

## 6. Monitoring, rollback & blast radius
- **Blast radius:** 5 named, opt-in machines; **time-boxed**; supervised.
- **Live monitoring:** the OneCompute dashboard (fleet, throughput, yields) + per-machine pilot telemetry;
  we watch Defender alerts in parallel.
- **Instant rollback:** stop the orchestrator → all workers go idle within one poll; employees can
  **Ctrl-C / uninstall** anytime; the local profile is the only artifact and is removable.
- **Exit:** at pilot end the agent is uninstalled and the allow-list entry removed.

## 7. Roadmap (post-pilot, for production)
Intune-managed deployment, full code-signing + SLSA provenance, Defender allow-listing as policy,
Purview-sanctioned data channels, and (for sensitive workloads) TEE/confidential compute. The pilot
validates unobtrusiveness + safety at small scale before any of that.

---

### Allow-list details (filled at build time)
- **Binary:** `onecompute-worker.exe`
- **SHA-256:** `<inserted from the build — see scripts/build_worker_exe.* output / dist hash>`
- **Authenticode publisher:** `<corp code-signing cert subject>` *(the pilot lead will sign with the
  corporate cert; an unsigned build + hash is available for hash-based allow-listing in the interim)*
- **Version / build date:** `<filled at build>`
- **Orchestrator endpoint(s):** `https://<orchestrator-host>:<port>`
- **Pilot machines (5):** `<asset tags / hostnames>`
- **Pilot window:** `<start> – <end>`
