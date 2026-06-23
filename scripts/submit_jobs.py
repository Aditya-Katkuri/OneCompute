"""Submit a batch of jobs to a running OneCompute orchestrator (pilots / manual testing).

Examples:
    uv run python scripts/submit_jobs.py --url https://host:8443 --kind fanout --n 8 --items 120
    uv run python scripts/submit_jobs.py --url https://host:8443 --kind challenge   # one integrity ringer
    uv run python scripts/submit_jobs.py --url https://host:8443 --kind ai          # ai.batch_infer slices
    uv run python scripts/submit_jobs.py --url https://host:8443 --kind gpu --n 2   # render (needs_gpu, host-side)

Kinds: fanout (CPU data.transform), ai (ai.batch_infer), gpu (render/needs_gpu), challenge (ringer).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from contracts import SubmitRequest  # noqa: E402
from trust import make_challenge  # noqa: E402
from workloads.ai_batch import build_prompt_jobs  # noqa: E402
from workloads.cpu_fanout import generate_jobs  # noqa: E402
from workloads.gpu import generate_gpu_jobs  # noqa: E402
from workloads.submit import submit_all  # noqa: E402


def build_jobs(kind: str, n: int, items: int, op: str = "square") -> list[dict]:
    if kind == "fanout":
        return generate_jobs(n_jobs=n, items_per_job=items, op=op)
    if kind == "ai":
        return build_prompt_jobs(slice_size=3)
    if kind == "gpu":
        return generate_gpu_jobs(n_jobs=n)
    challenge_input, _ = make_challenge()
    return [SubmitRequest(kind="challenge", input=challenge_input, units=1).model_dump()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit jobs to a running OneCompute orchestrator.")
    parser.add_argument("--url", required=True, help="Orchestrator base URL (e.g. https://host:8443)")
    parser.add_argument("--kind", choices=("fanout", "ai", "gpu", "challenge"), default="fanout")
    parser.add_argument("--n", type=int, default=6, help="Number of jobs (fanout/gpu)")
    parser.add_argument("--items", type=int, default=120, help="Items per fan-out job")
    parser.add_argument("--op", choices=("square", "sha256", "upper"), default="square",
                        help="Fan-out op (sha256 is heavier -> longer jobs, good for the yield test)")
    args = parser.parse_args()

    jobs = build_jobs(args.kind, args.n, args.items, args.op)
    try:
        ids = submit_all(args.url, jobs)
    except Exception as exc:
        print(f"submit failed: {exc}", file=sys.stderr)
        return 1
    print(f"submitted {len(ids)} {args.kind} job(s) to {args.url}")
    for job_id in ids:
        print(f"  {job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
