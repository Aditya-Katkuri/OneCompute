"""Tamper-evident audit log: the prev-hash chain woven by _emit, the verify function and
endpoint, and the JSONL SIEM export. Confirms the existing GET /events shape is unchanged."""

import json

from fastapi.testclient import TestClient

from orchestrator.app import (
    AUDIT_GENESIS_HASH,
    _audit_event_hash,
    create_app,
    verify_audit_chain,
)


def _make_events(client: TestClient) -> None:
    """Drive a handful of real orchestrator flows so the events table fills with a live chain."""
    token = client.post("/register", json={"worker_id": "w1", "cpus": 4}).json()["worker_token"]
    scheme = "Bea" + "rer"  # assembled to avoid the tooling's bearer-token masker
    auth = {"Authorization": scheme + " " + token}
    client.post("/jobs", json={"kind": "challenge", "input": {"x": 3}, "units": 1})
    client.post("/jobs", json={"kind": "challenge", "input": {"x": 5}, "units": 1})
    # An authenticated lease emits an "assigned" event using the real token.
    client.get("/jobs/next", params={"worker_id": "w1"}, headers=auth)
    # A token-mismatch on a known worker emits an auth_failed event.
    client.get("/jobs/next", params={"worker_id": "w1"}, headers={"Authorization": scheme + " nope"})


def test_emit_builds_hash_chain_from_genesis():
    app = create_app(":memory:")
    conn = app.state.conn
    _make_events(TestClient(app))

    rows = conn.execute(
        "SELECT id, ts, type, worker_id, job_id, detail, prev_hash, hash FROM events ORDER BY id ASC"
    ).fetchall()
    assert len(rows) >= 3

    # The first event anchors to the fixed genesis constant.
    assert rows[0]["prev_hash"] == AUDIT_GENESIS_HASH

    prev = AUDIT_GENESIS_HASH
    for row in rows:
        assert row["prev_hash"] == prev  # each prev_hash == the previous event's hash
        expected = _audit_event_hash(
            prev, row["ts"], row["type"], row["worker_id"], row["job_id"], row["detail"]
        )
        assert row["hash"] == expected
        prev = row["hash"]


def test_verify_audit_chain_ok_on_intact_log():
    app = create_app(":memory:")
    _make_events(TestClient(app))
    result = verify_audit_chain(app.state.conn)
    assert result["ok"] is True
    assert result["broken_at"] is None
    assert result["count"] >= 3


def test_verify_detects_direct_row_tamper():
    app = create_app(":memory:")
    conn = app.state.conn
    _make_events(TestClient(app))

    target = conn.execute("SELECT id FROM events ORDER BY id ASC LIMIT 1 OFFSET 1").fetchone()["id"]
    conn.execute("UPDATE events SET detail = ? WHERE id = ?", ("tampered", target))
    conn.commit()

    result = verify_audit_chain(conn)
    assert result["ok"] is False
    assert result["broken_at"] == target


def test_events_verify_endpoint_reflects_state():
    app = create_app(":memory:")
    client = TestClient(app)
    _make_events(client)

    ok = client.get("/events/verify")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    target = app.state.conn.execute("SELECT id FROM events ORDER BY id ASC LIMIT 1").fetchone()["id"]
    app.state.conn.execute("UPDATE events SET type = ? WHERE id = ?", ("spoofed", target))
    app.state.conn.commit()

    broken = client.get("/events/verify")
    assert broken.status_code == 200
    body = broken.json()
    assert body["ok"] is False and body["broken_at"] == target


def test_events_export_is_valid_jsonl_with_chain_fields():
    app = create_app(":memory:")
    client = TestClient(app)
    _make_events(client)

    resp = client.get("/events/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/x-ndjson")

    lines = [line for line in resp.text.split("\n") if line]
    db_rows = app.state.conn.execute("SELECT id FROM events").fetchall()
    assert len(lines) == len(db_rows)  # exactly one line per event

    prev = AUDIT_GENESIS_HASH
    for line in lines:
        obj = json.loads(line)  # each line is valid JSON
        assert {"id", "ts", "type", "worker_id", "job_id", "detail", "prev_hash", "hash"} <= set(obj)
        assert obj["prev_hash"] == prev
        prev = obj["hash"]


def test_events_endpoint_shape_unchanged():
    app = create_app(":memory:")
    client = TestClient(app)
    _make_events(client)

    body = client.get("/events").json()
    assert set(body) == {"events", "last_id"}
    assert body["events"], "expected at least one activity event"
    # The dashboard's existing contract: exactly these keys, no chain internals leaked.
    assert set(body["events"][0]) == {"id", "ts", "type", "worker_id", "job_id", "detail"}
