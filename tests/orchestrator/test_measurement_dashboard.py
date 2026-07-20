"""The dashboard's "Measured idle headroom" beat: the served HTML wires a poll of
GET /measurement, and that endpoint answers the documented empty-fleet shape on a fresh app.

The beat is static HTML updated by client-side JS, so it is verified at the HTML + API level
(there is no browser in CI). The measurement rollup math has its own coverage in
tests/orchestrator/test_profile_ingest.py; this only proves the UI is wired and that the
endpoint contract the UI depends on holds. Hermetic: in-memory db, no network, no sleeps.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from orchestrator.app import create_app


def test_dashboard_html_wires_the_measurement_beat() -> None:
    client = TestClient(create_app(":memory:"))
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.text

    # The beat's key elements are present in the served dashboard...
    assert 'id="measurement"' in html
    assert 'id="measurementCpuRange"' in html
    assert 'id="measurementEmpty"' in html
    # ...and the poll cycle actually fetches the endpoint that feeds them.
    assert 'operatorFetch("/measurement"' in html
    # An empty fleet stays calm instead of rendering a hollow "0-0%".
    assert "Awaiting measurement profiles" in html


def test_measurement_endpoint_empty_shape_on_fresh_app() -> None:
    client = TestClient(create_app(":memory:"))
    resp = client.get("/measurement")
    assert resp.status_code == 200
    m = resp.json()

    # Light shape assertion only (the rollup math is covered in test_profile_ingest.py).
    assert m["device_count"] == 0
    assert m["total_coverage_buckets"] == 0
    for key in ("margin_pct", "harvest_low", "harvest_high", "ram_avg", "ram_headroom"):
        assert key in m
    for meter in ("cpu", "gpu"):
        assert set(m[meter]) == {"avg", "peak", "recoverable_low", "recoverable_high"}
        assert m[meter]["recoverable_low"] == 0.0
        assert m[meter]["recoverable_high"] == 0.0
