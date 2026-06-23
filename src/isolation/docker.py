"""Docker command construction for NightShift isolated jobs.

Importing this module never requires Docker. Availability is only the presence of a
Docker executable; callers still fall back if the command itself fails.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from contracts import Limits


def docker_available() -> bool:
    """Return whether a docker executable is discoverable on PATH."""
    return shutil.which("docker") is not None


def build_docker_command(
    src_dir: Path,
    work_dir: Path,
    in_name: str,
    out_name: str,
    limits: Limits,
) -> list[str]:
    """Build the Docker invocation for a jobkit file-based execution."""
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        "none" if limits.network == "none" else "host",
        "-v",
        f"{src_dir}:/app/src:ro",
        "-v",
        f"{work_dir}:/work",
        "-w",
        "/work",
        "-e",
        "PYTHONPATH=/app/src",
    ]
    if limits.mem_gb and limits.mem_gb > 0:
        command.extend(["--memory", f"{limits.mem_gb}g"])
    command.extend([
        "python:3.12-slim",
        "python",
        "-m",
        "jobkit",
        f"/work/{in_name}",
        f"/work/{out_name}",
    ])
    return command
