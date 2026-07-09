"""In-process, per-client request rate limiting for the orchestrator control plane.

A fixed-window counter keyed by the caller's worker token (``Authorization: Bearer ...``)
when present, else the client IP. A single-process, SQLite-backed orchestrator makes an
in-memory limiter the right-sized DoS backstop for the PoC; a shared store (Redis) is the
multi-orchestrator upgrade. Thread-safe, because uvicorn may dispatch middleware from a
worker thread.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_SWEEP_EVERY = 1024  # opportunistically drop stale buckets so memory can't grow unbounded


class RateLimiter:
    """Fixed-window request limiter: at most ``limit`` requests per ``window_s`` per key."""

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit = int(limit)
        self.window_s = float(window_s)
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[float, int]] = {}  # key -> (window_start, count)
        self._ops = 0

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` and count the request when allowed.

        ``retry_after_seconds`` is 0 when allowed, else the whole seconds until the current
        window rolls over (at least 1).
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            self._ops += 1
            if self._ops % _SWEEP_EVERY == 0:
                self._sweep(now)
            start, count = self._buckets.get(key, (now, 0))
            if now - start >= self.window_s:  # window rolled over: reset
                start, count = now, 0
            if count >= self.limit:
                retry = max(1, int(self.window_s - (now - start)) + 1)
                return False, retry
            self._buckets[key] = (start, count + 1)
            return True, 0

    def _sweep(self, now: float) -> None:
        stale = [k for k, (start, _) in self._buckets.items() if now - start >= self.window_s]
        for k in stale:
            self._buckets.pop(k, None)


def client_key(request: Any) -> str:
    """Derive a rate-limit key: the worker token when a Bearer header is present, else the IP.

    Keying on the token means one noisy worker cannot exhaust another's budget, and an
    unauthenticated flood is bounded per source IP.
    """
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return "tok:" + auth[7:].strip()
    host = request.client.host if request.client else "unknown"
    return "ip:" + host
