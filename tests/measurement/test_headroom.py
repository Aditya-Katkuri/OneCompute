"""Tests for measurement.headroom: the shared, governor-consistent idle-headroom math.

These lock the pure functions both the CLI report (scripts/measure_report.py) and the orchestrator
rollup (GET /measurement) build on: bucket normalization, per-device summarize, and fleet
aggregate. All hand-computed and hermetic (no files, no network).
"""
from __future__ import annotations

import math

from measurement.headroom import (
    BUCKETS_PER_WEEK,
    DEFAULT_MARGIN_PCT,
    aggregate,
    finite,
    normalize_buckets,
    summarize_profile,
)


def _bucket(cpu: float, gpu: float, ram: float, *, n: int = 10) -> dict:
    return {
        "n": n,
        "cpu_mean": cpu,
        "cpu_max": cpu + 5,
        "gpu_mean": gpu,
        "gpu_max": gpu + 5,
        "ram_mean": ram,
        "ram_max": ram + 5,
    }


def _bucket_ac(cpu: float, ram: float, ac: float, idle: float, *, n: int = 10) -> dict:
    b = _bucket(cpu, 0.0, ram, n=n)
    b["ac_mean"] = ac
    b["idle_mean"] = idle
    return b


# --- finite ------------------------------------------------------------------


def test_finite_coerces_and_guards() -> None:
    assert finite(12.5) == 12.5
    assert finite("nope") == 0.0
    assert finite(None) == 0.0
    assert finite(float("nan")) == 0.0
    assert finite(float("inf")) == 0.0
    assert finite(float("-inf"), default=-1.0) == -1.0


# --- normalize_buckets -------------------------------------------------------


def test_normalize_keeps_only_populated_and_coerces() -> None:
    raw = [
        _bucket(20, 5, 40, n=10),
        {"n": 0, "cpu_mean": 99},              # unpopulated -> dropped
        "not-a-dict",                            # junk -> dropped
        {"n": 3, "cpu_mean": "bad", "gpu_mean": 7},  # bad field -> coerced to 0.0
    ]
    out = normalize_buckets(raw)
    assert len(out) == 2
    assert out[0]["cpu_mean"] == 20
    # the coerced bucket keeps its good field and zeroes the bad one
    assert out[1]["cpu_mean"] == 0.0
    assert out[1]["gpu_mean"] == 7


def test_normalize_non_list_is_empty() -> None:
    assert normalize_buckets(None) == []
    assert normalize_buckets({"buckets": []}) == []
    assert normalize_buckets("x") == []


def test_normalize_truncates_to_one_week_of_buckets() -> None:
    raw = [_bucket(10, 0, 0) for _ in range(BUCKETS_PER_WEEK + 50)]
    assert len(normalize_buckets(raw)) == BUCKETS_PER_WEEK


def test_normalize_skips_non_finite_n() -> None:
    # json.loads accepts the Infinity token; int(inf) raises OverflowError -> skipped, not raised.
    raw = [{"n": float("inf"), "cpu_mean": 5}, _bucket(10, 0, 0)]
    out = normalize_buckets(raw)
    assert len(out) == 1
    assert out[0]["cpu_mean"] == 10


# --- summarize_profile -------------------------------------------------------


def test_summarize_math_matches_governor_margin_and_harvest() -> None:
    profile = {"device": "d", "populated": [_bucket(20, 5, 40), _bucket(30, 15, 50)]}
    s = summarize_profile(profile)  # defaults: margin 25, harvest 0.20-0.40
    assert s["coverage_buckets"] == 2
    assert s["cpu"]["avg"] == 25.0
    assert s["cpu"]["peak"] == 35.0  # max cpu_max = 30 + 5
    # spares: max(0,100-20-25)=55, max(0,100-30-25)=45 -> mean 50
    assert s["cpu"]["mean_spare"] == 50.0
    assert math.isclose(s["cpu"]["recoverable_low"], 10.0)
    assert math.isclose(s["cpu"]["recoverable_high"], 20.0)
    # gpu has no extra margin: spares 95, 85 -> mean 90
    assert math.isclose(s["gpu"]["recoverable_low"], 18.0)
    assert math.isclose(s["gpu"]["recoverable_high"], 36.0)
    # ram headroom = 100 - mean(40,50)
    assert math.isclose(s["ram"]["headroom"], 55.0)


def test_summarize_tunable_margin_and_harvest() -> None:
    profile = {"device": "d", "populated": [_bucket(20, 0, 0)]}
    s = summarize_profile(profile, margin=10.0, harvest_low=0.5, harvest_high=1.0)
    # spare = 100 - 20 - 10 = 70; recoverable = 35 .. 70
    assert math.isclose(s["cpu"]["recoverable_low"], 35.0)
    assert math.isclose(s["cpu"]["recoverable_high"], 70.0)


def test_summarize_clamps_spare_at_zero_when_demand_exceeds_headroom() -> None:
    # cpu_mean 90 + margin 25 > 100 -> spare clamps to 0, so nothing recoverable.
    profile = {"device": "d", "populated": [_bucket(90, 0, 0)]}
    s = summarize_profile(profile)
    assert s["cpu"]["mean_spare"] == 0.0
    assert s["cpu"]["recoverable_low"] == 0.0
    assert s["cpu"]["recoverable_high"] == 0.0


def test_summarize_zero_coverage_is_zeroed() -> None:
    s = summarize_profile({"device": "idle", "populated": []})
    assert s["coverage_buckets"] == 0
    assert s["cpu"]["recoverable_high"] == 0.0
    assert s["ram"]["headroom"] == 0.0


