"""GPU jobs must run host-side (real device), never in a Docker container -- even when the
Docker daemon is up. These tests pin that routing decision."""
from __future__ import annotations

from isolation import runner


def test_host_side_skips_docker_even_when_available(monkeypatch):
    monkeypatch.setattr(runner, "docker_available", lambda *a, **k: True)

    def boom(*a, **k):
        raise AssertionError("docker path was used for a host_side=True job")

    monkeypatch.setattr(runner, "_run_docker", boom)
    out = runner.run_in_isolation("challenge", {"x": 6}, host_side=True)
    assert out == {"y": 37}  # ran on the host subprocess+Job-Object path


def test_non_host_side_still_uses_docker_when_available(monkeypatch):
    monkeypatch.setattr(runner, "docker_available", lambda *a, **k: True)
    called = {"docker": False}

    def fake_docker(in_path, out_path, work_dir, limits, should_yield):
        called["docker"] = True
        return {"y": 37}

    monkeypatch.setattr(runner, "_run_docker", fake_docker)
    out = runner.run_in_isolation("challenge", {"x": 6}, host_side=False)
    assert called["docker"] is True
    assert out == {"y": 37}


def test_subprocess_env_forwards_cuda(monkeypatch):
    # A host-side GPU job must see CUDA env so real CUDA engages on an NVIDIA worker.
    monkeypatch.setenv("CUDA_PATH", "C:\\fake\\cuda")
    monkeypatch.setenv("CUDA_PATH_V12_4", "C:\\fake\\cuda12")
    monkeypatch.setenv("CUPY_CACHE_DIR", "C:\\fake\\cupy-cache")
    env = runner._subprocess_env()
    assert env.get("CUDA_PATH") == "C:\\fake\\cuda"
    assert env.get("CUDA_PATH_V12_4") == "C:\\fake\\cuda12"
    assert env.get("CUPY_CACHE_DIR") == "C:\\fake\\cupy-cache"
    assert "PYTHONPATH" in env
