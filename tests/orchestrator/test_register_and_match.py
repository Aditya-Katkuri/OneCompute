from fastapi.testclient import TestClient

from orchestrator.app import create_app


def test_gpu_job_only_matches_gpu_worker():
    client = TestClient(create_app(":memory:"))
    gpu_cap = {
        "worker_id": "gpu-1",
        "cpus": 8,
        "has_gpu": True,
        "gpu_vram_gb": 8,
        "accel": ["cuda"],
    }
    cpu_cap = {"worker_id": "cpu-1", "cpus": 8, "has_gpu": False}

    gpu_token = client.post("/register", json=gpu_cap).json()["worker_token"]
    cpu_token = client.post("/register", json=cpu_cap).json()["worker_token"]
    submit = client.post(
        "/jobs",
        json={
            "kind": "data.transform",
            "input": {"items": ["a"], "op": "upper"},
            "requires": {"needs_gpu": True, "accel": ["cuda"], "min_vram_gb": 4},
            "units": 1,
        },
    )
    assert submit.status_code == 200
    job_id = submit.json()["job_id"]

    cpu_next = client.get(
        "/jobs/next",
        params={"worker_id": "cpu-1"},
        headers={"Authorization": f"Bearer {cpu_token}"},
    )
    assert cpu_next.status_code == 204

    gpu_next = client.get(
        "/jobs/next",
        params={"worker_id": "gpu-1"},
        headers={"Authorization": f"Bearer {gpu_token}"},
    )
    assert gpu_next.status_code == 200
    assert gpu_next.json()["signed_manifest"]["manifest"]["job_id"] == job_id


def test_submit_rejects_non_positive_units():
    client = TestClient(create_app(":memory:"))
    response = client.post("/jobs", json={"kind": "challenge", "input": {"x": 1}, "units": 0})
    assert response.status_code == 400
