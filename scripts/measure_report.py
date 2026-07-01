"""Read a OneCompute measurement pilot into a measured idle-headroom summary.

This is the *read* half of the measure-only pilot. The worker's measure-only mode
(``python -m worker --url <host> --measure-only``) folds each machine's live CPU/GPU/RAM into
an on-device usage profile (``worker.profiler.UsageProfiler``), persisted as JSON
``{"buckets": [BucketStat x168]}`` at ``%LOCALAPPDATA%\\OneCompute\\usage_profile.json``. A pilot
collects one such profile per device. This script turns one profile, or a directory of them, into
the number an org actually gets out of a two-week measurement pilot: how much idle headroom is
really there, measured, not modeled.

The headroom math is kept consistent with the on-device governor (``worker.governor``): it reserves
the same comfort ``margin`` (default 25%) above measured demand and reports a *conservative* harvest
range (default 20-40% of the spare headroom). Those are the same assumptions behind the governor's
"run in the slack, yield early" posture; the governor's 80/95% ceilings are safety maxima, never
targets, and never enter this estimate. Everything here is labelled an ESTIMATE derived from
measured idle profiles, paired with the assumptions that produced it, so it can honestly replace the
modeled figures in ``docs/Financial_Impact.md``.

Pure stdlib (argparse, json, os, pathlib, statistics, datetime): no third-party deps, so it runs
anywhere a profile file can be copied to.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from measurement.headroom import (
    BUCKETS_PER_WEEK,
    DEFAULT_HARVEST_HIGH,
    DEFAULT_HARVEST_LOW,
    DEFAULT_MARGIN_PCT,
    aggregate,
    normalize_buckets,
    summarize_profile,
)


def default_profile_path() -> Path:
    """Local profile path, matching ``worker.profiler._default_profile_path``."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "OneCompute" / "usage_profile.json"


# --- file discovery + loading (the headroom math itself lives in measurement.headroom) ----------


