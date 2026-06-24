"""Dashboard-readiness API: output retrieval (GET /jobs/{id}, GET /workloads/{id})
and one-call workload launch (POST /workloads)."""

from fastapi.testclient import TestClient

from jobkit.execute import execute
from orchestrator.app import create_app


def _register(client: TestClient, **cap) -> dict[str, str]:
    """Register a worker and return its bearer-auth headers (worker routes are token-gated)."""
    token = client.post("/register", json=cap).json()["worker_token"]
    return {"Authorization": f"Bearer {token}"}


def _complete_next(client: TestClient, worker_id: str, auth: dict[str, str]) -> str | None:
    """Lease the next job for worker_id, run it via jobkit, post the real output.

    Mirrors what a worker does, so the stored output is the genuine executor result.
    Returns the completed job_id, or None when there is no work to lease.
    """
    assignment = client.get("/jobs/next", params={"worker_id": worker_id}, headers=auth)
    if assignment.status_code == 204:
        return None
    data = assignment.json()
    manifest = data["signed_manifest"]["manifest"]
    job_id = manifest["job_id"]
    output = execute(manifest["kind"], data["input"])
    client.post(
        f"/results/{job_id}",
        json={
            "worker_id": worker_id,
            "job_id": job_id,
            "status": "completed",
            "output": output,
            "units": 1,
        },
        headers=auth,
    )
    return job_id


def test_job_detail_returns_stored_output():
    client = TestClient(create_app(":memory:"))
    auth = _register(client, worker_id="w1", cpus=4, ram_gb=8.0)
    job_id = client.post(
        "/jobs",
        json={"kind": "data.transform", "input": {"items": [1, 2, 3], "op": "square"}, "units": 3},
    ).json()["job_id"]

    queued = client.get(f"/jobs/{job_id}")
    assert queued.status_code == 200
    body = queued.json()
    assert body["state"] == "queued" and body["output"] is None and body["workload_id"] is None

    assert _complete_next(client, "w1", auth) == job_id
    done = client.get(f"/jobs/{job_id}").json()
    assert done["state"] == "completed"
    assert done["output"]["results"] == [1, 4, 9]


def test_job_detail_unknown_is_404():
    client = TestClient(create_app(":memory:"))
    assert client.get("/jobs/does-not-exist").status_code == 404


def test_jobs_next_literal_route_still_wins():
    # GET /jobs/next must remain the long-poll, not get swallowed by GET /jobs/{job_id}.
    client = TestClient(create_app(":memory:"))
    auth = _register(client, worker_id="w1", cpus=2)
    assert client.get("/jobs/next", params={"worker_id": "w1"}, headers=auth).status_code == 204


def test_launch_workload_splits_and_tags():
    client = TestClient(create_app(":memory:"))
    resp = client.post(
        "/workloads",
        json={"kind": "fractal", "n_tiles": 3, "params": {"width": 40, "height": 30, "max_iter": 40}},
    )
    assert resp.status_code == 200
    body = resp.json()
    workload_id = body["workload_id"]
    assert body["kind"] == "fractal" and len(body["job_ids"]) == 3

    view = client.get(f"/workloads/{workload_id}").json()
    assert view["total"] == 3 and view["completed"] == 0
    assert all(job["workload_id"] == workload_id for job in view["jobs"])
    for job_id in body["job_ids"]:
        assert client.get(f"/jobs/{job_id}").json()["workload_id"] == workload_id


def test_launch_workload_outputs_retrievable():
    client = TestClient(create_app(":memory:"))
    auth = _register(client, worker_id="w1", cpus=4, ram_gb=8.0)
    body = client.post(
        "/workloads",
        json={"kind": "fractal", "n_tiles": 3, "params": {"width": 30, "height": 24, "max_iter": 30}},
    ).json()
    workload_id = body["workload_id"]

    for _ in range(10):
        if _complete_next(client, "w1", auth) is None:
            break

    view = client.get(f"/workloads/{workload_id}").json()
    assert view["completed"] == 3
    rows_total = 0
    for job in view["jobs"]:
        assert job["output"] is not None and "rows" in job["output"]
        rows_total += len(job["output"]["rows"])
    assert rows_total == 24  # the tiles reassemble to the full image height


def test_launch_workload_rejects_bad_kind():
    client = TestClient(create_app(":memory:"))
    assert client.post("/workloads", json={"kind": "bogus"}).status_code == 400


def test_workload_unknown_is_404():
    client = TestClient(create_app(":memory:"))
    assert client.get("/workloads/nope").status_code == 404


def test_workloads_catalog_lists_launchable_examples():
    from contracts import LAUNCHABLE_KINDS

    client = TestClient(create_app(":memory:"))
    resp = client.get("/workloads/catalog")
    assert resp.status_code == 200  # not shadowed by /workloads/{workload_id}
    catalog = resp.json()["workloads"]
    assert len(catalog) >= 4
    kinds = {entry["kind"] for entry in catalog}
    assert {"fractal", "optimize", "ai.batch_infer", "ai.synth"}.issubset(kinds)
    for entry in catalog:
        assert entry["kind"] in LAUNCHABLE_KINDS
        assert {"label", "category", "default_params", "split"} <= set(entry)
    # every catalog entry is actually launchable end to end
    for entry in catalog:
        launched = client.post(
            "/workloads", json={"kind": entry["kind"], "n_tiles": 2, "params": entry["default_params"]}
        )
        assert launched.status_code == 200


def test_heartbeat_usage_is_exposed_in_state():
    # The dashboard's per-device usage graph reads cpu_pct/gpu_pct/free_ram_gb from /state;
    # confirm a heartbeat carrying live usage is reflected there.
    client = TestClient(create_app(":memory:"))
    auth = _register(client, worker_id="gpu-1", cpus=8, has_gpu=True)
    hb = client.post(
        "/heartbeat",
        json={"worker_id": "gpu-1", "idle": True, "cpu_pct": 57.5, "gpu_pct": 81.0, "free_ram_gb": 12.3},
        headers=auth,
    )
    assert hb.status_code == 200
    worker = next(w for w in client.get("/state").json()["workers"] if w["worker_id"] == "gpu-1")
    assert worker["cpu_pct"] == 57.5 and worker["gpu_pct"] == 81.0 and worker["free_ram_gb"] == 12.3
