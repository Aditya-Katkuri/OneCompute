"""Project the MEASURED business case from a measurement-pilot profile.

The *read* half of the pilot's dollar story: it takes one usage profile (or a directory of them),
computes the governor-consistent recoverable headroom with ``measurement.headroom``, and extrapolates
an Azure-equivalent annual savings range using the fleet/pricing assumptions from
``docs/Financial_Impact.md``. Measured awake capacity stays separate from the default wake-enabled
potential assigned to inferred sleep, shutdown, reboot, and observer-downtime gaps.

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

from measurement.availability import infer_telemetry_availability
from measurement.business_case import (
    DAYS_PER_YEAR,
    DEFAULT_WAKE_CPU_FRACTION,
    DEFAULT_WAKEABLE_FRACTION,
    DEVICE_DEFAULTS,
    DeviceClass,
    measured_availability_hours_per_day,
    project,
)
from measurement.headroom import BUCKETS_PER_WEEK, aggregate, normalize_buckets, summarize_profile


def _default_profile_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "OneCompute" / "usage_profile.json"


def _load_summary(target: Path, telemetry_path: Path | None = None) -> dict | None:
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
                    summarize_profile(
                        {
                            "device": f.stem,
                            "populated": normalize_buckets(data["buckets"]),
                            "availability": data.get("availability"),
                        }
                    )
                )
        contributing = [s for s in summaries if s["coverage_buckets"] > 0]
        if not contributing:
            return None
        agg = aggregate(summaries)
        # Map the fleet aggregate into the shape project() expects (per average contributing device).
        avg_cov = agg["total_coverage_buckets"] / max(1, agg["device_count"])
        summary = {
            "device": f"{agg['device_count']} devices (aggregate)",
            "coverage_buckets": avg_cov,
            "cpu": agg["cpu"],
            "gpu": agg["gpu"],
            "ram": {"avg": agg["ram"]["avg"], "peak": 0.0, "headroom": agg["ram"]["headroom"]},
            "ac_avg": agg["ac_avg"],
            "idle_avg": agg["idle_avg"],
            "availability": agg["availability"],
        }
        if telemetry_path is not None:
            inferred = infer_telemetry_availability(telemetry_path)
            if inferred["span_seconds"] > summary["availability"].get("span_seconds", 0.0):
                summary["availability"] = inferred
        return summary
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("buckets"), list):
        return None
    summary = summarize_profile(
        {
            "device": target.stem,
            "populated": normalize_buckets(data["buckets"]),
            "availability": data.get("availability"),
        }
    )
    candidate = telemetry_path or target.with_name("pilot-telemetry.jsonl")
    if candidate.is_file():
        inferred = infer_telemetry_availability(candidate)
        if inferred["span_seconds"] > summary["availability"].get("span_seconds", 0.0):
            summary["availability"] = inferred
    return summary


def _m(x: float) -> str:
    """Format a dollar amount compactly ($k / $M / $B)."""
    a = abs(x)
    if a >= 1e9:
        return f"${x / 1e9:.2f}B"
    if a >= 1e6:
        return f"${x / 1e6:.1f}M"
    if a >= 100_000:
        return f"${x / 1e3:.0f}k"
    return f"${x:,.0f}"


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
    measured_awake, measured_unavailable, availability_source = (
        measured_availability_hours_per_day(summary)
    )
    awake_display = args.awake_hours if args.awake_hours is not None else measured_awake
    unavailable_display = (
        args.unavailable_hours
        if args.unavailable_hours is not None
        else measured_unavailable
    )
    # Each class picks its own default awake-hours (measured for laptops, 24 for always-on classes)
    # unless the operator overrides with --awake-hours.
    per_class = [
        project(
            summary,
            device,
            awake_hours_per_day=args.awake_hours,
            ac_fraction=args.ac,
            unavailable_hours_per_day=args.unavailable_hours,
            include_unavailable=not args.awake_only,
            wakeable_fraction=args.wakeable_fraction,
            wake_cpu_fraction=args.wake_cpu_fraction,
        )
        for device in classes
    ]
    total_low = sum(p["savings_usd_year_low"] for p in per_class)
    total_high = sum(p["savings_usd_year_high"] for p in per_class)
    deployable_low = sum(
        p["awake_vcpu_hours_day_low"] * DAYS_PER_YEAR * p["count"] * p["price_per_vcpu_hr"]
        for p in per_class
    )
    deployable_high = sum(
        p["awake_vcpu_hours_day_high"] * DAYS_PER_YEAR * p["count"] * p["price_per_vcpu_hr"]
        for p in per_class
    )
    cov = summary.get("coverage_buckets", 0)
    availability = summary.get("availability", {})

    w = 92
    lines = ["=" * w, "OneCompute MEASURED business case (Azure-equivalent recoverable compute value)", "-" * w]
    lines.append(f"Source: {source}")
    lines.append("")
    lines.append("MEASURED inputs (from the pilot profile):")
    lines.append(
        f"  coverage        : {cov:g}/{BUCKETS_PER_WEEK} hour-of-week buckets "
        f"(legacy coverage proxy)"
    )
    span_hours = float(availability.get("span_seconds", 0.0)) / 3_600.0
    sample_count = int(availability.get("sample_count", 0))
    gap_count = int(availability.get("gap_count", 0))
    if availability_source == "coverage":
        lines.append(
            f"  observed awake  : {awake_display:.1f} h/day "
            "(legacy hour-bucket coverage proxy)"
        )
        lines.append(
            f"  unavailable gap : {unavailable_display:.1f} h/day assumed as "
            "24 hours minus the coverage proxy; no timestamp gaps were available"
        )
    else:
        lines.append(
            f"  observed awake  : {awake_display:.1f} h/day "
            f"({availability_source}, {span_hours:.1f}h span, {sample_count:,} samples)"
        )
        lines.append(
            f"  unavailable gap : {unavailable_display:.1f} h/day inferred across "
            f"{gap_count} gaps (sleep, shutdown, reboot, or observer downtime)"
        )
        if span_hours < 168.0:
            lines.append(
                "  timing confidence: preliminary, less than one full 168-hour week is represented"
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
    if args.awake_only:
        lines.append("  awake-only mode: inferred unavailable gaps contribute zero compute.")
    else:
        lines.append(
            f"  wake-enabled mode: {args.wakeable_fraction * 100:.0f}% of inferred gaps are "
            f"assumed wakeable, powered, and usable at {args.wake_cpu_fraction * 100:.0f}% CPU."
        )
        lines.append(
            "  The current worker does not wake or power on devices; this component is modeled "
            "potential, not currently executable capacity."
        )
        lines.append(
            f"  Wake RAM remains capped at measured headroom ({summary['ram']['headroom']:.1f}%); "
            "Modern Standby preserves application memory."
        )
    lines.append("")
    lines.append("Projection (annual, Azure-equivalent recoverable compute value):")
    for p in per_class:
        if p["wake_enabled"]:
            timing = (
                f"awake-AC {p['awake_harvest_hours_per_day']:.1f}h + "
                f"wake {p['wake_hours_per_day']:.1f}h"
            )
        else:
            timing = f"awake-AC {p['awake_harvest_hours_per_day']:.1f}h"
        lines.append(
            f"  {p['device_class']:<20} {p['count']:>9,} devices x {p['vcpu_cores']:g} cores, "
            f"{timing}, {p['vcpu_hours_day_low']:.1f}-{p['vcpu_hours_day_high']:.1f} "
            f"vCPU-h/day  ->  {_m(p['savings_usd_year_low'])} - "
            f"{_m(p['savings_usd_year_high'])}/yr"
        )
    lines.append("")
    if args.awake_only:
        lines.append(
            f"  TOTAL currently executable awake-only value: "
            f"{_m(deployable_low)} - {_m(deployable_high)}"
        )
    else:
        lines.append(
            f"  CURRENT WORKER awake-only value: {_m(deployable_low)} - {_m(deployable_high)}"
        )
        lines.append(
            f"  WAKE-ENABLED modeled potential: {_m(total_low)} - {_m(total_high)}"
        )
    lines.append("-" * w)
    lines.append(
        "ESTIMATE. Measured awake inputs and inferred unavailable timing are kept separate from "
        "the wake/power assumptions;"
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Project the measured OneCompute business case from a pilot profile")
    parser.add_argument("target", nargs="?", default=None, help="Profile file or a directory of profiles (default: local pilot profile)")
    parser.add_argument("--device-class", choices=[*DEVICE_DEFAULTS.keys(), "all"], default="all",
                        help="Project one class (tunable below) or 'all' (default)")
    parser.add_argument("--count", type=int, default=None, help="Override fleet size for the selected class")
    parser.add_argument("--cores", type=float, default=None, help="Override vCPU-equivalent cores per device")
    parser.add_argument("--price", type=float, default=None, help="Override Azure-equivalent $/vCPU-hr")
    parser.add_argument("--always-on", action="store_true", help="Treat the class as on AC 100% (ignore measured AC)")
    parser.add_argument("--awake-hours", type=float, default=None, help="Override measured awake hours/day")
    parser.add_argument(
        "--unavailable-hours",
        type=float,
        default=None,
        help="Override inferred unavailable hours/day (0..24)",
    )
    parser.add_argument("--ac", type=float, default=None, help="Override measured AC fraction (0..1)")
    parser.add_argument(
        "--awake-only",
        action="store_true",
        help="Exclude all inferred unavailable gaps and show currently deployable awake-only capacity",
    )
    parser.add_argument(
        "--wakeable-fraction",
        type=float,
        default=DEFAULT_WAKEABLE_FRACTION,
        help="Fraction of inferred unavailable time assumed wakeable and powered (default 1.0)",
    )
    parser.add_argument(
        "--wake-cpu-fraction",
        type=float,
        default=DEFAULT_WAKE_CPU_FRACTION,
        help="CPU fraction assigned during modeled wake-enabled gaps (default 0.75)",
    )
    parser.add_argument(
        "--telemetry",
        default=None,
        help="Local pilot-telemetry.jsonl used to reconstruct historical gaps",
    )
    args = parser.parse_args(argv)
    args.wakeable_fraction = max(0.0, min(1.0, args.wakeable_fraction))
    args.wake_cpu_fraction = max(0.0, min(1.0, args.wake_cpu_fraction))
    if args.unavailable_hours is not None:
        args.unavailable_hours = max(0.0, min(24.0, args.unavailable_hours))

    target = Path(args.target) if args.target else _default_profile_path()
    if not target.exists():
        parser.error(f"profile not found: {target}")
    telemetry_path = Path(args.telemetry) if args.telemetry else None
    summary = _load_summary(target, telemetry_path)
    if summary is None:
        parser.error(f"no usable profile data in: {target}")
    print(format_report(summary, _classes(args), args, str(target)))


if __name__ == "__main__":
    main()
