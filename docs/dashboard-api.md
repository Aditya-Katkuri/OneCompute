# OneCompute — Dashboard API reference

Everything a front-end needs to build the operator dashboard. The backend is complete and
verified; a UI integrates by polling and calling these HTTP endpoints on the orchestrator
(`python -m orchestrator`, default `http://<host>:8080`). All bodies are JSON. No auth header in
the PoC; device onboarding is gated by the **approval** flow below.

## Live polling model

Poll these on a timer (the bundled dashboard uses 500 ms):

- `GET /state` → the whole fleet + jobs snapshot:
  ```jsonc
  {
    "workers": [{
      "worker_id": "laptop-ana", "idle": false, "busy": true, "has_gpu": false,
      "cpu_pct": 63.0, "gpu_pct": null, "free_ram_gb": 11.2,
      "blacklisted": false, "credits": 40.0,
      "approved": true, "device_code": null          // device_code set only while pending
    }],
    "jobs": [{ "job_id": "…", "kind": "fractal", "state": "completed", "assigned_worker": "laptop-ana" }],
    "total_credits": 120.0
  }
  ```
- `GET /events?since=<last_id>` → activity feed for a live log:
  ```jsonc
  { "events": [{ "id": 12, "ts": "…", "type": "approved", "worker_id": "laptop-ana", "job_id": null, "detail": "…" }],
    "last_id": 12 }
  ```
  `type` ∈ `registered | approved | submitted | assigned | completed | yielded | failed | blacklisted`.

## 1. Connect / approve new devices

A laptop/dev box joins with `python -m worker --url http://<host>:8080`. If the orchestrator was
started with `--require-approval`, the new worker shows up in `/state` with `approved=false` and a
`device_code`. Approve it:

- `POST /workers/{worker_id}/approve` → `{ "ok": true, "worker_id": "…" }` (404 if unknown).

Until approved, the worker cannot lease work. Render any `approved=false` worker as **Pending** with
its `device_code` + an Approve button calling this endpoint.

## 2. Show connected devices + usage graphs

Read the `workers` array from `GET /state`. Each worker carries live **`cpu_pct`**, **`gpu_pct`**
(`null` on non-GPU machines), and **`free_ram_gb`** — the worker streams these every ~1 s
(tunable via the worker's `--usage-interval`, floored at 0.25 s), so a UI that keeps a rolling
per-`worker_id` history can draw a live usage sparkline/graph per device.
`busy` (has a leased job) and `idle` drive the tile state; `credits` is the reward tally
(GPU machines earn 5×).

## 3. Launch the example workloads

Get the launchable catalog (so buttons aren't hardcoded — add entries server-side to add more):

- `GET /workloads/catalog` →
  ```jsonc
  { "workloads": [
    { "kind": "fractal",        "label": "Fractal render",       "category": "non-AI", "ai": false,
      "blurb": "…", "default_params": { "width": 720, "height": 480, "max_iter": 120 }, "split": "per_machine" },
    { "kind": "optimize",       "label": "Param-sweep optimize", "category": "non-AI", "ai": false,
      "blurb": "…", "default_params": { "n_candidates": 30000, "dims": 8 }, "split": "per_machine" },
    { "kind": "ai.batch_infer", "label": "AI inference",         "category": "AI",     "ai": true,
      "blurb": "…", "default_params": { "slice_size": 3 }, "split": "slice_size" },
    { "kind": "ai.synth",       "label": "AI synthetic data",    "category": "AI",     "ai": true,
      "blurb": "…", "default_params": { "total_rows": 30 }, "split": "per_machine" }
  ] }
  ```
  `split: "per_machine"` → pass `n_tiles` = number of approved workers; `"slice_size"` → the builder
  splits internally, `n_tiles` is ignored.

Launch one across the fleet in a single call:

- `POST /workloads` body `{ "kind": "fractal", "n_tiles": 3, "params": { "width": 720, "height": 480, "max_iter": 120 } }`
  → `{ "workload_id": "…", "kind": "fractal", "job_ids": ["…", "…", "…"] }`
  (400 on an unknown kind or bad params.) The work is split into one tile per machine and enqueued;
  the fleet picks tiles up automatically.

## 4. Show workload outputs + completion status

- `GET /workloads/{workload_id}` →
  ```jsonc
  { "workload_id": "…", "kind": "fractal", "total": 3, "completed": 2,
    "jobs": [{ "job_id": "…", "kind": "fractal", "state": "completed",
               "assigned_worker": "dev-box", "units": 160, "workload_id": "…",
               "output": { … }, "created_at": "…", "updated_at": "…" }] }
  ```
  Poll until `completed === total`. `completed/total` drives a progress bar; per-job `state` +
  `assigned_worker` show which machine ran which tile.
- `GET /jobs/{job_id}` → the same single-job shape (incl. `output`).

### Output shape + suggested visualization per kind

| kind | per-job `output` | suggested visual |
|---|---|---|
| `fractal` | `{ width, height, row_start, row_end, max_iter, rows: [[int per pixel], …] }` | draw to a `<canvas>`: place each row at `y = row_start + i`, color each escape count (`>= max_iter` → black, else a ramp). Redraw as tiles complete → image fills in band-by-band. |
| `optimize` | `{ best_score, best_params: [float…], best_index, evaluated }` | reduce to the global best (max `best_score`); show the winning machine + score; a small bar of per-tile scores. |
| `ai.batch_infer` | `{ results: [{ prompt, completion, tokens }], backend }` | a list of prompt → completion cards; tag the `backend`. |
| `ai.synth` | `{ rows: [{ name, role, team, summary }, …], backend }` | a table of the merged rows across all tiles. |

## Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| GET | `/state` | fleet + jobs snapshot (poll) |
| GET | `/events?since=N` | activity feed (poll) |
| POST | `/workers/{id}/approve` | approve a pending device |
| GET | `/workloads/catalog` | launchable example workloads |
| POST | `/workloads` | launch a workload across the fleet |
| GET | `/workloads/{id}` | workload status + per-tile outputs |
| GET | `/jobs/{id}` | single job + output |
| GET | `/healthz` | reachability probe |

> The bundled `src/dashboard/index.html` already implements the live poll loop, fleet tiles, the
> device approval flow, and the events feed against these endpoints — extend it (launch panel,
> results panels, usage graphs) rather than starting from scratch.
