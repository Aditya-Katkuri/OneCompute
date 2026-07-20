"""Tests for the measured business-case projection (measurement.business_case).

Locks the bridge from a measured headroom summary to Azure-equivalent dollars: measured recoverable
%, measured awake-hours (bucket coverage), and the measured AC fraction combine with fleet/price
assumptions into a recoverable-vCPU-hour and dollar range. All hand-computed and hermetic.
"""
from __future__ import annotations

import math

import pytest

from measurement.business_case import (
    DEFAULT_WAKE_CPU_FRACTION,
    DEVICE_DEFAULTS,
    DeviceClass,
    measured_availability_hours_per_day,
    measured_awake_hours_per_day,
    project,
    project_fleet,
)


def _summary(rec_low: float, rec_high: float, *, coverage: int = 70, ac_avg: float = 100.0,
             idle_avg: float = 0.0, observed_hours: float | None = None,
             unavailable_hours: float | None = None) -> dict:
    summary = {
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
    if observed_hours is not None and unavailable_hours is not None:
        summary["availability"] = {
            "source": "profile",
            "span_seconds": 86_400.0,
            "observed_seconds": observed_hours * 3_600.0,
            "unavailable_seconds": unavailable_hours * 3_600.0,
            "observed_hours_per_day": observed_hours,
            "unavailable_hours_per_day": unavailable_hours,
            "observed_pct": 100.0 * observed_hours / 24.0,
            "unavailable_pct": 100.0 * unavailable_hours / 24.0,
            "gap_count": 2,
        }
    return summary


def test_measured_awake_hours_from_coverage() -> None:
    assert measured_awake_hours_per_day(70) == 10.0    # 70 hour-of-week slots / 7 days
    assert measured_awake_hours_per_day(0) == 0.0
    assert measured_awake_hours_per_day(336) == 24.0   # clamped at 24


def test_persistent_availability_takes_precedence_over_coverage() -> None:
    summary = _summary(
        10.0,
        20.0,
        coverage=168,
        observed_hours=8.0,
        unavailable_hours=16.0,
    )
    observed, unavailable, source = measured_availability_hours_per_day(summary)
    assert observed == 8.0
    assert unavailable == 16.0
    assert source == "profile"


def test_coverage_fallback_assigns_the_remaining_day_to_unavailable() -> None:
    observed, unavailable, source = measured_availability_hours_per_day(
        _summary(10.0, 20.0, coverage=70)
    )
    assert observed == 10.0
    assert unavailable == 14.0
    assert source == "coverage"


def test_project_uses_measured_recoverable_awake_and_ac() -> None:
    dev = DeviceClass("Laptops", count=1000, vcpu_cores=12.0, price_per_vcpu_hr=0.02)
    s = _summary(10.0, 20.0, coverage=70, ac_avg=100.0)  # 10h awake/day, always plugged in
    p = project(s, dev, include_unavailable=False)
    assert math.isclose(p["awake_hours_per_day"], 10.0)
    assert math.isclose(p["harvest_hours_per_day"], 10.0)  # 10h awake x 100% AC
    # per device/day low = 0.10 x 12 cores x 10h = 12 vCPU-hrs; fleet/year = x 365 x 1000
    assert math.isclose(p["vcpu_hours_year_low"], 0.10 * 12 * 10 * 365 * 1000)
    assert math.isclose(p["savings_usd_year_low"], p["vcpu_hours_year_low"] * 0.02)
    assert p["savings_usd_year_high"] > p["savings_usd_year_low"]


def test_ac_fraction_discounts_a_laptop_on_battery_half_the_time() -> None:
    dev = DeviceClass("Laptops", count=100, vcpu_cores=10.0, price_per_vcpu_hr=0.05)
    s = _summary(20.0, 20.0, coverage=70, ac_avg=50.0)  # plugged in only half the awake time
    p = project(s, dev, include_unavailable=False)
    assert math.isclose(p["harvest_hours_per_day"], 5.0)  # 10h awake x 50% AC


def test_always_on_class_defaults_to_24h_and_full_ac() -> None:
    # An always-on class ignores a laptop's measured awake/AC: on all day, always powered.
    dev = DeviceClass("Dev boxes", count=100, vcpu_cores=16.0, price_per_vcpu_hr=0.05, always_on=True)
    s = _summary(10.0, 10.0, coverage=14, ac_avg=30.0)  # measured on a laptop (2h awake, 30% AC)
    p = project(s, dev)
    assert math.isclose(p["awake_hours_per_day"], 24.0)
    assert math.isclose(p["ac_fraction"], 1.0)
    assert math.isclose(p["harvest_hours_per_day"], 24.0)
    assert p["wake_hours_per_day"] == 0.0


def test_always_on_class_ignores_persistent_unavailable_timing() -> None:
    dev = DeviceClass("Dev boxes", count=1, vcpu_cores=16.0, price_per_vcpu_hr=0.05, always_on=True)
    summary = _summary(
        10.0,
        10.0,
        observed_hours=8.0,
        unavailable_hours=16.0,
    )
    projection = project(summary, dev)
    assert projection["availability_source"] == "always_on"
    assert projection["wake_hours_per_day"] == 0.0
    assert projection["harvest_hours_per_day"] == 24.0


def test_awake_only_overrides_take_precedence_over_measured() -> None:
    dev = DEVICE_DEFAULTS["laptop_assigned"]
    s = _summary(10.0, 10.0, coverage=70, ac_avg=100.0)
    p = project(
        s,
        dev,
        awake_hours_per_day=4.0,
        ac_fraction=0.5,
        include_unavailable=False,
    )
    assert math.isclose(p["harvest_hours_per_day"], 2.0)  # 4h x 0.5, both overridden


def test_default_wake_model_counts_all_inferred_gaps_at_75_percent_cpu() -> None:
    dev = DeviceClass("Laptops", count=1, vcpu_cores=12.0, price_per_vcpu_hr=0.02)
    summary = _summary(
        10.0,
        20.0,
        ac_avg=50.0,
        observed_hours=8.0,
        unavailable_hours=16.0,
    )

    projection = project(summary, dev)

    assert projection["awake_harvest_hours_per_day"] == pytest.approx(4.0)
    assert projection["wake_hours_per_day"] == pytest.approx(16.0)
    assert projection["wake_cpu_fraction"] == DEFAULT_WAKE_CPU_FRACTION
    assert projection["wake_vcpu_hours_day"] == pytest.approx(0.75 * 12 * 16)
    assert projection["vcpu_hours_day_low"] == pytest.approx((0.10 * 12 * 4) + (0.75 * 12 * 16))
    assert projection["vcpu_hours_day_high"] == pytest.approx((0.20 * 12 * 4) + (0.75 * 12 * 16))


def test_awake_only_mode_assigns_zero_compute_to_unavailable_gaps() -> None:
    dev = DeviceClass("Laptops", count=1, vcpu_cores=12.0, price_per_vcpu_hr=0.02)
    summary = _summary(
        10.0,
        20.0,
        ac_avg=50.0,
        observed_hours=8.0,
        unavailable_hours=16.0,
    )

    projection = project(summary, dev, include_unavailable=False)

    assert projection["wake_enabled"] is False
    assert projection["wake_hours_per_day"] == 0.0
    assert projection["vcpu_hours_day_low"] == pytest.approx(0.10 * 12 * 4)


def test_wake_fraction_overrides_scale_the_gap_component() -> None:
    dev = DeviceClass("Laptops", count=1, vcpu_cores=12.0, price_per_vcpu_hr=0.02)
    summary = _summary(
        10.0,
        20.0,
        ac_avg=50.0,
        observed_hours=8.0,
        unavailable_hours=16.0,
    )
    projection = project(
        summary,
        dev,
        wakeable_fraction=0.5,
        wake_cpu_fraction=0.6,
    )
    assert projection["wake_hours_per_day"] == pytest.approx(8.0)
    assert projection["wake_vcpu_hours_day"] == pytest.approx(0.6 * 12 * 8)


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
