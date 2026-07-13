from fastapi.testclient import TestClient

from orchestrator.app import create_app


def test_job_routes_only_to_worker_with_enough_ram():
    client = TestClient(create_app(":memory:"))
    small = {"worker_id": "small", "cpus": 8, "ram_gb": 4.0, "has_gpu": False}
    big = {"worker_id": "big", "cpus": 8, "ram_gb": 32.0, "has_gpu": False}
    small_token = client.post("/register", json=small).json()["worker_token"]
    big_token = client.post("/register", json=big).json()["worker_token"]
    # Elevate both to 'managed' so the default (internal) job is routable; fresh workers default to
    # the fail-closed 'untrusted' tier (see docs/routing-policy.md). The RAM gate still applies.
    for _wid in ("small", "big"):
        assert client.post(
            f"/workers/{_wid}/tier", json={"trust_tier": "managed"}
        ).status_code == 200

    submit = client.post(
        "/jobs",
        json={
            "kind": "data.transform",
            "input": {"items": [1, 2], "op": "square"},
            "requires": {"min_ram_gb": 16},
            "units": 2,
        },
    )
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]

    # the 4 GB worker is skipped...
    assert client.get(
        "/jobs/next",
        params={"worker_id": "small"},
        headers={"Authorization": f"Bearer {small_token}"},
    ).status_code == 204
    # ...the 32 GB worker gets it.
    nxt = client.get(
        "/jobs/next",
        params={"worker_id": "big"},
        headers={"Authorization": f"Bearer {big_token}"},
    )
    assert nxt.status_code == 200
    assert nxt.json()["signed_manifest"]["manifest"]["job_id"] == job_id


def test_no_min_ram_matches_any_worker():
    client = TestClient(create_app(":memory:"))
    token = client.post(
        "/register", json={"worker_id": "tiny", "cpus": 2, "ram_gb": 2.0}
    ).json()["worker_token"]
    # Elevate to 'managed' so the default (internal) job routes; fresh workers default to the
    # fail-closed 'untrusted' tier (see docs/routing-policy.md).
    assert client.post(
        "/workers/tiny/tier", json={"trust_tier": "managed"}
    ).status_code == 200
    client.post(
        "/jobs",
        json={"kind": "data.transform", "input": {"items": [1], "op": "square"}, "units": 1},
    )
    # a job with no min_ram_gb still lands on the small worker
    assert client.get(
        "/jobs/next",
        params={"worker_id": "tiny"},
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 200
