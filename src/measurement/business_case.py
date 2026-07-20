"""Turn a MEASURED idle-headroom summary into a defensible cost-savings projection (pure stdlib).

This is the bridge from the measurement pilot to the dollar business case. The pilot measures four
things per device, which this module feeds into the same shape of model as ``docs/Financial_Impact.md``
but with the previously hand-waved inputs replaced by pilot data and explicit wake assumptions:

  * ``recoverable CPU%`` -- the conservatively-recoverable fraction of CPU capacity (already the
    governor-consistent 20-40% harvest of measured spare, from ``measurement.headroom``).
  * ``observed hours/day`` -- derived from persistent sample timing when available, with bucket
    coverage retained as a backward-compatible fallback.
  * ``unavailable hours/day`` -- inferred from long intervals with no successful observer sample.
    These may be sleep, shutdown, reboot, or observer downtime and are not currently executable.
  * ``AC fraction`` -- the measured % of observed time on mains power, so a laptop is credited with
    harvest during the hours it is actually plugged in (never on battery, per idea.md 8).

Everything a market/fleet assumption (device counts, vCPU-equivalent cores, Azure-equivalent price)
is labelled and tunable; everything measured comes from the profile. The output separates the two so
the number stays honest: measured inputs, modeled assumptions, and the projection they combine into.

Model, per device class:
    measured vCPU-hours/day = recoverable_CPU% x cores x observed_hours/day x AC_fraction
    wake vCPU-hours/day     = wake_CPU_fraction x cores x unavailable_hours/day x wakeable_fraction
    annual value ($)        = (measured + wake vCPU-hours/day) x 365 x count x price_per_vCPU_hr

The wake component is enabled by default as a potential-capacity scenario. It assumes OneCompute can
wake or prevent sleep and can supply power during every inferred gap. The current worker does neither,
so callers can disable it for a currently deployable awake-only view.
"""

from __future__ import annotations

from dataclasses import dataclass

DAYS_PER_YEAR = 365
BUCKETS_PER_WEEK = 168  # hour-of-week slots; mirrors worker.profiler.BUCKETS
DEFAULT_WAKE_CPU_FRACTION = 0.75
DEFAULT_WAKEABLE_FRACTION = 1.0