def test_cpu_only_profile_does_not_invent_recoverable_gpu_capacity() -> None:
    profile = {
        "device": "cpu-only",
        "gpu_supported": False,
        "populated": [_bucket(20, 0, 40)],
    }

    summary = summarize_profile(profile)

    assert summary["gpu_sampled"] is False
    assert summary["gpu"] == {
        "avg": 0.0,
        "peak": 0.0,
        "mean_spare": 0.0,
        "recoverable_low": 0.0,
        "recoverable_high": 0.0,
    }


def test_idle_supported_gpu_is_distinct_from_no_gpu() -> None:
    summary = summarize_profile(
        {
            "device": "gpu-device",
            "gpu_supported": True,
            "populated": [_bucket(20, 0, 40)],
        }
    )

    assert summary["gpu_sampled"] is True
    assert summary["gpu"]["avg"] == 0.0
    assert summary["gpu"]["recoverable_low"] == 20.0
    assert summary["gpu"]["recoverable_high"] == 40.0


def test_gpu_summary_excludes_buckets_without_a_valid_gpu_sample() -> None:
    missing_gpu = _bucket(20, 0, 40)
    missing_gpu["gpu_n"] = 0
    measured_gpu = _bucket(30, 20, 50)
    measured_gpu["gpu_n"] = 10

    summary = summarize_profile(
        {
            "device": "gpu-device",
            "gpu_supported": True,
            "populated": [missing_gpu, measured_gpu],
        }
    )

    assert summary["coverage_buckets"] == 2
    assert summary["gpu_sampled"] is True
    assert summary["gpu"]["avg"] == 20.0
    assert summary["gpu"]["recoverable_high"] == 32.0


def test_gpu_hardware_without_a_valid_sample_reports_not_measured() -> None:
    bucket = _bucket(20, 0, 40)
    bucket["gpu_n"] = 0

    summary = summarize_profile(
        {
            "device": "gpu-device",
            "gpu_supported": True,
            "populated": [bucket],
        }
    )

    assert summary["gpu_sampled"] is False
    assert summary["gpu"]["recoverable_high"] == 0.0


# --- aggregate ---------------------------------------------------------------


def test_aggregate_equal_weights_contributing_devices() -> None:
    a = summarize_profile({"device": "a", "populated": [_bucket(20, 0, 30)]})
    b = summarize_profile({"device": "b", "populated": [_bucket(40, 0, 50)]})
    agg = aggregate([a, b])
    assert agg["device_count"] == 2
    assert agg["total_coverage_buckets"] == 2
    # spares: a=100-20-25=55, b=100-40-25=35 -> mean 45 -> recoverable 9 .. 18
    assert math.isclose(agg["cpu"]["recoverable_low"], 9.0)
    assert math.isclose(agg["cpu"]["recoverable_high"], 18.0)
    assert math.isclose(agg["ram"]["avg"], 40.0)


def test_aggregate_gpu_uses_only_devices_that_sampled_a_gpu() -> None:
    cpu_only = summarize_profile(
        {
            "device": "cpu",
            "gpu_supported": False,
            "populated": [_bucket(20, 0, 30)],
        }
    )
    gpu = summarize_profile(
        {
            "device": "gpu",
            "gpu_supported": True,
            "populated": [_bucket(40, 10, 50)],
        }
    )

    agg = aggregate([cpu_only, gpu])

    assert agg["device_count"] == 2
    assert agg["gpu_device_count"] == 1
    assert agg["gpu"]["avg"] == 10.0
    assert agg["gpu"]["recoverable_low"] == 18.0


def test_aggregate_idle_device_does_not_dilute() -> None:
    live = summarize_profile({"device": "live", "populated": [_bucket(20, 0, 0)]})
    idle = summarize_profile({"device": "idle", "populated": []})
    agg = aggregate([live, idle])
    # only the contributing device counts toward averages
    assert agg["device_count"] == 1
    assert agg["total_coverage_buckets"] == 1
    single = summarize_profile({"device": "live", "populated": [_bucket(20, 0, 0)]})
    assert math.isclose(agg["cpu"]["recoverable_low"], single["cpu"]["recoverable_low"])


def test_aggregate_empty_is_zeroed() -> None:
    agg = aggregate([])
    assert agg["device_count"] == 0
    assert agg["cpu"]["recoverable_high"] == 0.0
    assert agg["ram"]["headroom"] == 0.0


def test_default_margin_is_governor_comfort_margin() -> None:
    # Guards the "matches the governor" promise: the default must stay the 25% comfort margin.
    assert DEFAULT_MARGIN_PCT == 25.0


def test_summarize_reports_ac_and_idle_averages() -> None:
    profile = {
        "device": "d",
        "populated": [_bucket_ac(20, 40, ac=100.0, idle=60.0), _bucket_ac(30, 50, ac=80.0, idle=40.0)],
    }
    s = summarize_profile(profile)
    assert math.isclose(s["ac_avg"], 90.0)    # mean(100, 80)
    assert math.isclose(s["idle_avg"], 50.0)  # mean(60, 40)


def test_aggregate_averages_ac_and_idle_across_devices() -> None:
    a = summarize_profile({"device": "a", "populated": [_bucket_ac(20, 30, ac=100.0, idle=50.0)]})
    b = summarize_profile({"device": "b", "populated": [_bucket_ac(40, 50, ac=60.0, idle=30.0)]})
    agg = aggregate([a, b])
    assert math.isclose(agg["ac_avg"], 80.0)    # mean(100, 60)
    assert math.isclose(agg["idle_avg"], 40.0)  # mean(50, 30)


def test_summarize_defaults_ac_idle_when_absent() -> None:
    # Buckets from a pre-metric profile carry no ac/idle: summarize must default them to 0, not raise.
    s = summarize_profile({"device": "d", "populated": [_bucket(20, 0, 40)]})
    assert s["ac_avg"] == 0.0 and s["idle_avg"] == 0.0
