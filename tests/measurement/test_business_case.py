"""Tests for the measured business-case projection (measurement.business_case).

Locks the bridge from a measured headroom summary to Azure-equivalent dollars: measured recoverable
%, measured awake-hours (bucket coverage), and the measured AC fraction combine with fleet/price
assumptions into a recoverable-vCPU-hour and dollar range. All hand-computed and hermetic.
"""
from __future__ import annotations

import math

from measurement.business_case import (
    DEVICE_DEFAULTS,
    DeviceClass,
    measured_awake_hours_per_day,
    project,
    project_fleet,
)


def _summary(rec_low: float, rec_high: float, *, coverage: int = 70, ac_avg: float = 100.0,
             idle_avg: float = 0.0) -> dict:
    return {
        "device": "d",
        "coverage_buckets": coverage,
        "cpu": {"avg": 30.0, "peak": 60.0, "mean_spare": 0.0,
                "recoverable_low": rec_low, "recoverable_high": rec_high},
        "gpu": {"avg": 0.0, "peak": 0.0, "mean_spare": 0.0,
                "recoverable_low": 0.0, "recoverable_high": 0.0},
        "ram": {"avg": 50.0, "peak": 60.0, "headroom": 50.0},
        "ac_avg": ac_avg,
        "idle_avg": idle_avg,
    }


def test_measured_awake_hours_from_coverage() -> None:
    assert measured_awake_hours_per_day(70) == 10.0    # 70 hour-of-week slots / 7 days
    assert measured_awake_hours_per_day(0) == 0.0
    assert measured_awake_hours_per_day(336) == 24.0   # clamped at 24


def test_project_uses_measured_recoverable_awake_and_ac() -> None:
    dev = DeviceClass("Laptops", count=1000, vcpu_cores=12.0, price_per_vcpu_hr=0.02)
    s = _summary(10.0, 20.0, coverage=70, ac_avg=100.0)  # 10h awake/day, always plugged in
    p = project(s, dev)
    assert math.isclose(p["awake_hours_per_day"], 10.0)
    assert math.isclose(p["harvest_hours_per_day"], 10.0)  # 10h awake x 100% AC
    # per device/day low = 0.10 x 12 cores x 10h = 12 vCPU-hrs; fleet/year = x 365 x 1000
    assert math.isclose(p["vcpu_hours_year_low"], 0.10 * 12 * 10 * 365 * 1000)
    assert math.isclose(p["savings_usd_year_low"], p["vcpu_hours_year_low"] * 0.02)
    assert p["savings_usd_year_high"] > p["savings_usd_year_low"]


def test_ac_fraction_discounts_a_laptop_on_battery_half_the_time() -> None:
    dev = DeviceClass("Laptops", count=100, vcpu_cores=10.0, price_per_vcpu_hr=0.05)
    s = _summary(20.0, 20.0, coverage=70, ac_avg=50.0)  # plugged in only half the awake time
    p = project(s, dev)
    assert math.isclose(p["harvest_hours_per_day"], 5.0)  # 10h awake x 50% AC


def test_always_on_class_defaults_to_24h_and_full_ac() -> None:
    # An always-on class ignores a laptop's measured awake/AC: on all day, always powered.
    dev = DeviceClass("Dev boxes", count=100, vcpu_cores=16.0, price_per_vcpu_hr=0.05, always_on=True)
    s = _summary(10.0, 10.0, coverage=14, ac_avg=30.0)  # measured on a laptop (2h awake, 30% AC)
    p = project(s, dev)
    assert math.isclose(p["awake_hours_per_day"], 24.0)
    assert math.isclose(p["ac_fraction"], 1.0)
    assert math.isclose(p["harvest_hours_per_day"], 24.0)


def test_overrides_take_precedence_over_measured() -> None:
    dev = DEVICE_DEFAULTS["laptop_assigned"]
    s = _summary(10.0, 10.0, coverage=70, ac_avg=100.0)
    p = project(s, dev, awake_hours_per_day=4.0, ac_fraction=0.5)
    assert math.isclose(p["harvest_hours_per_day"], 2.0)  # 4h x 0.5, both overridden


def test_project_fleet_totals_the_classes() -> None:
    s = _summary(10.0, 20.0, coverage=70, ac_avg=100.0)
    fleet = project_fleet(s, list(DEVICE_DEFAULTS.values()))
    assert len(fleet["per_class"]) == 3
    assert math.isclose(
        fleet["savings_usd_year_low"], sum(p["savings_usd_year_low"] for p in fleet["per_class"])
    )
    assert fleet["savings_usd_year_high"] > fleet["savings_usd_year_low"]


def test_defaults_mirror_financial_impact_model() -> None:
    # Guards the reconciliation promise: the default fleet/pricing matches docs/Financial_Impact.md.
    assert DEVICE_DEFAULTS["laptop_assigned"].count == 200_000
    assert DEVICE_DEFAULTS["laptop_assigned"].price_per_vcpu_hr == 0.018
    assert DEVICE_DEFAULTS["devbox"].vcpu_cores == 16.0
