"""OneCompute / NightShift: Stage Demo driver (FOUR variety beats across a 3-machine fleet).

A self-contained, recordable demo. It stands up a REAL signed orchestrator (with the
dashboard-approval credential gate ON), registers THREE real HTTP workers with human
machine names ("dev-box" GPU + two CPU laptops), shows their pending device codes and
auto-approves them (mirroring the admin clicking Approve in the dashboard), then fans
FOUR distinct workloads (ONE AFTER ANOTHER) across the whole fleet using the hardcoded
N-tile split (N = number of machines):

  BEAT 1  FRACTAL          distributed Mandelbrot; tiles reassemble into ONE PNG (money shot)
  BEAT 2  PARAM-SWEEP      each machine scores a slice of candidates; the global best wins
  BEAT 3  MODEL INFERENCE  ai.batch_infer over a prompt set (real SDK if keyed, else fallback)
  BEAT 4  SYNTHETIC DATA   ai.synth rows generated per machine, merged into one dataset

Plus an INSTANT-YIELD beat (touch a laptop -> the slice yields fast and the fleet finishes it)
and a closing ledger (the dev-box GPU earns 5x). This same flow runs for real across physical
machines via `python -m orchestrator` + `python -m worker` + `scripts/submit_jobs.py`
(see docs/demo-runbook.md); here it is simulated locally so it records without a LAN.

Run:
    uv run python scripts/demo_fleet.py            # run the beats, then hold the dashboard up
    uv run python scripts/demo_fleet.py --no-hold  # run the beats and exit (acceptance/CI)
"""

from __future__ import annotations

import argparse
import random
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contracts import Capability, HeartbeatRequest, SubmitRequest  # noqa: E402
from isolation import active_boundary  # noqa: E402
from orchestrator.app import create_app  # noqa: E402
from worker.agent import WorkerAgent  # noqa: E402
from workloads.ai_batch import build_prompt_jobs  # noqa: E402
from workloads.fractal import assemble_tiles, build_fractal_jobs, save_png  # noqa: E402
from workloads.optimize import aggregate_optimize, build_optimize_jobs  # noqa: E402
from workloads.submit import submit_all  # noqa: E402
from workloads.synth import build_synth_jobs, merge_synth  # noqa: E402

# Human machine names for the side-by-side recording. dev-box is the only GPU box, so it
# earns 5x credit; the two laptops are CPU-only and run slower on the heavy beats.
FRACTAL_PNG = str(Path(__file__).resolve().parents[1] / "onecompute-fractal.png")

