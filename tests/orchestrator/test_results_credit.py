from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from orchestrator.app import create_app
from orchestrator.db import connect, write_lock


def test_completed_result_credits_gpu_weight():
    client = TestClient(create_app(":memory:"))
    register = client.post(
        "/register",
        json={
            "worker_id": "gpu-1",
            "cpus": 8,
            "has_gpu": True,
            "gpu_vram_gb": 8,
            "accel": ["cuda"],
        },
    )
    assert register.status_code == 200
    # Elevate to 'managed' so the default (internal) job routes; fresh workers default to the
    # fail-closed 'untrusted' tier (see docs/routing-policy.md). GPU credit weighting is unchanged.
    assert client.post(
        "/workers/gpu-1/tier", json={"trust_tier": "managed"}
    ).status_code == 200
    auth = {"Authorization": f"Bearer {register.json()['worker_token']}"}
    submit = client.post(
        "/jobs",
        json={"kind": "challenge", "input": {"x": 3}, "requires": {"needs_gpu": True}, "units": 3},
    )
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assignment = client.get("/jobs/next", params={"worker_id": "gpu-1"}, headers=auth)
    assert assignment.status_code == 200

    result = client.post(
        f"/results/{job_id}",
        json={
            "worker_id": "gpu-1",
            "job_id": job_id,
            "status": "completed",
            "output": {"y": 10},
            "units": 3,
        },
        headers=auth,
    )
    assert result.status_code == 200
    assert result.json()["accepted"] is True
    assert result.json()["credited"] == 15.0

    state = client.get("/state")
    assert state.status_code == 200
    assert state.json()["total_credits"] == 15.0


def test_results_require_lease_owner_and_do_not_double_credit():
    client = TestClient(create_app(":memory:"))
    owner_token = client.post("/register", json={"worker_id": "owner", "cpus": 2}).json()[
        "worker_token"
    ]
    other_token = client.post("/register", json={"worker_id": "other", "cpus": 2}).json()[
        "worker_token"
    ]
    # Elevate both to 'managed' so the default (internal) job routes; fresh workers default to the
    # fail-closed 'untrusted' tier (see docs/routing-policy.md).
    for _wid in ("owner", "other"):
        assert client.post(
            f"/workers/{_wid}/tier", json={"trust_tier": "managed"}
        ).status_code == 200
    owner_auth = {"Authorization": f"Bearer {owner_token}"}
    other_auth = {"Authorization": f"Bearer {other_token}"}
    submit = client.post(
        "/jobs",
        json={"kind": "challenge", "input": {"x": 2}, "units": 2},
    )
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert client.get(
        "/jobs/next",
        params={"worker_id": "owner"},
        headers=owner_auth,
    ).status_code == 200

    stolen = client.post(
        f"/results/{job_id}",
        json={"worker_id": "other", "job_id": job_id, "status": "completed", "units": 99},
        headers=other_auth,
    )
    assert stolen.status_code == 200
    assert stolen.json() == {"accepted": False, "credited": 0.0, "reason": "not_leased"}

    accepted = client.post(
        f"/results/{job_id}",
        json={
            "worker_id": "owner",
            "job_id": job_id,
            "status": "completed",
            "output": {"y": 5},
            "units": 99,
        },
        headers=owner_auth,
    )
    assert accepted.status_code == 200
    assert accepted.json()["credited"] == 2.0

    duplicate = client.post(
        f"/results/{job_id}",
        json={
            "worker_id": "owner",
            "job_id": job_id,
            "status": "completed",
            "output": {"y": 5},
            "units": 99,
        },
        headers=owner_auth,
    )
    assert duplicate.status_code == 200
    assert duplicate.json() == {"accepted": False, "credited": 0.0, "reason": "not_leased"}
    assert client.get("/state").json()["total_credits"] == 2.0


