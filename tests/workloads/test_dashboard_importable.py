import dashboard


def test_dashboard_index_html_importable() -> None:
    assert dashboard.INDEX_HTML.exists()
    assert '"/state"' in dashboard.INDEX_HTML.read_text(encoding="utf-8")

