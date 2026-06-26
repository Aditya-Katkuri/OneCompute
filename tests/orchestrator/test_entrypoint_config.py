import importlib

main_mod = importlib.import_module("orchestrator.__main__")


def test_coerce_port_accepts_valid():
    port, warning = main_mod._coerce_port("9000", 8080, "REEVE_PORT")
    assert port == 9000
    assert warning is None


def test_coerce_port_empty_uses_fallback_silently():
    port, warning = main_mod._coerce_port("", 8080, "REEVE_PORT")
    assert port == 8080
    assert warning is None


def test_coerce_port_non_integer_falls_back_with_warning():
    port, warning = main_mod._coerce_port("not-a-number", 8080, "REEVE_PORT")
    assert port == 8080
    assert warning


def test_coerce_port_out_of_range_falls_back_with_warning():
    port, warning = main_mod._coerce_port("99999", 8080, "REEVE_PORT")
    assert port == 8080
    assert warning


def test_banner_lists_worker_command_and_trust_caveat():
    lines = main_mod._banner_lines("0.0.0.0", 8080, "C:\\onecompute\\fleet.db")
    text = "\n".join(lines)
    assert "OneCompute Orchestrator" in text
    assert "python -m worker --url http://" in text
    assert ":8080" in text
    assert "Trust:" in text


def test_prepare_db_path_creates_missing_parent_dir(tmp_path):
    target = tmp_path / "deep" / "nested" / "fleet.db"
    result = main_mod._prepare_db_path(str(target))
    assert result == str(target.resolve()) or result == str(target)
    assert (tmp_path / "deep" / "nested").is_dir()


def test_prepare_db_path_is_absolute_and_never_raises():
    # A bare filename resolves to an absolute path under cwd without throwing.
    result = main_mod._prepare_db_path("reeve-orchestrator.db")
    assert main_mod.os.path.isabs(result)
