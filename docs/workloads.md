# OneCompute — the 4 example workloads

The demo fans **four hardcoded example workloads** across the fleet (2 laptops + a dev box). Each
is submitted as a set of jobs that the fleet picks up in parallel; the work is split with a
**hardcoded partition** — one tile per machine — because the dynamic headroom governor is
deliberately set aside for the demo (see [`architecture.md`](./architecture.md) §3.2). Two are
**non-AI** and two are **AI**, to show range.

| # | Workload | Job kind | AI? | Executor (where it runs) | Split across the fleet | Aggregated by |
|---|---|---|---|---|---|---|
| 1 | Fractal render | `fractal` | no | `jobkit.execute._fractal` — **pure stdlib**, runs inside the Docker sandbox | image rows → one band per machine | `workloads.fractal.assemble_tiles` → PNG |
| 2 | Param-sweep optimize | `optimize` | no | `jobkit.execute._optimize` — **pure stdlib**, runs inside the Docker sandbox | candidate index range → one slice per machine | `workloads.optimize.aggregate_optimize` |
| 3 | Model inference | `ai.batch_infer` | yes | `jobkit.execute._ai_batch_infer` — **host-side** (real SDK + key) | prompts → slices of `slice_size` | concatenate `results` |
| 4 | Synthetic data | `ai.synth` | yes | `jobkit.execute._ai_synth` — **host-side** (real SDK + key) | rows → one slice per machine | `workloads.synth.merge_synth` |

> **Why the AI ones run host-side.** A jobkit executor also runs inside a `python:3.12-slim`
> Docker container whose payload is stdlib-only and gets **no API key**. So the non-AI executors
> must be pure stdlib (they do real work in the sandbox), and the AI kinds route **host-side**
> (`worker/agent.py` sends any `ai.*` kind to the on-host Job-Object path) where the
> Anthropic/OpenAI SDK and key are available. Without a key the AI executors fall back to a
> **disclosed deterministic** output, so they always run.

The hardcoded split lives in [`src/workloads/partition.py`](../src/workloads/partition.py):
`even_ranges(total, n)` and `weighted_ranges(total, weights)` carve `[0, total)` into contiguous,
gap-free ranges. Each workload's `build_*_jobs(n_tiles=…)` helper uses it to produce one job per
tile, with `units` = the size of that tile.

---

## 1. Fractal render (`fractal`) — non-AI

A distributed **Mandelbrot**: each machine renders a horizontal **band** of the image, and the
bands reassemble into one picture. Visual, embarrassingly parallel, deterministic.

- **Builder:** `workloads.fractal.build_fractal_jobs(n_tiles, width=900, height=600, max_iter=120, weights=None)` → splits the image **height** into `n_tiles` row-bands.
- **Input (per tile):** `{ width, height, row_start, row_end, max_iter, x_min, x_max, y_min, y_max }` (default window is the classic full view).
- **Output (per tile):** `{ width, row_start, row_end, max_iter, rows: [[int per pixel], …], yielded }` — each int is the escape count (`>= max_iter` ⇒ in-set).
- **Reassemble:** `workloads.fractal.assemble_tiles(results, width, height, max_iter)` places each row at absolute `y = row_start + i`, colorizes the escape counts (in-set → black), and returns a `PIL.Image`; `save_png(img, path)` writes it. (PIL/numpy are host-side only, guarded.)
- **Preemptible:** chunked per row — `should_yield()` is checked before each row, returning the partial rows with `yielded: True`.

## 2. Param-sweep optimize (`optimize`) — non-AI

A distributed **search**: each machine evaluates a slice of thousands of candidate configs against
a fixed objective; the global best across the fleet wins. Deterministic — the same winner emerges
regardless of how the work was split.

- **Builder:** `workloads.optimize.build_optimize_jobs(n_tiles, n_candidates=30000, dims=8, seed=0, weights=None)` → splits candidate indices `[0, n_candidates)` into `n_tiles` slices.
- **Input (per tile):** `{ idx_start, idx_end, dims, seed }`.
- **Objective:** each index `i` deterministically maps to a config (`random.Random(seed*K + i)`), scored by **negative Rastrigin** (higher is better; global max `0.0` at all-zeros).
- **Output (per tile):** `{ best_score, best_params: [float…], best_index, evaluated, yielded }`.
- **Aggregate:** `workloads.optimize.aggregate_optimize(results)` → the global best (max `best_score`, tie-break lower `best_index`) + total `evaluated`.
- **Preemptible:** chunked per candidate.

## 3. Model inference (`ai.batch_infer`) — AI

Batch **LLM inference**: each machine scores a slice of a prompt set. Real model calls when a key
is present, a disclosed token-proportional fallback otherwise.

- **Builder:** `workloads.ai_batch.build_prompt_jobs(prompts=None, slice_size=3, model="", max_tokens=48)` → splits prompts into slices of `slice_size` (note: this kind splits by `slice_size`, not `n_tiles`).
- **Input (per tile):** `{ prompts: [str, …], model, max_tokens }`.
- **Backend:** real **Anthropic** (`ANTHROPIC_API_KEY`) or **OpenAI** (`OPENAI_API_KEY`) SDK if a key is set, else a disclosed `"fallback"`.
- **Output (per tile):** `{ results: [{ prompt, completion, tokens }, …], backend, yielded }`.

