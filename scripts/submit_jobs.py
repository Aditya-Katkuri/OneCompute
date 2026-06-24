"""Submit a batch of jobs to a running OneCompute orchestrator (pilots / manual testing).

One command per workload feeds the REAL fleet. The four variety beats (fan one workload
across all N machines via the hardcoded N-tile split):
    uv run python scripts/submit_jobs.py --url http://<dev-box-ip>:8080 --kind fractal  --n 3
    uv run python scripts/submit_jobs.py --url http://<dev-box-ip>:8080 --kind optimize --n 3
    uv run python scripts/submit_jobs.py --url http://<dev-box-ip>:8080 --kind ai
    uv run python scripts/submit_jobs.py --url http://<dev-box-ip>:8080 --kind synth    --n 3

Other examples:
    uv run python scripts/submit_jobs.py --url https://host:8443 --kind fanout --n 8 --items 120
    uv run python scripts/submit_jobs.py --url https://host:8443 --kind challenge   # one integrity ringer
    uv run python scripts/submit_jobs.py --url https://host:8443 --kind gpu --n 2   # render (needs_gpu, host-side)

Kinds:
    fractal   distributed Mandelbrot   (--n tiles, --width, --height, --max-iter)
    optimize  distributed param-sweep  (--n tiles, --candidates, --dims)
    ai        ai.batch_infer prompts    (prompt slices)
    synth     ai.synth synthetic data  (--n tiles, --rows)
    fanout    CPU data.transform       (--n jobs, --items, --op)
    gpu       render / needs_gpu        (--n jobs, host-side)
    challenge integrity ringer
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
from workloads.fractal import build_fractal_jobs  # noqa: E402
from workloads.gpu import generate_gpu_jobs  # noqa: E402
from workloads.optimize import build_optimize_jobs  # noqa: E402
from workloads.submit import submit_all  # noqa: E402
from workloads.synth import build_synth_jobs  # noqa: E402


def build_jobs(args: argparse.Namespace) -> list[dict]:
    kind = args.kind
    if kind == "fractal":
        # One horizontal band per machine; tiles reassemble into one image host-side.
        return build_fractal_jobs(
            n_tiles=args.n, width=args.width, height=args.height, max_iter=args.max_iter
        )
    if kind == "optimize":
        # Each machine scores a slice of the candidate space; the global best wins.
        return build_optimize_jobs(n_tiles=args.n, n_candidates=args.candidates, dims=args.dims)
    if kind == "synth":
        # Each machine generates a row-slice; merged into one dataset host-side.
        return build_synth_jobs(n_tiles=args.n, total_rows=args.rows)
    if kind == "fanout":
        return generate_jobs(n_jobs=args.n, items_per_job=args.items, op=args.op)
    if kind == "ai":
        return build_prompt_jobs(slice_size=3)
    if kind == "gpu":
        return generate_gpu_jobs(n_jobs=args.n)
    challenge_input, _ = make_challenge()
    return [SubmitRequest(kind="challenge", input=challenge_input, units=1).model_dump()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit jobs to a running OneCompute orchestrator.")
    parser.add_argument("--url", required=True, help="Orchestrator base URL (e.g. http://<dev-box-ip>:8080)")
    parser.add_argument(
        "--kind",
        choices=("fractal", "optimize", "ai", "synth", "fanout", "gpu", "challenge"),
        default="fanout",
    )
    parser.add_argument("--n", type=int, default=3, help="Tiles/jobs (fleet kinds default to one tile per machine)")
    # fractal
    parser.add_argument("--width", type=int, default=1200, help="Fractal image width")
    parser.add_argument("--height", type=int, default=800, help="Fractal image height")
    parser.add_argument("--max-iter", type=int, default=256, help="Fractal max iterations (detail/cost)")
    # optimize
    parser.add_argument("--candidates", type=int, default=200_000, help="Total optimize candidates")
    parser.add_argument("--dims", type=int, default=8, help="Optimize objective dimensions")
    # synth
    parser.add_argument("--rows", type=int, default=60, help="Total synthetic rows")
    # fanout
    parser.add_argument("--items", type=int, default=120, help="Items per fan-out job")
    parser.add_argument("--op", choices=("square", "sha256", "upper"), default="square",
                        help="Fan-out op (sha256 is heavier -> longer jobs, good for the yield test)")
    args = parser.parse_args()

    jobs = build_jobs(args)
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
