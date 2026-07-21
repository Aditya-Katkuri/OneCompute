from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_personal_observer_has_explicit_stop_and_purge_controls() -> None:
    text = (ROOT / "scripts" / "observe_me.ps1").read_text(encoding="utf-8")

    assert "[switch]$Purge" in text
    assert "Stop-Process -Id" in text
    assert "Stop-Process -Name" not in text
    assert "pilot-telemetry.jsonl.*" in text
    assert '"observer-id"' in text
    assert '"--no-telemetry"' in text
    assert "Remote measurement uploads require HTTPS" in text


def test_managed_installer_requires_pinned_mtls_for_remote_fleet() -> None:
    text = (ROOT / "scripts" / "install_observer.ps1").read_text(encoding="utf-8")

    assert "Remote measurement pilots require HTTPS" in text
    assert "require -TlsCa, -ClientCert, and -ClientKey" in text
    assert "[switch]$AllowInsecureLocalhost" in text
    assert "[switch]$Purge" in text
    assert '"--no-telemetry"' in text
    assert "ExtraArgs" not in text
