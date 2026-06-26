"""Phase-1 live smoke demo (Chief-of-Staff integration artifact).

Starts a REAL orchestrator (uvicorn on localhost), then runs a small heterogeneous
fleet of REAL HTTP workers that drain a batch of jobs, proving the over-the-network
slice and showing the rewards ledger tick up (GPU workers earn 5x via the
server-assigned class weight). Run:

    $uv = "C:\\Users\\t-cfinney\\AppData\\Local\\Programs\\Python\\Python312-arm64\\Scripts\\uv.exe"
    & $uv run python scripts/smoke.py
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contracts import Capability, SubmitRequest  # noqa: E402
from orchestrator.app import create_app  # noqa: E402
from worker.agent import WorkerAgent  # noqa: E402


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(base: str, port: int):
    db = os.path.join(tempfile.mkdtemp(prefix="onecompute_"), "state.db")
    config = uvicorn.Config(create_app(db), host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):  # wait for readiness
        try:
            if httpx.get(f"{base}/state", timeout=0.5).status_code == 200:
                return server, thread
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError("orchestrator did not become ready")


def main() -> None:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    server, thread = _start_server(base, port)
    print(f"orchestrator live at {base}")

    batch = [
        {"items": [1, 2, 3, 4], "op": "square"},
        {"items": ["a", "b", "c"], "op": "upper"},
        {"items": [10, 20], "op": "square"},
        {"items": ["onecompute"], "op": "sha256"},
        {"items": [5, 6, 7], "op": "square"},
    ]
    with httpx.Client(base_url=base, timeout=5.0) as submitter:
        for payload in batch:
            submitter.post(
                "/jobs",
                json=SubmitRequest(
                    kind="data.transform", input=payload, units=len(payload["items"])
                ).model_dump(),
            )
    print(f"submitted {len(batch)} jobs")

    fleet = [
        WorkerAgent(base, Capability(worker_id="gpu-1", cpus=8, ram_gb=16.0,
                                     has_gpu=True, accel=["cuda"], gpu_vram_gb=8)),
        WorkerAgent(base, Capability(worker_id="cpu-1", cpus=4, ram_gb=8.0)),
        WorkerAgent(base, Capability(worker_id="cpu-2", cpus=2, ram_gb=4.0)),
    ]
    for w in fleet:
        w.register()

    idle_rounds = 0
    while idle_rounds < len(fleet):  # round-robin drain until the queue is empty
        progressed = False
        for w in fleet:
            rr = w.run_once()
            if rr is not None:
                print(f"  {rr.worker_id:6} ran {rr.job_id[:8]} -> {rr.status} ({rr.units} units)")
                progressed = True
        idle_rounds = 0 if progressed else idle_rounds + 1

    state = httpx.get(f"{base}/state", timeout=5.0).json()
    print("\nfleet ledger:")
    for wv in sorted(state["workers"], key=lambda v: -v["credits"]):
        tag = "GPU" if wv["has_gpu"] else "CPU"
        print(f"  {wv['worker_id']:6} [{tag}] credits={wv['credits']}")
    completed = sum(1 for j in state["jobs"] if j["state"] == "completed")
    print(f"\ntotal credits harvested: {state['total_credits']}")
    print(f"jobs completed: {completed}/{len(state['jobs'])}")

    for w in fleet:
        w.close()
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