def test_expired_result_is_requeued_without_credit(tmp_path):
    db_path = str(tmp_path / "results.db")
    client = TestClient(create_app(db_path))
    token = client.post("/register", json={"worker_id": "worker", "cpus": 2}).json()[
        "worker_token"
    ]
    # Elevate to 'managed' so the default (internal) job routes; fresh workers default to the
    # fail-closed 'untrusted' tier (see docs/routing-policy.md).
    assert client.post(
        "/workers/worker/tier", json={"trust_tier": "managed"}
    ).status_code == 200
    auth = {"Authorization": f"Bearer {token}"}
    submit = client.post("/jobs", json={"kind": "challenge", "input": {"x": 5}, "units": 4})
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert client.get("/jobs/next", params={"worker_id": "worker"}, headers=auth).status_code == 200

    expired = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    conn = connect(db_path)
    with write_lock:
        conn.execute("UPDATE jobs SET lease_expires = ? WHERE job_id = ?", (expired, job_id))
        conn.commit()
    conn.close()

    result = client.post(
        f"/results/{job_id}",
        json={"worker_id": "worker", "job_id": job_id, "status": "completed", "units": 4},
        headers=auth,
    )
    assert result.status_code == 200
    assert result.json() == {"accepted": False, "credited": 0.0, "reason": "lease_expired"}
    state = client.get("/state").json()
    assert state["total_credits"] == 0.0
    assert state["jobs"][0]["state"] == "queued"


def test_invalid_challenge_result_blacklists_the_cheater():
    client = TestClient(create_app(":memory:"))
    token = client.post("/register", json={"worker_id": "worker", "cpus": 2}).json()[
        "worker_token"
    ]
    # Elevate to 'managed' so the default (internal) challenge job routes; fresh workers default to
    # the fail-closed 'untrusted' tier (see docs/routing-policy.md).
    assert client.post(
        "/workers/worker/tier", json={"trust_tier": "managed"}
    ).status_code == 200
    auth = {"Authorization": f"Bearer {token}"}
    submit = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}, "units": 4})
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert client.get("/jobs/next", params={"worker_id": "worker"}, headers=auth).status_code == 200

    result = client.post(
        f"/results/{job_id}",
        json={"worker_id": "worker", "job_id": job_id, "status": "completed", "output": {"y": 999}},
        headers=auth,
    )
    assert result.status_code == 200
    assert result.json() == {"accepted": False, "credited": 0.0, "reason": "cheater_blacklisted"}
    state = client.get("/state").json()
    assert state["total_credits"] == 0.0
    assert state["jobs"][0]["state"] == "queued"
    # the cheater is blacklisted and earns nothing
    worker_view = next(w for w in state["workers"] if w["worker_id"] == "worker")
    assert worker_view["blacklisted"] is True
    assert worker_view["credits"] == 0.0


def test_self_reported_gpu_does_not_inflate_credit_on_a_cpu_job():
    # A worker can self-report has_gpu=True (unverified), but credit rewards the JOB's actual GPU
    # requirement, not the worker's claim: a CPU job (needs_gpu unset) credits 1x, never 5x.
    client = TestClient(create_app(":memory:"))
    scheme = "Bearer"
    token = client.post(
        "/register",
        json={"worker_id": "faker", "cpus": 8, "has_gpu": True, "accel": ["cuda"]},
    ).json()["worker_token"]
    # Elevate to 'managed' so the default (internal) CPU job routes; fresh workers default to the
    # fail-closed 'untrusted' tier (see docs/routing-policy.md). Credit weighting is unchanged.
    assert client.post(
        "/workers/faker/tier", json={"trust_tier": "managed"}
    ).status_code == 200
    auth = {"Authorization": f"{scheme} {token}"}
    submit = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}, "units": 4})
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]
    assert client.get("/jobs/next", params={"worker_id": "faker"}, headers=auth).status_code == 200

    result = client.post(
        f"/results/{job_id}",
        json={
            "worker_id": "faker",
            "job_id": job_id,
            "status": "completed",
            "output": {"y": 10},  # challenge x=3 -> y = 3*3+1 = 10, passes verification
            "units": 4,
        },
        headers=auth,
    )
    assert result.status_code == 200
    assert result.json()["accepted"] is True
    # 4 units x 1 (CPU job), NOT 20 (which is 4 x 5 from the bogus self-reported GPU claim).
    assert result.json()["credited"] == 4.0
    assert client.get("/state").json()["total_credits"] == 4.0

