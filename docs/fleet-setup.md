# OneCompute — CPU fleet setup (local model + long-running workloads)

How to stand up the demo fleet (a dev box + laptops, **CPU-only, no cloud APIs**) and run the
long-running, high-utilisation workloads. The AI workloads use a **local model via Ollama**; the
non-AI ones are pure-Python and fan across every core.

## 0. The fleet

| Machine | Arch | Cores | RAM | Model |
|---|---|---|---|---|
| Dev box | x86-64 (AMD EPYC) | 16 | 64 GB | `llama3.1:8b` (stronger judge) |
| Laptops (Surface Laptop 7) | ARM64 (Snapdragon X) | 12 | 32 GB | `llama3.2:3b` |

No GPU and no API keys are required. Everything below is CPU-only.

## 1. Install the local model (every machine)

1. **Install Ollama** — `winget install Ollama.Ollama` (Windows x86 and ARM64 are both supported), or the installer from ollama.com. It runs a server at `http://127.0.0.1:11434` exposing an OpenAI-compatible API.
2. **Pull a model:**
   - Laptops (32 GB): `ollama pull llama3.2:3b`  (~2 GB, comfortable on CPU)
   - Dev box (64 GB): `ollama pull llama3.1:8b`  (~5 GB; stronger, used as the eval judge)
3. **Verify:** `ollama run llama3.2:3b "hi"` should reply.

> If a machine has no model, the AI workloads still run — they fall back to a **disclosed
> deterministic stub** (set `ONECOMPUTE_NO_LLM=1` to force it). So the fleet never blocks.

## 2. Worker environment (per machine)

Set before launching the worker (all optional — sensible defaults shown):

| Env var | Default | Purpose |
|---|---|---|
| `ONECOMPUTE_LLM_MODEL` | `llama3.2:3b` | which local model the AI workloads call (set `llama3.1:8b` on the dev box) |
| `ONECOMPUTE_LLM_URL` | `http://127.0.0.1:11434/v1` | local model endpoint |
| `ONECOMPUTE_MAX_WORKERS` | all cores | cap the cores a tile uses (leave the employee some headroom) |
| `ONECOMPUTE_NO_LLM` | unset | set to `1` to force the no-model fallback |

```powershell
$env:ONECOMPUTE_LLM_MODEL = "llama3.2:3b"   # "llama3.1:8b" on the dev box
```

## 3. Run the fleet

```powershell
# Dev box (orchestrator + dashboard):
uv run python -m orchestrator --require-approval     # prints the dashboard URL + worker command

# Each laptop (worker):
uv run python -m worker --url http://<dev-box-ip>:8080
#   -> prints a Fleet access code; click Approve on its tile in the dashboard
```

The worker streams live CPU usage every ~1 s and **renews its lease while a long tile runs**, so
15-minute jobs aren't reaped mid-run.

## 4. Launch the workloads

From the dashboard's launcher (buttons are populated from `GET /workloads/catalog`), or:

```powershell
uv run python scripts/submit_jobs.py --url http://<dev-box-ip>:8080 --workload montecarlo
```

Each launch fans **one tile per approved machine** and pins every core on each.

| Workload | Type | What the fleet does |
|---|---|---|
| `montecarlo` | non-AI | millions of market paths → portfolio Value-at-Risk + a risk chart |
| `hashcrack` | non-AI | brute-force a SHA-256 with a target prefix → fleet hash-rate + winner |
| `ai.infer` | AI (local model) | a big prompt batch run through the on-device model |
| `ai.eval` | AI (local model) | grade answers with an LLM judge → leaderboard + score chart |
| `ai.synth` | AI (local model) | generate synthetic records → merged dataset |
| `ai.graph` | AI (local model) | extract entities/relations from a corpus → one rendered knowledge graph |

## 5. Sizing for ~15 minutes

Runtime scales linearly with the workload's size knob, so **benchmark small, then scale**:

1. Launch once with the catalog default and time a tile (the dashboard shows per-tile completion).
2. Multiply the size param by `target_minutes / observed_minutes`.

Size knobs (pass via the dashboard launcher's params or `POST /workloads {params}`):

| Workload | Knob (param) | Notes |
|---|---|---|
| `montecarlo` | `total_paths` (× `horizon_days`) | compute ∝ `total_paths × horizon_days` |
| `hashcrack` | `keyspace`; `target_prefix` | runtime ∝ `keyspace`; each extra hex digit in the target is ~16× rarer to find |
| `ai.infer` | `n_prompts` | ≈ `n_prompts` local inferences across the fleet |
| `ai.eval` | `items` count | one judging call per item |
| `ai.synth` | `total_rows` | one generation call per row |
| `ai.graph` | `docs` count | one extraction call per document |

The fleet is heterogeneous, so the **slowest laptop is the long pole** with the default even
split. To keep the fast dev box busy too, give it a larger tile via the builders' `weights=`
argument (e.g. `weights=[3,1,1,1,1]`) — exposed in the demo driver; the dashboard launcher uses
an even split today.

> See [`workloads.md`](./workloads.md) for each workload's input/output shape and
> [`dashboard-api.md`](./dashboard-api.md) for the launch/results API.