## 4. Synthetic data (`ai.synth`) — AI

Distributed **synthetic-data generation**: each machine generates a slice of records, merged into
one dataset. Real LLM-generated rows when keyed, a deterministic disclosed fallback otherwise.

- **Builder:** `workloads.synth.build_synth_jobs(n_tiles, total_rows=300, fields=None, model="", spec="a software employee record", weights=None)` → splits `total_rows` into `n_tiles` slices, each with a distinct `start_index` so seeds never collide.
- **Input (per tile):** `{ n_rows, spec, fields, model, start_index }` (default `fields = [name, role, team, summary]`).
- **Output (per tile):** `{ rows: [{ <fields> }, …], backend, yielded }`.
- **Merge:** `workloads.synth.merge_synth(results)` concatenates tile rows in `start_index` order.

---

## How to run them

- **Local simulated fleet (recording):** `uv run python scripts/demo_fleet.py` runs all four as
  separate beats across a 3-machine in-process fleet (+ the instant-yield beat) and writes
  `onecompute-fractal.png`.
- **Real fleet:** start the orchestrator on the dev box and a worker on each laptop (see
  [`demo-runbook.md`](./demo-runbook.md)), then submit per workload:
  `uv run python scripts/submit_jobs.py --url http://<host>:8080 --kind fractal|optimize|ai|synth`.
- **From a dashboard / API:** `GET /workloads/catalog` lists these four (label, category, default
  params); `POST /workloads {kind, n_tiles, params}` launches one across the fleet;
  `GET /workloads/{id}` returns per-tile status + outputs. See [`dashboard-api.md`](./dashboard-api.md).

---

## Long-running workloads (CPU fleet) — the 15-minute, full-utilisation set

A second set, added for a **CPU-only, no-API** fleet (e.g. a dev box + Snapdragon laptops). They
**saturate every core** (multi-core engine) or run on a **local model via Ollama** (no GPU, no
cloud), are sized to run for many minutes, and the worker **renews its lease while a tile runs**
so a 15-min job isn't reaped. Setup + sizing: [`fleet-setup.md`](./fleet-setup.md).

| Workload | Kind | AI? | Engine | Input (size knob) | Output (per tile) | Aggregator |
|---|---|---|---|---|---|---|
| Monte-Carlo finance risk | `montecarlo` | no | multi-core stdlib | `n_paths`, `horizon_days`, `mu`, `sigma` | `{paths, mean_return, stdev, worst_return, hist, hist_lo, hist_hi}` | `workloads.montecarlo.aggregate_montecarlo` → VaR/CVaR + `render_risk_chart` |
| Hash crack / PoW | `hashcrack` | no | multi-core stdlib | `nonce_start/_end` (`keyspace`), `target_prefix` | `{found, nonce, hash, hashes_tried}` | `workloads.hashcrack.aggregate_hashcrack` → winner + fleet hash-rate |
| Local LLM inference | `ai.infer` | yes | local model (Ollama) | `prompts` (`n_prompts`) | `{results:[{prompt,completion,tokens}], backend}` | concatenate |
| Model evaluation (judge) | `ai.eval` | yes | local model (Ollama) | `items:[{question,answer,label?}]`, `rubric` | `{results:[{question,score,verdict,label?}], backend}` | `workloads.eval.aggregate_eval` → leaderboard + distribution |
| Knowledge graph | `ai.graph` | yes | local model (Ollama) | `docs:[str]` | `{nodes:[str], edges:[{source,relation,target}], backend}` | `workloads.graph.aggregate_graph` → `render_graph_png` |

**Engine notes.**
- **Multi-core (`jobkit._parallel_map`).** A single `montecarlo`/`hashcrack` tile fans across all
  cores (fork in a Linux sandbox, spawn on Windows); `ONECOMPUTE_MAX_WORKERS` caps it. Many small
  chunks keep it yield-able and load-balanced.
- **Local model.** `ai.*` executors pick a backend in `jobkit.execute._detect_ai_backend`: local
  **Ollama** (`ONECOMPUTE_LLM_URL` / `ONECOMPUTE_LLM_MODEL`) → cloud SDK key → disclosed fallback
  (`ONECOMPUTE_NO_LLM=1` forces it). AI kinds run **host-side** so they can reach the local server.
- **Builders** live in `src/workloads/{montecarlo,hashcrack,infer,eval,graph}.py`; all are
  launchable via `GET /workloads/catalog` → `POST /workloads`.

## Adding another workload

1. Add the new string to the `JobKind` literal in `src/contracts/models.py`.
2. Add a stdlib executor to `src/jobkit/execute.py` (pure stdlib if it must run in the Docker
   sandbox; follow the `ai.batch_infer` pattern + host-side routing for an AI kind) and register
   it in `EXECUTORS`.
3. Add a `build_<kind>_jobs(...)` (and an aggregator/assembler if needed) in `src/workloads/`,
   using `workloads.partition` for the hardcoded split.
4. To make it launchable from the API/dashboard: add the kind to `LAUNCHABLE_KINDS` (contracts),
   a branch in `_build_workload_jobs` and an entry in `WORKLOAD_CATALOG` (`orchestrator/app.py`).
5. Add tests mirroring `tests/jobkit/` and `tests/workloads/`.
