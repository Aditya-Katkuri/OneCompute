"""Runtime entrypoints for executing jobkit behind the T3 isolation seam.

Two boundaries, one contract. ``run_in_isolation`` always preempts sub-second on a
``should_yield()`` signal -- on BOTH the Docker-per-job path (poll ~20ms, then
``docker kill <name>``) and the always-available subprocess+JobObject fallback
(close the Job Object handle -> process tree dies). Active boundary is reported
honestly via ``active_boundary()`` and a WARNING is logged whenever Docker is
unreachable and we degrade to the subprocess path.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from contracts import Limits
from isolation.docker import (
    IMAGE,
    build_docker_command,
    docker_available,
    image_present,
)
from isolation.jobobject import assign_process, close, create_job_object

logger = logging.getLogger(__name__)

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
YieldFn = Callable[[], bool]

# How long to keep retrying `docker kill` after a yield/timeout. The image is ensured
# present before the run, so the container registers in ~1s and this is ample; we also
# keep retrying while the run process is alive so a late-registering container can't
# escape. Kept short so the "money shot" stays visibly sub-second.
_CONTAINER_STOP_DEADLINE_S = 15.0
# Bound for a one-time `docker pull` if the pre-pull process hasn't landed the image yet.
_IMAGE_PULL_TIMEOUT_S = 300


class _DockerInfraError(RuntimeError):
    """Docker itself could not run the job (daemon died, image missing, start failure).

    Distinct from a job-level timeout/failure: only an infra error falls back to the
    subprocess+JobObject path. A real timeout or job error propagates so we never silently
    re-run the job on a weaker boundary (which could, e.g., double-bill an AI job).
    """


# stderr fingerprints that mean "the docker CLI/daemon failed", not "the job failed".
_DOCKER_INFRA_MARKERS = (
    "cannot connect to the docker daemon",
    "error during connect",
    "request returned 500",
    "is the docker daemon running",
    "dial unix",
    "open //./pipe",
    "no such image",
    "unable to find image",
    "pull access denied",
    "docker: error response from daemon",
)


def _looks_like_docker_infra_error(stderr: str) -> bool:
    text = (stderr or "").lower()
    return any(marker in text for marker in _DOCKER_INFRA_MARKERS)


def active_boundary() -> str:
    """Return the boundary ``run_in_isolation`` will actually use right now.

    ``"docker"`` when the daemon responds, else ``"subprocess+jobobject"``. Lets the demo
    / dashboard state the real boundary instead of guessing from ``shutil.which``.
    """
    return "docker" if docker_available() else "subprocess+jobobject"


def _stage_payload(work_dir: Path) -> None:
    """Copy the tiny stdlib-only payload the container needs into ``work_dir/src``.

    We deliberately do NOT bind-mount the repo ``src`` -- it lives under a OneDrive path
    that contains a space, which is fragile through Docker Desktop. Instead we assemble
    ``jobkit`` + ``contracts.hashing`` under the per-job temp dir (a clean ``%TEMP%`` path)
    and mount only that. ``contracts/__init__`` is minimized to the hashing helpers so the
    container never needs pydantic (``python:3.12-slim`` stays sufficient for the CPU kinds
    ``data.transform`` / ``challenge`` / ``eval``).
    """
    src_root = work_dir / "src"
    contracts_dir = src_root / "contracts"
    jobkit_dir = src_root / "jobkit"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    jobkit_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(SRC_DIR / "contracts" / "hashing.py", contracts_dir / "hashing.py")
    (contracts_dir / "__init__.py").write_text(
        '"""Minimal contracts shim for the sandbox payload (stdlib-only, no pydantic)."""\n'
        "from contracts.hashing import canonical_bytes, sha256_hex\n\n"
        '__all__ = ["canonical_bytes", "sha256_hex"]\n',
        encoding="utf-8",
    )
    for name in ("__init__.py", "__main__.py", "execute.py"):
        shutil.copy2(SRC_DIR / "jobkit" / name, jobkit_dir / name)


def _docker_force_remove(name: str) -> None:
    """Last-resort ``docker rm -f`` so a named container can never linger."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        pass


