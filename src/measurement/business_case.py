"""Turn a MEASURED idle-headroom summary into a defensible cost-savings projection (pure stdlib).

This is the bridge from the measurement pilot to the dollar business case. The pilot measures three
things per device, which this module feeds into the same shape of model as ``docs/Financial_Impact.md``
but with the previously hand-waved inputs replaced by pilot data:

  * ``recoverable CPU%`` -- the conservatively-recoverable fraction of CPU capacity (already the
    governor-consistent 20-40% harvest of measured spare, from ``measurement.headroom``).
  * ``awake hours/day`` -- derived from bucket coverage: a measurement-only worker samples only while
    the machine is on, so the number of distinct hour-of-week slots it ever populated is a measured
    proxy for how many hours a day the device is actually powered on (over a ~1-week pilot).
  * ``AC fraction`` -- the measured % of time on mains power, so a laptop is only credited with
    harvest during the hours it is actually plugged in (never on battery, per idea.md 8).

Everything a market/fleet assumption (device counts, vCPU-equivalent cores, Azure-equivalent price)
is labelled and tunable; everything measured comes from the profile. The output separates the two so
the number stays honest: measured inputs, modeled assumptions, and the projection they combine into.

Model, per device class:
    recoverable vCPU-hours/day = (recoverable_CPU% / 100) x vcpu_cores x harvest_hours/day
    harvest_hours/day          = awake_hours/day x AC_fraction   (AC_fraction = 1 for always-on classes)
    annual value ($)           = recoverable vCPU-hours/day x 365 x device_count x price_per_vCPU_hr
"""

from __future__ import annotations

from dataclasses import dataclass

DAYS_PER_YEAR = 365
BUCKETS_PER_WEEK = 168  # hour-of-week slots; mirrors worker.profiler.BUCKETS


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


def project(
    summary: dict,
    device: DeviceClass,
    *,
    awake_hours_per_day: float | None = None,
    ac_fraction: float | None = None,
) -> dict:
    """Project one device class's annual recoverable vCPU-hours and Azure-equivalent $ from a measured
    per-device ``summary`` (as produced by ``measurement.headroom.summarize_profile``).

    ``awake_hours_per_day`` defaults to the measured ``measured_awake_hours_per_day(coverage)`` and
    ``ac_fraction`` to the measured ``ac_avg`` (or 1.0 for an always-on class); pass either to override
    the measured value with an explicit assumption. Returns a dict of measured inputs, the derived
    harvest hours, and the low/high recoverable-vCPU-hour and dollar range for the whole class.
    """
    rec_low = max(0.0, summary["cpu"]["recoverable_low"]) / 100.0
    rec_high = max(0.0, summary["cpu"]["recoverable_high"]) / 100.0
    if awake_hours_per_day is not None:
        awake = awake_hours_per_day
    elif device.always_on:
        awake = 24.0  # always-on class (dev box/desktop): on all day, not a laptop's measured uptime
    else:
        awake = measured_awake_hours_per_day(summary.get("coverage_buckets", 0))
    if ac_fraction is not None:
        ac = max(0.0, min(1.0, ac_fraction))
    elif device.always_on:
        ac = 1.0
    else:
        ac = max(0.0, min(1.0, summary.get("ac_avg", 0.0) / 100.0))
    harvest_hours = awake * ac

    per_device_day_low = rec_low * device.vcpu_cores * harvest_hours
    per_device_day_high = rec_high * device.vcpu_cores * harvest_hours
    fleet_year_low = per_device_day_low * DAYS_PER_YEAR * device.count
    fleet_year_high = per_device_day_high * DAYS_PER_YEAR * device.count
    return {
        "device_class": device.name,
        "count": device.count,
        "vcpu_cores": device.vcpu_cores,
        "price_per_vcpu_hr": device.price_per_vcpu_hr,
        "recoverable_cpu_low_pct": summary["cpu"]["recoverable_low"],
        "recoverable_cpu_high_pct": summary["cpu"]["recoverable_high"],
        "awake_hours_per_day": awake,
        "ac_fraction": ac,
        "harvest_hours_per_day": harvest_hours,
        "vcpu_hours_year_low": fleet_year_low,
        "vcpu_hours_year_high": fleet_year_high,
        "savings_usd_year_low": fleet_year_low * device.price_per_vcpu_hr,
        "savings_usd_year_high": fleet_year_high * device.price_per_vcpu_hr,
    }


def project_fleet(summary: dict, devices: list[DeviceClass]) -> dict:
    """Project several device classes against the SAME measured summary (the pilot's per-device
    envelope, applied to each class) and total the annual dollar range. Empty list -> zeros."""
    per_class = [project(summary, d) for d in devices]
    return {
        "per_class": per_class,
        "savings_usd_year_low": sum(p["savings_usd_year_low"] for p in per_class),
        "savings_usd_year_high": sum(p["savings_usd_year_high"] for p in per_class),
        "vcpu_hours_year_low": sum(p["vcpu_hours_year_low"] for p in per_class),
        "vcpu_hours_year_high": sum(p["vcpu_hours_year_high"] for p in per_class),
    }
