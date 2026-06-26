"""NON-AI: distributed Monte-Carlo portfolio risk (host-side builder + aggregator).

The fleet simulates millions of geometric-Brownian-motion price paths; each machine runs a
slice (one tile) and returns a MERGEABLE histogram of terminal returns plus moments.
``aggregate_montecarlo`` merges the tiles into fleet-wide mean / stdev / worst and Value-at-Risk
(VaR) / Conditional VaR (CVaR); ``render_risk_chart`` draws the loss distribution (matplotlib,
host-side only, guarded). Compute scales with ``total_paths * horizon_days`` -- size
``total_paths`` to hit the demo runtime.
"""

from __future__ import annotations

from typing import Any

from workloads.partition import even_ranges, weighted_ranges


def build_montecarlo_jobs(
    n_tiles: int,
    total_paths: int = 1_000_000,
    horizon_days: int = 252,
    mu: float = 0.07,
    sigma: float = 0.20,
    hist_lo: float = -1.0,
    hist_hi: float = 2.0,
    hist_bins: int = 120,
    weights: list[float] | None = None,
) -> list[dict]:
    """Build ``n_tiles`` ``montecarlo`` jobs, splitting ``total_paths`` across the fleet."""
    if n_tiles <= 0:
        raise ValueError("n_tiles must be positive")
    if total_paths <= 0:
        raise ValueError("total_paths must be positive")
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")

    if weights is None:
        ranges = even_ranges(total_paths, n_tiles)
    else:
        if len(weights) != n_tiles:
            raise ValueError("weights length must equal n_tiles")
        ranges = weighted_ranges(total_paths, weights)

    jobs: list[dict] = []
    for index, (start, end) in enumerate(ranges):
        n_paths = end - start
        if n_paths <= 0:
            continue
        jobs.append(
            {
                "kind": "montecarlo",
                "input": {
                    "n_paths": n_paths,
                    "horizon_days": horizon_days,
                    "mu": mu,
                    "sigma": sigma,
                    "seed": index + 1,
                    "hist_lo": hist_lo,
                    "hist_hi": hist_hi,
                    "hist_bins": hist_bins,
                },
                "units": n_paths,
            }
        )
    return jobs


def aggregate_montecarlo(results: list[dict], var_levels: tuple[float, ...] = (0.95, 0.99)) -> dict:
    """Merge ``montecarlo`` tile results into fleet-wide risk stats (mean/stdev/worst + VaR/CVaR).

    VaR_c is the loss at the (1-c) percentile of returns, read off the merged histogram; CVaR_c
    is the mean loss beyond it. Returns paths=0 if nothing usable came back.
    """
    parts = [r for r in results if r and "hist" in r and r.get("paths")]
    if not parts:
        return {"paths": 0}

    lo = float(parts[0]["hist_lo"])
    hi = float(parts[0]["hist_hi"])
    nbins = len(parts[0]["hist"])
    width = (hi - lo) / nbins if nbins else 1.0

    hist = [0] * nbins
    paths = 0
    weighted_mean = 0.0
    weighted_sq = 0.0  # sum over tiles of E[X^2]*n  (E[X^2] = var + mean^2)
    worst = 0.0
    for part in parts:
        n = int(part["paths"])
        paths += n
        weighted_mean += float(part["mean_return"]) * n
        weighted_sq += (float(part["stdev"]) ** 2 + float(part["mean_return"]) ** 2) * n
        worst = min(worst, float(part["worst_return"]))
        for i, value in enumerate(part["hist"]):
            hist[i] += value

    mean = weighted_mean / paths if paths else 0.0
    variance = (weighted_sq / paths - mean * mean) if paths else 0.0

    def _return_at(rank: float) -> tuple[int, float]:
        """Return (bin_index, return_value) where the cumulative count first reaches ``rank``."""
        cum = 0
        for i, count in enumerate(hist):
            cum += count
            if cum >= rank:
                return i, lo + (i + 0.5) * width
        return nbins - 1, hi

    risk: dict[str, float] = {}
    for level in var_levels:
        tail = paths * (1.0 - level)
        idx, ret = _return_at(tail)
        # CVaR: mean return of the tail (bins up to and including idx).
        tail_count = sum(hist[: idx + 1])
        tail_sum = sum(hist[b] * (lo + (b + 0.5) * width) for b in range(idx + 1))
        cvar_ret = (tail_sum / tail_count) if tail_count else ret
        pct = int(round(level * 100))
        risk[f"var_{pct}"] = -ret
        risk[f"cvar_{pct}"] = -cvar_ret

    return {
        "paths": paths,
        "mean_return": mean,
        "stdev": variance**0.5,
        "worst_return": worst,
        "hist": hist,
        "hist_lo": lo,
        "hist_hi": hi,
        **risk,
    }


def render_risk_chart(agg: dict, path: str) -> str:
    """Render the merged return distribution + VaR markers to a PNG. Needs matplotlib (host-side)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("render_risk_chart requires matplotlib (host-side only).") from exc

    hist = agg.get("hist") or []
    lo = float(agg.get("hist_lo", -1.0))
    hi = float(agg.get("hist_hi", 2.0))
    nbins = len(hist)
    width = (hi - lo) / nbins if nbins else 1.0
    centers = [lo + (i + 0.5) * width for i in range(nbins)]

    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.bar(centers, hist, width=width * 0.95, color="#6c8cff", edgecolor="none")
    for pct, color in ((95, "#e8a33c"), (99, "#e05c5c")):
        var = agg.get(f"var_{pct}")
        if var is not None:
            ax.axvline(-var, color=color, linestyle="--", linewidth=1.6, label=f"VaR{pct} = {var:.1%}")
    ax.set_title(
        f"Portfolio return distribution - {agg.get('paths', 0):,} Monte-Carlo paths "
        f"(mean {agg.get('mean_return', 0):.1%}, σ {agg.get('stdev', 0):.1%})"
    )
    ax.set_xlabel("terminal return")
    ax.set_ylabel("paths")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


__all__: list[Any] = ["build_montecarlo_jobs", "aggregate_montecarlo", "render_risk_chart"]
