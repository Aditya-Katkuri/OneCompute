"""Docker command construction + honest daemon-availability probe for NightShift.

Importing this module never requires Docker and never touches the daemon. Availability
means **the Docker daemon actually responds** (a short, cached ``docker version`` probe),
not merely that a ``docker`` executable is on PATH -- on the demo SKU the Desktop engine
is intermittently down (HTTP 500) and a PATH-only check makes ``run_in_isolation`` try
Docker, eat the 500, and silently fall back. The probe result is cached so the demo path
does not pay the probe cost on every job.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

from contracts import Limits

# The only image the CPU kinds need; stdlib-only payload, so slim is sufficient.
IMAGE = "python:3.12-slim"

# Cache the daemon probe: the engine state changes on the order of seconds-to-minutes,
# so a short TTL keeps us honest without probing on every single job.
_PROBE_TTL_S = 30.0
_PROBE_TIMEOUT_S = 6.0
_probe_lock = threading.Lock()
_probe_cache: tuple[float, bool] | None = None


def _probe_daemon() -> bool:
    """Return True only if the Docker daemon answers a trivial server query.

    Never raises. A missing executable, an unreachable daemon (the HTTP 500 we see on
    this SKU), or a hung client (capped by the timeout) all read as "not available".
    """
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except Exception:
        return False
    # A reachable daemon prints its server version; a down daemon returns non-zero
    # (and an empty server version) even though the client itself runs fine.
    return proc.returncode == 0 and bool(proc.stdout.strip())


def docker_available(*, force: bool = False) -> bool:
    """Return whether the Docker daemon is actually reachable (cached for ``_PROBE_TTL_S``).

    Pass ``force=True`` to bypass the cache and re-probe (used by tests / a dashboard
    refresh). Import-time cost is zero: the daemon is only probed on first call.
    """
    global _probe_cache
    now = time.monotonic()
    with _probe_lock:
        if not force and _probe_cache is not None:
            ts, value = _probe_cache
            if now - ts < _PROBE_TTL_S:
                return value
    available = _probe_daemon()
    with _probe_lock:
        _probe_cache = (time.monotonic(), available)
    return available


def reset_docker_probe_cache() -> None:
    """Drop the cached daemon-availability result (tests / explicit refresh)."""
    global _probe_cache, _image_cache
    with _probe_lock:
        _probe_cache = None
    with _image_lock:
        _image_cache = None


# Cache whether the slim image is already present locally, so we don't `inspect` per job.
_image_lock = threading.Lock()
_image_cache: bool | None = None


def image_present() -> bool:
    """Return whether ``IMAGE`` is already pulled locally (cached, never raises).

    Ensuring the image exists *before* a timed run means the container registers with the
    daemon in ~1s, so yield/timeout can kill it by name without racing an inline pull.
    """
    global _image_cache
    with _image_lock:
        if _image_cache:
            return True
    present = _inspect_image()
    with _image_lock:
        _image_cache = present
    return present


def _inspect_image() -> bool:
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except Exception:
        return False
    return proc.returncode == 0


def build_docker_command(
    work_dir: Path,
    in_name: str,
    out_name: str,
    limits: Limits,
    name: str,
) -> list[str]:
    """Build the Docker invocation for a jobkit file-based execution.

    We mount ONLY the per-job temp work dir (which lives under ``%TEMP%`` -- a clean path
    with no spaces, unlike the OneDrive repo ``src``). The tiny stdlib-only payload
    (``jobkit`` + ``contracts.hashing``) is staged into ``work_dir/src`` by the runner and
    exposed via ``PYTHONPATH=/work/src``; the container never sees the worker's files.
    The container is given a unique ``--name`` so yield/timeout can kill it by name.
    """
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        name,
        "--network",
        "none" if limits.network == "none" else "host",
        "-v",
        f"{work_dir}:/work",
        "-w",
        "/work",
        "-e",
        "PYTHONPATH=/work/src",
        "-e",
        "PYTHONDONTWRITEBYTECODE=1",
    ]
    if limits.mem_gb and limits.mem_gb > 0:
        command.extend(["--memory", f"{limits.mem_gb}g"])
    command.extend([
        IMAGE,
        "python",
        "-m",
        "jobkit",
        f"/work/{in_name}",
        f"/work/{out_name}",
    ])
    return command
