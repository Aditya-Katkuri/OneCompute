# OneCompute: fleet demo runbook

The demo shows ONE fleet (2 laptops + 1 dev box, side by side) running **four distinct
compute workloads, one after another**, each fanned across **every** machine. Joining a
machine is gated by a **dashboard device-code approval**, and the work is split with a
**hardcoded N-tile split** (work divided into one tile per machine, N = number of machines).

The four variety beats:

| # | Workload | Job kind | What the fleet does |
|---|----------|----------|---------------------|
| 1 | **Mandelbrot fractal** (non-AI) | `fractal` | Each machine renders a horizontal band; tiles reassemble into ONE image. |
| 2 | **Param-sweep optimization** (non-AI) | `optimize` | Each machine scores a slice of thousands of candidate configs; the global best wins. |
| 3 | **Model inference** (AI) | `ai.batch_infer` | Each machine runs a prompt batch through the LLM (real SDK if keyed, else a disclosed fallback). |
| 4 | **Synthetic data generation** (AI) | `ai.synth` | Each machine generates N synthetic records; merged into one dataset. |

Plus an **instant-yield** moment: touch a laptop mid-job and its slice yields in
milliseconds and is requeued to the rest of the fleet. The dev-box GPU earns **5×** credit.

There are two ways to run it:

- **Section A**: the real multi-machine fleet over a LAN (the live demo).
- **Section B**: a single-box simulated 3-machine fleet (records without a LAN).

---

## A) Real fleet: 2 laptops + 1 dev box, side by side

All three machines need the repo checked out and `uv` available. Commands use the project
`uv` (`C:\Users\<you>\.local\bin\uv.exe`). **Run `uv sync` once per machine first**. It
installs OneCompute into the project venv, so `uv run python -m orchestrator` and
`uv run python -m worker` work from any checkout (no `PYTHONPATH` needed).

### A1. Dev box: start the orchestrator (with the credential gate ON)

```powershell
uv sync                 # one-time: installs OneCompute + deps into .venv
uv run python -m orchestrator --require-approval
```

This binds `0.0.0.0:8080` and prints, for each LAN IP, the **dashboard URL** and the exact
**worker command**, e.g.:

```
  Dashboard:  http://10.0.8.72:8080/
  Worker:     uv run python -m worker --url http://10.0.8.72:8080
  ...
  Credential gate: ON. Workers join PENDING and need dashboard approval (device code).
```

Note your dev-box LAN IP (call it `<dev-box-ip>`). Optional flags: `--port <n>`,
`--db <path>` (state persists across restarts; default `./reeve-orchestrator.db`).

> **AI backend (beats 3 & 4):** the worker resolves an LLM backend in precedence order: a
> **local Ollama** model first (`ONECOMPUTE_LLM_URL`, default `http://127.0.0.1:11434/v1`), then
> `OPENAI_API_KEY`, then `ANTHROPIC_API_KEY`. The local model is the default the demo relies on
> (the fleet is CPU-only, no cloud); for cloud inference instead, set a key **in the worker
> environment** before launching the worker (AI jobs run host-side on the worker, so that is
> where the backend/key must live). With none reachable the AI beats use a disclosed deterministic
> fallback, and the demo still completes. Set `ONECOMPUTE_NO_LLM` to disable the LLM entirely.

### A2. Open the dashboard

On the dev box (projector), open the printed `http://<dev-box-ip>:8080/`. You'll watch
worker tiles, per-machine busy/idle, the device-code **PENDING** tiles with an **Approve**
button, credits, and the workloads-run tally.

### A3. Each laptop (and the dev box itself): join a worker

On **laptop-ana**, **laptop-ben**, and the **dev box**:

```powershell
uv run python -m worker --url http://<dev-box-ip>:8080
```

First confirm reachability from a laptop: `curl http://<dev-box-ip>:8080/state` returns JSON.

Each worker registers, then prints and **waits**:

```
Fleet access code: WX7Q-12, waiting for approval in the dashboard…
```

### A4. Admin approves each machine (the credential step)

In the dashboard, each waiting machine shows up as a **PENDING** tile with its short device
code. Click **Approve** on that machine's tile. The worker flips to:

```
[+] Access granted, <worker-id> joined the fleet
```

Only then can that worker pull work. (This is the access-control story: a joining PC proves
itself with a short code an admin admits in the dashboard.)

### A5. Run the four workloads: one command per beat

