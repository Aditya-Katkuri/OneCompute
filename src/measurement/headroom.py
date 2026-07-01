"""Governor-consistent idle-headroom math for the OneCompute measurement pilot (pure stdlib).

Both the CLI report (``scripts/measure_report.py``) and the orchestrator's fleet rollup
(``GET /measurement``) import this module so the "how much idle headroom is really there" number
is computed one way, everywhere. It reads plain ``BucketStat``-shaped dicts (see
``worker.profiler.BucketStat``) so it needs neither pydantic nor the profiler itself, and it runs
anywhere a profile can be copied to.

The math mirrors the on-device governor (``worker.governor.AdaptiveGovernor``): reserve the same
comfort ``margin`` (default 25%) above measured demand, then reclaim only a *conservative*
``harvest`` fraction (default 20-40%) of the spare headroom. Everything here is an ESTIMATE
derived from measured idle profiles, paired with the assumptions that produced it. The governor's
80/95% ceilings are safety maxima, never targets, and never enter this estimate.
"""

from __future__ import annotations

from statistics import fmean

# worker.profiler.BUCKETS: hour-of-week buckets (weekday*24 + hour), 0 = Mon 00:00 ... 167 = Sun.
BUCKETS_PER_WEEK = 168
# worker.governor.AdaptiveGovernor.margin_pct: comfort headroom reserved above measured demand.
DEFAULT_MARGIN_PCT = 25.0
# Conservative-harvest posture (docs/Financial_Impact.md, docs/idea.md §5): reclaim ~20-40% of the
# spare headroom, never all of it.
DEFAULT_HARVEST_LOW = 0.20
DEFAULT_HARVEST_HIGH = 0.40

# BucketStat numeric fields we read (see worker.profiler.BucketStat). ``index``/``cpu_min``/
# ``updated_at`` are intentionally ignored: the estimate only needs demand means and peaks.
BUCKET_FIELDS = ("cpu_mean", "cpu_max", "gpu_mean", "gpu_max", "ram_mean", "ram_max")


