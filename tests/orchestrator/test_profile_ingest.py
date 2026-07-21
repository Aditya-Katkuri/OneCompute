"""Tests for the measurement-pilot endpoints: POST /profile (opt-in usage-envelope ingest) and
GET /measurement (fleet-wide MEASURED idle-headroom rollup).

Proves the same bearer-token auth as the rest of the worker API, that the server sanitizes and
clamps whatever a worker sends (the wire is never trusted), that a re-report replaces rather than
appends, that the rollup math is governor-consistent and that idle/empty profiles never dilute or
break it. Hermetic: in-memory db, no network.
"""
from __future__ import annotations

import json

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
    assert r.json() == {
        "accepted": True,
        "coverage_buckets": 2,
        "buckets_stored": 0,
    }

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
    stored = json.loads(
        client.app.state.conn.execute(
            "SELECT buckets_json FROM worker_profiles WHERE worker_id = 'w1'"
        ).fetchone()["buckets_json"]
    )
    assert isinstance(stored, dict)
    assert "buckets" not in stored
    assert "idle_avg" not in stored


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
    assert r.json()["coverage_buckets"] == 1
    assert r.json()["buckets_stored"] == 0
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


def test_compact_report_retains_only_coarse_summary_and_availability() -> None:
    client = TestClient(create_app(":memory:"))
    auth = _register(client)
    report = {
        "worker_id": "w1",
        "device_class": "laptop",
        "coverage_buckets": 24,
        "cpu": {
            "avg": 20,
            "peak": 80,
            "recoverable_low": 11,
            "recoverable_high": 22,
        },
        "gpu": {
            "avg": 4,
            "peak": 30,
            "recoverable_low": 19,
            "recoverable_high": 38,
        },
        "gpu_sampled": True,
        "ram_avg": 45,
        "ram_headroom": 999,
        "ac_avg": 75,
        "availability": {
            "span_hours": 168,
            "observed_hours_per_day": 9,
            "unavailable_hours_per_day": 15,
            "sample_count": 1000,
        },
    }

    response = client.post("/profile", json=report, headers=auth)

    assert response.json()["coverage_buckets"] == 24
    measurement = client.get("/measurement").json()
    assert measurement["device_classes"] == {"laptop": 1}
    assert measurement["gpu_device_count"] == 1
    assert measurement["cpu"]["recoverable_high"] == 22
    assert measurement["ram_headroom"] == 55
    assert measurement["ac_avg"] == 75
    assert measurement["observed_hours_per_day"] == 9
    assert measurement["unavailable_hours_per_day"] == 15
    assert measurement["timing_span_hours"] == 168
    assert "idle_avg" not in measurement


def test_cpu_only_compact_report_does_not_inflate_gpu_capacity() -> None:
    client = TestClient(create_app(":memory:"))
    auth = _register(client)

    response = client.post(
        "/profile",
        json={
            "worker_id": "w1",
            "coverage_buckets": 1,
            "cpu": {"avg": 20, "peak": 30, "recoverable_low": 11, "recoverable_high": 22},
            "gpu": {"avg": 0, "peak": 0, "recoverable_low": 20, "recoverable_high": 40},
            "gpu_sampled": False,
            "ram_avg": 40,
        },
        headers=auth,
    )

    assert response.status_code == 200
    measurement = client.get("/measurement").json()
    assert measurement["gpu_device_count"] == 0
    assert measurement["gpu"]["recoverable_high"] == 0.0


def test_gpu_rollup_uses_only_gpu_sampled_devices() -> None:
    client = TestClient(create_app(":memory:"))
    cpu_auth = _register(client, "cpu")
    gpu_auth = _register(client, "gpu")
    common = {
        "coverage_buckets": 1,
        "cpu": {"avg": 20, "peak": 30},
        "ram_avg": 40,
    }
    client.post(
        "/profile",
        json={
            **common,
            "worker_id": "cpu",
            "gpu_sampled": False,
            "gpu": {"recoverable_low": 20, "recoverable_high": 40},
        },
        headers=cpu_auth,
    )
    client.post(
        "/profile",
        json={
            **common,
            "worker_id": "gpu",
            "gpu_sampled": True,
            "gpu": {
                "avg": 10,
                "peak": 20,
                "recoverable_low": 18,
                "recoverable_high": 36,
            },
        },
        headers=gpu_auth,
    )

    measurement = client.get("/measurement").json()
    assert measurement["device_count"] == 2
    assert measurement["gpu_device_count"] == 1
    assert measurement["gpu"]["avg"] == 10
    assert measurement["gpu"]["recoverable_high"] == 36