From any machine that can reach the orchestrator (e.g. the dev box), submit each workload in
turn. Each command fans the workload across all machines via the hardcoded split (`--n 3` =
one tile per machine. Set `--n` to your machine count).

```powershell
# BEAT 1: distributed Mandelbrot fractal (non-AI)
uv run python scripts/submit_jobs.py --url http://<dev-box-ip>:8080 --kind fractal  --n 3

# BEAT 2: distributed param-sweep optimization (non-AI)
uv run python scripts/submit_jobs.py --url http://<dev-box-ip>:8080 --kind optimize --n 3

# BEAT 3: model inference over a prompt set (AI)
uv run python scripts/submit_jobs.py --url http://<dev-box-ip>:8080 --kind ai

# BEAT 4: synthetic data generation (AI)
uv run python scripts/submit_jobs.py --url http://<dev-box-ip>:8080 --kind synth    --n 3
```

For each beat, watch the **tiles light up busy per machine**, completed jobs rise, and credits
tick (the dev-box GPU climbs ~5× faster). Submit the next beat once the previous one drains.

Useful knobs (defaults are projector-friendly):
`--kind fractal --width 1600 --height 1000 --max-iter 300` (bigger/higher-res image),
`--kind optimize --candidates 400000 --dims 8` (fatter bar; the global best is deterministic
regardless of how it's split), `--kind synth --rows 300` (more rows).

### A6. Instant yield: the employee stays in control

While a beat is running on a laptop, **touch that laptop's mouse/keyboard**. Its current tile
goes amber (**yielded**) within milliseconds and the slice is **requeued** to the rest of the
fleet, which finishes it. The machine is never fought for. (A heavier slice makes this easy to
catch, e.g. submit `--kind optimize --candidates 400000` and touch a laptop mid-run.)

### A7. The money shot: the reassembled image

After BEAT 1 drains, the host-side assembler stitches the bands into one image. To produce the
PNG explicitly (e.g. on the dev box for the projector), run the simulated driver once (it
writes `./onecompute-fractal.png`) or open the image the recording driver in Section B
produces. **Open `onecompute-fractal.png`**: a single Mandelbrot rendered across all machines.

### A8. Close

Show the dashboard ledger: per-machine credits (dev-box GPU 5×), total credits, jobs completed,
and the **workloads run: fractal, optimize, ai.batch_infer, ai.synth** tally. The pitch: one
fleet, four very different jobs, each spread across every idle machine, credited only for
verified work, and yielding the instant a human comes back.

---

## B) Local simulated fleet: record without a LAN

For a clean screen recording on a single machine (no second/third PC, no network setup), the
self-contained driver stands up a **real** signed orchestrator (credential gate ON), three
**real HTTP workers** with human names (`dev-box` GPU + `laptop-ana`/`laptop-ben` CPU), runs the
device-code approval, then drives all four beats + the instant-yield beat + the ledger:

```powershell
uv run python scripts/demo_fleet.py            # run the beats, then hold the dashboard up (Ctrl-C to stop)
uv run python scripts/demo_fleet.py --no-hold  # run the beats and exit (used for acceptance/CI)
```

It prints the dashboard URL (open it to watch the tiles), shows each worker's PENDING device
code then auto-approves it (narrating "in the real demo the admin clicks Approve"), and for
each beat prints per-machine progress + the aggregate. BEAT 1 writes **`./onecompute-fractal.png`**
. Open it to show the image rendered across the (simulated) fleet. AI beats use the disclosed
fallback unless `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` is set in the environment.

The local flow mirrors the real flow exactly (same orchestrator, same contracts, same approval
gate, same workload builders/aggregators, same hardcoded N-tile split). Only the worker
machines are simulated on one box.

---

## Notes

- **Hardcoded split:** total work is carved into one tile per machine (`--n` / `n_tiles`). The
  dynamic governor/profiler is intentionally set aside for this demo.
- **Credential = dashboard approval (device code):** a joining worker shows a short code and is
  PENDING until an admin clicks Approve in the dashboard (`POST /workers/{id}/approve`).
- **AI runs host-side:** `ai.batch_infer` and `ai.synth` execute on the worker host (real SDK +
  API-key env), never inside the stdlib sandbox container. Non-AI kinds (`fractal`, `optimize`)
  run in the sandbox.
- **Credit:** metered on server-assigned class weight: the GPU dev box earns 5×, CPU laptops 1×.
```
