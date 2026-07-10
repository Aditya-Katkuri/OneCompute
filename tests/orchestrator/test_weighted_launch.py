"""Capability-weighted workload launch: a heavier / idler machine is handed a larger tile.

The launcher reads the live approved worker rows, turns each into a positive weight (capability x
idle headroom x free RAM) and, when there is one worker per tile and the fleet is heterogeneous,
sizes the tiles proportionally. A homogeneous fleet keeps the original uniform split.
"""

from fastapi.testclient import TestClient

from orchestrator.app import create_app
from workloads.partition import weighted_ranges, worker_weight


def _register(client: TestClient, **cap) -> None:
    """Register (and auto-approve) a worker; we only care about the row it leaves behind."""
    assert client.post("/register", json=cap).status_code == 200


def _launch_fractal_units(client: TestClient, n_tiles: int, height: int) -> list[int]:
    """Launch a fractal workload and return the per-tile row counts (job ``units``)."""
    body = client.post(
        "/workloads",
        json={
            "kind": "fractal",
            "n_tiles": n_tiles,
            "params": {"width": 40, "height": height, "max_iter": 40},
        },
    ).json()
    view = client.get(f"/workloads/{body['workload_id']}").json()
    return [job["units"] for job in view["jobs"]]


def test_heavier_worker_gets_a_larger_tile():
    client = TestClient(create_app(":memory:"))
    # w-a: GPU class (weight 5) with ample free RAM; w-b: CPU class (weight 1), RAM-tight.
    _register(client, worker_id="w-a", cpus=8, ram_gb=32.0, free_ram_gb=16.0, has_gpu=True)
    _register(client, worker_id="w-b", cpus=2, ram_gb=4.0, free_ram_gb=2.0, has_gpu=False)

    height = 100
    units = _launch_fractal_units(client, n_tiles=2, height=height)

    assert sum(units) == height  # exact coverage, no rows lost
    assert max(units) > min(units)  # weighting actually kicked in (not a uniform 50/50)

    # The tile sizes match what the capability weights predict, and the heavier worker's tile is
    # the larger one. Workers are ordered by worker_id, so w-a (heavier) maps to tile 0.
    w_a = worker_weight(5.0, free_ram_gb=16.0, load_pct=0.0)
    w_b = worker_weight(1.0, free_ram_gb=2.0, load_pct=0.0)
    expected = [end - start for start, end in weighted_ranges(height, [w_a, w_b])]
    assert sorted(units) == sorted(expected)
    assert max(expected) == expected[0]  # heavier worker's tile is the biggest


def test_homogeneous_fleet_keeps_uniform_split():
    client = TestClient(create_app(":memory:"))
    # Two identical workers -> equal weights -> the launcher falls back to the even split.
    _register(client, worker_id="w1", cpus=4, ram_gb=8.0, free_ram_gb=8.0, has_gpu=False)
    _register(client, worker_id="w2", cpus=4, ram_gb=8.0, free_ram_gb=8.0, has_gpu=False)

    units = _launch_fractal_units(client, n_tiles=2, height=100)
    assert units == [50, 50]  # uniform, exactly as before the weighting change


def test_no_workers_falls_back_to_uniform():
    client = TestClient(create_app(":memory:"))
    # No registered workers: nothing to weight by, so the classic even split is used.
    units = _launch_fractal_units(client, n_tiles=3, height=99)
    assert units == [33, 33, 33]


def test_tile_count_mismatch_falls_back_to_uniform():
    client = TestClient(create_app(":memory:"))
    # One worker but three tiles: no clean one-per-machine mapping, so keep the even split.
    _register(client, worker_id="w-a", cpus=8, ram_gb=32.0, free_ram_gb=16.0, has_gpu=True)
    units = _launch_fractal_units(client, n_tiles=3, height=99)
    assert units == [33, 33, 33]


def test_every_tile_gets_work_even_when_heavily_skewed():
    client = TestClient(create_app(":memory:"))
    _register(client, worker_id="w-a", cpus=16, ram_gb=64.0, free_ram_gb=32.0, has_gpu=True)
    _register(client, worker_id="w-b", cpus=1, ram_gb=2.0, free_ram_gb=0.5, has_gpu=False)

    units = _launch_fractal_units(client, n_tiles=2, height=50)
    assert sum(units) == 50
    assert all(u >= 1 for u in units)  # the light worker still gets a (small) band
