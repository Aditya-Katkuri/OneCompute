from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

import pytest

from contracts import Limits
from isolation import runner
from isolation.mxc import build_mxc_command, mxc_available, reset_mxc_probe_cache
from isolation.mxc_policy import build_policy, denies_delete_outside, permits_write


@pytest.fixture(autouse=True)
def _reset_probe_cache():
    reset_mxc_probe_cache()
    yield
    reset_mxc_probe_cache()


def _config_from_command(command: list[str]) -> dict:
    marker = command.index("--config-base64")
    raw = base64.b64decode(command[marker + 1]).decode("utf-8")
    return json.loads(raw)


def test_mxc_available_false_when_runtime_absent(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("ONECOMPUTE_MXC_EXE", raising=False)
    monkeypatch.delenv("MXC_BIN_DIR", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    assert mxc_available(force=True) is False
    assert mxc_available() is False


def test_mxc_probe_never_raises(monkeypatch, tmp_path: Path):
    import isolation.mxc as mxc

    fake_exe = tmp_path / "wxc-exec.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(mxc, "_find_mxc_exe", lambda: str(fake_exe))

    def boom(*args, **kwargs):
        raise OSError("probe exploded")

    monkeypatch.setattr(mxc.subprocess, "run", boom)
    assert mxc.mxc_available(force=True) is False


def test_mxc_probe_rejects_unknown_json(monkeypatch, tmp_path: Path):
    import subprocess

    import isolation.mxc as mxc

    fake_exe = tmp_path / "wxc-exec.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(mxc, "_find_mxc_exe", lambda: str(fake_exe))
    monkeypatch.setattr(
        mxc.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout="[]",
            stderr="",
        ),
    )

    assert mxc.mxc_available(force=True) is False


def test_mxc_probe_rejects_missing_process_container_support(monkeypatch, tmp_path: Path):
    import subprocess

    import isolation.mxc as mxc

    fake_exe = tmp_path / "wxc-exec.exe"
    fake_exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(mxc, "_find_mxc_exe", lambda: str(fake_exe))
    monkeypatch.setattr(
        mxc.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps({"available": True, "processContainer": {"supported": False}}),
            stderr="",
        ),
    )

    assert mxc.mxc_available(force=True) is False


def test_mxc_probe_accepts_supported_tier_before_dry_run(monkeypatch):
    import isolation.mxc as mxc

    assert mxc._probe_payload_is_supported(
        {
            "tier": "base-container",
            "needsDaclAugmentation": False,
            "warnings": [],
            "probes": {},
        }
    )
    assert not mxc._probe_payload_is_supported(
        {
            "tier": "base-container",
            "needsDaclAugmentation": False,
            "warnings": [],
            "processContainer": {"supported": False},
            "probes": {},
        }
    )
    assert not mxc._probe_payload_is_supported(
        {
            "tier": "base-container",
            "needsDaclAugmentation": True,
            "warnings": [],
            "probes": {},
        }
    )


def test_mxc_infra_classifier_does_not_match_job_policy_error():
    import isolation.mxc as mxc

    assert not mxc._looks_like_mxc_infra_error(
        "Traceback: ValueError: policy is not a valid op"
    )
    assert not mxc._looks_like_mxc_infra_error(
        "Traceback: RuntimeError: config item failed"
    )
    assert mxc._looks_like_job_failure(
        "Traceback: ValueError: unknown op: wxc-exec runtime failed"
    )
    assert mxc._looks_like_mxc_infra_error("wxc-exec runtime failed to start")


def test_active_boundary_prefers_mxc_when_available(monkeypatch):
    monkeypatch.setattr(runner, "mxc_available", lambda *args, **kwargs: False)
    monkeypatch.setattr(runner, "docker_available", lambda *args, **kwargs: False)
    assert runner.active_boundary() == "subprocess+jobobject"

    monkeypatch.setattr(runner, "docker_available", lambda *args, **kwargs: True)
    assert runner.active_boundary() == "docker"

    monkeypatch.setattr(runner, "mxc_available", lambda *args, **kwargs: True)
    assert runner.active_boundary() == "mxc"


