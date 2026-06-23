# Run NightShift on a LAN PC

> **What / why.** The doctrine (`.github/copilot-instructions.md` §7) mandates the
> orchestrator run on a **physical LAN PC**, never the cloud dev box (it is GPU-less,
> Sandbox-less, and unreachable from worker machines). `python -m orchestrator` is the
> production-shaped entrypoint: it binds uvicorn to a LAN-reachable address and persists
> the fleet/queue/ledger to a SQLite file so state survives a restart. The demo harness
> (`scripts/demo.py`) stays loopback + in-memory for self-contained runs.

---

## 1. Start the orchestrator (one command)

On the LAN PC that will host the control plane, from the repo root:

```powershell
$env:PYTHONPATH = "src"          # the project runs from src/ (not an installed package)
uv run python -m orchestrator
```

> The `PYTHONPATH=src` line is the same prerequisite the worker entrypoint
> (`python -m worker`) needs — set it once per shell session. (Packaging the project
> so the `-m` commands work without it is a roadmap item owned outside this slice.)

Defaults: binds `0.0.0.0:8080` (reachable by any worker on the LAN) and persists to
`./reeve-orchestrator.db`. Override via flags or environment variables — **flags win over
env vars, which win over defaults**:

| Setting | Flag | Env var | Default |
|---|---|---|---|
| Bind host | `--host` | `REEVE_HOST` | `0.0.0.0` |
| Bind port | `--port` | `REEVE_PORT` | `8080` |
| DB file | `--db` | `REEVE_DB` | `reeve-orchestrator.db` |

```powershell
uv run python -m orchestrator --port 9000 --db C:\nightshift\fleet.db
# or
$env:REEVE_PORT = "9000"; uv run python -m orchestrator
```

On startup it prints a banner with the dashboard URL and the exact worker command for
each detected LAN IPv4, e.g.:

```
============================================================
  NightShift Orchestrator
  Bind:  0.0.0.0:8080
  DB:    C:\Users\you\reeve-orchestrator.db  (persistent)

  Dashboard:  http://192.168.1.50:8080/
  Worker:     python -m worker --url http://192.168.1.50:8080

  Reachability: from a worker PC, first confirm
                curl http://192.168.1.50:8080/state  returns JSON

  Trust: 0.0.0.0 exposes the control plane to the whole LAN. Fine on a
         trusted/isolated switch for the PoC; allow-listing is roadmap.
============================================================
```

A bad/missing `REEVE_PORT` falls back to the default with a clear warning; the process
never throws on startup. If the port is already in use, it exits with a friendly message
(try another `--port`). Press **Ctrl-C** for a clean shutdown.

## 2. Connect a worker (from another machine)

On each worker PC on the same LAN, point the existing worker entrypoint at the printed URL
(from the repo root, with `src` on the path):

```powershell
$env:PYTHONPATH = "src"
uv run python -m worker --url http://192.168.1.50:8080
```

The worker auto-detects its capability, gates on input-idle, and **short-polls every
1–2 s** (`GET /jobs/next`, 204 when there is no work) — matching the doctrine's transport
(plain HTTP short-poll for a handful of workers, not 60 s long-poll).

## 3. Hour-1 reachability check

Before debugging anything fancier, confirm the worker PC can reach the control plane:

```powershell
curl http://192.168.1.50:8080/state     # should return JSON (fleet + jobs + ledger)
curl http://192.168.1.50:8080/healthz    # should return {"ok": true}
```

If these fail from the worker but succeed locally on the orchestrator, it is almost always
a **host firewall** blocking inbound on the port (allow it for a private network) or the
two machines being on different subnets/VLANs.

## 4. Persistence

State lives in the SQLite file (`--db` / `REEVE_DB`), opened in WAL mode. Registered
workers, jobs, results, the rewards ledger, and the activity feed all survive stopping and
restarting `python -m orchestrator` against the same file. The schema is created with
`CREATE TABLE IF NOT EXISTS`, so re-opening an existing DB is safe and idempotent.

## 5. Trust caveat (PoC)

Binding `0.0.0.0` exposes the control plane to **every** host on the LAN — any machine that
can reach the port can register as a worker and submit/pull jobs. That is acceptable on a
trusted, isolated switch for this proof-of-concept. Token/IP allow-listing and TLS are
roadmap items, not in scope for the demo. To restrict exposure on a shared network, bind a
specific interface instead (e.g. `--host 192.168.1.50`).
