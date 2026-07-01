"""Tests for scripts/measure_report.py: the read half of the measurement-only pilot.

These build synthetic usage profiles matching worker.profiler's on-disk format
(``{"buckets": [BucketStat x168]}``, only ``n > 0`` buckets populated) and assert the report's
measured stats and its ESTIMATED conservatively-recoverable headroom, that a directory of profiles
aggregates correctly, that malformed/empty files are skipped without raising, and that --json parses
and matches. Hermetic and fast: no network, no real profiler, only tmp_path files.

``scripts`` is not an importable package (only ``src`` is on the path), so the script is loaded by
file location.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "measure_report",
    Path(__file__).resolve().parents[1] / "scripts" / "measure_report.py",
)
mr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mr)


def _bucket(
    cpu_mean: float,
    gpu_mean: float,
    ram_mean: float,
    *,
    n: int = 10,
    cpu_max: float | None = None,
    gpu_max: float | None = None,
    ram_max: float | None = None,
) -> dict:
    """A populated BucketStat dict; maxes default to the means unless a peak is given."""
    return {
        "n": n,
        "cpu_mean": cpu_mean,
        "cpu_max": cpu_mean if cpu_max is None else cpu_max,
        "cpu_min": max(0.0, cpu_mean - 5.0),
        "gpu_mean": gpu_mean,
        "gpu_max": gpu_mean if gpu_max is None else gpu_max,
        "ram_mean": ram_mean,
        "ram_max": ram_mean if ram_max is None else ram_max,
        "updated_at": 0.0,
    }


def _empty_bucket() -> dict:
    return {
        "n": 0,
        "cpu_mean": 0.0,
        "cpu_max": 0.0,
        "cpu_min": 100.0,
        "gpu_mean": 0.0,
        "gpu_max": 0.0,
        "ram_mean": 0.0,
        "ram_max": 0.0,
        "updated_at": 0.0,
    }


def _write_profile(path: Path, populated: list[dict], *, pad_to: int = 168) -> Path:
    """Write a full 168-bucket profile with ``populated`` at the front, rest empty."""
    buckets = list(populated)
    while len(buckets) < pad_to:
        buckets.append(_empty_bucket())
    path.write_text(json.dumps({"buckets": buckets}), encoding="utf-8")
    return path


def test_summarize_computes_expected_stats_and_recoverable_range(tmp_path):
    path = _write_profile(
        tmp_path / "dev.json",
        [
            _bucket(20.0, 0.0, 40.0, cpu_max=25.0),
            _bucket(30.0, 10.0, 50.0, cpu_max=35.0),
            _bucket(40.0, 20.0, 60.0, cpu_max=95.0),
        ],
    )
    profile = mr.load_profile(path)
    assert profile is not None
    assert profile["device"] == "dev"

    summary = mr.summarize_profile(profile, margin=25.0, harvest_low=0.20, harvest_high=0.40)

    assert summary["coverage_buckets"] == 3
    # CPU: means [20,30,40] -> avg 30, peak = max cpu_max = 95
    assert summary["cpu"]["avg"] == pytest.approx(30.0)
    assert summary["cpu"]["peak"] == pytest.approx(95.0)
    # spare per bucket = 100 - mean - 25 = [55,45,35] -> mean 45; recoverable 45*[.2,.4] = 9..18
    assert summary["cpu"]["mean_spare"] == pytest.approx(45.0)
    assert summary["cpu"]["recoverable_low"] == pytest.approx(9.0)
    assert summary["cpu"]["recoverable_high"] == pytest.approx(18.0)
    # GPU: means [0,10,20] -> avg 10; spare (no margin) [100,90,80] -> mean 90; recoverable 18..36
    assert summary["gpu"]["avg"] == pytest.approx(10.0)
    assert summary["gpu"]["recoverable_low"] == pytest.approx(18.0)
    assert summary["gpu"]["recoverable_high"] == pytest.approx(36.0)
    # RAM: means [40,50,60] -> avg 50; headroom 100 - 50 = 50
    assert summary["ram"]["avg"] == pytest.approx(50.0)
    assert summary["ram"]["headroom"] == pytest.approx(50.0)


def test_margin_and_harvest_are_tunable(tmp_path):
    path = _write_profile(tmp_path / "one.json", [_bucket(20.0, 0.0, 30.0)])
    profile = mr.load_profile(path)

    summary = mr.summarize_profile(profile, margin=10.0, harvest_low=0.50, harvest_high=1.00)

    # spare = 100 - 20 - 10 = 70; recoverable = 70*[.5,1.0] = 35..70
    assert summary["cpu"]["mean_spare"] == pytest.approx(70.0)
    assert summary["cpu"]["recoverable_low"] == pytest.approx(35.0)
    assert summary["cpu"]["recoverable_high"] == pytest.approx(70.0)


def test_cpu_spare_clamps_at_zero_per_bucket(tmp_path):
    # A busy bucket (90% mean) has negative raw spare and must clamp to 0, not drag the mean down.
    path = _write_profile(tmp_path / "busy.json", [_bucket(90.0, 0.0, 80.0), _bucket(20.0, 0.0, 40.0)])
    profile = mr.load_profile(path)

    summary = mr.summarize_profile(profile, margin=25.0, harvest_low=0.20, harvest_high=0.40)

    # spare = [max(0, 100-90-25)=0, max(0, 100-20-25)=55] -> mean 27.5
    assert summary["cpu"]["mean_spare"] == pytest.approx(27.5)
    assert summary["cpu"]["recoverable_low"] == pytest.approx(5.5)
    assert summary["cpu"]["recoverable_high"] == pytest.approx(11.0)


def test_directory_of_profiles_aggregates_correctly(tmp_path):
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    _write_profile(
        pilot / "a.json",
        [_bucket(20.0, 0.0, 40.0), _bucket(30.0, 10.0, 50.0), _bucket(40.0, 20.0, 60.0)],
    )
    _write_profile(pilot / "b.json", [_bucket(10.0, 0.0, 30.0), _bucket(20.0, 0.0, 50.0)])

    paths, skipped = mr.discover_paths(pilot)
    assert len(paths) == 2
    assert skipped == []

    summaries = [
        mr.summarize_profile(mr.load_profile(p), margin=25.0, harvest_low=0.20, harvest_high=0.40)
        for p in paths
    ]
    agg = mr.aggregate(summaries, harvest_low=0.20, harvest_high=0.40)

    assert agg["device_count"] == 2
    assert agg["total_coverage_buckets"] == 5
    # per-device avg CPU: a=30, b=15 -> fleet avg 22.5
    assert agg["cpu"]["avg"] == pytest.approx(22.5)
    # per-device mean spare: a=45, b=mean([65,55])=60 -> fleet 52.5; recoverable 10.5..21.0
    assert agg["cpu"]["mean_spare"] == pytest.approx(52.5)
    assert agg["cpu"]["recoverable_low"] == pytest.approx(10.5)
    assert agg["cpu"]["recoverable_high"] == pytest.approx(21.0)


def test_malformed_and_empty_files_are_skipped_without_raising(tmp_path, capsys):
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    _write_profile(pilot / "good.json", [_bucket(20.0, 0.0, 40.0)])
    (pilot / "bad.json").write_text("{ not valid json", encoding="utf-8")  # broken JSON
    (pilot / "empty.json").write_text("", encoding="utf-8")  # zero-byte file
    (pilot / "list.json").write_text("[1, 2, 3]", encoding="utf-8")  # JSON but not an object

    # load_profile never raises; it returns None for each bad file.
    assert mr.load_profile(pilot / "bad.json") is None
    assert mr.load_profile(pilot / "empty.json") is None
    assert mr.load_profile(pilot / "list.json") is None

    rc = mr.main([str(pilot), "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["devices"]) == 1
    assert len(report["skipped"]) == 3
    assert report["aggregate"]["device_count"] == 1


def test_all_empty_profile_yields_zero_coverage_summary(tmp_path):
    path = _write_profile(tmp_path / "idle.json", [])  # valid file, all 168 buckets empty
    profile = mr.load_profile(path)
    assert profile is not None  # valid-but-empty is NOT malformed
    assert profile["populated"] == []

    summary = mr.summarize_profile(profile)
    assert summary["coverage_buckets"] == 0
    assert summary["coverage_pct"] == 0.0
    assert summary["cpu"]["recoverable_low"] == 0.0
    assert summary["cpu"]["recoverable_high"] == 0.0
    assert summary["ram"]["headroom"] == 0.0

    # aggregating an all-empty device contributes nothing and does not crash.
    agg = mr.aggregate([summary])
    assert agg["device_count"] == 0
    assert agg["cpu"]["recoverable_high"] == 0.0


def test_json_output_parses_and_matches(tmp_path, capsys):
    path = _write_profile(tmp_path / "dev.json", [_bucket(20.0, 0.0, 40.0), _bucket(40.0, 20.0, 60.0)])

    rc = mr.main(
        [str(path), "--json", "--margin", "25", "--harvest-low", "0.2", "--harvest-high", "0.4"]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)

    assert report["assumptions"]["margin_pct"] == 25.0
    assert report["assumptions"]["harvest_low"] == 0.2
    assert report["assumptions"]["harvest_high"] == 0.4
    device = report["devices"][0]
    # CPU means [20,40] -> avg 30; spare [55,35] -> mean 45; recoverable 9..18
    assert device["cpu"]["avg"] == pytest.approx(30.0)
    assert device["cpu"]["recoverable_low"] == pytest.approx(9.0)
    assert device["cpu"]["recoverable_high"] == pytest.approx(18.0)
    assert report["aggregate"]["cpu"]["recoverable_low"] == pytest.approx(9.0)


def test_text_report_has_honest_headline_and_no_em_dash(tmp_path, capsys):
    path = _write_profile(tmp_path / "dev.json", [_bucket(20.0, 0.0, 40.0), _bucket(40.0, 20.0, 60.0)])

    rc = mr.main([str(path)])
    assert rc == 0
    text = capsys.readouterr().out

    assert "Estimated conservatively-recoverable CPU headroom across 1 devices:" in text
    assert "9.0-18.0 percent" in text
    assert "margin=25%" in text
    assert "harvest 20-40%" in text
    assert "\u2014" not in text  # no em dash anywhere in the output


def test_default_profile_path_used_when_no_target_given(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    default = tmp_path / "OneCompute" / "usage_profile.json"
    default.parent.mkdir(parents=True)
    _write_profile(default, [_bucket(25.0, 0.0, 45.0)])

    assert mr.default_profile_path() == default

    rc = mr.main([])  # no path -> falls back to the local profile path
    assert rc == 0
    assert "usage_profile" in capsys.readouterr().out


def test_missing_target_is_reported_not_crashed(tmp_path, capsys):
    missing = tmp_path / "nope.json"

    rc = mr.main([str(missing), "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["aggregate"]["device_count"] == 0
    assert any("nope.json" in skip["path"] for skip in report["skipped"])


def test_non_utf8_file_is_skipped_without_raising(tmp_path, capsys):
    pilot = tmp_path / "pilot"
    pilot.mkdir()
    _write_profile(pilot / "good.json", [_bucket(20.0, 0.0, 40.0)])
    (pilot / "binary.json").write_bytes(b"\xff\xfe\x00\x01\x02rubbish")  # not valid UTF-8

    assert mr.load_profile(pilot / "binary.json") is None  # must not raise UnicodeDecodeError

    rc = mr.main([str(pilot), "--json"])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["devices"]) == 1
    assert len(report["skipped"]) == 1


def test_more_than_168_buckets_cannot_exceed_full_coverage(tmp_path):
    # A malformed over-long profile must not push coverage past 100% (mirror profiler's [:168]).
    path = tmp_path / "toolong.json"
    path.write_text(json.dumps({"buckets": [_bucket(30.0, 0.0, 50.0) for _ in range(200)]}),
                    encoding="utf-8")

    summary = mr.summarize_profile(mr.load_profile(path))
    assert summary["coverage_buckets"] == 168
    assert summary["coverage_pct"] == pytest.approx(100.0)


def test_non_finite_bucket_count_is_skipped_not_crashed(tmp_path):
    # json.loads accepts the Infinity/NaN tokens; a bucket whose "n" is one must be skipped
    # (int(inf) raises OverflowError, int(nan) raises ValueError), not crash the loader.
    path = tmp_path / "weird.json"
    path.write_text(
        '{"buckets": [{"n": Infinity, "cpu_mean": 10}, {"n": NaN, "cpu_mean": 5}]}',
        encoding="utf-8",
    )

    profile = mr.load_profile(path)
    assert profile is not None  # valid object, just no usable buckets
    assert profile["populated"] == []
    assert mr.summarize_profile(profile)["coverage_buckets"] == 0


def test_reversed_or_negative_harvest_bounds_are_normalized(tmp_path, capsys):
    path = _write_profile(tmp_path / "dev.json", [_bucket(20.0, 0.0, 40.0)])

    # Pass the harvest bounds backwards and a negative margin; the report must still be well-formed.
    rc = mr.main(
        [str(path), "--json", "--harvest-low", "0.4", "--harvest-high", "0.2", "--margin", "-5"]
    )
    assert rc == 0
    report = json.loads(capsys.readouterr().out)

    assert report["assumptions"]["harvest_low"] == 0.2
    assert report["assumptions"]["harvest_high"] == 0.4
    assert report["assumptions"]["margin_pct"] == 0.0
    cpu = report["devices"][0]["cpu"]
    assert cpu["recoverable_low"] <= cpu["recoverable_high"]  # never printed backwards

