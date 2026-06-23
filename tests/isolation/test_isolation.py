from pathlib import Path

import pytest

from contracts import Limits
from isolation import (
    active_boundary,
    docker_available,
    isolation_proof,
    run_in_isolation,
)
from isolation.docker import build_docker_command, reset_docker_probe_cache
from isolation.jobobject import close, create_job_object
from isolation.runner import _looks_like_docker_infra_error, _stage_payload


def test_run_challenge():
    assert run_in_isolation("challenge", {"x": 6}) == {"y": 37}


def test_run_data_transform():
    output = run_in_isolation("data.transform", {"items": [1, 2, 3], "op": "square"})
    assert output["results"] == [1, 4, 9]


def test_yield_kills():
    output = run_in_isolation(
        "data.transform",
        {"items": list(range(100000)), "op": "square"},
        should_yield=lambda: True,
    )
    assert output == {"yielded": True, "results": []}


def test_isolation_proof_shape():
    proof = isolation_proof()
    assert isinstance(proof["isolated"], bool)
    assert isinstance(proof["method"], str)


def test_jobobject_import():
    handle = create_job_object(Limits(mem_gb=0.1))
    close(handle)


def test_docker_available_shape():
    assert isinstance(docker_available(), bool)


def test_docker_available_is_cached():
    # First call populates the cache; force re-probes. Both must be the same bool type.
    reset_docker_probe_cache()
    first = docker_available()
    cached = docker_available()
    forced = docker_available(force=True)
    assert first == cached
    assert isinstance(forced, bool)


def test_active_boundary_matches_availability():
    boundary = active_boundary()
    assert boundary in ("docker", "subprocess+jobobject")
    expected = "docker" if docker_available() else "subprocess+jobobject"
    assert boundary == expected


def test_build_docker_command_mounts_only_work_dir(tmp_path: Path):
    cmd = build_docker_command(tmp_path, "in.json", "out.json", Limits(mem_gb=2.0), "nightshift-abc")
    # Container is named so yield/timeout can kill it by name.
    assert "--name" in cmd and "nightshift-abc" in cmd
    # We mount the clean temp work dir as /work and never the OneDrive repo `src`.
    assert f"{tmp_path}:/work" in cmd
    assert not any("/app/src" in part for part in cmd)
    assert "PYTHONPATH=/work/src" in cmd
    # Sealed network + memory cap from limits.
    assert "none" in cmd
    assert "--memory" in cmd and "2.0g" in cmd
    # Runs the file-based jobkit entrypoint inside the slim image.
    assert "python:3.12-slim" in cmd
    assert cmd[-5:] == ["python", "-m", "jobkit", "/work/in.json", "/work/out.json"]


def test_stage_payload_is_stdlib_only(tmp_path: Path):
    _stage_payload(tmp_path)
    contracts_init = tmp_path / "src" / "contracts" / "__init__.py"
    hashing = tmp_path / "src" / "contracts" / "hashing.py"
    execute = tmp_path / "src" / "jobkit" / "execute.py"
    assert contracts_init.exists() and hashing.exists() and execute.exists()
    # The staged contracts shim must NOT pull pydantic into the slim container.
    init_text = contracts_init.read_text(encoding="utf-8")
    assert "import pydantic" not in init_text
    assert "from pydantic" not in init_text


def test_staged_payload_executes_self_contained(tmp_path: Path):
    """The staged jobkit payload runs `python -m jobkit` using ONLY work_dir/src.

    This is the daemon-free proof that what we mount into the container is complete and
    correct: with PYTHONPATH pointed solely at the staged tree, jobkit produces the right
    result -- exactly what `python:3.12-slim` will do inside the container.
    """
    import json
    import os
    import subprocess
    import sys

    _stage_payload(tmp_path)
    in_path = tmp_path / "in.json"
    out_path = tmp_path / "out.json"
    in_path.write_text(
        json.dumps({"kind": "data.transform", "input": {"items": [2, 3], "op": "square"}}),
        encoding="utf-8",
    )
    env = {"PYTHONPATH": str(tmp_path / "src")}
    for key in ("SystemRoot", "PATH", "COMSPEC", "PATHEXT", "WINDIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    proc = subprocess.run(
        [sys.executable, "-m", "jobkit", str(in_path), str(out_path)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out_path.read_text(encoding="utf-8"))["results"] == [4, 9]


def test_docker_infra_error_classifier():
    # Daemon/CLI failures must be classified as infra (-> fall back); job tracebacks must not.
    assert _looks_like_docker_infra_error("Cannot connect to the Docker daemon at npipe://...")
    assert _looks_like_docker_infra_error("request returned 500 Internal Server Error")
    assert _looks_like_docker_infra_error("docker: Error response from daemon: no such image")
    assert not _looks_like_docker_infra_error("Traceback (most recent call last): ValueError: boom")
    assert not _looks_like_docker_infra_error("")


def test_timeout_raises_not_silent_yield():
    # A real timeout must surface as an error, never be masked as a successful/yielded run.
    with pytest.raises((RuntimeError, TimeoutError)):
        run_in_isolation(
            "data.transform",
            {"items": list(range(10)), "op": "square"},
            limits=Limits(timeout_s=0),
        )
