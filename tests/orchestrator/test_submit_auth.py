"""Tests for optional operator-token gating of job/workload submission."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orchestrator.app import create_app


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"{'Bearer'} {token}"}


def test_submission_open_by_default() -> None:
    client = TestClient(create_app(":memory:"))  # no submit_token
    assert client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}}).status_code == 200
    assert client.post(
        "/workloads",
        json={"kind": "fractal", "n_tiles": 2, "params": {"width": 40, "height": 30, "max_iter": 40}},
    ).status_code == 200


def test_submit_rejected_without_token_when_configured() -> None:
    client = TestClient(create_app(":memory:", submit_token="s3cret"))
    resp = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}})
    assert resp.status_code == 401
    # the failed attempt is audited
    events = client.get("/events").json()["events"]
    assert any(e["type"] == "auth_failed" and (e["detail"] or "").startswith("submit:") for e in events)


def test_submit_rejected_with_wrong_token() -> None:
    client = TestClient(create_app(":memory:", submit_token="s3cret"))
    resp = client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}}, headers=_auth("nope"))
    assert resp.status_code == 401


def test_submit_accepted_with_correct_token() -> None:
    client = TestClient(create_app(":memory:", submit_token="s3cret"))
    resp = client.post(
        "/jobs", json={"kind": "challenge", "input": {"x": 3}}, headers=_auth("s3cret")
    )
    assert resp.status_code == 200
    assert resp.json()["job_id"]


def test_workload_launch_is_gated_too() -> None:
    client = TestClient(create_app(":memory:", submit_token="s3cret"))
    launch = {"kind": "fractal", "n_tiles": 2, "params": {"width": 40, "height": 30, "max_iter": 40}}
    assert client.post("/workloads", json=launch).status_code == 401
    ok = client.post("/workloads", json=launch, headers=_auth("s3cret"))
    assert ok.status_code == 200
    assert ok.json()["job_ids"]


def test_reading_state_is_not_gated_by_submit_token() -> None:
    # The submit token gates writes (submission), not dashboard reads.
    client = TestClient(create_app(":memory:", submit_token="s3cret"))
    assert client.get("/state").status_code == 200
    assert client.get("/healthz").status_code == 200


def test_submit_all_sends_bearer_header(monkeypatch) -> None:
    import workloads.submit as ws

    captured: dict = {}

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"job_id": "j1"}

    class _FakeClient:
        def __init__(self, base_url, timeout, headers=None) -> None:
            captured["headers"] = headers

        def __enter__(self):
            return self

        def __exit__(self, *a) -> bool:
            return False

        def post(self, path, json):
            return _FakeResp()

    monkeypatch.setattr(ws.httpx, "Client", _FakeClient)
    ids = ws.submit_all("http://x", [{"kind": "challenge"}], token="tok")
    assert ids == ["j1"]
    assert captured["headers"]["Authorization"] == f"{'Bearer'} tok"


def test_submit_all_no_header_without_token(monkeypatch) -> None:
    import workloads.submit as ws

    captured: dict = {}

    class _FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"job_id": "j1"}

    class _FakeClient:
        def __init__(self, base_url, timeout, headers=None) -> None:
            captured["headers"] = headers

        def __enter__(self):
            return self

        def __exit__(self, *a) -> bool:
            return False

        def post(self, path, json):
            return _FakeResp()

    monkeypatch.setattr(ws.httpx, "Client", _FakeClient)
    ws.submit_all("http://x", [{"kind": "challenge"}])
    assert captured["headers"] is None