def _ensure_image() -> None:
    """Make sure ``IMAGE`` is present locally before a timed run.

    A separate process pre-pulls the image, so this is usually a fast cached inspect. If
    the image is still missing we pull it once (bounded); a failed pull is an infra error
    so the caller falls back to the subprocess path instead of racing an inline pull during
    the timed run (which would let yield/timeout miss the container).
    """
    if image_present():
        return
    try:
        proc = subprocess.run(
            ["docker", "pull", IMAGE],
            capture_output=True,
            text=True,
            timeout=_IMAGE_PULL_TIMEOUT_S,
            check=False,
        )
    except Exception as exc:
        raise _DockerInfraError(f"docker pull {IMAGE} failed: {exc}") from exc
    if proc.returncode != 0 or not image_present():
        raise _DockerInfraError((proc.stderr or f"docker pull {IMAGE} failed").strip())


def _stop_container(name: str, proc: subprocess.Popen) -> None:
    """Stop the named container sub-second, tolerating the run/kill race.

    ``docker run`` may not have registered the container the instant we want to kill it (or
    it may register slightly late), so we keep issuing ``docker rm -f`` while the run process
    is still alive, then guarantee a final force-removal. A single ``docker rm -f`` both
    SIGKILLs *and* removes a running container in one CLI invocation, so we pay only one
    ``docker`` process spawn per attempt (not a ``kill`` *and* a ``rm``) — meaningfully faster
    on Windows, where each docker CLI invocation has notable startup cost. The container can
    never survive to run to completion once we've decided to preempt.
    """
    deadline = time.monotonic() + _CONTAINER_STOP_DEADLINE_S
    while proc.poll() is None:
        _docker_force_remove(name)  # SIGKILL + remove in a single docker invocation
        try:
            proc.wait(timeout=0.2)
        except Exception:
            pass
        if proc.poll() is not None:
            break
        if time.monotonic() > deadline:
            break
        time.sleep(0.02)
    # Final belt-and-suspenders removal in case the container registered at the last moment.
    _docker_force_remove(name)


