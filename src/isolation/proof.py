"""Honest proof/reporting for the currently available isolation boundary."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from contracts import Limits
from isolation.docker import build_docker_command, docker_available
from isolation.runner import _new_container_name, _stage_payload


def isolation_proof() -> dict:
    """Report what boundary is active without overstating subprocess governance."""
    if not docker_available():
        return {
            "isolated": False,
            "method": "subprocess+jobobject",
            "note": (
                "Resource governance + kill-on-close only; filesystem isolation requires "
                "Docker/Windows Sandbox (roadmap on this SKU)."
            ),
        }

    denied_path = str(Path.home())
    probe = (
        "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
        "print(p.exists()); print(list(p.iterdir())[:1])"
    )
    try:
        with tempfile.TemporaryDirectory(prefix="onecompute-isolation-proof-") as temp_name:
            work_dir = Path(temp_name)
            in_path = work_dir / "in.json"
            out_path = work_dir / "out.json"
            with in_path.open("w", encoding="utf-8") as fh:
                json.dump({"kind": "challenge", "input": {"x": 1}}, fh)
            _stage_payload(work_dir)
            name = _new_container_name()
            job_proc = subprocess.run(
                build_docker_command(work_dir, in_path.name, out_path.name, Limits(), name),
                cwd=str(work_dir),
                capture_output=True,
                text=True,
                timeout=Limits().timeout_s,
                check=False,
            )
            if job_proc.returncode != 0 or not out_path.exists():
                evidence = (job_proc.stdout + job_proc.stderr).strip() or f"rc={job_proc.returncode}"
                return {
                    "isolated": False,
                    "method": "docker-unusable",
                    "denied_path": denied_path,
                    "evidence": evidence,
                    "note": (
                        "Docker exists, but the jobkit Docker path did not run; jobs fall "
                        "back to subprocess+jobobject."
                    ),
                }
            proc = subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "-v",
                    f"{Path(temp_name)}:/work:ro",
                    "python:3.12-slim",
                    "python",
                    "-c",
                    probe,
                    denied_path,
                ],
                capture_output=True,
                text=True,
                timeout=Limits().timeout_s,
                check=False,
            )
        evidence = (proc.stdout + proc.stderr).strip() or f"rc={proc.returncode}"
        denied = proc.returncode != 0 or "False" in proc.stdout
        return {
            "isolated": bool(denied),
            "method": "docker",
            "denied_path": denied_path,
            "evidence": evidence,
        }
    except Exception as exc:
        return {
            "isolated": False,
            "method": "docker-unverified",
            "denied_path": denied_path,
            "evidence": f"docker probe failed: {exc}",
        }
