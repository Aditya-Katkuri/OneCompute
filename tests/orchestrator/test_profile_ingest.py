"""Tests for the measurement-pilot endpoints: POST /profile (opt-in usage-envelope ingest) and
GET /measurement (fleet-wide MEASURED idle-headroom rollup).

Proves the same bearer-token auth as the rest of the worker API, that the server sanitizes and
clamps whatever a worker sends (the wire is never trusted), that a re-report replaces rather than
appends, that the rollup math is governor-consistent and that idle/empty profiles never dilute or
break it. Hermetic: in-memory db, no network.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from orchestrator.app import create_app


def _register(client: TestClient, worker_id: str = "w1") -> dict[str, str]:
    resp = client.post("/register", json={"worker_id": worker_id, "cpus": 2})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['worker_token']}"}


def _bucket(index: int, cpu: float, gpu: float, ram: float, *, n: int = 10) -> dict:
    return {
        "index": index,
        "n": n,
        "cpu_mean": cpu,
        "cpu_max": cpu + 8,
        "gpu_mean": gpu,
        "gpu_max": gpu + 4,
        "ram_mean": ram,
        "ram_max": ram + 6,
    }


# --- auth --------------------------------------------------------------------


def test_profile_requires_auth() -> None:
    client = TestClient(create_app(":memory:"))
    _register(client)
    assert client.post("/profile", json={"worker_id": "w1", "buckets": []}).status_code == 401


def test_profile_rejects_wrong_token() -> None:
    client = TestClient(create_app(":memory:"))
    _register(client)
    bad = {"Authorization": "Bearer nope"}
    r = client.post("/profile", json={"worker_id": "w1", "buckets": []}, headers=bad)
    assert r.status_code == 401


def test_profile_rejects_unregistered_worker() -> None:
    client = TestClient(create_app(":memory:"))
    r = client.post(
        "/profile",
        json={"worker_id": "ghost", "buckets": []},
        headers={"Authorization": "Bearer anything"},
    )
    assert r.status_code == 401


# --- ingest + rollup ---------------------------------------------------------


def test_ingest_then_measurement_rolls_up_governor_consistent_headroom() -> None:
    client = TestClient(create_app(":memory:"))
    auth = _register(client)
    buckets = [_bucket(5, 20, 5, 40), _bucket(100, 30, 10, 50)]
    r = client.post("/profile", json={"worker_id": "w1", "buckets": buckets}, headers=auth)
    assert r.status_code == 200
    assert r.json() == {"accepted": True, "buckets_stored": 2}

    m = client.get("/measurement").json()
    assert m["device_count"] == 1
    assert m["total_coverage_buckets"] == 2
    assert m["margin_pct"] == 25.0
    assert m["harvest_low"] == 0.2 and m["harvest_high"] == 0.4
    assert m["cpu"]["avg"] == 25.0
    # spares 55, 45 -> mean 50 -> recoverable 10 .. 20
    assert abs(m["cpu"]["recoverable_low"] - 10.0) < 1e-6
    assert abs(m["cpu"]["recoverable_high"] - 20.0) < 1e-6
    assert abs(m["ram_headroom"] - 55.0) < 1e-6


def test_measurement_empty_fleet_is_zeroed() -> None:
    client = TestClient(create_app(":memory:"))
    m = client.get("/measurement").json()
    assert m["device_count"] == 0
    assert m["total_coverage_buckets"] == 0
    assert m["cpu"]["recoverable_high"] == 0.0
    assert m["ram_headroom"] == 0.0


# --- server-side sanitizing (never trust the wire) ---------------------------


def test_ingest_drops_unpopulated_bad_index_and_duplicate_buckets() -> None:
    client = TestClient(create_app(":memory:"))
    auth = _register(client)
    buckets = [
        _bucket(5, 20, 0, 0),
        _bucket(5, 40, 0, 0),          # duplicate index -> dropped (first wins)
        _bucket(7, 99, 0, 0, n=0),     # unpopulated -> dropped
        _bucket(999, 10, 0, 0),        # out-of-range index -> dropped
        _bucket(-1, 10, 0, 0),         # negative index -> dropped
    ]
    r = client.post("/profile", json={"worker_id": "w1", "buckets": buckets}, headers=auth)
    assert r.json()["buckets_stored"] == 1
    m = client.get("/measurement").json()
    assert m["cpu"]["avg"] == 20.0  # only the first index-5 bucket survived


def test_ingest_clamps_out_of_range_percentages() -> None:
    client = TestClient(create_app(":memory:"))
    auth = _register(client)
    # cpu_mean 150 and a negative ram must be clamped into 0..100, never stored raw.
    buckets = [_bucket(5, 150, 0, -20)]
    client.post("/profile", json={"worker_id": "w1", "buckets": buckets}, headers=auth)
    m = client.get("/measurement").json()
    assert m["cpu"]["avg"] == 100.0
    assert m["ram_avg"] == 0.0
    # cpu demand at the ceiling -> no recoverable headroom
    assert m["cpu"]["recoverable_high"] == 0.0


def test_reprofile_replaces_not_appends() -> None:
    client = TestClient(create_app(":memory:"))
    auth = _register(client)
    client.post(
        "/profile",
        json={"worker_id": "w1", "buckets": [_bucket(1, 20, 0, 0), _bucket(2, 20, 0, 0)]},
        headers=auth,
    )
    assert client.get("/measurement").json()["total_coverage_buckets"] == 2
    # a later, smaller report replaces the stored profile
    client.post(
        "/profile", json={"worker_id": "w1", "buckets": [_bucket(1, 10, 0, 0)]}, headers=auth
    )
    m = client.get("/measurement").json()
    assert m["total_coverage_buckets"] == 1
    assert m["cpu"]["avg"] == 10.0


# --- multiple devices --------------------------------------------------------


def test_measurement_aggregates_multiple_devices_equally() -> None:
    client = TestClient(create_app(":memory:"))
    a = _register(client, "a")
    b = _register(client, "b")
    client.post("/profile", json={"worker_id": "a", "buckets": [_bucket(5, 20, 0, 30)]}, headers=a)
    client.post("/profile", json={"worker_id": "b", "buckets": [_bucket(5, 40, 0, 50)]}, headers=b)
    m = client.get("/measurement").json()
    assert m["device_count"] == 2
    # per-device spares 55, 35 -> mean 45 -> recoverable 9 .. 18
    assert abs(m["cpu"]["recoverable_low"] - 9.0) < 1e-6
    assert abs(m["cpu"]["recoverable_high"] - 18.0) < 1e-6
    assert abs(m["ram_avg"] - 40.0) < 1e-6


def test_worker_with_only_empty_buckets_does_not_count_as_a_device() -> None:
    client = TestClient(create_app(":memory:"))
    live = _register(client, "live")
    idle = _register(client, "idle")
    client.post(
        "/profile", json={"worker_id": "live", "buckets": [_bucket(5, 20, 0, 0)]}, headers=live
    )
    # idle worker reports only unpopulated buckets -> stored coverage 0
    resp = client.post(
        "/profile",
        json={"worker_id": "idle", "buckets": [_bucket(5, 0, 0, 0, n=0)]},
        headers=idle,
    )
    assert resp.json()["buckets_stored"] == 0
    m = client.get("/measurement").json()
    assert m["device_count"] == 1  # only the live device contributes
