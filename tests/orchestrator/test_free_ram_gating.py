"""Free-RAM gating: min_ram_gb is matched against LIVE free RAM, not total."""

from fastapi.testclient import TestClient

from orchestrator.app import create_app


def test_min_ram_gates_on_live_free_ram_not_total():
    client = TestClient(create_app(":memory:"))
    # Two 32 GB machines: one currently busy (low free RAM), one with ample free RAM.
    busy_token = client.post(
        "/register", json={"worker_id": "busy-big", "cpus": 8, "ram_gb": 32.0}
    ).json()["worker_token"]
    # Elevate to 'managed' so the default (internal) job is routable (fresh workers default to the
    # fail-closed 'untrusted' tier); the live free-RAM gate still applies. See docs/routing-policy.md.
    assert client.post(
        "/workers/busy-big/tier", json={"trust_tier": "managed"}
    ).status_code == 200
    client.post(
        "/heartbeat",
        json={"worker_id": "busy-big", "free_ram_gb": 2.0},
        headers={"Authorization": f"Bearer {busy_token}"},
    )
    free_token = client.post(
        "/register", json={"worker_id": "free-big", "cpus": 8, "ram_gb": 32.0}
    ).json()["worker_token"]
    assert client.post(
        "/workers/free-big/tier", json={"trust_tier": "managed"}
    ).status_code == 200
    client.post(
        "/heartbeat",
        json={"worker_id": "free-big", "free_ram_gb": 24.0},
        headers={"Authorization": f"Bearer {free_token}"},
    )

    submit = client.post(
        "/jobs",
        json={
            "kind": "data.transform",
            "input": {"items": [1], "op": "square"},
            "requires": {"min_ram_gb": 16},
            "units": 1,
        },
    )
    job_id = submit.json()["job_id"]

    # 32 GB total but only 2 GB free -> skipped
    assert client.get(
        "/jobs/next",
        params={"worker_id": "busy-big"},
        headers={"Authorization": f"Bearer {busy_token}"},
    ).status_code == 204
    # 24 GB free -> gets the job
    nxt = client.get(
        "/jobs/next",
        params={"worker_id": "free-big"},
        headers={"Authorization": f"Bearer {free_token}"},
    )
    assert nxt.status_code == 200
    assert nxt.json()["signed_manifest"]["manifest"]["job_id"] == job_id


def test_free_ram_defaults_to_total_before_first_heartbeat():
    client = TestClient(create_app(":memory:"))
    token = client.post(
        "/register", json={"worker_id": "w", "cpus": 4, "ram_gb": 32.0}
    ).json()["worker_token"]
    # Elevate to 'managed' so the default (internal) job routes; fresh workers default to the
    # fail-closed 'untrusted' tier (see docs/routing-policy.md).
    assert client.post(
        "/workers/w/tier", json={"trust_tier": "managed"}
    ).status_code == 200
    client.post(
        "/jobs",
        json={
            "kind": "data.transform",
            "input": {"items": [1], "op": "square"},
            "requires": {"min_ram_gb": 16},
            "units": 1,
        },
    )
    # No heartbeat yet -> free RAM falls back to total (32) -> the job still lands.
    assert client.get(
        "/jobs/next",
        params={"worker_id": "w"},
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 200


def test_registration_free_ram_snapshot_is_used():
    client = TestClient(create_app(":memory:"))
    # Worker advertises 32 GB total but only 4 GB free at registration time.
    token = client.post(
        "/register",
        json={"worker_id": "tight", "cpus": 8, "ram_gb": 32.0, "free_ram_gb": 4.0},
    ).json()["worker_token"]
    client.post(
        "/jobs",
        json={
            "kind": "data.transform",
            "input": {"items": [1], "op": "square"},
            "requires": {"min_ram_gb": 16},
            "units": 1,
        },
    )
    # Only 4 GB free at registration -> skipped even before any heartbeat.
    assert client.get(
        "/jobs/next",
        params={"worker_id": "tight"},
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 204
    # And /state surfaces the live free RAM for the dashboard.
    view = next(w for w in client.get("/state").json()["workers"] if w["worker_id"] == "tight")
    assert view["free_ram_gb"] == 4.0