def _bounded(value: object, upper: float, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return max(0.0, min(upper, number))


@dataclass(frozen=True)
class DeviceClass:
    """Fleet + pricing assumptions for one class of device. Defaults mirror docs/Financial_Impact.md
    so a measured run reconciles directly against the modeled case."""

    name: str
    count: int                 # fleet size (assumption)
    vcpu_cores: float          # vCPU-equivalent cores per device (assumption)
    price_per_vcpu_hr: float   # Azure-equivalent $/vCPU-hr (assumption)
    always_on: bool = False    # dev boxes/desktops: treat as on AC 100% of the time (ignore measured AC)


# Assumptions lifted from docs/Financial_Impact.md so measured vs modeled compare apples to apples.
DEVICE_DEFAULTS: dict[str, DeviceClass] = {
    "laptop_assigned": DeviceClass("Assigned laptops", 200_000, 12.0, 0.018),
    "laptop_unassigned": DeviceClass("Unassigned laptops", 10_000, 12.0, 0.052, always_on=True),
    "devbox": DeviceClass("Idle dev boxes", 20_000, 16.0, 0.052, always_on=True),
}


def measured_awake_hours_per_day(coverage_buckets: int) -> float:
    """Distinct populated hour-of-week slots / 7 ~= hours/day the device was powered on and sampled.

    A measurement-only worker samples only while awake, so coverage is a measured proxy for daily
    uptime over a ~1-week pilot. Clamped to 24; for a run much longer than a week it saturates toward
    all 168 slots and should be overridden with an explicit assumption.
    """
    return max(0.0, min(24.0, coverage_buckets / 7.0))


def measured_availability_hours_per_day(summary: dict) -> tuple[float, float, str]:
    """Return observed and unavailable hours/day, preferring exact persistent sample timing."""
    availability = summary.get("availability", {})
    if isinstance(availability, dict) and _bounded(
        availability.get("span_seconds"), float("inf")
    ) > 0.0:
        observed = _bounded(availability.get("observed_hours_per_day"), 24.0)
        unavailable = _bounded(
            availability.get("unavailable_hours_per_day"),
            24.0 - observed,
        )
        return observed, unavailable, str(availability.get("source", "profile"))
    observed = measured_awake_hours_per_day(summary.get("coverage_buckets", 0))
    return observed, max(0.0, 24.0 - observed), "coverage"


def project(
    summary: dict,
    device: DeviceClass,
    *,
    awake_hours_per_day: float | None = None,
    ac_fraction: float | None = None,
    unavailable_hours_per_day: float | None = None,
    include_unavailable: bool = True,
    wakeable_fraction: float = DEFAULT_WAKEABLE_FRACTION,
    wake_cpu_fraction: float = DEFAULT_WAKE_CPU_FRACTION,
) -> dict:
    """Project one device class's annual recoverable vCPU-hours and Azure-equivalent $ from a measured
    per-device ``summary`` (as produced by ``measurement.headroom.summarize_profile``).

    Persistent timing supplies observed/unavailable hours when present; old profiles fall back to
    hour-of-week coverage. ``include_unavailable`` defaults to the wake-enabled potential scenario.
    The current worker does not wake machines, so set it false for the deployable awake-only case.
    """
    rec_low = max(0.0, summary["cpu"]["recoverable_low"]) / 100.0
    rec_high = max(0.0, summary["cpu"]["recoverable_high"]) / 100.0
    measured_awake, measured_unavailable, availability_source = (
        measured_availability_hours_per_day(summary)
    )
    if device.always_on:
        awake = 24.0  # always-on class (dev box/desktop): on all day, not a laptop's measured uptime
        unavailable = 0.0
        availability_source = "always_on"
    elif awake_hours_per_day is not None:
        awake = _bounded(awake_hours_per_day, 24.0)
        unavailable = (
            _bounded(unavailable_hours_per_day, 24.0 - awake)
            if unavailable_hours_per_day is not None
            else max(0.0, 24.0 - awake)
        )
    else:
        awake = measured_awake
        unavailable = (
            _bounded(unavailable_hours_per_day, 24.0 - awake)
            if unavailable_hours_per_day is not None
            else measured_unavailable
        )
    if ac_fraction is not None:
        ac = _bounded(ac_fraction, 1.0)
    elif device.always_on:
        ac = 1.0
    else:
        ac = _bounded(summary.get("ac_avg", 0.0), 100.0) / 100.0
    awake_harvest_hours = awake * ac
    wakeable = _bounded(wakeable_fraction, 1.0)
    wake_cpu = _bounded(wake_cpu_fraction, 1.0)
    wake_hours = unavailable * wakeable if include_unavailable and not device.always_on else 0.0
    harvest_hours = awake_harvest_hours + wake_hours

    awake_day_low = rec_low * device.vcpu_cores * awake_harvest_hours
    awake_day_high = rec_high * device.vcpu_cores * awake_harvest_hours
    wake_day = wake_cpu * device.vcpu_cores * wake_hours
    per_device_day_low = awake_day_low + wake_day
    per_device_day_high = awake_day_high + wake_day
    fleet_year_low = per_device_day_low * DAYS_PER_YEAR * device.count
    fleet_year_high = per_device_day_high * DAYS_PER_YEAR * device.count
    return {
        "device_class": device.name,
        "count": device.count,
        "vcpu_cores": device.vcpu_cores,
        "price_per_vcpu_hr": device.price_per_vcpu_hr,
        "recoverable_cpu_low_pct": summary["cpu"]["recoverable_low"],
        "recoverable_cpu_high_pct": summary["cpu"]["recoverable_high"],
        "availability_source": availability_source,
        "awake_hours_per_day": awake,
        "unavailable_hours_per_day": unavailable,
        "ac_fraction": ac,
        "awake_harvest_hours_per_day": awake_harvest_hours,
        "wake_enabled": bool(include_unavailable and not device.always_on),
        "wakeable_fraction": wakeable,
        "wake_cpu_fraction": wake_cpu,
        "wake_hours_per_day": wake_hours,
        "harvest_hours_per_day": harvest_hours,
        "awake_vcpu_hours_day_low": awake_day_low,
        "awake_vcpu_hours_day_high": awake_day_high,
        "wake_vcpu_hours_day": wake_day,
        "vcpu_hours_day_low": per_device_day_low,
        "vcpu_hours_day_high": per_device_day_high,
        "measured_ram_headroom_pct": _bounded(
            summary.get("ram", {}).get("headroom", 0.0), 100.0
        ),
        "wake_ram_usable_pct": _bounded(
            summary.get("ram", {}).get("headroom", 0.0), 100.0
        ),
        "vcpu_hours_year_low": fleet_year_low,
        "vcpu_hours_year_high": fleet_year_high,
        "savings_usd_year_low": fleet_year_low * device.price_per_vcpu_hr,
        "savings_usd_year_high": fleet_year_high * device.price_per_vcpu_hr,
    }


def project_fleet(summary: dict, devices: list[DeviceClass], **project_options) -> dict:
    """Project several device classes against the SAME measured summary (the pilot's per-device
    envelope, applied to each class) and total the annual dollar range. Empty list -> zeros."""
    per_class = [project(summary, d, **project_options) for d in devices]
    return {
        "per_class": per_class,
        "savings_usd_year_low": sum(p["savings_usd_year_low"] for p in per_class),
        "savings_usd_year_high": sum(p["savings_usd_year_high"] for p in per_class),
        "vcpu_hours_year_low": sum(p["vcpu_hours_year_low"] for p in per_class),
        "vcpu_hours_year_high": sum(p["vcpu_hours_year_high"] for p in per_class),
    }
