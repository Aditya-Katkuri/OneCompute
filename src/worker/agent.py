"""HTTP worker agent for the NightShift control-plane contracts."""

from __future__ import annotations

import asyncio
import logging
import threading
from time import perf_counter
from typing import Any

import httpx

from contracts import (
    Capability,
    HeartbeatRequest,
    HeartbeatResponse,
    JobAssignment,
    RegisterResponse,
    ResultRequest,
    ResultResponse,
    sha256_hex,
)
from worker.runner import default_runner

logger = logging.getLogger(__name__)


class WorkerAgent:
    def __init__(
        self,
        base_url: str,
        capability: Capability,
        runner=default_runner,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url
        self.capability = capability
        self.runner = runner or default_runner
        self.client = client or httpx.Client(base_url=base_url, timeout=10.0)
        self._owns_client = client is None
        self._yield = threading.Event()
        self.should_yield = self._yield.is_set
        self.worker_token: str | None = None
        self.poll_interval_s: float | None = None
        self.registered = False

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except AttributeError as exc:
            if "handle_request" not in str(exc):
                raise
            response = self._request_via_async_transport(method, url, **kwargs)
            response.raise_for_status()
            return response

    def _request_via_async_transport(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        transport = getattr(self.client, "_transport", None)
        if transport is None or not hasattr(transport, "handle_async_request"):
            raise AttributeError("client transport does not support requests")

        async def send() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=transport,
                base_url=self.client.base_url,
                timeout=self.client.timeout,
            ) as async_client:
                response = await async_client.request(method, url, **kwargs)
                await response.aread()
                return response

        return asyncio.run(send())

    def register(self) -> None:
        try:
            resp = self._request("POST", "/register", json=self.capability.model_dump())
            register_response = RegisterResponse(**resp.json())
            self.worker_token = register_response.worker_token
            self.poll_interval_s = register_response.poll_interval_s
            self.registered = True
        except httpx.HTTPError as exc:
            logger.warning("worker registration failed: %s", exc)
            self.registered = False
        except Exception as exc:
            logger.warning("worker registration failed: %s", exc)
            self.registered = False

    def poll_once(self) -> JobAssignment | None:
        try:
            resp = self._request("GET", f"/jobs/next?worker_id={self.capability.worker_id}")
            if resp.status_code == 204:
                return None
            return JobAssignment(**resp.json())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 204:
                return None
            logger.warning("worker poll failed: %s", exc)
            return None
        except httpx.HTTPError as exc:
            logger.warning("worker poll failed: %s", exc)
            return None
        except Exception as exc:
            logger.warning("worker poll failed: %s", exc)
            return None

    def run_job(self, assignment: JobAssignment) -> ResultRequest:
        manifest = assignment.signed_manifest.manifest
        t0 = perf_counter()
        try:
            out = self.runner(manifest, assignment.input, should_yield=self.should_yield)
            yielded = bool(out.get("yielded"))
            status = "yielded" if yielded else "completed"
        except Exception as exc:
            logger.exception("worker job failed: %s", exc)
            out = {"error": str(exc)}
            status = "failed"
        finally:
            self._yield.clear()
        units = len(assignment.input.get("items", [])) or 1
        return ResultRequest(
            worker_id=self.capability.worker_id,
            job_id=manifest.job_id,
            status=status,
            output=out,
            proof_sha256=sha256_hex(out),
            duration_s=perf_counter() - t0,
            units=units,
        )

    def heartbeat(self, current_job_id: str | None = None) -> HeartbeatResponse:
        request = HeartbeatRequest(
            worker_id=self.capability.worker_id,
            idle=not bool(current_job_id),
            current_job_id=current_job_id,
        )
        try:
            resp = self._request("POST", "/heartbeat", json=request.model_dump())
            heartbeat_response = HeartbeatResponse(**resp.json())
            if heartbeat_response.preempt:
                self._yield.set()
            return heartbeat_response
        except httpx.HTTPError as exc:
            logger.warning("worker heartbeat failed: %s", exc)
            return HeartbeatResponse(ack=False)
        except Exception as exc:
            logger.warning("worker heartbeat failed: %s", exc)
            return HeartbeatResponse(ack=False)

    def report_result(self, rr: ResultRequest) -> ResultResponse:
        try:
            resp = self._request("POST", f"/results/{rr.job_id}", json=rr.model_dump())
            return ResultResponse(**resp.json())
        except httpx.HTTPError as exc:
            logger.warning("worker result report failed: %s", exc)
            return ResultResponse(accepted=False, reason=str(exc))
        except Exception as exc:
            logger.warning("worker result report failed: %s", exc)
            return ResultResponse(accepted=False, reason=str(exc))

    def run_once(self) -> ResultRequest | None:
        if not self.registered:
            self.register()
        if not self.registered:
            return None
        assignment = self.poll_once()
        if assignment is None:
            return None
        rr = self.run_job(assignment)
        self.report_result(rr)
        return rr

    def close(self) -> None:
        try:
            self.client.close()
        except AttributeError:
            pass
        except httpx.HTTPError as exc:
            logger.warning("worker client close failed: %s", exc)
