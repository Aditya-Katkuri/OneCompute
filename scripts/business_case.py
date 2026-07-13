"""Project the MEASURED business case from a measurement-pilot profile.

The *read* half of the pilot's dollar story: it takes one usage profile (or a directory of them),
computes the governor-consistent recoverable headroom with ``measurement.headroom``, and extrapolates
an Azure-equivalent annual savings range using the fleet/pricing assumptions from
``docs/Financial_Impact.md`` -- but with the previously hand-waved "recoverable hours/day" replaced by
MEASURED recoverable intensity, measured awake-hours (bucket coverage), and the measured AC fraction.

Measured inputs come from the profile; fleet size, vCPU-equivalent cores, and price stay labelled
assumptions. Everything is an ESTIMATE and the assumptions travel with the number.

Pure stdlib (argparse, json, pathlib). Run:
    uv run python scripts/business_case.py "%LOCALAPPDATA%\\OneCompute\\usage_profile.json"
    uv run python scripts/business_case.py <dir-of-profiles> --device-class laptop_assigned
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from measurement.business_case import (
    DEVICE_DEFAULTS,
    DeviceClass,
    measured_awake_hours_per_day,
    project,
)
from measurement.headroom import BUCKETS_PER_WEEK, aggregate, normalize_buckets, summarize_profile


def _default_profile_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "OneCompute" / "usage_profile.json"


def _load_summary(target: Path) -> dict | None:
    """Build one representative measured summary from a profile file or a directory of them.

    A single file summarizes directly. A directory aggregates every ``*.json`` device profile into a
    fleet-average envelope (so a multi-person pilot yields one representative device). Returns None if
    nothing usable is found.
    """
    if target.is_dir():
        summaries: list[dict] = []
        for f in sorted(target.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("buckets"), list):
                summaries.append(
                    summarize_profile({"device": f.stem, "populated": normalize_buckets(data["buckets"])})
                )
        contributing = [s for s in summaries if s["coverage_buckets"] > 0]
        if not contributing:
            return None
        agg = aggregate(summaries)
        # Map the fleet aggregate into the shape project() expects (per average contributing device).
        avg_cov = agg["total_coverage_buckets"] / max(1, agg["device_count"])
        return {
            "device": f"{agg['device_count']} devices (aggregate)",
            "coverage_buckets": avg_cov,
            "cpu": agg["cpu"],
            "gpu": agg["gpu"],
            "ram": {"avg": agg["ram"]["avg"], "peak": 0.0, "headroom": agg["ram"]["headroom"]},
            "ac_avg": agg["ac_avg"],
            "idle_avg": agg["idle_avg"],
        }
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("buckets"), list):
        return None
    return summarize_profile({"device": target.stem, "populated": normalize_buckets(data["buckets"])})


def _m(x: float) -> str:
    """Format a dollar amount compactly ($k / $M / $B)."""
    a = abs(x)
    if a >= 1e9:
        return f"${x / 1e9:.2f}B"
    if a >= 1e6:
        return f"${x / 1e6:.1f}M"
    if a >= 1e3:
        return f"${x / 1e3:.0f}k"
    return f"${x:.0f}"


def _classes(args) -> list[DeviceClass]:
    if args.device_class == "all":
        base = list(DEVICE_DEFAULTS.values())
    else:
        base = [DEVICE_DEFAULTS[args.device_class]]
    # A single selected class can be tuned on the CLI (count/cores/price/always-on).
    if args.device_class != "all" and (args.count or args.cores or args.price or args.always_on):
        d = base[0]
        base = [
            DeviceClass(
                name=d.name,
                count=args.count if args.count is not None else d.count,
                vcpu_cores=args.cores if args.cores is not None else d.vcpu_cores,
                price_per_vcpu_hr=args.price if args.price is not None else d.price_per_vcpu_hr,
                always_on=args.always_on or d.always_on,
            )
        ]
    return base


def format_report(summary: dict, classes: list[DeviceClass], args, source: str) -> str:
    measured_awake = measured_awake_hours_per_day(summary.get("coverage_buckets", 0))
    awake_display = args.awake_hours if args.awake_hours is not None else measured_awake
    # Each class picks its own default awake-hours (measured for laptops, 24 for always-on classes)
    # unless the operator overrides with --awake-hours.
    per_class = [project(summary, d, awake_hours_per_day=args.awake_hours, ac_fraction=args.ac) for d in classes]
    total_low = sum(p["savings_usd_year_low"] for p in per_class)
    total_high = sum(p["savings_usd_year_high"] for p in per_class)
    cov = summary.get("coverage_buckets", 0)

    w = 92
    lines = ["=" * w, "OneCompute MEASURED business case (Azure-equivalent recoverable compute value)", "-" * w]
    lines.append(f"Source: {source}")
    lines.append("")
    lines.append("MEASURED inputs (from the pilot profile):")
    lines.append(
        f"  coverage        : {cov:g}/{BUCKETS_PER_WEEK} hour-of-week buckets "
        f"(~{awake_display:.1f} awake hours/day measured)"
    )
    lines.append(
        f"  recoverable CPU : {summary['cpu']['recoverable_low']:.1f}-"
        f"{summary['cpu']['recoverable_high']:.1f}% of capacity "
        f"(governor-consistent, 20-40% harvest of measured spare)"
    )
    ac_shown = args.ac * 100 if args.ac is not None else summary.get("ac_avg", 0.0)
    lines.append(f"  on AC power     : {ac_shown:.1f}% of measured time")
    lines.append(f"  user idle/away  : {summary.get('idle_avg', 0.0):.1f}% of measured time")
    lines.append("")
    lines.append("ASSUMPTIONS (modeled, tunable; defaults from docs/Financial_Impact.md):")
    lines.append("  fleet size, vCPU-equivalent cores, and Azure-equivalent $/vCPU-hr per class.")
    lines.append("")
    lines.append("Projection (annual, Azure-equivalent recoverable compute value):")
    for p in per_class:
        lines.append(
            f"  {p['device_class']:<20} {p['count']:>9,} devices x {p['vcpu_cores']:g} cores, "
            f"harvest {p['harvest_hours_per_day']:.1f} h/day  ->  {_m(p['savings_usd_year_low'])} - "
            f"{_m(p['savings_usd_year_high'])}/yr"
        )
    lines.append("")
    lines.append(f"  TOTAL projected annual value: {_m(total_low)} - {_m(total_high)}")
    lines.append("-" * w)
    lines.append(
        "ESTIMATE. Measured inputs (recoverable %, awake hours, AC fraction) replace the modeled "
        "recoverable-hours;"
    )
    lines.append(
        "fleet size and price remain assumptions. The measured envelope comes from the pilot "
        "device(s) and is"
    )
    lines.append(
        "applied as a fleet proxy (per-class pilots would refine it). Gross value, before energy "
        "and depreciation;"
    )
    lines.append("see docs/Financial_Impact.md for the net model and its caveats.")
    lines.append("=" * w)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Project the measured OneCompute business case from a pilot profile")
    parser.add_argument("target", nargs="?", default=None, help="Profile file or a directory of profiles (default: local pilot profile)")
    parser.add_argument("--device-class", choices=[*DEVICE_DEFAULTS.keys(), "all"], default="all",
                        help="Project one class (tunable below) or 'all' (default)")
    parser.add_argument("--count", type=int, default=None, help="Override fleet size for the selected class")
    parser.add_argument("--cores", type=float, default=None, help="Override vCPU-equivalent cores per device")
    parser.add_argument("--price", type=float, default=None, help="Override Azure-equivalent $/vCPU-hr")
    parser.add_argument("--always-on", action="store_true", help="Treat the class as on AC 100% (ignore measured AC)")
    parser.add_argument("--awake-hours", type=float, default=None, help="Override measured awake hours/day")
    parser.add_argument("--ac", type=float, default=None, help="Override measured AC fraction (0..1)")
    args = parser.parse_args()

    target = Path(args.target) if args.target else _default_profile_path()
    if not target.exists():
        parser.error(f"profile not found: {target}")
    summary = _load_summary(target)
    if summary is None:
        parser.error(f"no usable profile data in: {target}")
    print(format_report(summary, _classes(args), args, str(target)))


if __name__ == "__main__":
    main()
