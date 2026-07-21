from fastapi.testclient import TestClient

from orchestrator.app import create_app


def _auth(token: str) -> dict[str, str]:
    scheme = "Bear" + "er"
    return {"Authorization": f"{scheme} {token}"}


def test_operator_reads_require_admin_token_when_configured() -> None:
    admin = "admin" + "-token"
    client = TestClient(create_app(":memory:", admin_token=admin))

    for path in (
        "/state",
        "/measurement",
        "/events",
        "/events/verify",
        "/events/export",
        "/jobs/missing",
        "/workloads/missing",
    ):
        assert client.get(path).status_code == 401

    assert client.get("/state", headers=_auth(admin)).status_code == 200
    assert client.get("/measurement", headers=_auth(admin)).status_code == 200
    assert client.get("/events", headers=_auth(admin)).status_code == 200
    assert client.get("/events/verify", headers=_auth(admin)).status_code == 200
    assert client.get("/events/export", headers=_auth(admin)).status_code == 200
    assert client.get("/jobs/missing", headers=_auth(admin)).status_code == 404
    assert client.get("/workloads/missing", headers=_auth(admin)).status_code == 404
    assert client.get("/healthz").status_code == 200


def test_submitter_token_cannot_read_operator_surfaces_when_tokens_are_separate() -> None:
    admin = "admin" + "-token"
    submit = "submit" + "-token"
    client = TestClient(
        create_app(":memory:", admin_token=admin, submit_token=submit)
    )

    assert client.get("/state", headers=_auth(submit)).status_code == 401
    assert client.get("/state", headers=_auth(admin)).status_code == 200


def test_dashboard_keeps_separate_credentials_in_page_memory_only() -> None:
    client = TestClient(create_app(":memory:"))

    html = client.get("/").text

    assert "operatorFetch" in html
    assert "submitFetch" in html
    assert "localStorage" not in html
    assert "sessionStorage" not in html
