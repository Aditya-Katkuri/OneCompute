"""HTTP worker agent for the OneCompute control-plane contracts."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any

import httpx

from contracts import (
    Capability,
    HeartbeatRequest,
    HeartbeatResponse,
    JobAssignment,
    ProfileReport,
    RegisterResponse,
    ResultRequest,
    ResultResponse,
    UsageBucket,
    sha256_hex,
)
from isolation import IsolationUnavailableError, run_in_isolation
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
        require_isolation: bool = False,
        trusted_public_key_hex: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.capability = capability
        self.runner = runner or default_runner
        self.client = client or httpx.Client(base_url=base_url, timeout=10.0)
        self._owns_client = client is None
        self.verify = verify
        self.isolated = isolated
        # Fail closed: when True, refuse any job that would run without an OS-enforced sandbox
        # (Docker down, or a host-side GPU/AI job) instead of degrading to the unsandboxed
        # subprocess fallback. Wired from the worker's --require-isolation.
        self.require_isolation = require_isolation
        # Out-of-band trusted signer key (hex). When set, only manifests signed by exactly this
        # key are accepted, so a compromised orchestrator cannot inject a self-signed job.
        self.trusted_public_key_hex = trusted_public_key_hex
        self._yield = threading.Event()
        self._job_running = threading.Event()
        self._yield_watcher_stop = threading.Event()
        self._yield_watcher_thread: threading.Thread | None = None
        self.should_yield = self._yield.is_set
        self.worker_token: str | None = None
        self.poll_interval_s: float | None = None
        self.registered = False
        self.approved: bool = True
        self.device_code: str | None = None

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        if self.worker_token:
            headers = dict(kwargs.get("headers") or {})
            headers["Authorization"] = f"Bearer {self.worker_token}"
            kwargs["headers"] = headers
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
            self.approved = register_response.approved
            self.device_code = register_response.device_code
            self.registered = True
        except httpx.HTTPError as exc:
            logger.warning("worker registration failed: %s", exc)
            self.registered = False
        except Exception as exc:
            logger.warning("worker registration failed: %s", exc)
            self.registered = False

    def wait_for_approval(self, poll_s: float | None = None, once: bool = False) -> bool:
        """Block (heartbeating) until the dashboard approves this worker.

        Returns True once approved. With once=True, send a single heartbeat and return the
        current approval state without looping (keeps --once non-hanging). The device code is
        printed prominently so an admin can approve it in the dashboard.
        """
        if self.approved:
            return True
        code = self.device_code or "????-??"
        print(
            f"Fleet access code: {code}, waiting for approval in the dashboard…",
            flush=True,
        )
        delay = poll_s if poll_s is not None else (self.poll_interval_s or 1.5)
        while True:
            hb = self.heartbeat()
            if hb.approved:
                self.approved = True
                self.device_code = None
                print(
                    f"[+] Access granted: {self.capability.worker_id} joined the fleet",
                    flush=True,
                )
                return True
            if once:
                return False
            time.sleep(max(0.0, delay))

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
        """Refuse tampered or untrusted work before executing: signature, provenance, expiry, and
        input-hash checks.

        With a trusted key pinned (``self.trusted_public_key_hex``), trust is STRICT: the manifest
        must be signed AND signed by exactly that key, so a compromised orchestrator's self-signed
        job is rejected and an unsigned manifest is refused outright. Without a pinned key (the PoC
        default), a present signature is still integrity-checked, but an unsigned manifest is
        allowed through (the un-provisioned demo path).
        """
        sm = assignment.signed_manifest
        manifest = sm.manifest
        trusted = self.trusted_public_key_hex
        if trusted:
            if not sm.signature:
                return False, "unsigned_manifest"
            if not verify_manifest(sm, trusted_public_key_hex=trusted):
                return False, "untrusted_signer"
        elif sm.signature and not verify_manifest(sm):
            return False, "bad_signature"
        if manifest.expires_at and manifest.expires_at <= datetime.now(UTC):
            return False, "manifest_expired"
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
                # the GPU. AI kinds also run host-side so the real SDK + API-key env are
                # available (the slim container has neither). Route both to the on-host
                # Job-Object path even when Docker is up.
                host_side = (
                    manifest.requires.needs_gpu
                    or manifest.sandbox.type == "job_object"
                    or manifest.kind.startswith("ai.")
                )
                out = run_in_isolation(
                    manifest.kind,
                    assignment.input,
                    manifest.limits,
                    should_yield=self.should_yield,
                    host_side=host_side,
                    allow_unsandboxed=not self.require_isolation,
                )
            else:
                out = self.runner(manifest, assignment.input, should_yield=self.should_yield)
            yielded = bool(out.get("yielded"))
            status = "yielded" if yielded else "completed"
        except IsolationUnavailableError as exc:
            logger.warning("worker refused job %s (no OS sandbox): %s", manifest.job_id, exc)
            out = {"error": f"refused: {exc}"}
            status = "failed"
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

    def heartbeat(
        self,
        current_job_id: str | None = None,
        cpu_pct: float = 0.0,
        gpu_pct: float | None = None,
        idle: bool | None = None,
    ) -> HeartbeatResponse:
        """Report liveness + live usage. cpu_pct/gpu_pct feed the dashboard's per-device usage
        graphs; idle defaults to 'no job running' so a usage-only heartbeat (no current_job_id)
        still reflects busy state without touching the lease."""
        if idle is None:
            idle = not (bool(current_job_id) or self._job_running.is_set())
        request = HeartbeatRequest(
            worker_id=self.capability.worker_id,
            idle=idle,
            cpu_pct=cpu_pct,
            gpu_pct=gpu_pct,
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

    def report_profile(self, profiler: Any) -> bool:
        """Upload this machine's on-device usage envelope to the orchestrator for the measurement
        pilot (opt-in). Sends only populated (n>0) hour-of-week buckets carrying derived stats --
        no raw activity, files, or wall-clock timestamps. Best-effort and offline-safe: returns
        ``False`` (never raises) when the worker isn't registered or the POST fails, so the pilot
        keeps learning locally even with no reachable orchestrator."""
        if not self.registered or not self.worker_token:
            return False
        buckets = [
            UsageBucket(
                index=i,
                n=b.n,
                cpu_mean=b.cpu_mean,
                cpu_max=b.cpu_max,
                gpu_mean=b.gpu_mean,
                gpu_max=b.gpu_max,
                ram_mean=b.ram_mean,
                ram_max=b.ram_max,
            )
            for i, b in enumerate(getattr(profiler, "buckets", []))
            if getattr(b, "n", 0) > 0
        ]
        report = ProfileReport(worker_id=self.capability.worker_id, buckets=buckets)
        try:
            self._request("POST", "/profile", json=report.model_dump())
            return True
        except Exception as exc:  # offline / rejected / transport error -> stay local-only
            logger.debug("worker profile report failed (staying local-only): %s", exc)
            return False

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
        if not self.approved:
            # --once must not hang: a single heartbeat, then bail if still pending.
            if not self.wait_for_approval(once=True):
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
            name="onecompute-yield-watcher",
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