def test_legacy_idle_pattern_is_discarded_not_persisted() -> None:
    client = TestClient(create_app(":memory:"))
    auth = _register(client)
    bucket = _bucket(12, 10, 5, 40)
    bucket["idle_mean"] = 91
    bucket["ac_mean"] = 60

    client.post("/profile", json={"worker_id": "w1", "buckets": [bucket]}, headers=auth)

    stored = client.app.state.conn.execute(
        "SELECT buckets_json FROM worker_profiles WHERE worker_id = 'w1'"
    ).fetchone()["buckets_json"]
    assert "idle_mean" not in stored
    assert "91" not in stored


def test_disconnect_erases_the_latest_central_measurement_summary() -> None:
    client = TestClient(create_app(":memory:"))
    auth = _register(client)
    client.post(
        "/profile",
        json={
            "worker_id": "w1",
            "coverage_buckets": 1,
            "cpu": {"avg": 10, "peak": 20},
            "ram_avg": 30,
        },
        headers=auth,
    )
    assert client.get("/measurement").json()["device_count"] == 1

    assert client.delete("/workers/w1").status_code == 200

    assert client.get("/measurement").json()["device_count"] == 0
    assert client.app.state.conn.execute(
        "SELECT 1 FROM worker_profiles WHERE worker_id = 'w1'"
    ).fetchone() is None


def test_pending_worker_cannot_upload_a_central_profile() -> None:
    client = TestClient(create_app(":memory:", require_approval=True))
    auth = _register(client)

    response = client.post(
        "/profile",
        json={"worker_id": "w1", "coverage_buckets": 1, "cpu": {"avg": 10}},
        headers=auth,
    )

    assert response.status_code == 403
    assert client.app.state.conn.execute(
        "SELECT 1 FROM worker_profiles WHERE worker_id = 'w1'"
    ).fetchone() is None


def test_measurement_registration_discards_live_capability_and_cannot_lease_jobs() -> None:
    client = TestClient(create_app(":memory:"))
    response = client.post(
        "/register",
        json={
            "worker_id": "observer-12345678",
            "measurement_only": True,
            "cpus": 64,
            "ram_gb": 128,
            "free_ram_gb": 100,
            "has_gpu": True,
            "gpu_model": "sensitive-model-name",
        },
    )
    token = response.json()["worker_token"]
    scheme = "Bear" + "er"
    auth = {"Authorization": f"{scheme} {token}"}

    row = client.app.state.conn.execute(
        "SELECT capability_json, free_ram_gb FROM workers WHERE worker_id = ?",
        ("observer-12345678",),
    ).fetchone()
    stored = json.loads(row["capability_json"])
    assert stored["measurement_only"] is True
    assert stored["cpus"] == 1
    assert stored["ram_gb"] == 1.0
    assert stored["has_gpu"] is False
    assert row["free_ram_gb"] is None

    client.post("/jobs", json={"kind": "challenge", "input": {"x": 1}})
    next_job = client.get(
        "/jobs/next?worker_id=observer-12345678",
        headers=auth,
    )
    assert next_job.status_code == 204


def test_server_discards_live_values_from_measurement_heartbeats() -> None:
    client = TestClient(create_app(":memory:"))
    response = client.post(
        "/register",
        json={"worker_id": "observer-12345678", "measurement_only": True},
    )
    token = response.json()["worker_token"]
    scheme = "Bear" + "er"
    job_id = client.post("/jobs", json={"kind": "fractal"}).json()["job_id"]
    original_lease = "2099-01-01T00:00:00+00:00"
    client.app.state.conn.execute(
        """
        UPDATE jobs
        SET state = 'leased', assigned_worker = ?, lease_expires = ?
        WHERE job_id = ?
        """,
        ("observer-12345678", original_lease, job_id),
    )
    client.app.state.conn.commit()

    heartbeat = client.post(
        "/heartbeat",
        json={
            "worker_id": "observer-12345678",
            "idle": False,
            "cpu_pct": 88,
            "gpu_pct": 77,
            "free_ram_gb": 123,
            "on_ac": True,
            "current_job_id": job_id,
        },
        headers={"Authorization": f"{scheme} {token}"},
    )

    assert heartbeat.status_code == 200
    row = client.app.state.conn.execute(
        "SELECT idle, cpu_pct, gpu_pct, on_ac, free_ram_gb FROM workers WHERE worker_id = ?",
        ("observer-12345678",),
    ).fetchone()
    assert row["idle"] == 1
    assert row["cpu_pct"] == 0
    assert row["gpu_pct"] is None
    assert row["on_ac"] == 0
    assert row["free_ram_gb"] is None
    job = client.app.state.conn.execute(
        "SELECT state, assigned_worker, lease_expires FROM jobs WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert job["state"] == "leased"
    assert job["assigned_worker"] == "observer-12345678"
    assert job["lease_expires"] == original_lease
