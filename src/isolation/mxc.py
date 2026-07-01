"""MXC command construction plus a fail-closed runtime probe.

Microsoft Execution Containers are an optional preview backend for the T3 isolation
seam. What OneCompute places inside the sandbox is a lightweight, deterministic job
(``python -m jobkit``), not an autonomous agent: MXC was built to contain agents, and
we reuse the same OS-enforced containment for a far more constrained principal (no
tool use, no UI, no network by default, no persistence, short-lived).

Importing this module has no runtime cost. Availability means the native
``wxc-exec`` runtime is present and its probe succeeds, not just that an executable
name exists on PATH. Missing preview bits, failed probes, malformed probe output,
and launch failures all read as unavailable or infrastructure errors so current
machines keep their Docker or Job Object behavior.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from contracts import Limits
from isolation.jobobject import assign_process, close, create_job_object
from isolation.mxc_policy import build_policy, policy_to_json

MXC_EXE = "wxc-exec.exe" if os.name == "nt" else "wxc-exec"
MXC_EXE_ENV = "ONECOMPUTE_MXC_EXE"
MXC_BIN_DIR_ENV = "MXC_BIN_DIR"
CONFIG_SCHEMA_VERSION = "0.7.0-alpha"

_PROBE_TTL_S = 30.0
_PROBE_TIMEOUT_S = 5.0
_STOP_DEADLINE_S = 1.0
_probe_lock = threading.Lock()
_probe_cache: tuple[float, bool] | None = None

_MXC_INFRA_MARKERS = (
    "wxc-exec",
    "execution container",
    "processcontainer",
    "process container",
    "probe",
    "host preparation",
    "host prep",
    "container failed to start",
    "runtime failed",
)

_JOB_FAILURE_MARKERS = (
    "traceback (most recent call last)",
    "valueerror",
    "runtimeerror",
    "isolated job failed",
)


class _MxcInfraError(RuntimeError):
    """MXC itself could not run the job, so the caller may fall back."""


def mxc_available(*, force: bool = False) -> bool:
    """Return whether the MXC preview runtime is usable, cached for a short TTL.

    The probe never raises. It fails closed on absent binaries, probe errors, timeouts,
    malformed probe JSON, and probe warnings that host preparation is still required.
    """
    global _probe_cache
    now = time.monotonic()
    with _probe_lock:
        if not force and _probe_cache is not None:
            ts, value = _probe_cache
            if now - ts < _PROBE_TTL_S:
                return value

    try:
        available = _probe_runtime()
    except Exception:
        available = False
    with _probe_lock:
        _probe_cache = (time.monotonic(), available)
    return available


def reset_mxc_probe_cache() -> None:
    """Drop the cached MXC runtime probe result."""
    global _probe_cache
    with _probe_lock:
        _probe_cache = None


def build_mxc_command(
    work_dir: Path,
    in_name: str,
    out_name: str,
    limits: Limits,
    name: str,
    policy_path_or_b64: str | os.PathLike[str] | None = None,
    *,
    input_dir: Path | None = None,
    payload_dir: Path | None = None,
    writable_dir: Path | None = None,
) -> list[str]:
    """Build a ``wxc-exec`` command for file-based jobkit execution.

    By default the MXC config is embedded with ``--config-base64``. If a filesystem
    path is supplied, the config is written there and ``--config`` is used instead.
    The embedded config includes the declarative OneCompute policy produced by
    ``mxc_policy.build_policy`` so tests and diagnostics inspect the same data that
    drives the runtime command.
    """
    config = build_mxc_config(
        work_dir,
        in_name,
        out_name,
        limits,
        name,
        input_dir=input_dir,
        payload_dir=payload_dir,
        writable_dir=writable_dir,
    )
    exe = _mxc_exe_for_command()
    if policy_path_or_b64 is None:
        return [exe, "--config-base64", _config_to_base64(config)]

    raw_policy_arg = os.fspath(policy_path_or_b64)
    if isinstance(policy_path_or_b64, os.PathLike) or _looks_like_config_path(raw_policy_arg):
        target = Path(raw_policy_arg)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_config_to_json(config), encoding="utf-8")
        return [exe, "--config", str(target)]

    return [exe, "--config-base64", raw_policy_arg]


def _least_privilege(privileges: dict[str, Any]) -> bool:
    """True only if the policy denies elevation, new privileges, and admin.

    Links wxc-exec's ``leastPrivilege`` flag to the declarative policy so the
    no-elevation guarantee is derived from the policy, not hard-coded here.
    """
    return (
        privileges.get("elevation") == "deny"
        and not privileges.get("allow_new_privileges", False)
        and not privileges.get("allow_admin", False)
    )


def build_mxc_config(
    work_dir: Path,
    in_name: str,
    out_name: str,
    limits: Limits,
    name: str,
    *,
    input_dir: Path | None = None,
    payload_dir: Path | None = None,
    writable_dir: Path | None = None,
) -> dict[str, Any]:
    """Translate the canonical OneCompute policy into wxc-exec's supported config fields.

    The declarative policy from ``build_policy`` is mapped to what the runtime enforces:
    read-only input + payload, a single writable work dir (so delete/rename outside the
    sandbox is impossible by construction), deny-by-default elsewhere plus the protected
    locations, network per ``limits``, and ``leastPrivilege`` + empty ``capabilities`` as
    wxc-exec's representation of the policy's no-elevation / no-new-privileges / no-admin
    guarantee. ``leastPrivilege`` is derived from the policy (not hard-coded) so the
    elevation guarantee tracks the policy rather than drifting from it.
    """
    input_path, payload_path, write_path = _layout_paths(
        work_dir,
        input_dir=input_dir,
        payload_dir=payload_dir,
        writable_dir=writable_dir,
    )
    policy = build_policy(write_path, limits, payload_subdir="src", job_id=name)
    network_mode = policy["network"]["mode"]
    protected_rule = next(
        rule
        for rule in policy["filesystem"]["deny_rules"]
        if rule.get("scope") == "protected_locations"
    )

    return {
        "version": CONFIG_SCHEMA_VERSION,
        "containerId": name,
        "containment": "process",
        "fallback": {
            "allowDaclMutation": False,
        },
        "lifecycle": {
            "destroyOnExit": True,
            "preservePolicy": False,
        },
        "process": {
            "commandLine": _command_line([
                "python",
                "-m",
                "jobkit",
                str(input_path / in_name),
                str(write_path / out_name),
            ]),
            "cwd": str(write_path),
            "env": [
                f"PYTHONPATH={payload_path}",
                "PYTHONDONTWRITEBYTECODE=1",
                "ONECOMPUTE_ISOLATION=mxc",
                "ONECOMPUTE_PRINCIPAL=job",
                f"ONECOMPUTE_JOB_ID={name}",
            ],
            "timeout": max(int(float(limits.timeout_s) * 1000), 1),
        },
        "filesystem": {
            "readonlyPaths": [str(input_path), str(payload_path)],
            "readwritePaths": [str(write_path)],
            "deniedPaths": _filter_denied_paths(
                [location["path"] for location in protected_rule["locations"]],
                [input_path, payload_path, write_path],
            ),
        },
        "network": {
            "defaultPolicy": "allow" if network_mode == "host" else "block",
        },
        "ui": {
            "disable": True,
            "clipboard": "none",
            "injection": False,
        },
        "processContainer": {
            "leastPrivilege": _least_privilege(policy["privileges"]),
            "capabilities": [],
        },
    }


def start_mxc(
    work_dir: Path,
    in_name: str,
    out_name: str,
    limits: Limits,
    name: str | None = None,
    *,
    input_dir: Path | None = None,
    payload_dir: Path | None = None,
    writable_dir: Path | None = None,
):
    """Start ``wxc-exec`` under a Job Object and return process, handle, and ID."""
    container_id = name or new_mxc_container_name()
    command = build_mxc_command(
        work_dir,
        in_name,
        out_name,
        limits,
        container_id,
        input_dir=input_dir,
        payload_dir=payload_dir,
        writable_dir=writable_dir,
    )
    proc = _popen_mxc(command, work_dir)
    job_handle = create_job_object(limits)
    try:
        job_handle.process = proc
    except Exception:
        pass
    assign_process(job_handle, proc.pid)
    return proc, job_handle, container_id


def _run_mxc(
    in_path: Path,
    out_path: Path,
    work_dir: Path,
    limits: Limits,
    should_yield,
    *,
    input_dir: Path | None = None,
    payload_dir: Path | None = None,
    writable_dir: Path | None = None,
) -> dict:
    """Run one job under MXC, preserving the existing yield and timeout contract."""
    proc, job_handle, container_id = start_mxc(
        work_dir,
        in_path.name,
        out_path.name,
        limits,
        input_dir=input_dir,
        payload_dir=payload_dir,
        writable_dir=writable_dir,
    )
    outcome = "ok"
    stdout = ""
    stderr = ""
    try:
        deadline = time.monotonic() + max(float(limits.timeout_s), 0.001)
        while proc.poll() is None:
            if should_yield():
                outcome = "yielded"
                _stop_mxc(container_id, proc, job_handle)
                break
            if time.monotonic() > deadline:
                outcome = "timeout"
                _stop_mxc(container_id, proc, job_handle)
                break
            time.sleep(0.02)

        try:
            stdout, stderr = proc.communicate(timeout=10)
        except Exception:
            _stop_mxc(container_id, proc, job_handle)
            try:
                proc.kill()
            except Exception:
                pass
    except BaseException:
        # An unexpected error (e.g. should_yield() raising inside the poll loop)
        # must not orphan the sandboxed process or leak the Job Object handle.
        # Tear down the container, then let close() (kill-on-close) reap the tree.
        _stop_mxc(container_id, proc, job_handle)
        raise
    finally:
        close(job_handle)

    if outcome == "yielded":
        return {"yielded": True, "results": []}
    if outcome == "timeout":
        raise TimeoutError("isolated MXC job timed out")

    if proc.returncode != 0:
        message = (stderr or stdout or "mxc isolated job failed").strip()
        if _looks_like_job_failure(message):
            raise RuntimeError(message)
        if _looks_like_mxc_infra_error(message):
            raise _MxcInfraError(message)
        raise RuntimeError(message)

    if not out_path.exists():
        raise _MxcInfraError("mxc job completed without producing an output file")

    with out_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _stop_mxc(container_id: str, proc: subprocess.Popen, job_handle=None) -> None:
    """Tear down an MXC run using Job Object close, then process terminate or kill."""
    close(job_handle)
    try:
        if proc.poll() is None:
            proc.terminate()
    except Exception:
        pass

    deadline = time.monotonic() + _STOP_DEADLINE_S
    while proc.poll() is None and time.monotonic() < deadline:
        try:
            proc.wait(timeout=0.05)
        except Exception:
            pass

    try:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=0.2)
    except Exception:
        pass
    _best_effort_mxc_teardown(container_id)


def new_mxc_container_name() -> str:
    """Return a unique OneCompute MXC container ID for one job."""
    return f"onecompute-job-{uuid.uuid4().hex[:12]}"


def _probe_runtime() -> bool:
    try:
        exe = _find_mxc_exe()
        if exe is None:
            return False
        proc = subprocess.run(
            [exe, "--probe"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except Exception:
        return False
    if proc.returncode != 0:
        return False

    text = (proc.stdout or "").strip()
    if not text:
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    return (
        _probe_payload_is_supported(payload)
        and _probe_policy_dry_run(exe)
        and _probe_filesystem_denial(exe)
        and _probe_kill_semantics(exe)
    )


def _probe_payload_is_supported(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    text = json.dumps(payload, sort_keys=True).lower()
    if "host preparation" in text or "host prep" in text:
        return False
    if "not supported" in text or "unsupported" in text:
        return False
    if _probe_has_process_container_rejection(payload):
        return False
    return _probe_has_supported_tier(payload) or _probe_has_process_container_support(payload)


def _probe_has_supported_tier(payload: dict[str, Any]) -> bool:
    tier = payload.get("tier")
    if not isinstance(tier, str):
        return False
    if tier.casefold() not in {
        "base-container",
        "appcontainer-bfs",
        "appcontainer-dacl",
        "processcontainer",
        "process-container",
    }:
        return False
    if payload.get("needsDaclAugmentation") is True:
        return False
    warnings = payload.get("warnings")
    return not warnings


def _probe_has_process_container_support(payload: dict[str, Any]) -> bool:
    stack: list[Any] = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                normalized = key.replace("_", "").casefold()
                if normalized == "processcontainer":
                    return _support_value_is_true(value)
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return False


def _probe_has_process_container_rejection(payload: dict[str, Any]) -> bool:
    stack: list[Any] = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                normalized = key.replace("_", "").casefold()
                if normalized == "processcontainer" and _support_value_is_false(value):
                    return True
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return False


def _support_value_is_true(value: Any) -> bool:
    if value is True:
        return True
    if not isinstance(value, dict):
        return False
    if value.get("supported") is False or value.get("available") is False:
        return False
    if value.get("supported") is True or value.get("available") is True:
        return True
    return any(_support_value_is_true(child) for child in value.values())


def _support_value_is_false(value: Any) -> bool:
    if value is False:
        return True
    if not isinstance(value, dict):
        return False
    return value.get("supported") is False or value.get("available") is False


def _probe_policy_dry_run(exe: str) -> bool:
    probe_dir = Path.cwd() / f".onecompute-mxc-dryrun-{uuid.uuid4().hex[:8]}"
    _ensure_layout_dirs(probe_dir)
    config = build_mxc_config(
        probe_dir,
        "in.json",
        "out.json",
        Limits(mem_gb=0.1, timeout_s=1),
        "onecompute-probe",
    )
    config_arg = _config_to_base64(config)
    try:
        proc = subprocess.run(
            [exe, "--dry-run", "--config-base64", config_arg],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except Exception:
        return False
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)
    return proc.returncode == 0 and not _probe_output_has_blocking_warning(
        f"{proc.stdout}\n{proc.stderr}"
    )


def _probe_filesystem_denial(exe: str) -> bool:
    container_id = "onecompute-fs-probe"
    probe_dir = Path.cwd() / f".onecompute-mxc-fs-probe-{uuid.uuid4().hex[:8]}"
    _, _, write_path = _ensure_layout_dirs(probe_dir)
    outside_marker = probe_dir / "outside-marker.txt"
    result_path = write_path / "fs-result.json"
    outside_marker.write_text("keep", encoding="utf-8")
    config = build_mxc_config(
        probe_dir,
        "in.json",
        "out.json",
        Limits(mem_gb=0.1, timeout_s=5),
        container_id,
    )
    script = (
        "import json,pathlib;"
        f"p=pathlib.Path({str(outside_marker)!r});"
        f"r=pathlib.Path({str(result_path)!r});"
        "read_denied=False\n"
        "delete_denied=False\n"
        "try:\n"
        " p.read_text(encoding='utf-8')\n"
        "except Exception:\n"
        " read_denied=True\n"
        "try:\n"
        " p.unlink()\n"
        "except Exception:\n"
        " delete_denied=True\n"
        "r.write_text(json.dumps({'read_denied': read_denied, 'delete_denied': delete_denied}), encoding='utf-8')\n"
    )
    config["process"]["commandLine"] = _command_line(["python", "-c", script])
    config["process"]["timeout"] = 5_000
    try:
        proc = subprocess.run(
            [exe, "--config-base64", _config_to_base64(config)],
            cwd=str(probe_dir),
            env=_mxc_env(),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode != 0 or not result_path.exists():
            return False
        result = json.loads(result_path.read_text(encoding="utf-8"))
        return (
            result.get("read_denied") is True
            and result.get("delete_denied") is True
            and outside_marker.exists()
        )
    except Exception:
        return False
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def _probe_kill_semantics(exe: str) -> bool:
    container_id = "onecompute-kill-probe"
    limits = Limits(mem_gb=0.1, timeout_s=30)
    probe_dir = Path.cwd() / f".onecompute-mxc-probe-{uuid.uuid4().hex[:8]}"
    _, _, write_path = _ensure_layout_dirs(probe_dir)
    heartbeat = write_path / "heartbeat.txt"
    config = build_mxc_config(
        probe_dir,
        "in.json",
        "out.json",
        limits,
        container_id,
    )
    script = (
        "import pathlib,time;"
        f"p=pathlib.Path({str(heartbeat)!r});"
        "i=0\n"
        "while True:\n"
        " i+=1\n"
        " p.write_text(str(i), encoding='utf-8')\n"
        " time.sleep(0.05)\n"
    )
    config["process"]["commandLine"] = _command_line(["python", "-c", script])
    config["process"]["timeout"] = 30_000
    try:
        proc = subprocess.Popen(
            [exe, "--config-base64", _config_to_base64(config)],
            cwd=str(Path.cwd()),
            env=_mxc_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        shutil.rmtree(probe_dir, ignore_errors=True)
        return False

    job_handle = create_job_object(limits)
    try:
        job_handle.process = proc
    except Exception:
        pass
    assign_process(job_handle, proc.pid)

    started = time.monotonic()
    try:
        while not heartbeat.exists() and proc.poll() is None:
            if time.monotonic() - started > 2.0:
                return False
            time.sleep(0.02)
        if proc.poll() is not None or not heartbeat.exists():
            return False
        before_stop = heartbeat.read_text(encoding="utf-8")
        stop_started = time.monotonic()
        _stop_mxc(container_id, proc, job_handle)
        try:
            proc.wait(timeout=_STOP_DEADLINE_S)
        except Exception:
            pass
        stopped_at = time.monotonic()
        time.sleep(0.2)
        after_stop = heartbeat.read_text(encoding="utf-8")
        return (
            proc.poll() is not None
            and stopped_at - stop_started < _STOP_DEADLINE_S
            and after_stop == before_stop
        )
    finally:
        close(job_handle)
        try:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=0.2)
        except Exception:
            pass
        shutil.rmtree(probe_dir, ignore_errors=True)


def _probe_output_has_blocking_warning(output: str) -> bool:
    text = output.lower()
    return any(
        marker in text
        for marker in (
            "warning",
            "unsupported",
            "not supported",
            "host preparation",
            "host prep",
            "allowdaclmutation",
            "dacl",
            "overly permissive",
        )
    )


def _find_mxc_exe() -> str | None:
    override = os.environ.get(MXC_EXE_ENV)
    if override:
        return _resolve_executable_override(override)

    for candidate in _candidate_mxc_exes():
        if _is_file(candidate):
            return str(candidate)

    for name in _mxc_exe_names():
        found = shutil.which(name)
        if found:
            return found
    return None


def _candidate_mxc_exes() -> Iterator[Path]:
    bin_dir = os.environ.get(MXC_BIN_DIR_ENV)
    if bin_dir:
        root = Path(bin_dir).expanduser()
        for arch in ("x64", "arm64"):
            for name in _mxc_exe_names():
                yield root / arch / name
        for name in _mxc_exe_names():
            yield root / name

    for root in _candidate_install_dirs():
        for arch in ("x64", "arm64"):
            for name in _mxc_exe_names():
                yield root / arch / name
        for name in _mxc_exe_names():
            yield root / name


def _candidate_install_dirs() -> Iterator[Path]:
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        raw = os.environ.get(key)
        if not raw:
            continue
        root = Path(raw).expanduser()
        yield root / "Microsoft" / "MXC"
        yield root / "Microsoft Execution Containers"
        yield root / "mxc"


def _resolve_executable_override(raw: str) -> str | None:
    expanded = Path(raw).expanduser()
    if _is_file(expanded):
        return str(expanded)
    found = shutil.which(raw)
    if found:
        return found
    return None


def _mxc_exe_for_command() -> str:
    return _find_mxc_exe() or MXC_EXE


def _mxc_exe_names() -> tuple[str, ...]:
    return ("wxc-exec.exe", "wxc-exec")


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _looks_like_config_path(value: str) -> bool:
    separators = {os.sep}
    if os.altsep:
        separators.add(os.altsep)
    separators.update({"\\", "/"})
    return value.lower().endswith(".json") or any(separator in value for separator in separators)


def _layout_paths(
    root_dir: Path,
    *,
    input_dir: Path | None = None,
    payload_dir: Path | None = None,
    writable_dir: Path | None = None,
) -> tuple[Path, Path, Path]:
    root_path = _resolve_path(root_dir)
    input_path = _resolve_path(input_dir) if input_dir is not None else _resolve_path(root_path / "input")
    payload_path = (
        _resolve_path(payload_dir)
        if payload_dir is not None
        else _resolve_path(root_path / "payload" / "src")
    )
    write_path = (
        _resolve_path(writable_dir)
        if writable_dir is not None
        else _resolve_path(root_path / "work")
    )
    return input_path, payload_path, write_path


def _ensure_layout_dirs(root_dir: Path) -> tuple[Path, Path, Path]:
    input_path, payload_path, write_path = _layout_paths(root_dir)
    for path in (input_path, payload_path, write_path):
        path.mkdir(parents=True, exist_ok=True)
    return input_path, payload_path, write_path


def _popen_mxc(command: list[str], work_dir: Path) -> subprocess.Popen:
    try:
        return subprocess.Popen(
            command,
            cwd=str(work_dir),
            env=_mxc_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as exc:
        raise _MxcInfraError(f"mxc runtime failed to start: {exc}") from exc


def _best_effort_mxc_teardown(container_id: str) -> None:
    try:
        exe = _find_mxc_exe()
    except Exception:
        exe = None
    if exe is None:
        return
    for verb in ("--teardown", "--stop"):
        try:
            subprocess.run(
                [exe, verb, container_id],
                capture_output=True,
                text=True,
                timeout=0.05,
                check=False,
            )
        except Exception:
            pass


def _mxc_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key in ("SystemRoot", "PATH", "COMSPEC", "PATHEXT", "WINDIR", "TEMP", "TMP", "USERPROFILE"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def _config_to_base64(config: dict[str, Any]) -> str:
    return base64.b64encode(_config_to_json(config).encode("utf-8")).decode("ascii")


def _config_to_json(config: dict[str, Any]) -> str:
    return policy_to_json(config)


def _command_line(args: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def _resolve_path(path: Path | str | os.PathLike[str]) -> Path:
    candidate = Path(path).expanduser()
    try:
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return candidate.absolute()


def _filter_denied_paths(paths: list[str], allowed_paths: list[Path]) -> list[str]:
    resolved_allowed = [_resolve_path(path) for path in allowed_paths]
    filtered: list[str] = []
    for raw_path in paths:
        resolved_denied = _resolve_path(raw_path)
        if any(
            _is_same_or_child(allowed_path, resolved_denied)
            for allowed_path in resolved_allowed
        ):
            continue
        filtered.append(raw_path)
    return filtered


def _is_same_or_child(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        candidate_text = _comparison_text(candidate)
        root_text = _comparison_text(root).rstrip("\\/")
        if candidate_text == root_text:
            return True
        return candidate_text.startswith(f"{root_text}{os.sep}")


def _comparison_text(path: Path) -> str:
    text = str(path)
    if os.name == "nt":
        return text.replace("/", "\\").casefold()
    return text


def _looks_like_mxc_infra_error(stderr: str) -> bool:
    text = (stderr or "").lower()
    return any(marker in text for marker in _MXC_INFRA_MARKERS)


def _looks_like_job_failure(stderr: str) -> bool:
    text = (stderr or "").lower()
    return any(marker in text for marker in _JOB_FAILURE_MARKERS)
