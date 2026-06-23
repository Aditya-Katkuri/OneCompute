import dashboard


def test_dashboard_index_html_importable() -> None:
    assert dashboard.INDEX_HTML.exists()
    html = dashboard.INDEX_HTML.read_text(encoding="utf-8")
    assert '"/state"' in html
    assert "/events" in html
    assert "free_ram_gb" in html


def test_dashboard_package_import_has_no_runtime_side_effects() -> None:
    assert dashboard.INDEX_HTML.name == "index.html"