def finite(value: object, default: float = 0.0) -> float:
    """Coerce ``value`` to a finite float, or ``default`` on anything non-numeric/NaN/inf."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if result != result or result in (float("inf"), float("-inf")):  # NaN or +/-inf
        return default
    return result


def normalize_buckets(raw_buckets: object) -> list[dict]:
    """Extract only the populated (``n > 0``) hour-of-week buckets from a raw buckets list, each
    normalized to finite floats.

    Non-list input yields ``[]``. Only the first 168 buckets are meaningful (mirrors
    ``worker.profiler.UsageProfiler._load``'s ``[:BUCKETS]`` truncation) so a malformed over-long
    list can't push coverage past 100%. ``int(Infinity)`` and other junk ``n`` values are skipped,
    not raised. Never raises.
    """
    if not isinstance(raw_buckets, list):
        return []
    populated: list[dict] = []
    for raw in raw_buckets[:BUCKETS_PER_WEEK]:
        if not isinstance(raw, dict):
            continue
        try:
            n = int(raw.get("n", 0))
        except (TypeError, ValueError, OverflowError):
            # OverflowError guards int(Infinity): json.loads accepts the Infinity/NaN tokens.
            continue
        if n <= 0:
            continue
        bucket = {"n": n}
        for field in BUCKET_FIELDS:
            bucket[field] = finite(raw.get(field, 0.0))
        populated.append(bucket)
    return populated


def _recoverable(mean_spare: float, harvest_low: float, harvest_high: float) -> tuple[float, float]:
    return mean_spare * harvest_low, mean_spare * harvest_high


def _empty_metric() -> dict:
    return {
        "avg": 0.0,
        "peak": 0.0,
        "mean_spare": 0.0,
        "recoverable_low": 0.0,
        "recoverable_high": 0.0,
    }


def summarize_profile(
    profile: dict,
    *,
    margin: float = DEFAULT_MARGIN_PCT,
    harvest_low: float = DEFAULT_HARVEST_LOW,
    harvest_high: float = DEFAULT_HARVEST_HIGH,
) -> dict:
    """Summarize one device's populated buckets into measured utilization + an ESTIMATED
    conservatively-recoverable headroom range.

    ``profile`` is ``{"device", "path", "populated": [bucket, ...]}`` (as produced by the CLI's
    ``load_profile`` or by ``normalize_buckets`` over stored buckets). Over the populated
    hour-of-week buckets: average and peak CPU/GPU/RAM, coverage (how many of the 168 buckets were
    ever measured), and the recoverable range. For each bucket the spare CPU is
    ``max(0, 100 - cpu_mean - margin)`` (reserve the comfort margin, clamp at 0); the mean spare
    across buckets times ``[harvest_low, harvest_high]`` is the estimated recoverable CPU %. GPU is
    the same with no extra margin (``max(0, 100 - gpu_mean)``). RAM headroom is simply
    ``100 - ram_mean``; RAM is reported as headroom, not harvested. Zero populated buckets ->
    zeroed summary.
    """
    populated = profile.get("populated", [])
    coverage = len(populated)
    device = profile.get("device", "device")
    path = profile.get("path", "")
    if coverage == 0:
        return {
            "device": device,
            "path": path,
            "coverage_buckets": 0,
            "coverage_pct": 0.0,
            "cpu": _empty_metric(),
            "gpu": _empty_metric(),
            "ram": {"avg": 0.0, "peak": 0.0, "headroom": 0.0},
        }

    cpu_means = [b["cpu_mean"] for b in populated]
    gpu_means = [b["gpu_mean"] for b in populated]
    ram_means = [b["ram_mean"] for b in populated]
    cpu_spares = [max(0.0, 100.0 - m - margin) for m in cpu_means]
    gpu_spares = [max(0.0, 100.0 - m) for m in gpu_means]
    mean_cpu_spare = fmean(cpu_spares)
    mean_gpu_spare = fmean(gpu_spares)
    cpu_low, cpu_high = _recoverable(mean_cpu_spare, harvest_low, harvest_high)
    gpu_low, gpu_high = _recoverable(mean_gpu_spare, harvest_low, harvest_high)
    avg_ram = fmean(ram_means)

    return {
        "device": device,
        "path": path,
        "coverage_buckets": coverage,
        "coverage_pct": 100.0 * coverage / BUCKETS_PER_WEEK,
        "cpu": {
            "avg": fmean(cpu_means),
            "peak": max(b["cpu_max"] for b in populated),
            "mean_spare": mean_cpu_spare,
            "recoverable_low": cpu_low,
            "recoverable_high": cpu_high,
        },
        "gpu": {
            "avg": fmean(gpu_means),
            "peak": max(b["gpu_max"] for b in populated),
            "mean_spare": mean_gpu_spare,
            "recoverable_low": gpu_low,
            "recoverable_high": gpu_high,
        },
        "ram": {
            "avg": avg_ram,
            "peak": max(b["ram_max"] for b in populated),
            "headroom": max(0.0, 100.0 - avg_ram),
        },
    }


def aggregate(
    summaries: list[dict],
    *,
    harvest_low: float = DEFAULT_HARVEST_LOW,
    harvest_high: float = DEFAULT_HARVEST_HIGH,
) -> dict:
    """Aggregate per-device summaries into a fleet view (per average contributing device).

    Only devices that actually measured something (coverage > 0) contribute to averages, so idle
    profiles with no data cannot dilute the estimate; ``device_count`` is the number of
    contributing devices and ``total_coverage_buckets`` sums coverage across all summaries. The
    combined recoverable range is the mean of the devices' mean spare times the harvest factors,
    matching the per-device math exactly. Empty input -> zeroed aggregate.
    """
    contributing = [s for s in summaries if s["coverage_buckets"] > 0]
    total_coverage = sum(s["coverage_buckets"] for s in summaries)
    if not contributing:
        return {
            "device_count": 0,
            "total_coverage_buckets": total_coverage,
            "cpu": _empty_metric(),
            "gpu": _empty_metric(),
            "ram": {"avg": 0.0, "headroom": 0.0},
        }

    mean_cpu_spare = fmean([s["cpu"]["mean_spare"] for s in contributing])
    mean_gpu_spare = fmean([s["gpu"]["mean_spare"] for s in contributing])
    cpu_low, cpu_high = _recoverable(mean_cpu_spare, harvest_low, harvest_high)
    gpu_low, gpu_high = _recoverable(mean_gpu_spare, harvest_low, harvest_high)
    avg_ram = fmean([s["ram"]["avg"] for s in contributing])

    return {
        "device_count": len(contributing),
        "total_coverage_buckets": total_coverage,
        "cpu": {
            "avg": fmean([s["cpu"]["avg"] for s in contributing]),
            "peak": max(s["cpu"]["peak"] for s in contributing),
            "mean_spare": mean_cpu_spare,
            "recoverable_low": cpu_low,
            "recoverable_high": cpu_high,
        },
        "gpu": {
            "avg": fmean([s["gpu"]["avg"] for s in contributing]),
            "peak": max(s["gpu"]["peak"] for s in contributing),
            "mean_spare": mean_gpu_spare,
            "recoverable_low": gpu_low,
            "recoverable_high": gpu_high,
        },
        "ram": {"avg": avg_ram, "headroom": max(0.0, 100.0 - avg_ram)},
    }
