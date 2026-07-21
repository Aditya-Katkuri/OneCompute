import importlib

import pytest

from orchestrator.mtls_protocol import VerifiedClientCertH11Protocol

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


def test_secure_measurement_pilot_preset_enables_fail_closed_controls(
    tmp_path, monkeypatch
):
    captured = {}

    def fake_serve(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(main_mod, "_serve", fake_serve)
    result = main_mod.main(
        [
            "--secure-measurement-pilot",
            "--tls-cert",
            "server.pem",
            "--tls-key",
            "server.key",
            "--tls-client-ca",
            "client-ca.pem",
            "--submit-token",
            "submit-token",
            "--admin-token",
            "admin-token",
            "--db",
            str(tmp_path / "pilot.db"),
        ]
    )

    assert result == 0
    assert captured["require_approval"] is True
    assert captured["bind_device_identity"] is True
    assert captured["tls_client_ca"] == "client-ca.pem"
    assert captured["submit_token"] == "submit-token"
    assert captured["admin_token"] == "admin-token"


def test_secure_measurement_pilot_preset_rejects_incomplete_configuration() -> None:
    with pytest.raises(SystemExit):
        main_mod.main(["--secure-measurement-pilot"])


def test_secure_measurement_pilot_preset_requires_separate_tokens() -> None:
    with pytest.raises(SystemExit):
        main_mod.main(
            [
                "--secure-measurement-pilot",
                "--tls-cert",
                "server.pem",
                "--tls-key",
                "server.key",
                "--tls-client-ca",
                "client-ca.pem",
                "--submit-token",
                "same-token",
                "--admin-token",
                "same-token",
            ]
        )


def test_serve_uses_peer_certificate_protocol_for_mtls(monkeypatch) -> None:
    captured = {}

    class FakeServer:
        def __init__(self, config):
            captured["config"] = config

        def run(self):
            captured["ran"] = True

    def fake_config(app, **kwargs):
        captured["config_kwargs"] = kwargs
        return object()

    monkeypatch.setattr(main_mod, "create_app", lambda *args, **kwargs: object())
    monkeypatch.setattr(main_mod, "server_ssl_kwargs", lambda *args, **kwargs: {})
    monkeypatch.setattr(main_mod.uvicorn, "Config", fake_config)
    monkeypatch.setattr(main_mod.uvicorn, "Server", FakeServer)

    main_mod._serve(
        "127.0.0.1",
        8080,
        ":memory:",
        "info",
        "server.crt",
        "server.key",
        tls_client_ca="client-ca.crt",
        bind_device_identity=True,
    )

    assert captured["config_kwargs"]["http"] is VerifiedClientCertH11Protocol
    assert captured["ran"] is True