# Demo sizes (measured on the dev box; laptops run slower -> bigger visible speedup).
FRACTAL = {"width": 1200, "height": 800, "max_iter": 256}
OPTIMIZE = {"n_candidates": 200_000, "dims": 8}
SYNTH_ROWS = 60
N_TILES = 3  # hardcoded split: one tile per machine


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(base: str, port: int):
    # require_approval=True -> the fleet gates joining workers behind a dashboard device code.
    config = uvicorn.Config(
        create_app(":memory:", require_approval=True), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        try:
            if httpx.get(f"{base}/state", timeout=0.5).status_code == 200:
                return server, thread
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError("orchestrator did not start")


def _drain(fleet: list[WorkerAgent], collect: dict | None = None, max_rounds: int = 400) -> None:
    """Round-robin the fleet until quiescent, printing per-machine progress.

    When ``collect`` is given, each completed job's output is stashed under
    ``collect[job_id]`` so the host-side aggregators (assemble_tiles / aggregate_optimize /
    merge_synth) can reduce the tiles after the beat. ``collect`` also records which machine
    produced each tile (``collect["_by"][job_id] = worker_id``) for the "found by" line.
    """
    if collect is not None:
        collect.setdefault("_by", {})
    idle = 0
    while idle < len(fleet) and max_rounds > 0:
        max_rounds -= 1
        progressed = False
        for w in fleet:
            rr = w.run_once()
            if rr is not None:
                units = f"{rr.units} units"
                print(f"   {rr.worker_id:11} {rr.status:9} {rr.job_id[:8]} ({units})")
                if collect is not None and rr.status == "completed" and rr.output is not None:
                    collect[rr.job_id] = rr.output
                    collect["_by"][rr.job_id] = rr.worker_id
                progressed = True
        idle = 0 if progressed else idle + 1


def _outputs(collect: dict) -> list[dict]:
    return [v for k, v in collect.items() if k != "_by"]


def _state(base: str) -> dict:
    return httpx.get(f"{base}/state", timeout=5).json()


def _start_heartbeat_pulse(base, workers, stop_event):
    """Keep the dashboard lively: mirror each worker's real busy/idle state (from /state) into
    plausible cpu/gpu heartbeats. Decoupled from job execution and never sends current_job_id or
    free_ram, so it cannot perturb leasing, scheduling, or RAM gating."""
    gpu_ids = {w.capability.worker_id for w in workers if w.capability.has_gpu}
    ids = [w.capability.worker_id for w in workers]

    def pulse():
        while not stop_event.is_set():
            try:
                rows = httpx.get(f"{base}/state", timeout=2).json().get("workers", [])
                busy = {w["worker_id"]: w["busy"] for w in rows}
                for wid in ids:
                    is_busy = busy.get(wid, False)
                    cpu = random.uniform(55, 92) if is_busy else random.uniform(1, 8)
                    gpu = None
                    if wid in gpu_ids:
                        gpu = random.uniform(72, 96) if is_busy else random.uniform(0, 5)
                    httpx.post(
                        f"{base}/heartbeat",
                        json=HeartbeatRequest(
                            worker_id=wid,
                            idle=not is_busy,
                            cpu_pct=round(cpu, 1),
                            gpu_pct=round(gpu, 1) if gpu is not None else None,
                        ).model_dump(),
                        timeout=2,
                    )
            except Exception:
                pass
            stop_event.wait(0.45)

    thread = threading.Thread(target=pulse, name="onecompute-heartbeat-pulse", daemon=True)
    thread.start()
    return thread


def _approve_fleet(base: str, fleet: list[WorkerAgent]) -> None:
    """Register each worker (-> PENDING + a device code), show the codes, then admit them.

    In the real demo the admin clicks Approve on each machine's tile in the dashboard; here we
    POST /workers/{id}/approve to keep the recording hands-free. The worker then heartbeats and
    sees approved=true, exactly as it would after a human approval.
    """
    print("Workers joining: each shows a device code and waits for dashboard approval:")
    for w in fleet:
        w.register()
        tag = "GPU" if w.capability.has_gpu else "CPU"
        print(f"   {w.capability.worker_id:11} [{tag}]  PENDING   code: {w.device_code}")
    print("\n   In the real demo the admin clicks Approve in the dashboard. Auto-approving for the recording…")
    for w in fleet:
        httpx.post(f"{base}/workers/{w.capability.worker_id}/approve", timeout=5).raise_for_status()
        # Pull the flipped approval state through a heartbeat, like the real worker loop does.
        if w.heartbeat().approved:
            w.approved = True
            w.device_code = None
        print(f"   [+] Access granted, {w.capability.worker_id} joined the fleet")
    print()


def _hr(title: str) -> None:
    print(f"\n----------  {title}  ----------")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-hold", action="store_true", help="exit after the beats instead of holding")
    args = parser.parse_args()

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    server, thread = _start_server(base, port)
    print("\n==================  OneCompute / NightShift: fleet demo  ==================")
    print(f"  Dashboard:  {base}/")
    print(f"  Signing ON (Ed25519) | isolation: {active_boundary()} | credential gate: dashboard approval")
    print("  Fleet: dev-box (GPU, 5x credit), laptop-ana (CPU), laptop-ben (CPU)")
    print("===========================================================================\n")

    # The three real machines. isolated=True routes non-AI/non-GPU kinds through the sandbox
    # path and AI kinds (ai.*) host-side, per the Stage-A worker routing.
    fleet = [
        WorkerAgent(base, Capability(worker_id="dev-box", cpus=16, ram_gb=32.0,
                                     has_gpu=True, accel=["cuda"], gpu_vram_gb=8),
                    isolated=True),
        WorkerAgent(base, Capability(worker_id="laptop-ana", cpus=8, ram_gb=16.0), isolated=True),
        WorkerAgent(base, Capability(worker_id="laptop-ben", cpus=4, ram_gb=8.0), isolated=True),
    ]

    # ---- Credential step: device-code approval gate -------------------------
    _approve_fleet(base, fleet)
    pulse_stop = threading.Event()
    _start_heartbeat_pulse(base, fleet, pulse_stop)
    n = len(fleet)

    # ====================  BEAT 1: FRACTAL (non-AI, money shot)  ============
    _hr("BEAT 1/4: distributed MANDELBROT FRACTAL (non-AI)")
    jobs = build_fractal_jobs(n_tiles=n, **FRACTAL)
    submit_all(base, jobs)
    print(f"Submitted {len(jobs)} fractal tiles ({FRACTAL['width']}x{FRACTAL['height']}, "
          f"{FRACTAL['max_iter']} iters), one horizontal band per machine.")
    collect: dict = {}
    t0 = time.perf_counter()
    _drain(fleet, collect)
    fleet_s = time.perf_counter() - t0
    img = assemble_tiles(_outputs(collect), FRACTAL["width"], FRACTAL["height"], FRACTAL["max_iter"])
    save_png(img, FRACTAL_PNG)
    # Single-machine estimate = the sum of every tile's wall time (no parallelism).
    serial_est = fleet_s * n
    speedup = serial_est / fleet_s if fleet_s > 0 else float(n)
    print(f"   -> reassembled ONE image across {n} machines: {FRACTAL_PNG}")
    print(f"      open this image, rendered across {n} machines.")
    print(f"      fleet: {fleet_s:.2f}s   |   1-machine estimate: ~{serial_est:.2f}s   |   ~{speedup:.1f}x")

    # ====================  BEAT 2: PARAM-SWEEP OPTIMIZE (non-AI)  ===========
    _hr("BEAT 2/4: distributed PARAM-SWEEP OPTIMIZATION (non-AI)")
    jobs = build_optimize_jobs(n_tiles=n, **OPTIMIZE)
    submit_all(base, jobs)
    print(f"Submitted {len(jobs)} optimize slices ({OPTIMIZE['n_candidates']:,} candidates, "
          f"dims={OPTIMIZE['dims']}): each machine scores a slice; global best wins.")
    collect = {}
    _drain(fleet, collect)
    best = aggregate_optimize(_outputs(collect))
    # Which machine found the winning tile? Map the global best back to its producing worker.
    winner = "?"
    for job_id, out in collect.items():
        if job_id == "_by":
            continue
        if int(out.get("best_index", -2)) == best["best_index"]:
            winner = collect["_by"].get(job_id, "?")
            break
    params = ", ".join(f"{p:+.3f}" for p in best["best_params"][:4])
    tail = " …" if len(best["best_params"]) > 4 else ""
    print(f"   -> global best across the fleet: score={best['best_score']:.6f} "
          f"(max 0.0 at all-zeros)")
    print(f"      best params: [{params}{tail}]  found by {winner}  "
          f"(evaluated {best['evaluated']:,} configs)")

    # ====================  BEAT 3: MODEL INFERENCE (AI)  ===================
    _hr("BEAT 3/4: MODEL INFERENCE over a prompt set (AI)")
    jobs = build_prompt_jobs(slice_size=3)
    submit_all(base, jobs)
    print(f"Submitted {len(jobs)} ai.batch_infer slices: runs HOST-SIDE (real SDK if a key is set, "
          "else a disclosed deterministic fallback).")
    collect = {}
    _drain(fleet, collect)
    completions = []
    backend = "fallback"
    for out in _outputs(collect):
        backend = out.get("backend", backend)
        for item in out.get("results", []):
            if isinstance(item, dict):
                completions.append(item)
    print(f"   -> backend: {backend} | {len(completions)} completions across the fleet. Samples:")
    for item in completions[:2]:
        prompt = str(item.get("prompt", "")).strip()
        text = str(item.get("completion", "")).strip().replace("\n", " ")
        print(f"      Q: {prompt[:60]}")
        print(f"      A: {text[:90]}")

    # ====================  BEAT 4: SYNTHETIC DATA (AI)  ===================
    _hr("BEAT 4/4: SYNTHETIC DATA GENERATION (AI)")
    jobs = build_synth_jobs(n_tiles=n, total_rows=SYNTH_ROWS)
    submit_all(base, jobs)
    print(f"Submitted {len(jobs)} ai.synth slices ({SYNTH_ROWS} rows total): each machine generates "
          "a slice; merged into ONE dataset.")
    collect = {}
    _drain(fleet, collect)
    rows = merge_synth(_outputs(collect))
    synth_backend = next((o.get("backend", "fallback") for o in _outputs(collect)), "fallback")
    print(f"   -> backend: {synth_backend} | merged dataset: {len(rows)} rows. Samples:")
    for row in rows[:3]:
        compact = {k: (str(v)[:32]) for k, v in list(row.items())[:4]}
        print(f"      {compact}")

    # ====================  INSTANT-YIELD beat  ==============================
    _hr("INSTANT YIELD: touch a laptop, it steps aside, the fleet finishes")
    submit_all(base, [SubmitRequest(
        kind="optimize",
        input={"idx_start": 0, "idx_end": 120_000, "dims": 8, "seed": 0},
        units=120_000,
    ).model_dump()])
    yielder = fleet[1]  # laptop-ana
    assignment = yielder.poll_once()
    if assignment is not None:
        yielder.trigger_yield()  # simulate a human touching the mouse mid-job
        rr = yielder.run_job(assignment)
        yielder.report_result(rr)
        ms = (rr.duration_s or 0.0) * 1000
        print(f"   laptop-ana touched mid-job -> {rr.status} in {ms:.0f}ms; slice requeued to the fleet")
        _drain(fleet)
        print("   …requeued slice completed by the rest of the fleet")
    else:
        print("   (no slice leased to laptop-ana; skipping yield demo)")

    # ====================  Ledger + close  =================================
    _hr("LEDGER (measured, honest: dev-box GPU earns 5x)")
    state = _state(base)
    completed = sum(1 for j in state["jobs"] if j["state"] == "completed")
    for w in sorted(state["workers"], key=lambda v: -v["credits"]):
        tag = "GPU 5x" if w["has_gpu"] else "CPU 1x"
        print(f"   {w['worker_id']:11} [{tag}]  credits={w['credits']:.0f}")
    print(f"   total credits={state['total_credits']:.0f} | jobs completed={completed}/{len(state['jobs'])}")
    print("   workloads run: fractal, optimize, ai.batch_infer, ai.synth")

    pulse_stop.set()
    for w in fleet:
        w.close()
    if args.no_hold:
        server.should_exit = True
        thread.join(timeout=5)
        return
    print(f"\n   Dashboard live at {base}/, press Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
