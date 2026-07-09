"""End-to-end validation of the MXC launch path against a stub wxc-exec runtime.

Unlike ``test_mxc_runner`` / ``test_mxc_policy`` (which mock the runtime at a high
level), these tests point the runtime-resolution env vars at a real stub program
(``fake_wxc_exec.py`` behind a ``.cmd`` shim) and drive the *actual*
``isolation.mxc`` code: the probe (``mxc_available``), ``active_boundary``, and the
real ``_run_mxc`` launch path via ``run_in_isolation(..., host_side=False)``. See
``docs/mxc-validation.md`` for the reverse-engineered protocol and scope.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

import pytest

from contracts import Limits
from isolation import runner
from isolation.mxc import _MxcInfraError, _run_mxc, mxc_available, reset_mxc_probe_cache
from isolation.runner import _stage_mxc_layout

_FAKE_RUNTIME = Path(__file__).with_name("fake_wxc_exec.py")


def _write_shim(tmp_path: Path, control_dir: Path) -> Path:
    """Create a ``wxc-exec.cmd`` that runs the stub under this interpreter.

    The shim ``set``s ``FAKE_WXC_CONTROL`` *inside* the batch file so it survives
    ``isolation.mxc._mxc_env`` (which forwards only a fixed allowlist to the
    runtime), letting the harness toggle the stub's infra-failure mode via a
    marker file.
    """
    shim = tmp_path / "wxc-exec.cmd"
    shim.write_text(
        "@echo off\r\n"
        f'set "FAKE_WXC_CONTROL={control_dir}"\r\n'
        f'"{sys.executable}" "{_FAKE_RUNTIME}" %*\r\n',
        encoding="ascii",
    )
    return shim


@pytest.fixture
def stub_runtime(tmp_path, monkeypatch):
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    shim = _write_shim(tmp_path, control_dir)

    monkeypatch.setenv("ONECOMPUTE_MXC_EXE", str(shim))
    monkeypatch.delenv("MXC_BIN_DIR", raising=False)
    reset_mxc_probe_cache()
    try:
        yield control_dir
    finally:
        reset_mxc_probe_cache()


def _run_mxc_direct(kind: str, input: dict, limits: Limits) -> dict:
    """Drive the real ``_run_mxc`` with the same staging ``run_in_isolation`` uses."""
    with tempfile.TemporaryDirectory(prefix="onecompute-mxc-validation-") as temp_name:
        work_dir = Path(temp_name)
        in_path = work_dir / "in.json"
        out_path = work_dir / "out.json"
        in_path.write_text(json.dumps({"kind": kind, "input": input}), encoding="utf-8")
        mxc_root = work_dir / "mxc"
        mxc_in, mxc_out, input_dir, payload_dir, writable_dir = _stage_mxc_layout(
            mxc_root, in_path, out_path.name
        )
        return _run_mxc(
            mxc_in,
            mxc_out,
            mxc_root,
            limits,
            lambda: False,
            input_dir=input_dir,
            payload_dir=payload_dir,
            writable_dir=writable_dir,
        )


@pytest.mark.skipif(sys.platform != "win32", reason="MXC/Job-Object path is Windows-only")
def test_probe_passes_and_boundary_is_mxc(stub_runtime):
    assert mxc_available(force=True) is True
    assert mxc_available() is True
    assert runner.active_boundary() == "mxc"


@pytest.mark.skipif(sys.platform != "win32", reason="MXC/Job-Object path is Windows-only")
def test_real_cpu_job_runs_through_mxc(stub_runtime, monkeypatch):
    assert mxc_available(force=True) is True

    # Prove the real _run_mxc launch path executed (not a mock, not a fallback):
    # wrap it so it still runs for real while we count invocations.
    real_run_mxc = runner._run_mxc
    calls = {"n": 0}

    def counting_run_mxc(*args, **kwargs):
        calls["n"] += 1
        return real_run_mxc(*args, **kwargs)

    monkeypatch.setattr(runner, "_run_mxc", counting_run_mxc)
    # Guarantee any (unexpected) fallback would be observable rather than silent.
    monkeypatch.setattr(runner, "docker_available", lambda *a, **k: False)

    challenge = runner.run_in_isolation("challenge", {"x": 6}, limits=Limits(timeout_s=60))
    assert challenge == {"y": 37}

    transform = runner.run_in_isolation(
        "data.transform",
        {"items": [1, 2, 3, 4], "op": "square"},
        limits=Limits(timeout_s=60),
    )
    assert transform["results"] == [1, 4, 9, 16]
    assert transform["yielded"] is False

    assert calls["n"] == 2


@pytest.mark.skipif(sys.platform != "win32", reason="MXC/Job-Object path is Windows-only")
def test_run_mxc_directly_returns_real_output(stub_runtime):
    assert mxc_available(force=True) is True
    assert _run_mxc_direct("challenge", {"x": 9}, Limits(timeout_s=60)) == {"y": 82}


@pytest.mark.skipif(sys.platform != "win32", reason="MXC/Job-Object path is Windows-only")
def test_infra_failure_raises_mxc_infra_error(stub_runtime):
    control_dir = stub_runtime
    assert mxc_available(force=True) is True  # probe containers are never failed

    (control_dir / "fail-infra").write_text("1", encoding="utf-8")

    with pytest.raises(_MxcInfraError):
        _run_mxc_direct("challenge", {"x": 6}, Limits(timeout_s=60))


@pytest.mark.skipif(sys.platform != "win32", reason="MXC/Job-Object path is Windows-only")
def test_infra_failure_falls_back_and_warns(stub_runtime, monkeypatch, caplog):
    control_dir = stub_runtime
    assert mxc_available(force=True) is True

    (control_dir / "fail-infra").write_text("1", encoding="utf-8")
    # No Docker, so the documented fallback is subprocess+jobobject.
    monkeypatch.setattr(runner, "docker_available", lambda *a, **k: False)
    caplog.set_level(logging.WARNING, logger="isolation.runner")

    output = runner.run_in_isolation("challenge", {"x": 6}, limits=Limits(timeout_s=60))

    assert output == {"y": 37}
    assert "MXC could not run the job" in caplog.text
    assert "falling back to Docker/subprocess" in caplog.text


def test_probe_fails_without_stub_and_boundary_falls_back(monkeypatch, tmp_path):
    monkeypatch.delenv("ONECOMPUTE_MXC_EXE", raising=False)
    monkeypatch.delenv("MXC_BIN_DIR", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    reset_mxc_probe_cache()
    try:
        assert mxc_available(force=True) is False
        monkeypatch.setattr(runner, "docker_available", lambda *a, **k: False)
        assert runner.active_boundary() == "subprocess+jobobject"
    finally:
        reset_mxc_probe_cache()
