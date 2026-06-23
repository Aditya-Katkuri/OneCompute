"""NightShift isolation seam."""

from isolation.docker import docker_available
from isolation.proof import isolation_proof
from isolation.runner import JobHandle, run_in_isolation, start_in_isolation

__all__ = [
    "JobHandle",
    "docker_available",
    "isolation_proof",
    "run_in_isolation",
    "start_in_isolation",
]
