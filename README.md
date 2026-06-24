# NightShift - Let your compute work when you are not.

NightShift turns the idle CPUs and GPUs of a company's existing PC fleet into an
opt-in, privacy-preserving internal compute grid. Each employee machine runs a
lightweight worker that registers with a central orchestrator, then pulls
**signed + sandboxed** jobs over an **outbound-only HTTP** connection (no inbound
ports — it works through corporate NAT/firewalls), runs them in isolation,
and returns results. Contributing machines earn credits for the verified work
they do. This repo is a **hackathon proof-of-concept** — the full vision (an
internal, incentivized, governed "BOINC/Folding@home for the AI-PC era") is in
[`docs/idea.md`](docs/idea.md).

## What it does today

- **A real multi-machine fleet** — a dev box plus laptops cooperating over the LAN,
  coordinated by one FastAPI orchestrator.
- **Device-code dashboard approval** — when run with `--require-approval`, a joining
  machine shows a short code and stays PENDING until an admin clicks **Approve** in
  the dashboard; only then can it pull work.
- **Four example workloads** fanned across the fleet via a hardcoded split (one tile
  per machine) — see [`docs/workloads.md`](docs/workloads.md):
  - `fractal` — distributed Mandelbrot; each machine renders a band, reassembled into one image.
  - `optimize` — distributed param-sweep; each machine scores a slice of candidates, global best wins.
  - `ai.batch_infer` — batch model inference; each machine scores a slice of a prompt set.
  - `ai.synth` — synthetic-data generation; each machine produces rows, merged into one dataset.
- **Per-job isolation** — non-AI jobs run in a sandbox (Docker container / Windows Job Object);
  AI jobs route host-side where the SDK + API key live.
- **Instant-yield** — touch a worker's mouse/keyboard mid-job and its slice yields in
  milliseconds and is requeued to the rest of the fleet.
- **A live dashboard** — fleet tiles, per-device usage (CPU/GPU/RAM), credits, and an
  activity feed, backed by a documented HTTP API ([`docs/dashboard-api.md`](docs/dashboard-api.md)).

## Quickstart — real fleet (over a LAN)

All machines need the repo checked out and `uv` available. `uv run` puts `src/` on the
path. On the dev box, start the orchestrator with the approval gate on:

```bash
uv run python -m orchestrator --require-approval
```

It binds `0.0.0.0:8080` and prints, per LAN IP, the **dashboard URL** and the exact
**worker command**. Open the dashboard at `http://<dev-box-ip>:8080/`.

On each laptop (and the dev box itself), join a worker:

```bash
uv run python -m worker --url http://<dev-box-ip>:8080
```

Each worker prints a short device code and waits; click **Approve** on its PENDING tile
in the dashboard. Then submit work from any machine that can reach the orchestrator:

```bash
uv run python scripts/submit_jobs.py --url http://<dev-box-ip>:8080 --kind fractal|optimize|ai|synth
```

> For real AI inference/synthesis, set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` **in the
> worker environment** (AI jobs run host-side on the worker). Without a key the AI beats
> use a disclosed deterministic fallback and still complete. Full walkthrough:
> [`docs/demo-runbook.md`](docs/demo-runbook.md).

## Quickstart — local demo (no LAN, for recording)

Stand up a real signed orchestrator and three real HTTP workers on a single box, run the
device-code approval, then drive all four workloads plus the instant-yield beat:

```bash
uv run python scripts/demo_fleet.py
```

It prints the dashboard URL and writes `onecompute-fractal.png` (the Mandelbrot rendered
across the simulated fleet). The local flow mirrors the real one exactly — only the worker
machines are simulated.

## Tests

```bash
uv run pytest -q
```

The repo uses `uv` (`pythonpath = src`); **144 tests pass**.

## Docs

- [`docs/idea.md`](docs/idea.md) — the vision, market case, and roadmap.
- [`docs/architecture.md`](docs/architecture.md) — system design (orchestrator, worker, sandbox, governor).
- [`docs/workloads.md`](docs/workloads.md) — the four example workloads in detail.
- [`docs/dashboard-api.md`](docs/dashboard-api.md) — the orchestrator HTTP API for the dashboard.
- [`docs/demo-runbook.md`](docs/demo-runbook.md) — step-by-step real-fleet and local-demo runbook.
- [`docs/contracts.md`](docs/contracts.md) — the job/manifest contracts and data model.

## Status

This is a proof-of-concept. The dashboard front-end is in progress — the backend API is
complete, and a bundled `src/dashboard/index.html` implements the live poll loop, fleet
tiles, approval flow, and events feed. Larger pieces of the full vision (NPU harvesting,
the demand-adaptive headroom governor, TEE/confidential compute, cross-machine model
sharding, cosign/OIDC signing) are roadmap, not built here — see [`docs/idea.md`](docs/idea.md).
