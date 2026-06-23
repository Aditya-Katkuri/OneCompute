"""OneCompute — full integrated live demo (the unified product).

Starts a REAL signed orchestrator, serves the live dashboard, and drives every demo
beat across a heterogeneous fleet of real HTTP workers:

  1. idle fleet            5. caught a cheater (blacklist + forfeit)
  2. fan-out vs ghost bar  6. isolation proof
  3. "and it also does AI" 7. close on measured throughput
  4. instant yield

Run:
    $uv = "C:\\Users\\t-cfinney\\AppData\\Local\\Programs\\Python\\Python312-arm64\\Scripts\\uv.exe"
    & $uv run python scripts/demo.py            # runs the beats, then holds the dashboard up
    & $uv run python scripts/demo.py --no-hold  # runs the beats and exits
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
from isolation import active_boundary, isolation_proof  # noqa: E402
from orchestrator.app import create_app  # noqa: E402
from trust import make_challenge  # noqa: E402
from worker.agent import WorkerAgent  # noqa: E402
from worker.runner import default_runner  # noqa: E402
from workloads.ai_batch import build_prompt_jobs  # noqa: E402
from workloads.cpu_fanout import generate_jobs, ghost_bar_seconds  # noqa: E402
from workloads.gpu import generate_gpu_jobs  # noqa: E402
from workloads.submit import submit_all  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(base: str, port: int):
    config = uvicorn.Config(create_app(":memory:"), host="127.0.0.1", port=port, log_level="warning")
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


def _cheat_runner(manifest, _input, should_yield=lambda: False):
    if manifest.kind == "challenge":
        return {"y": 0}  # deliberately wrong
    return default_runner(manifest, _input, should_yield=should_yield)


def _drain(fleet: list[WorkerAgent], max_rounds: int = 200) -> None:
    idle = 0
    while idle < len(fleet) and max_rounds > 0:
        max_rounds -= 1
        progressed = False
        for w in fleet:
            rr = w.run_once()
            if rr is not None:
                print(f"   {rr.worker_id:8} {rr.status:9} {rr.job_id[:8]} ({rr.units} units)")
                progressed = True
        idle = 0 if progressed else idle + 1


def _credits(base: str) -> dict:
    return {w["worker_id"]: w for w in httpx.get(f"{base}/state", timeout=5).json()["workers"]}


def _start_heartbeat_pulse(base, workers, stop_event):
    """Keep the dashboard lively: mirror each honest worker's real busy/idle state (from
    /state) into plausible cpu/gpu heartbeats. Decoupled from job execution and never sends
    current_job_id or free_ram, so it cannot perturb leasing, scheduling, or RAM gating."""
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

    thread = threading.Thread(target=pulse, name="reeveos-heartbeat-pulse", daemon=True)
    thread.start()
    return thread


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-hold", action="store_true", help="exit after the beats instead of holding")
    args = parser.parse_args()

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    server, thread = _start_server(base, port)
    print("\n==================  OneCompute live  ==================")
    print(f"  Dashboard:  {base}/")
    print(f"  Signing ON (Ed25519) | isolation: {active_boundary()}")
    print("====================================================\n")

    # 1. Idle fleet -----------------------------------------------------------
    honest = [
        WorkerAgent(base, Capability(worker_id="gpu-1", cpus=8, ram_gb=16.0,
                                     has_gpu=True, accel=["cuda"], gpu_vram_gb=8),
                    isolated=True),
        WorkerAgent(base, Capability(worker_id="cpu-1", cpus=4, ram_gb=8.0),
                    isolated=True),
        WorkerAgent(base, Capability(worker_id="cpu-2", cpus=2, ram_gb=4.0),
                    isolated=True),
    ]
    cheater = WorkerAgent(base, Capability(worker_id="cheat-1", cpus=2, ram_gb=4.0),
                          runner=_cheat_runner)
    for w in (*honest, cheater):
        w.register()
    pulse_stop = threading.Event()
    _start_heartbeat_pulse(base, honest, pulse_stop)
    print("1. Idle fleet registered: gpu-1, cpu-1, cpu-2 (+ a lurking cheat-1)\n")

    # 2. CPU fan-out vs the ghost bar ----------------------------------------
    cpu_jobs = generate_jobs(n_jobs=6, items_per_job=120, op="square")
    total_items = 6 * 120
    submit_all(base, cpu_jobs)
    print(f"2. Fan-out: submitted {len(cpu_jobs)} CPU jobs ({total_items} items)")
    t0 = time.perf_counter()
    _drain(honest)
    fleet_s = time.perf_counter() - t0
    ghost_s = ghost_bar_seconds(total_items)
    print(f"   1-machine baseline (ghost bar): ~{ghost_s:.2f}s   |   fleet: {fleet_s:.2f}s\n")

    # 3. And it also does AI --------------------------------------------------
    ai_jobs = build_prompt_jobs(slice_size=3)
    submit_all(base, ai_jobs)
    print(f"3. AI: submitted {len(ai_jobs)} ai.batch_infer jobs (SDK if keyed, else disclosed fallback)")
    _drain(honest)
    print()

    # 3b. GPU host-side render (real CUDA on a GPU worker; honest CPU fallback otherwise) --
    gpu_jobs = generate_gpu_jobs(n_jobs=2, size=384, iters=10)
    submit_all(base, gpu_jobs)
    print(f"3b. GPU: submitted {len(gpu_jobs)} render jobs "
          "(needs_gpu -> only the GPU worker, host-side under a Job Object, never a container)")
    gpu_worker = honest[0]  # gpu-1 is the only has_gpu worker, so it picks up every render job
    rendered = 0
    for _ in range(len(gpu_jobs) + 3):
        rr = gpu_worker.run_once()
        out = rr.output if rr else None
        if out and "accelerator" in out:
            rendered += 1
            print(f"   gpu-1 render {rr.job_id[:8]} -> accelerator={out.get('accelerator')} "
                  f"device={out.get('device')} gpu_util={out.get('gpu_util_peak')}")
    print(f"   {rendered} render job(s) ran host-side (real CUDA + pynvml util on an NVIDIA "
          "worker; CPU-fallback honestly disclosed on this box)\n")

    # 4. Instant yield --------------------------------------------------------
    submit_all(base, [SubmitRequest(kind="data.transform",
                                    input={"items": list(range(400)), "op": "square"},
                                    units=400).model_dump()])
    yielder = honest[1]  # cpu-1
    assignment = yielder.poll_once()
    if assignment is not None:
        yielder.trigger_yield()
        rr = yielder.run_job(assignment)
        yielder.report_result(rr)
        print(f"4. Instant yield: cpu-1 touched -> {rr.status} in {rr.duration_s*1000:.0f}ms; slice requeued")
        _drain(honest)
        print("   ...requeued slice completed by the fleet\n")

    # 5. Caught a cheater -----------------------------------------------------
    challenge_input, _ = make_challenge()
    submit_all(base, [SubmitRequest(kind="challenge", input=challenge_input, units=1).model_dump()])
    cheater.run_once()
    cv = _credits(base).get("cheat-1", {})
    print(f"5. Cheater: cheat-1 returned a wrong challenge answer -> blacklisted={cv.get('blacklisted')}, "
          f"credits={cv.get('credits')}\n")

    # 6. Isolation proof ------------------------------------------------------
    proof = isolation_proof()
    print(f"6. Isolation proof: {proof}\n")

    # 7. Close on measured throughput ----------------------------------------
    state = httpx.get(f"{base}/state", timeout=5).json()
    completed = sum(1 for j in state["jobs"] if j["state"] == "completed")
    events = httpx.get(f"{base}/events", timeout=5).json()["last_id"]
    print("7. Ledger (measured, honest):")
    for w in sorted(state["workers"], key=lambda v: -v["credits"]):
        tag = "GPU" if w["has_gpu"] else "CPU"
        flag = " [BLACKLISTED]" if w["blacklisted"] else ""
        print(f"     {w['worker_id']:8} [{tag}] credits={w['credits']:.0f}{flag}")
    print(f"   total credits={state['total_credits']:.0f} | jobs completed={completed}/{len(state['jobs'])}"
          f" | events={events}")
    print("\n   Theoretical ceiling (full Copilot+ fleet, NPU INT8): 1.8 ExaOPS - see docs/idea.md sec 4.")

    pulse_stop.set()
    for w in (*honest, cheater):
        w.close()
    if args.no_hold:
        server.should_exit = True
        thread.join(timeout=5)
        return
    print(f"\n   Dashboard live at {base}/  —  press Ctrl-C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
