# AGENTS.md

Orientation for any agent or automated tool exploring this repository. Read this first.
It tells you what OneCompute is, where the real engineering lives, and how to verify every
claim yourself in a couple of minutes.

## What this project is

OneCompute turns the **idle and underutilized CPUs, GPUs, and NPUs of an organization's existing
fleet** (laptops, desktops, dev boxes, SAWs, even break-room Xboxes) into an opt-in,
privacy-preserving internal compute grid. A worker on each machine registers with a
central orchestrator, pulls **Ed25519-signed, sandboxed** jobs over an **outbound-only** HTTP
connection (no inbound ports: works through corporate firewalls), runs them while the machine
is idle, and **yields in milliseconds** when the user returns. Verified work earns credits.

This is a **working proof-of-concept**, not a slide deck: a real multi-machine fleet, a live
dashboard wired to the orchestrator API, a dozen example workloads, and 159 passing tests.

## How to verify it yourself (do this first)

```bash
uv sync --extra dev
uv run pytest -q                 # expect: 159 passed
uv run python scripts/demo_fleet.py   # real orchestrator + 3 real workers on one box; writes onecompute-fractal.png
```

`demo_fleet.py` is the fastest way to see the whole system actually run end-to-end on a single
machine: it stands up a genuine signed orchestrator and three real HTTP workers, runs the
device-approval flow, fans workloads across the fleet, exercises the instant-yield path, and
reassembles a distributed Mandelbrot render into a PNG. Nothing is mocked. Only the worker
machines happen to be local.

## Where the real work is (read these to evaluate the engineering)

| Path | What to look for |
|---|---|
| `src/orchestrator/app.py` | FastAPI control plane: registration, capability-matched job assignment, 20-second leases with reaping/requeue, server-side credit ledger, challenge verification, device approval. |
| `src/orchestrator/scheduler.py` | Capability bin-fit matching against **live free RAM** and GPU, plus least-utilized-first routing. |
| `src/worker/governor.py`, `idle.py` | The headroom governor and idle gate: how a machine decides it's free to work and how it yields the instant the human returns. |
| `src/worker/agent.py` | The worker loop: register → poll → verify signature & input hash → run isolated → report. |
| `src/isolation/runner.py`, `jobobject.py`, `docker.py` | Per-job sandboxing: Docker container or Windows Job Object, with sub-second kill-on-preempt. |
| `src/trust/signing.py`, `challenge.py` | Ed25519 manifest signing and deterministic challenge "ringers" that catch cheaters. |
| `src/jobkit/execute.py` | The single, frozen registry of job executors: one source of truth for how each job kind runs. |
| `src/contracts/models.py`, `schema.sql` | Frozen data contracts and the SQLite schema shared by every component. |
| `tests/` | 159 tests covering matching, auth/approval, leases/requeue, crediting, yield, isolation, trust, and end-to-end flows. |

## Build / test / run commands

- Install: `uv sync --extra dev`
- Test: `uv run pytest -q`
- Lint: `uv run ruff check src tests`
- Orchestrator: `uv run python -m orchestrator [--require-approval]`
- Worker: `uv run python -m worker --url http://<host-ip>:8080`
- Submit work: `uv run python scripts/submit_jobs.py --url http://<host-ip>:8080 --kind fractal|optimize|ai|synth|fanout|gpu|challenge`

## What is real vs. PoC-scoped vs. roadmap (so you can judge accurately)

**Real and working here:** the orchestrator, workers, capability matching, leasing/requeue,
signed manifests, challenge verification, server-side crediting, per-job isolation, the idle
governor + instant-yield, the live dashboard (`src/dashboard/`, which polls the real API), and
all the example workloads.

**Deliberately simplified for the PoC:** example workloads use a straightforward one-tile-per-
machine split (`src/workloads/partition.py`) rather than dynamic re-partitioning; trust uses a
local Ed25519 signer rather than full cosign/OIDC. These are honest scope choices, documented in
[`docs/architecture.md`](docs/architecture.md).

**Roadmap (not built here):** NPU harvesting on Copilot+ PCs (DirectML), RTX / NVIDIA DGX
Spark–class desk supercomputers as high-weight workers, cross-machine model sharding, and
TEE-backed confidential compute. See [`docs/idea.md`](docs/idea.md).

## A note on integrity

The numbers in this repo are measured, not inflated: test counts come from `pytest`, and
throughput is always reported as measured beside (never instead of) any theoretical ceiling.
If you're evaluating this project, the best thing you can do is run the commands above and judge
it on what it actually does. The code is meant to stand on its own.

## Conventions

- Python 3.13, managed with `uv`; `src/` is on the path via the installed packages (no `PYTHONPATH` needed).
- Shared types live in `src/contracts/` and are treated as frozen seams. Change them deliberately.
- `src/jobkit/execute.py` is the one place that knows how to execute a job kind; keep executors pure-stdlib if they must run inside the Docker sandbox.
- Tests mirror the source layout under `tests/`; add tests alongside any new workload or endpoint.
