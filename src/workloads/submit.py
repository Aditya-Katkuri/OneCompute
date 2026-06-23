"""Submit demo jobs to a NightShift orchestrator."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import httpx


def _resolve(value: Any) -> Any:
    if not inspect.isawaitable(value):
        return value
    return asyncio.run(value)


def _post_job(client: Any, job: dict) -> str:
    response = _resolve(client.post("/jobs", json=job))
    response.raise_for_status()
    payload = response.json()
    job_id = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("POST /jobs response did not include a job_id")
    return job_id


def submit_all(base_url: str, jobs: list[dict], client=None) -> list[str]:
    """POST each job to `/jobs` and return the assigned job IDs."""
    if client is not None:
        return [_post_job(client, job) for job in jobs]

    with httpx.Client(base_url=base_url, timeout=10.0) as http_client:
        return [_post_job(http_client, job) for job in jobs]
