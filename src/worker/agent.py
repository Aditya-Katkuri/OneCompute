"""HTTP worker agent for the NightShift control-plane contracts."""

from __future__ import annotations

import asyncio
import logging
import threading
from time import perf_counter
from typing import TYPE_CHECKING, Any

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
from isolation import run_in_isolation
from trust import verify_manifest
from worker.capability import free_ram_gb as _current_free_ram_gb
from worker.runner import default_runner

if TYPE_CHECKING:
    from worker.idle import IdleGate

logger = logging.getLogger(__name__)


class WorkerAgent:
    def __init__(
        self,
        base_url: str,
        capability: Capability,
        runner=default_runner,
        client: httpx.Client | None = None,
        verify: bool = True,
        isolated: bool = False,
    ) -> None:
        self.base_url = base_url
        self.capability = capability
        self.runner = runner or default_runner
        self.client = client or httpx.Client(base_url=base_url, timeout=10.0)
        self._owns_client = client is None
        self.verify = verify
        self.isolated = isolated
        self._yield = threading.Event()
        self._job_running = threading.Event()
        self._yield_watcher_stop = threading.Event()
        self._yield_watcher_thread: threading.Thread | None = None
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

    def _verify_assignment(self, assignment: JobAssignment) -> tuple[bool, str]:
        """Refuse tampered work before executing: signature + input-hash checks."""
        sm = assignment.signed_manifest
        manifest = sm.manifest
        if sm.signature and not verify_manifest(sm):
            return False, "bad_signature"
        if manifest.input_sha256 and sha256_hex(assignment.input) != manifest.input_sha256:
            return False, "input_hash_mismatch"
        return True, ""

    def run_job(self, assignment: JobAssignment) -> ResultRequest:
        manifest = assignment.signed_manifest.manifest
        t0 = perf_counter()
        if self.verify:
            ok, reason = self._verify_assignment(assignment)
            if not ok:
                refused = {"error": f"verification failed: {reason}"}
                logger.warning("worker refused job %s: %s", manifest.job_id, reason)
                return ResultRequest(
                    worker_id=self.capability.worker_id,
                    job_id=manifest.job_id,
                    status="failed",
                    output=refused,
                    proof_sha256=sha256_hex(refused),
                    duration_s=perf_counter() - t0,
                    units=1,
                )
        self._job_running.set()
        try:
            if self.isolated:
                # GPU jobs must run host-side (real CUDA device); a Linux container can't see
                # the GPU. Route them to the on-host Job-Object path even when Docker is up.
                host_side = (
                    manifest.requires.needs_gpu or manifest.sandbox.type == "job_object"
                )
                out = run_in_isolation(
                    manifest.kind,
                    assignment.input,
                    manifest.limits,
                    should_yield=self.should_yield,
                    host_side=host_side,
                )
            else:
                out = self.runner(manifest, assignment.input, should_yield=self.should_yield)
            yielded = bool(out.get("yielded"))
            status = "yielded" if yielded else "completed"
        except Exception as exc:
            logger.exception("worker job failed: %s", exc)
            out = {"error": str(exc)}
            status = "failed"
        finally:
            self._job_running.clear()
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
            free_ram_gb=_current_free_ram_gb(),
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

    def start_yield_watcher(self, gate: IdleGate, poll_s: float = 0.1) -> None:
        """Set the yield flag quickly when fresh human input is observed during a job."""
        self.stop_yield_watcher()
        self._yield_watcher_stop.clear()

        def watch() -> None:
            while not self._yield_watcher_stop.wait(max(0.01, poll_s)):
                try:
                    if self._job_running.is_set() and gate.active_now():
                        self._yield.set()
                except Exception:
                    continue

        self._yield_watcher_thread = threading.Thread(
            target=watch,
            name="nightshift-yield-watcher",
            daemon=True,
        )
        self._yield_watcher_thread.start()

    def stop_yield_watcher(self) -> None:
        self._yield_watcher_stop.set()
        thread = self._yield_watcher_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._yield_watcher_thread = None

    def trigger_yield(self) -> None:
        self._yield.set()

    def run_guarded(self, gate: IdleGate) -> ResultRequest | None:
        if not gate.should_run():
            return None
        self.start_yield_watcher(gate)
        try:
            return self.run_once()
        finally:
            self.stop_yield_watcher()

    def close(self) -> None:
        self.stop_yield_watcher()
        try:
            self.client.close()
        except AttributeError:
            pass
        except httpx.HTTPError as exc:
            logger.warning("worker client close failed: %s", exc)
