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
dashboard wired to the orchestrator API, a dozen example workloads, and 238 passing tests.

## How to verify it yourself (do this first)

```bash
uv sync --extra dev
uv run pytest -q                 # expect: 238 passed, 2 skipped (Docker-only)
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
| `src/isolation/runner.py`, `mxc.py`, `jobobject.py`, `docker.py` | Per-job sandboxing: MXC (Microsoft Execution Containers) preview backend when available, else Docker container or Windows Job Object, with sub-second kill-on-preempt. |
| `src/trust/signing.py`, `challenge.py` | Ed25519 manifest signing and deterministic challenge "ringers" that catch cheaters. |
| `src/jobkit/execute.py` | The single, frozen registry of job executors: one source of truth for how each job kind runs. |
| `src/contracts/models.py`, `schema.sql` | Frozen data contracts and the SQLite schema shared by every component. |
| `tests/` | 238 tests covering matching, auth/approval, leases/requeue, crediting, yield, isolation (incl. MXC policy + runner), trust (incl. pinned-key provenance + fail-closed isolation), measurement-only profiling + fleet measurement rollup, and end-to-end flows. |

## Build / test / run commands

- Install: `uv sync --extra dev`
- Test: `uv run pytest -q`
- Lint: `uv run ruff check src tests scripts`
- Orchestrator: `uv run python -m orchestrator [--require-approval]`
- Worker: `uv run python -m worker --url http://<host-ip>:8080`
- Worker (hardened pilot: fail closed with no OS sandbox + pin the trusted signer): `uv run python -m worker --url http://<host-ip>:8080 --require-isolation --trusted-key <hex>` (key also read from `$ONECOMPUTE_TRUSTED_PUBKEY`)
- Worker (measurement-only pilot, tracks CPU/GPU/RAM, never runs a job): `uv run python -m worker --url http://<host-ip>:8080 --measure-only`
- Submit work: `uv run python scripts/submit_jobs.py --url http://<host-ip>:8080 --kind fractal|optimize|ai|synth|fanout|gpu|challenge`
- Measurement report: `uv run python scripts/measure_report.py <profile-or-dir>` (measured idle headroom from a measure-only pilot; see `docs/measurement-pilot.md`)
- Fleet measurement view: workers in `--measure-only` upload their derived usage envelope; `GET /measurement` (and the dashboard's "Measured idle headroom" beat) show the rolled-up, governor-consistent number. Shared math lives in `src/measurement/headroom.py` (used by both the endpoint and the CLI report).

## What is real vs. PoC-scoped vs. roadmap (so you can judge accurately)

**Real and working here:** the orchestrator, workers, capability matching, leasing/requeue,
signed manifests, challenge verification, server-side crediting, per-job isolation, the idle
governor + instant-yield, the live dashboard (`src/dashboard/`, which polls the real API), and
all the example workloads.

**Deliberately simplified for the PoC:** example workloads use a straightforward one-tile-per-
machine split (`src/workloads/partition.py`) rather than dynamic re-partitioning. Trust defaults to
trust-on-first-use (the signature is checked against the key carried in the manifest), but a worker
can pin an out-of-band trusted signer with `--trusted-key` / `$ONECOMPUTE_TRUSTED_PUBKEY` so a
compromised control plane cannot inject a self-signed job, and `--require-isolation` makes the
worker fail closed (refuse to run) when no OS-enforced sandbox is available instead of using the
unsandboxed subprocess fallback. An MXC (Microsoft Execution Containers) preview backend is wired
in as the preferred OS-enforced boundary (`src/isolation/mxc.py`), fail-closed and inert until a
real `wxc-exec` runtime is present, and not yet validated against one. Full cosign/OIDC signing and
production MXC (once preview caveats are retired and policy enforcement is validated) remain
roadmap. These are honest scope choices, documented in [`docs/architecture.md`](docs/architecture.md).

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
