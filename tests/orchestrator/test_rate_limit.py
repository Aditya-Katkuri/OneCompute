"""Tests for the orchestrator per-client rate limiter and its middleware wiring."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orchestrator.app import create_app
from orchestrator.ratelimit import RateLimiter

# --- unit: the limiter itself --------------------------------------------------------------

def test_rate_limiter_allows_up_to_limit_then_blocks() -> None:
    limiter = RateLimiter(limit=3, window_s=60.0)
    now = 1000.0
    assert [limiter.check("k", now)[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.check("k", now)
    assert allowed is False
    assert retry_after >= 1


def test_rate_limiter_resets_after_window() -> None:
    limiter = RateLimiter(limit=1, window_s=10.0)
    assert limiter.check("k", now=0.0)[0] is True
    assert limiter.check("k", now=5.0)[0] is False  # same window
    assert limiter.check("k", now=10.0)[0] is True  # window rolled over


def test_rate_limiter_is_per_key() -> None:
    limiter = RateLimiter(limit=1, window_s=60.0)
    assert limiter.check("a", now=0.0)[0] is True
    assert limiter.check("b", now=0.0)[0] is True  # different key, own budget
    assert limiter.check("a", now=0.0)[0] is False


# --- integration: the middleware on a real app ---------------------------------------------

def test_middleware_returns_429_with_retry_after_when_exceeded() -> None:
    client = TestClient(create_app(":memory:", rate_limit_per_min=3))
    codes = [client.get("/healthz").status_code for _ in range(3)]
    assert codes == [200, 200, 200]
    blocked = client.get("/healthz")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) >= 1
    # security headers still decorate the throttled response
    assert blocked.headers["X-Content-Type-Options"] == "nosniff"


def test_middleware_keys_clients_by_bearer_token() -> None:
    client = TestClient(create_app(":memory:", rate_limit_per_min=2))
    a = {"Authorization": "Bearer aaa"}
    b = {"Authorization": "Bearer bbb"}
    assert client.get("/healthz", headers=a).status_code == 200
    assert client.get("/healthz", headers=a).status_code == 200
    assert client.get("/healthz", headers=a).status_code == 429  # a exhausted
    assert client.get("/healthz", headers=b).status_code == 200  # b has its own budget


def test_rate_limiting_off_by_default() -> None:
    client = TestClient(create_app(":memory:"))  # no rate_limit_per_min
    for _ in range(50):
        assert client.get("/healthz").status_code == 200


def test_zero_or_negative_limit_disables_middleware() -> None:
    client = TestClient(create_app(":memory:", rate_limit_per_min=0))
    for _ in range(50):
        assert client.get("/healthz").status_code == 200