class JobHandle:
    """Handle for a running isolated job."""

    def __init__(
        self,
        proc: subprocess.Popen,
        out_path: Path,
        job_handle=None,
        cleanup=None,
        container_name: str | None = None,
    ) -> None:
        self.proc = proc
        self.out_path = out_path
        self._job_handle = job_handle
        self._cleanup = cleanup
        self._container_name = container_name
        self._killed = False

    def kill(self) -> None:
        """Kill the running job process tree as best as the active backend allows.

        Docker backend: stop the **container** by name (``docker kill``/``rm -f``) -- not
        just the docker CLI client, which would leave the container running. Subprocess
        backend: close the Job Object handle (kill-on-close kills the whole tree).
        """
        self._killed = True
        if self._container_name is not None:
            _stop_container(self._container_name, self.proc)
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
            except Exception:
                pass
            return
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
    for key in ("SystemRoot", "PATH", "COMSPEC", "PATHEXT", "WINDIR", "TEMP", "TMP", "USERPROFILE"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    # Forward CUDA/cupy env so a host-side GPU job engages REAL CUDA regardless of how cupy was
    # installed: system-toolkit builds resolve their DLLs via CUDA_PATH, and cupy caches JIT
    # kernels under TEMP. On a non-GPU box none of these exist, so this is a harmless no-op
    # (the job then honestly reports accelerator="cpu-fallback").
    for key, value in os.environ.items():
        if value and (key.startswith("CUDA") or key.startswith("CUPY") or key == "NVTOOLSEXT_PATH"):
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


def _new_container_name() -> str:
    return f"nightshift-{uuid.uuid4().hex[:12]}"


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
        try:
            _ensure_image()
            _stage_payload(work_dir)
            name = _new_container_name()
            command = build_docker_command(work_dir, in_path.name, out_path.name, limits, name)
            proc = subprocess.Popen(
                command,
                cwd=str(work_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return JobHandle(proc, out_path, cleanup=cleanup, container_name=name)
        except Exception as exc:
            logger.warning(
                "Docker isolation start failed (%s); falling back to subprocess+jobobject.",
                exc,
            )

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
    try:
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
    finally:
        # Always release the Windows Job Object kernel handle (no-op if already killed);
        # otherwise every completed subprocess job leaks one handle on a long-lived worker.
        close(handle._job_handle)


def _run_docker(
    in_path: Path,
    out_path: Path,
    work_dir: Path,
    limits: Limits,
    should_yield: YieldFn,
) -> dict:
    """Run one job in a named container, polling for yield/timeout so we can preempt.

    Returns ``{"yielded": True, "results": []}`` on a yield (so the orchestrator requeues).
    Raises ``TimeoutError`` on timeout and ``RuntimeError`` on a job-level failure (both
    propagate -- we never silently re-run the job on a weaker boundary). Raises
    ``_DockerInfraError`` only when Docker itself failed to run the job, which is the one
    case the caller falls back to subprocess for.
    """
    _ensure_image()  # raises _DockerInfraError if the image can't be made present
    name = _new_container_name()
    command = build_docker_command(work_dir, in_path.name, out_path.name, limits, name)
    proc = subprocess.Popen(
        command,
        cwd=str(work_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + max(float(limits.timeout_s), 0.001)
    outcome = "ok"
    while proc.poll() is None:
        if should_yield():
            outcome = "yielded"
            _stop_container(name, proc)
            break
        if time.monotonic() > deadline:
            outcome = "timeout"
            _stop_container(name, proc)
            break
        time.sleep(0.02)

    try:
        _, stderr = proc.communicate(timeout=10)
    except Exception:
        # The run client wouldn't reap; make sure the container is gone, not just the CLI.
        _stop_container(name, proc)
        try:
            proc.kill()
        except Exception:
            pass
        stderr = ""

    if outcome == "yielded":
        return {"yielded": True, "results": []}
    if outcome == "timeout":
        raise TimeoutError("isolated docker job timed out")
    if proc.returncode != 0:
        message = (stderr or "docker isolated job failed").strip()
        if _looks_like_docker_infra_error(message) or not out_path.exists():
            # Could not actually run the job (daemon/image/start failure) -> allow fallback.
            raise _DockerInfraError(message)
        raise RuntimeError(message)
    with out_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def run_in_isolation(
    kind: str,
    input: dict,
    limits: Limits = Limits(),
    should_yield: YieldFn = lambda: False,
    host_side: bool = False,
) -> dict:
    """Execute a job via jobkit inside the best available local boundary.

    Docker-per-job is used when the daemon actually responds; if Docker can't run the job
    (daemon down/image missing/start failure) we fall back to a restricted subprocess
    governed by a Windows Job Object. Both paths preempt sub-second on ``should_yield()``.
    A real job timeout or job failure on the Docker path propagates (no silent re-run on a
    weaker boundary). Falling back is logged at WARNING -- no silent degradation.

    ``host_side=True`` forces the on-host subprocess+Job-Object path even when the Docker
    daemon is up. GPU jobs use this: a Linux container has no access to the host's CUDA
    device (GPU-in-container/Sandbox is broken -- see architecture.md §3.3), so GPU work runs
    host-side where CUDA sees the real device, and the Job Object's kill-on-close still
    delivers the sub-second instant-yield.
    """
    with tempfile.TemporaryDirectory(prefix="nightshift-isolation-") as temp_name:
        work_dir = Path(temp_name)
        in_path = work_dir / "in.json"
        out_path = work_dir / "out.json"
        with in_path.open("w", encoding="utf-8") as fh:
            json.dump({"kind": kind, "input": input}, fh)

        if docker_available() and not host_side:
            try:
                _stage_payload(work_dir)
                return _run_docker(in_path, out_path, work_dir, limits, should_yield)
            except _DockerInfraError as exc:
                logger.warning(
                    "Docker could not run the job (%s); falling back to subprocess+jobobject "
                    "(resource governance + kill-on-close only, no filesystem boundary).",
                    exc,
                )
            # TimeoutError / RuntimeError (job-level) intentionally propagate.
        return _run_subprocess_with_existing_files(
            in_path,
            out_path,
            work_dir,
            limits,
            should_yield,
        )
