"""Runtime entrypoints for executing jobkit behind the T3 isolation seam."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from contracts import Limits
from isolation.docker import build_docker_command, docker_available
from isolation.jobobject import assign_process, close, create_job_object

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
YieldFn = Callable[[], bool]


class JobHandle:
    """Handle for a running isolated job."""

    def __init__(
        self,
        proc: subprocess.Popen,
        out_path: Path,
        job_handle=None,
        cleanup=None,
    ) -> None:
        self.proc = proc
        self.out_path = out_path
        self._job_handle = job_handle
        self._cleanup = cleanup
        self._killed = False

    def kill(self) -> None:
        """Kill the running job process tree as best as the active backend allows."""
        self._killed = True
        if self._job_handle is not None:
            close(self._job_handle)
            return
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
        except Exception:
            pass

    def wait(self) -> dict:
        """Wait for completion and return the jobkit output JSON."""
        try:
            _, stderr = self.proc.communicate()
            if self._killed:
                return {"yielded": True, "results": []}
            if self.proc.returncode != 0:
                raise RuntimeError((stderr or "isolated job failed").strip())
            with self.out_path.open(encoding="utf-8") as fh:
                return json.load(fh)
        finally:
            if self._cleanup is not None:
                self._cleanup()


def _subprocess_env() -> dict[str, str]:
    env: dict[str, str] = {"PYTHONPATH": str(SRC_DIR)}
    for key in ("SystemRoot", "PATH", "COMSPEC", "PATHEXT", "WINDIR"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _start_subprocess(in_path: Path, out_path: Path, cwd: Path, limits: Limits) -> JobHandle:
    proc = subprocess.Popen(
        [sys.executable, "-m", "jobkit", str(in_path), str(out_path)],
        cwd=str(cwd),
        env=_subprocess_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    job_handle = create_job_object(limits)
    try:
        job_handle.process = proc
    except Exception:
        pass
    assign_process(job_handle, proc.pid)
    return JobHandle(proc, out_path, job_handle=job_handle)


def start_in_isolation(
    kind: str,
    input: dict,
    limits: Limits = Limits(),
) -> JobHandle:
    """Start a jobkit task and return a handle that can kill or wait for it."""
    temp_dir = tempfile.TemporaryDirectory(prefix="nightshift-isolation-")
    work_dir = Path(temp_dir.name)
    in_path = work_dir / "in.json"
    out_path = work_dir / "out.json"
    with in_path.open("w", encoding="utf-8") as fh:
        json.dump({"kind": kind, "input": input}, fh)

    def cleanup() -> None:
        temp_dir.cleanup()

    if docker_available():
        command = build_docker_command(SRC_DIR, work_dir, in_path.name, out_path.name, limits)
        proc = subprocess.Popen(
            command,
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return JobHandle(proc, out_path, cleanup=cleanup)

    handle = _start_subprocess(in_path, out_path, work_dir, limits)
    handle._cleanup = cleanup
    return handle


def _run_subprocess_with_existing_files(
    in_path: Path,
    out_path: Path,
    work_dir: Path,
    limits: Limits,
    should_yield: YieldFn,
) -> dict:
    handle = _start_subprocess(in_path, out_path, work_dir, limits)
    deadline = time.monotonic() + max(float(limits.timeout_s), 0.001)
    while handle.proc.poll() is None:
        if should_yield():
            handle.kill()
            try:
                handle.proc.wait(timeout=1)
            except Exception:
                pass
            return {"yielded": True, "results": []}
        if time.monotonic() > deadline:
            handle.kill()
            try:
                handle.proc.wait(timeout=1)
            except Exception:
                pass
            raise RuntimeError("isolated job timed out")
        time.sleep(0.02)

    _, stderr = handle.proc.communicate()
    if handle.proc.returncode != 0:
        raise RuntimeError((stderr or "isolated job failed").strip())
    with out_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _run_docker_once(in_path: Path, out_path: Path, work_dir: Path, limits: Limits) -> dict:
    command = build_docker_command(SRC_DIR, work_dir, in_path.name, out_path.name, limits)
    proc = subprocess.run(
        command,
        cwd=str(work_dir),
        capture_output=True,
        text=True,
        timeout=limits.timeout_s,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "docker isolated job failed").strip())
    with out_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def run_in_isolation(
    kind: str,
    input: dict,
    limits: Limits = Limits(),
    should_yield: YieldFn = lambda: False,
) -> dict:
    """Execute a job via jobkit inside the best available local boundary.

    Docker is used only when a docker executable exists and succeeds. Otherwise the
    always-available path is a restricted subprocess governed by a Windows Job
    Object when Win32 supports it.
    """
    with tempfile.TemporaryDirectory(prefix="nightshift-isolation-") as temp_name:
        work_dir = Path(temp_name)
        in_path = work_dir / "in.json"
        out_path = work_dir / "out.json"
        with in_path.open("w", encoding="utf-8") as fh:
            json.dump({"kind": kind, "input": input}, fh)

        if docker_available():
            try:
                return _run_docker_once(in_path, out_path, work_dir, limits)
            except Exception:
                pass
        return _run_subprocess_with_existing_files(
            in_path,
            out_path,
            work_dir,
            limits,
            should_yield,
        )