def test_run_in_isolation_mxc_absent_keeps_existing_path(monkeypatch):
    monkeypatch.setattr(runner, "mxc_available", lambda *args, **kwargs: False)
    monkeypatch.setattr(runner, "docker_available", lambda *args, **kwargs: False)

    output = runner.run_in_isolation(
        "data.transform",
        {"items": [1, 2, 3], "op": "square"},
        limits=Limits(timeout_s=60),
    )

    assert output["results"] == [1, 4, 9]
    assert output["yielded"] is False


def test_run_in_isolation_falls_back_on_mxc_infra_error(monkeypatch, caplog):
    import isolation.mxc as mxc

    monkeypatch.setattr(runner, "mxc_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(runner, "docker_available", lambda *args, **kwargs: False)

    def fake_mxc(*args, **kwargs):
        raise mxc._MxcInfraError("fake MXC runtime failed")

    monkeypatch.setattr(runner, "_run_mxc", fake_mxc)
    caplog.set_level(logging.WARNING, logger="isolation.runner")

    output = runner.run_in_isolation("challenge", {"x": 6}, limits=Limits(timeout_s=60))

    assert output == {"y": 37}
    assert "MXC could not run the job" in caplog.text
    assert "falling back to Docker/subprocess" in caplog.text


def test_run_in_isolation_does_not_fallback_on_mxc_job_error(monkeypatch):
    monkeypatch.setattr(runner, "mxc_available", lambda *args, **kwargs: True)
    monkeypatch.setattr(runner, "docker_available", lambda *args, **kwargs: False)

    def fake_mxc(*args, **kwargs):
        raise RuntimeError("Traceback: ValueError: policy is not a valid op")

    monkeypatch.setattr(runner, "_run_mxc", fake_mxc)

    with pytest.raises(RuntimeError, match="policy is not a valid op"):
        runner.run_in_isolation("data.transform", {"items": [1], "op": "policy"})


def test_build_mxc_command_uses_policy_and_jobkit(tmp_path: Path):
    limits = Limits(mem_gb=2.0)
    command = build_mxc_command(
        tmp_path,
        "in.json",
        "out.json",
        limits,
        "onecompute-test",
    )
    config = _config_from_command(command)
    policy = build_policy(tmp_path / "work", limits, job_id="onecompute-test")
    work_dir = Path(policy["filesystem"]["work_dir"])
    outside = tmp_path.parent / "outside.txt"

    assert command[0].endswith("wxc-exec.exe") or command[0].endswith("wxc-exec")
    assert "python -m jobkit" in config["process"]["commandLine"]
    input_dir = tmp_path / "input"
    payload_dir = tmp_path / "payload" / "src"
    assert str(input_dir / "in.json") in config["process"]["commandLine"]
    assert str(work_dir / "out.json") in config["process"]["commandLine"]
    assert f"PYTHONPATH={payload_dir}" in config["process"]["env"]
    assert config["process"]["cwd"] == str(work_dir)
    assert config["filesystem"]["readwritePaths"] == [str(work_dir)]
    assert config["filesystem"]["readonlyPaths"] == [str(input_dir), str(payload_dir)]
    assert config["ui"] == {"disable": True, "clipboard": "none", "injection": False}
    for denied_path in config["filesystem"]["deniedPaths"]:
        denied = Path(denied_path)
        assert not _is_same_or_child(work_dir, denied)
        assert not _is_same_or_child(payload_dir, denied)
    assert config["fallback"]["allowDaclMutation"] is False
    assert policy["privileges"]["elevation"] == "deny"
    assert policy["privileges"]["allow_new_privileges"] is False
    assert denies_delete_outside(policy, outside) is True
    assert permits_write(policy, work_dir / "out.json") is True
    assert permits_write(policy, payload_dir / "jobkit" / "execute.py") is False
    assert permits_write(policy, input_dir / "in.json") is False
    assert permits_write(policy, outside) is False

    writable_rules = [
        rule for rule in policy["filesystem"]["rules"] if "write" in rule["access"]
    ]
    assert writable_rules == [
        {
            "effect": "allow",
            "path": str(work_dir),
            "access": ["read", "write"],
            "allow_delete": True,
            "allow_rename": True,
        }
    ]


def _is_same_or_child(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        candidate_text = str(candidate.resolve(strict=False)).replace("/", "\\").casefold()
        root_text = str(root.resolve(strict=False)).replace("/", "\\").casefold().rstrip("\\/")
        return candidate_text == root_text or candidate_text.startswith(f"{root_text}\\")
