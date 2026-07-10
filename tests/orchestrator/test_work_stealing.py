"""Work-stealing via over-decomposition (docs/work-stealing.md).

The launcher can carve a workload into MORE, SMALLER tiles than there are workers (``oversubscribe``).
Those tiles are ordinary queued jobs the existing pull queue distributes, so an idle/fast machine keeps
pulling the next tile while a slow machine only ever holds one small tile, and a dropped tile requeues
to whoever is free. These tests prove:

- ``oversubscribe=1`` (the default) is byte-identical to today's tile count -- nothing regresses;
- ``oversubscribe=k`` produces ~k x worker-count tiles that partition the work exactly, deterministically;
- an explicit ``n_tiles`` is respected verbatim (it wins over the worker-count computation);
- a fast worker steals strictly more tiles than a slow one, and every tile completes exactly once;
- a worker that leases a tile then disappears has that tile requeued (lease reaping) so a peer finishes it.
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from orchestrator.app import create_app
from orchestrator.db import connect, write_lock

_FRACTAL_PARAMS = {"width": 32, "height": 48, "max_iter": 40}


def _register(client: TestClient, worker_id: str, **cap) -> str:
    """Register (and auto-approve) a worker; return its bearer token."""
    resp = client.post("/register", json={"worker_id": worker_id, "cpus": 4, **cap})
    assert resp.status_code == 200
    return resp.json()["worker_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _launch(client: TestClient, **body) -> dict:
    """Launch a fractal workload and return its full view (jobs + units)."""
    body.setdefault("kind", "fractal")
    body.setdefault("params", dict(_FRACTAL_PARAMS))
    resp = client.post("/workloads", json=body)
    assert resp.status_code == 200, resp.text
    wl = resp.json()
    view = client.get(f"/workloads/{wl['workload_id']}").json()
    return view


def _poll(client: TestClient, worker_id: str, token: str):
    return client.get("/jobs/next", params={"worker_id": worker_id}, headers=_auth(token))


def _complete(client: TestClient, worker_id: str, token: str, job_id: str) -> dict:
    return client.post(
        f"/results/{job_id}",
        json={"worker_id": worker_id, "job_id": job_id, "status": "completed", "output": {"ok": 1}},
        headers=_auth(token),
    ).json()


# --- tile-count resolution --------------------------------------------------------------------


def test_default_oversubscribe_is_byte_identical_to_today(tmp_path):
    """oversubscribe defaults to 1: an explicit n_tiles launch is unchanged, tile-for-tile."""
    client = TestClient(create_app(str(tmp_path / "a.db")))
    _register(client, "w1")
    _register(client, "w2")

    baseline = _launch(client, n_tiles=3)
    explicit_one = _launch(client, n_tiles=3, oversubscribe=1)

    assert len(baseline["jobs"]) == 3
    assert [j["units"] for j in baseline["jobs"]] == [j["units"] for j in explicit_one["jobs"]]


def test_explicit_n_tiles_wins_over_oversubscribe(tmp_path):
    """A caller who pins n_tiles gets exactly that many tiles, even alongside oversubscribe."""
    client = TestClient(create_app(str(tmp_path / "b.db")))
    _register(client, "w1")
    _register(client, "w2")

    view = _launch(client, n_tiles=2, oversubscribe=5)
    assert len(view["jobs"]) == 2


def test_oversubscribe_over_decomposes_into_k_times_worker_count(tmp_path):
    """oversubscribe=k with no explicit n_tiles => ~k x (approved worker count) tiles, exact split."""
    client = TestClient(create_app(str(tmp_path / "c.db")))
    for i in range(3):
        _register(client, f"w{i}")

    view = _launch(client, oversubscribe=4, params={"width": 32, "height": 120, "max_iter": 40})
    units = [j["units"] for j in view["jobs"]]

    assert len(units) == 12  # 3 workers x 4
    assert sum(units) == 120  # rows partitioned exactly, nothing lost
    assert all(u >= 1 for u in units)  # every small tile carries work


def test_oversubscribe_is_deterministic(tmp_path):
    """Two identical over-decomposed launches produce the identical per-tile unit split."""
    client = TestClient(create_app(str(tmp_path / "d.db")))
    for i in range(2):
        _register(client, f"w{i}")

    params = {"width": 32, "height": 100, "max_iter": 40}
    first = _launch(client, oversubscribe=5, params=params)
    second = _launch(client, oversubscribe=5, params=params)
    assert [j["units"] for j in first["jobs"]] == [j["units"] for j in second["jobs"]]
    assert len(first["jobs"]) == 10


# --- the work-stealing itself -----------------------------------------------------------------


def test_fast_worker_steals_more_tiles_and_every_tile_completes_once(tmp_path):
    """A fast worker drains most tiles while a slow worker holds one; load flows to the idle machine.

    Both workers see the same over-decomposed queue. The slow worker leases exactly one small tile and
    sits on it; the fast worker keeps pulling and completing the rest. This is the work-stealing: the
    idle machine naturally acquires more work purely through the existing pull queue, and no tile is
    lost or run twice.
    """
    client = TestClient(create_app(str(tmp_path / "steal.db")))
    fast_token = _register(client, "fast")
    slow_token = _register(client, "slow")

    view = _launch(client, oversubscribe=4)  # 2 workers x 4 => 8 tiles
    total_tiles = len(view["jobs"])
    assert total_tiles == 8

    # The slow worker leases one tile and holds it (never reports until the very end).
    slow_first = _poll(client, "slow", slow_token)
    assert slow_first.status_code == 200
    slow_job = slow_first.json()["signed_manifest"]["manifest"]["job_id"]

    # The fast worker drains everything else, one lease at a time (one-lease-per-worker guard).
    completed_by_fast: list[str] = []
    for _ in range(total_tiles + 2):  # generous bound; loop exits on 204
        resp = _poll(client, "fast", fast_token)
        if resp.status_code == 204:
            break
        job_id = resp.json()["signed_manifest"]["manifest"]["job_id"]
        assert _complete(client, "fast", fast_token, job_id)["accepted"] is True
        completed_by_fast.append(job_id)

    # The slow worker finally finishes its single tile.
    assert _complete(client, "slow", slow_token, slow_job)["accepted"] is True

    fast_count = len(completed_by_fast)
    assert fast_count == total_tiles - 1  # fast did every tile except the one slow held
    assert fast_count > 1  # strictly more than the slow worker's single tile

    # Every tile completed exactly once: no lost or duplicated work.
    all_completed = set(completed_by_fast) | {slow_job}
    assert len(all_completed) == total_tiles
    final = client.get(f"/workloads/{view['workload_id']}").json()
    assert final["total"] == total_tiles
    assert final["completed"] == total_tiles


def test_dropped_worker_tile_is_requeued_and_finished_by_a_peer(tmp_path):
    """A worker leases a tile then vanishes; lease reaping requeues it so a peer completes the workload."""
    db_path = str(tmp_path / "drop.db")
    client = TestClient(create_app(db_path))
    gone_token = _register(client, "gone")  # will lease a tile then disappear
    survivor_token = _register(client, "survivor")

    view = _launch(client, n_tiles=2, params={"width": 32, "height": 40, "max_iter": 40})
    assert len(view["jobs"]) == 2

    # The doomed worker leases one tile, then goes away without ever reporting.
    dropped = _poll(client, "gone", gone_token)
    assert dropped.status_code == 200
    dropped_job = dropped.json()["signed_manifest"]["manifest"]["job_id"]

    # Simulate the machine dropping off: force its lease to have already expired.
    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    conn = connect(db_path)
    with write_lock:
        conn.execute("UPDATE jobs SET lease_expires = ? WHERE job_id = ?", (expired, dropped_job))
        conn.commit()
    conn.close()

    # The survivor now drains the whole queue: the reaper requeues the dropped tile on the next poll.
    completed: list[str] = []
    for _ in range(6):
        resp = _poll(client, "survivor", survivor_token)
        if resp.status_code == 204:
            break
        job_id = resp.json()["signed_manifest"]["manifest"]["job_id"]
        assert _complete(client, "survivor", survivor_token, job_id)["accepted"] is True
        completed.append(job_id)

    assert dropped_job in completed  # the stolen-back tile was finished by the peer
    final = client.get(f"/workloads/{view['workload_id']}").json()
    assert final["total"] == 2
    assert final["completed"] == 2  # the workload still finished despite the drop
