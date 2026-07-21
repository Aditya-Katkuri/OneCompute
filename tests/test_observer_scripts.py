import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which("pwsh")


def test_personal_observer_has_explicit_stop_and_purge_controls() -> None:
    text = (ROOT / "scripts" / "observe_me.ps1").read_text(encoding="utf-8")

    assert "[switch]$Purge" in text
    assert "Stop-Process -Id" in text
    assert "Stop-Process -Name" not in text
    assert "Stop-Process -Id $observerPid -ErrorAction Stop" not in text
    assert "pilot-telemetry.jsonl.*" in text
    assert '".observer-id.*.tmp"' in text
    assert '".observer-config.*.tmp"' in text
    assert '".$profileLeaf.*.tmp"' in text
    assert '"$ProfilePath.lock"' in text
    assert '"observer-id"' in text
    assert '"--no-telemetry"' in text
    assert '"--profile", $ProfilePath' in text
    assert "(?=\\s|$)" in text
    assert "executable_path = $Py" in text
    assert "startup_dir = $StartupRoot" in text
    assert "Remote measurement uploads require HTTPS" in text
    assert "for pinned mTLS" in text
    assert '[int]$IntervalSec = 30' in text
    assert '-Encoding ASCII' not in text
    assert 'else { "python" }' not in text
    assert "Unregister-ScheduledTask" in text


def test_managed_installer_requires_pinned_mtls_for_remote_fleet() -> None:
    text = (ROOT / "scripts" / "install_observer.ps1").read_text(encoding="utf-8")

    assert "Remote measurement pilots require HTTPS" in text
    assert "require -TlsCa, -ClientCert, and -ClientKey" in text
    assert "[switch]$AllowInsecureLocalhost" in text
    assert "[switch]$Purge" in text
    assert '"--no-telemetry"' in text
    assert '"--profile", $ProfilePath' in text
    assert "(?=\\s|$)" in text
    assert "executable_path = $execute" in text
    assert "startup_dir = $StartupRoot" in text
    assert "ExtraArgs" not in text
    assert "-RunLevel Highest" not in text
    assert "-RunLevel Limited" in text
    assert "S-1-5-18" in text
    assert "OneCompute-Observer.lnk" in text
    assert '".observer-id.*.tmp"' in text
    assert '".observer-config.*.tmp"' in text
    assert '"$ProfilePath.lock"' in text


@pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="requires PowerShell on Windows")
def test_personal_observer_dry_run_uses_absolute_interpreter_and_unicode_path(tmp_path) -> None:
    startup = tmp_path / "Startup"
    profile = tmp_path / "Zoë" / "usage profile.json"
    task_name = f"OneCompute Observer Test {uuid.uuid4().hex}"
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(ROOT / "scripts" / "observe_me.ps1"),
            "-Install",
            "-DryRun",
            "-StartupDir",
            str(startup),
            "-TaskName",
            task_name,
            "-ProfileFile",
            str(profile),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert str(profile) in result.stdout
    assert ".venv\\Scripts\\python.exe" in result.stdout
    assert not (startup / "OneCompute-Observer.lnk").exists()


@pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="requires PowerShell on Windows")
def test_personal_observer_refuses_bare_python_fallback(tmp_path) -> None:
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    copied = script_dir / "observe_me.ps1"
    shutil.copy2(ROOT / "scripts" / "observe_me.ps1", copied)
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(copied),
            "-Install",
            "-DryRun",
            "-StartupDir",
            str(tmp_path / "Startup"),
            "-TaskName",
            f"OneCompute Observer Test {uuid.uuid4().hex}",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode != 0
    assert "Observer Python not found" in (result.stdout + result.stderr)


@pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="requires PowerShell on Windows")
def test_managed_observer_dry_run_uses_limited_fixed_interpreter_and_unicode_path(
    tmp_path,
) -> None:
    profile = tmp_path / "Zoë" / "usage profile.json"
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(ROOT / "scripts" / "install_observer.ps1"),
            "-Url",
            "http://localhost:8080",
            "-AllowInsecureLocalhost",
            "-DryRun",
            "-RepoDir",
            str(ROOT),
            "-StartupDir",
            str(tmp_path / "Startup"),
            "-TaskName",
            f"OneCompute Observer Test {uuid.uuid4().hex}",
            "-ProfileFile",
            str(profile),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    assert str(profile) in result.stdout
    assert ".venv\\Scripts\\python.exe" in result.stdout
    assert "--measure-only" in result.stdout
    assert "--no-telemetry" in result.stdout


@pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="requires PowerShell on Windows")
def test_managed_status_recovers_the_configured_task_name(tmp_path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    data_dir = local_app_data / "OneCompute"
    data_dir.mkdir(parents=True)
    configured_task = f"Configured Observer {uuid.uuid4().hex}"
    (data_dir / "observer-config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "profile_path": str(data_dir / "usage_profile.json"),
                "mechanism": "scheduled-task",
                "task_name": configured_task,
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local_app_data)

    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(ROOT / "scripts" / "install_observer.ps1"),
            "-Status",
            "-StartupDir",
            str(tmp_path / "Startup"),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert f"Task '{configured_task}' is not installed." in result.stdout


@pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="requires PowerShell on Windows")
def test_personal_purge_removes_configured_profile_and_all_known_residue(tmp_path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    data_dir = local_app_data / "OneCompute"
    startup = tmp_path / "Startup"
    custom_dir = tmp_path / "custom"
    data_dir.mkdir(parents=True)
    startup.mkdir()
    custom_dir.mkdir()
    profile = custom_dir / "volunteer.json"
    artifacts = [
        profile,
        Path(str(profile) + ".lock"),
        custom_dir / ".volunteer.json.123.tmp",
        custom_dir / ".volunteer.json.123.probe",
        custom_dir / "volunteer.corrupt-20260720-abc.json",
        data_dir / "observer-id",
        data_dir / ".observer-id.abc.tmp",
        data_dir / ".observer-config.abc.tmp",
        data_dir / "pilot-telemetry.jsonl",
        data_dir / "pilot-telemetry.jsonl.1",
        startup / "OneCompute-Observer.lnk",
        startup / "OneCompute-Observer.cmd",
    ]
    for artifact in artifacts:
        artifact.write_text("test", encoding="utf-8")
    (data_dir / "observer-config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "profile_path": str(profile),
                "startup_dir": str(startup),
                "mechanism": "startup",
                "task_name": "not-installed",
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local_app_data)
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(ROOT / "scripts" / "observe_me.ps1"),
            "-Purge",
            "-TaskName",
            f"OneCompute Observer Test {uuid.uuid4().hex}",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert all(not artifact.exists() for artifact in artifacts)
    assert not (data_dir / "observer-config.json").exists()


@pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="requires PowerShell on Windows")
def test_managed_purge_removes_configured_profile_and_all_known_residue(tmp_path) -> None:
    local_app_data = tmp_path / "LocalAppData"
    data_dir = local_app_data / "OneCompute"
    startup = tmp_path / "Startup"
    custom_dir = tmp_path / "custom"
    data_dir.mkdir(parents=True)
    startup.mkdir()
    custom_dir.mkdir()
    profile = custom_dir / "volunteer.json"
    artifacts = [
        profile,
        Path(str(profile) + ".lock"),
        custom_dir / ".volunteer.json.123.tmp",
        custom_dir / ".volunteer.json.123.probe",
        custom_dir / "volunteer.corrupt-20260720-abc.json",
        data_dir / "observer-id",
        data_dir / ".observer-id.abc.tmp",
        data_dir / ".observer-config.abc.tmp",
        data_dir / "pilot-telemetry.jsonl",
        data_dir / "pilot-telemetry.jsonl.1",
        startup / "OneCompute-Observer.lnk",
        startup / "OneCompute-Observer.cmd",
    ]
    for artifact in artifacts:
        artifact.write_text("test", encoding="utf-8")
    (data_dir / "observer-config.json").write_text(
        json.dumps(
            {
                "version": 1,
                "profile_path": str(profile),
                "startup_dir": str(startup),
                "mechanism": "scheduled-task",
                "task_name": "not-installed",
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local_app_data)
    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(ROOT / "scripts" / "install_observer.ps1"),
            "-Purge",
            "-TaskName",
            f"OneCompute Observer Test {uuid.uuid4().hex}",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert all(not artifact.exists() for artifact in artifacts)
    assert not (data_dir / "observer-config.json").exists()