def load_profile(path: str | os.PathLike[str]) -> dict | None:
    """Load one usage-profile JSON file into ``{"path", "device", "populated": [bucket, ...]}``.

    ``populated`` holds only buckets with ``n > 0`` (the only ones the profiler fills), each
    normalized to finite floats via ``measurement.headroom.normalize_buckets``. Returns ``None``
    for anything unreadable or malformed (missing file, bad JSON, not an object, or no ``buckets``
    list) so a single bad file never aborts a fleet report. A *valid but all-empty* profile is NOT
    malformed: it loads with ``populated == []`` and later summarizes as zero-coverage. Never
    raises.
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # Missing/unreadable file, or a binary/non-UTF-8 blob dropped into the pilot folder.
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw_buckets = data.get("buckets")
    if not isinstance(raw_buckets, list):
        return None
    return {"path": str(p), "device": p.stem, "populated": normalize_buckets(raw_buckets)}


def discover_paths(target: str | os.PathLike[str]) -> tuple[list[Path], list[dict]]:
    """Resolve ``target`` to profile files. A directory yields its ``*.json`` files (a pilot drops
    one per device); a file yields itself; a missing path yields nothing plus a "not found" skip."""
    p = Path(target)
    if p.is_dir():
        return sorted(f for f in p.glob("*.json") if f.is_file()), []
    if p.is_file():
        return [p], []
    return [], [{"path": str(p), "reason": "not found"}]


# --- text rendering -----------------------------------------------------------------------------

_WIDTH = 92


def _pct(x: float) -> str:
    return f"{x:.1f}%"


def _rng(low: float, high: float) -> str:
    return f"{low:.1f}-{high:.1f}%"


def _row(cells: list[str], widths: list[int], aligns: list[str]) -> str:
    parts = [
        f"{c:<{w}}" if a == "l" else f"{c:>{w}}"
        for c, w, a in zip(cells, widths, aligns, strict=True)
    ]
    return "  ".join(parts).rstrip()


def format_text(
    summaries: list[dict],
    agg: dict,
    skipped: list[dict],
    *,
    margin: float,
    harvest_low: float,
    harvest_high: float,
    source: str,
) -> str:
    """Render a clean, aligned, projector-legible report. No em dashes; hyphens and commas only."""
    harvest = f"{harvest_low * 100:.0f}-{harvest_high * 100:.0f}%"
    lines: list[str] = []
    lines.append("=" * _WIDTH)
    lines.append("OneCompute measurement pilot report (MEASURED idle headroom)")
    lines.append("-" * _WIDTH)
    lines.append(f"Source: {source}")
    lines.append("Measured on-device usage profiles, one per device. These MEASURED numbers")
    lines.append("replace the modeled estimates in docs/Financial_Impact.md with pilot data.")
    lines.append(
        f"Assumptions (governor-matched, all ESTIMATES): comfort margin {margin:.0f}%, "
        f"conservative harvest {harvest} of spare."
    )
    lines.append(
        "Formula: recoverable = harvest x mean(max(0, 100 - CPU_mean - margin)) over populated"
    )
    lines.append(
        "hour-of-week buckets. The governor's 80/95% ceilings are safety maxima, never targets."
    )
    lines.append("")

    dev_names = [s["device"] for s in summaries]
    dev_w = min(28, max([len("Device")] + [len(n) for n in dev_names])) if dev_names else 12
    widths = [dev_w, 8, 8, 9, 15, 8, 15, 8, 9]
    aligns = ["l", "r", "r", "r", "r", "r", "r", "r", "r"]
    headers = [
        "Device", "Cover", "CPU avg", "CPU peak", "Recover CPU%",
        "GPU avg", "Recover GPU%", "RAM avg", "RAM head",
    ]
    lines.append(_row(headers, widths, aligns))
    lines.append(_row(["-" * w for w in widths], widths, aligns))
    if not summaries:
        lines.append("(no profiles found)")
    for s in summaries:
        name = s["device"]
        if len(name) > dev_w:
            name = name[: dev_w - 1] + "~"
        cover = f"{s['coverage_buckets']}/{BUCKETS_PER_WEEK}"
        if s["coverage_buckets"] == 0:
            cells = [name, cover, "-", "-", "-", "-", "-", "-", "-"]
        else:
            cells = [
                name,
                cover,
                _pct(s["cpu"]["avg"]),
                _pct(s["cpu"]["peak"]),
                _rng(s["cpu"]["recoverable_low"], s["cpu"]["recoverable_high"]),
                _pct(s["gpu"]["avg"]),
                _rng(s["gpu"]["recoverable_low"], s["gpu"]["recoverable_high"]),
                _pct(s["ram"]["avg"]),
                _pct(s["ram"]["headroom"]),
            ]
        lines.append(_row(cells, widths, aligns))
    lines.append("")

    n = agg["device_count"]
    lines.append("Aggregate (fleet, per average contributing device)")
    lines.append(f"  devices summarized : {n}")
    lines.append(
        f"  total coverage     : {agg['total_coverage_buckets']} device-buckets "
        f"(of {BUCKETS_PER_WEEK} hour-of-week buckets per device)"
    )
    if n:
        lines.append(
            f"  CPU  avg {_pct(agg['cpu']['avg'])}  peak {_pct(agg['cpu']['peak'])}  "
            f"est. recoverable {_rng(agg['cpu']['recoverable_low'], agg['cpu']['recoverable_high'])}"
        )
        lines.append(
            f"  GPU  avg {_pct(agg['gpu']['avg'])}  peak {_pct(agg['gpu']['peak'])}  "
            f"est. recoverable {_rng(agg['gpu']['recoverable_low'], agg['gpu']['recoverable_high'])}"
        )
        lines.append(
            f"  RAM  avg {_pct(agg['ram']['avg'])}  headroom {_pct(agg['ram']['headroom'])}"
        )
    lines.append("")

    if skipped:
        lines.append(f"Skipped {len(skipped)} unreadable/malformed file(s):")
        for sk in skipped:
            lines.append(f"  - {sk['path']} ({sk['reason']})")
        lines.append("")

    lines.append("-" * _WIDTH)
    lines.append(
        f"Estimated conservatively-recoverable CPU headroom across {n} devices: "
        f"{agg['cpu']['recoverable_low']:.1f}-{agg['cpu']['recoverable_high']:.1f} percent "
        f"(measured, margin={margin:.0f}%, harvest {harvest})"
    )
    lines.append("=" * _WIDTH)
    return "\n".join(lines)


# --- CLI ----------------------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="measure_report",
        description=(
            "Summarize OneCompute measure-only usage profiles into measured idle headroom "
            "(replaces the modeled docs/Financial_Impact.md estimates)."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help=(
            "A usage_profile.json file OR a directory of *.json profiles (a pilot collects one "
            "per device). Defaults to the local profile path."
        ),
    )
    parser.add_argument(
        "--path",
        dest="path_opt",
        default=None,
        help="Same as the positional path; overrides it when both are given.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the text report.",
    )
    parser.add_argument(
        "--harvest-low",
        type=float,
        default=DEFAULT_HARVEST_LOW,
        help="Low end of the conservative harvest fraction of spare headroom (default 0.20).",
    )
    parser.add_argument(
        "--harvest-high",
        type=float,
        default=DEFAULT_HARVEST_HIGH,
        help="High end of the conservative harvest fraction of spare headroom (default 0.40).",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=DEFAULT_MARGIN_PCT,
        help="Comfort margin percent reserved above measured demand (default 25.0).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    target = args.path_opt or args.path or default_profile_path()
    # Normalize the knobs so the report can never print a negative or backwards range: clamp the
    # margin and harvest fractions non-negative and order them low <= high. The header/assumptions
    # echo the values actually used, so this stays transparent rather than hiding a typo.
    margin = max(0.0, args.margin)
    harvest_low, harvest_high = sorted((max(0.0, args.harvest_low), max(0.0, args.harvest_high)))
    paths, skipped = discover_paths(target)

    summaries: list[dict] = []
    for p in paths:
        profile = load_profile(p)
        if profile is None:
            skipped.append({"path": str(p), "reason": "unreadable or malformed"})
            continue
        summaries.append(
            summarize_profile(
                profile,
                margin=margin,
                harvest_low=harvest_low,
                harvest_high=harvest_high,
            )
        )

    agg = aggregate(summaries, harvest_low=harvest_low, harvest_high=harvest_high)
    assumptions = {
        "margin_pct": margin,
        "harvest_low": harvest_low,
        "harvest_high": harvest_high,
        "buckets_per_week": BUCKETS_PER_WEEK,
    }

    if args.json:
        report = {
            "assumptions": assumptions,
            "source": str(target),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "devices": summaries,
            "aggregate": agg,
            "skipped": skipped,
        }
        print(json.dumps(report, indent=2))
    else:
        print(
            format_text(
                summaries,
                agg,
                skipped,
                margin=margin,
                harvest_low=harvest_low,
                harvest_high=harvest_high,
                source=str(target),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
