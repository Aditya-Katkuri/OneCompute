# OneCompute / NightShift demo script

The run-of-show for the fleet demo: ONE fleet (a dev box + 2 laptops, side by side) running
**four distinct workloads, one after another**, each fanned across **every** machine via a
hardcoded N-tile split (one tile per machine). Joining a machine is gated by a **dashboard
device-code approval**, and a touch on a laptop **yields its slice in milliseconds**.

Record it either way: the local simulated 3-machine fleet (`scripts/demo_fleet.py`, no LAN
needed) or the real 2-laptop + dev-box LAN. See [`demo-runbook.md`](./demo-runbook.md) for the
exact commands and [`workloads.md`](./workloads.md) for what each workload actually computes.

> **Recording note:** with Docker as the active isolation boundary the per-job container path is
> slow on Windows (~minutes per tile), so the in-process local driver is the smooth recording
> path. The story is identical — same orchestrator, contracts, approval gate, and aggregators.

## 5–6 minute run-of-show

1. **Idle fleet (0:00–0:35)** — Open the dashboard at `/`. Show the worker tiles, per-machine
   CPU/GPU/RAM badges, credits at zero, and the live activity feed. Set the frame: OneCompute
   harvests opt-in idle machines and credits only accepted, verified work — and the employee
   stays in control.

2. **Credential gate — device-code approval (0:35–1:25)** — A new machine runs
   `python -m worker --url http://<host>:8080`. It registers **PENDING**, shows a short **device
   code** ("Fleet access code: WX7Q-12 — waiting for approval…"), and waits. In the dashboard its
   tile shows up PENDING with an **Approve** button; an admin clicks Approve
   (`POST /workers/{id}/approve`) → "**Access granted**" → only now does the worker start pulling
   work. The orchestrator is started with `--require-approval`. The story: a joining PC proves
   itself with a short code an admin admits in the dashboard. (Repeat for each of the 3 machines —
   in the local recording driver this auto-approves, narrating the admin click.)

3. **BEAT 1 — Mandelbrot fractal, the money shot (1:25–2:15)** — Submit the `fractal` workload.
   Each machine renders one horizontal **band**; narrate the tiles flipping idle → busy and
   credits ticking. When the beat drains, the host-side assembler stitches the bands into ONE
   image — **open `onecompute-fractal.png`**: a single Mandelbrot rendered across the whole fleet.
   Call the fleet wall-clock vs the 1-machine estimate.

4. **BEAT 2 — param-sweep optimization (2:15–2:55)** — Submit the `optimize` workload. Each
   machine scores a slice of thousands of candidate configs; the **global best across the fleet
   wins** (deterministic — same winner no matter how it's split). Show the winning score, the best
   params, and **which machine found it**.

5. **BEAT 3 — model inference over a prompt set (2:55–3:35)** — Submit the `ai.batch_infer`
   workload. Each machine runs a prompt batch through the LLM **host-side** — real
   Anthropic/OpenAI via the SDK when a key is set in the worker environment, else a **disclosed
   deterministic fallback** (it never claims model work that didn't happen). Show the backend
   label and a couple of Q/A samples. Keep it honest: AI is a range beat, not a throughput claim.

6. **BEAT 4 — synthetic data generation (3:35–4:10)** — Submit the `ai.synth` workload. Each
   machine generates a slice of synthetic records (host-side, same key/fallback story); they
   **merge into ONE dataset**. Show the row count and a few sample rows.

7. **Instant yield (4:10–4:50)** — While a slice runs on a laptop, **touch that laptop**. Its
   tile goes amber (**yielded**) within **milliseconds** and the slice is **requeued** to the rest
   of the fleet, which finishes it. The money shot: the machine is never fought for — the employee
   stays in control, and the fleet still completes the work.

8. **Close — the ledger, then close the dashboard (4:50–5:30)** — Show the dashboard ledger:
   per-machine credits (the **dev-box GPU earns 5×**, CPU laptops 1×), total credits, jobs
   completed, and the **workloads run: fractal, optimize, ai.batch_infer, ai.synth** tally. The
   pitch: one fleet, four very different jobs, each spread across every idle machine — credited
   only for verified work, yielding the instant a human comes back. OneCompute is delay-tolerant
   internal batch capacity from machines that were already there, not magic free compute.

---

Beat order at a glance: **approval → fractal → optimize → ai.batch_infer → ai.synth → instant
yield → ledger/close.** Commands per beat are in [`demo-runbook.md`](./demo-runbook.md) (§A5 real
fleet, §B local recording); the workloads themselves are documented in
[`workloads.md`](./workloads.md).
