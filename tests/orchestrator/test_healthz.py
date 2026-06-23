from fastapi.testclient import TestClient

from orchestrator.app import create_app


def test_healthz_returns_ok():
    client = TestClient(create_app(":memory:"))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
