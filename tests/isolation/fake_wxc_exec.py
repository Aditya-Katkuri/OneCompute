"""A stub ``wxc-exec`` runtime for validating OneCompute's MXC launch path.

This program faithfully emulates the probe + run contract that
``src/isolation/mxc.py`` expects from a real Microsoft Execution Containers
runtime, so the OneCompute-side launch/policy/probe wiring can be exercised
end-to-end without a genuine kernel-enforced sandbox. See
``docs/mxc-validation.md`` for the reverse-engineered protocol and the exact
list of what this stub does and does NOT prove.

Contract summary (all reverse-engineered from ``src/isolation/mxc.py``):

* ``wxc-exec --probe`` -> exit 0 and print a JSON health document that
  ``_probe_payload_is_supported`` accepts (a supported ``processContainer``
  tier, no ``needsDaclAugmentation``, no warnings, no "host preparation").
* ``wxc-exec --dry-run --config-base64 <b64>`` -> exit 0 and emit no blocking
  warning (``_probe_policy_dry_run``).
* ``wxc-exec --config-base64 <b64>`` -> decode the config, launch the staged
  ``process.commandLine`` with ``process.cwd`` / ``process.env`` and a
  best-effort filesystem policy derived from ``filesystem.readonlyPaths`` /
  ``filesystem.readwritePaths``, then exit with the child's return code. This
  is the path that ``_run_mxc`` (and the filesystem/kill probes) drive.
* ``wxc-exec --teardown|--stop <id>`` -> best-effort, exit 0.

Failure injection: when a ``fail-infra`` marker file exists in the control
directory named by ``FAKE_WXC_CONTROL`` (which the harness bakes into the shim
so it survives ``mxc._mxc_env`` filtering), a real workload run emits an MXC
infrastructure error and exits non-zero so the ``_MxcInfraError`` fail-closed
fallback can be asserted. Probe container runs are never failed, so
``mxc_available`` still reports the runtime as healthy.

The filesystem boundary is emulated in-process with a ``sys.addaudithook``
policy shim (written into the sandbox's writable dir and imported via
``sitecustomize``). It denies reads/writes/deletes for user-data paths that are
reachable inside the container root but outside the declared read-only /
read-write roots. It is deliberately permissive about the interpreter's own
runtime paths (outside the container root) because a real base image would
provide those; this is a stub limitation, not a security boundary.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

# A healthy probe document. ``processContainer.supported`` plus a supported tier
# and no warnings / DACL augmentation is exactly what ``_probe_payload_is_supported``
# treats as a usable process-isolation runtime.
_PROBE_HEALTH = {
    "runtime": "fake-wxc-exec",
    "tier": "processContainer",
    "needsDaclAugmentation": False,
    "warnings": [],
    "processContainer": {"supported": True, "available": True},
}

# Must contain an ``_MXC_INFRA_MARKERS`` fingerprint and NONE of the
# ``_JOB_FAILURE_MARKERS`` so ``_run_mxc`` raises ``_MxcInfraError`` (infra
# fallback) rather than ``RuntimeError`` (job failure, no fallback).
_INFRA_MESSAGE = (
    "wxc-exec: execution container failed to start "
    "(simulated host preparation failure)"
)

# The audit-hook policy shim imported as ``sitecustomize`` inside the child. It
# reads the sandbox roots from ``FAKE_WXC_SANDBOX`` and denies access to
# user-data paths reachable inside the container root but outside the declared
# read-only / read-write roots.
_SANDBOX_BOOTSTRAP = r'''
import json
import os
import sys

_raw = os.environ.get("FAKE_WXC_SANDBOX")
if _raw:
    _policy = json.loads(_raw)
    _base = os.getcwd()

    def _norm(path):
        return os.path.normcase(os.path.normpath(os.path.join(_base, path)))

    _readonly = [_norm(p) for p in _policy.get("readonly", [])]
    _readwrite = [_norm(p) for p in _policy.get("readwrite", [])]
    _root = _norm(_policy["container_root"]) if _policy.get("container_root") else None

    def _under(path, root):
        return path == root or path.startswith(root + os.sep)

    def _denied(path, writing):
        if not isinstance(path, (str, bytes, os.PathLike)):
            return False
        try:
            candidate = _norm(os.fspath(path))
        except Exception:
            return False
        if any(_under(candidate, r) for r in _readwrite):
            return False
        if any(_under(candidate, r) for r in _readonly):
            return writing
        if _root is not None and _under(candidate, _root):
            return True
        return False

    def _hook(event, args):
        if event == "open":
            path = args[0]
            mode = args[1] if len(args) > 1 else None
            writing = bool(mode) and any(ch in str(mode) for ch in "wax+")
            if _denied(path, writing):
                raise PermissionError("wxc-exec sandbox denied open: %r" % (path,))
        elif event in ("os.remove", "os.unlink", "os.rmdir"):
            if _denied(args[0], True):
                raise PermissionError("wxc-exec sandbox denied delete: %r" % (args[0],))
        elif event in ("os.rename", "os.replace"):
            for path in args[:2]:
                if _denied(path, True):
                    raise PermissionError("wxc-exec sandbox denied rename: %r" % (path,))

    sys.addaudithook(_hook)
'''

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Keep the job handle alive for the life of the process so kill-on-job-close
# reaps the child the instant this stub is terminated (mirrors real teardown).
_JOB_STATE: dict[str, object] = {}


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _new_kill_on_close_job() -> object | None:
    """Create a kill-on-job-close Job Object so the child dies when we die."""
    if sys.platform != "win32":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = _ExtendedLimit()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject(
            wintypes.HANDLE(int(handle)),
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        _JOB_STATE["kernel32"] = kernel32
        _JOB_STATE["handle"] = int(handle)
        return int(handle)
    except Exception:
        return None


def _assign_to_job(handle: object, pid: int) -> None:
    kernel32 = _JOB_STATE.get("kernel32")
    if kernel32 is None or handle is None:
        return
    try:
        access = _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        proc_handle = kernel32.OpenProcess(access, False, pid)
        if not proc_handle:
            return
        try:
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(int(handle)), proc_handle
            )
        finally:
            kernel32.CloseHandle(proc_handle)
    except Exception:
        return


def _load_config(args: list[str]) -> dict:
    if "--config-base64" in args:
        raw = args[args.index("--config-base64") + 1]
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    if "--config" in args:
        path = args[args.index("--config") + 1]
        return json.loads(Path(path).read_text(encoding="utf-8"))
    raise ValueError("no --config or --config-base64 supplied")


def _is_probe_container(config: dict) -> bool:
    return "probe" in str(config.get("containerId", "")).casefold()


def _infra_requested() -> bool:
    control = os.environ.get("FAKE_WXC_CONTROL")
    if not control:
        return False
    return (Path(control) / "fail-infra").exists()


def _container_root(readonly: list[str], readwrite: list[str]) -> str | None:
    roots = [r for r in (*readonly, *readwrite) if r]
    if not roots:
        return None
    try:
        return os.path.commonpath(roots)
    except ValueError:
        return None


def _swap_interpreter(command_line: str) -> str:
    """Replace a leading ``python`` token with this stub's own interpreter.

    A real runtime runs ``python`` from inside its container image; on the host
    we substitute the driving interpreter so the staged job sees a working
    stdlib. Everything after the first token is preserved verbatim.
    """
    stripped = command_line.lstrip()
    if not stripped:
        return command_line
    if stripped.startswith('"'):
        end = stripped.find('"', 1)
        if end == -1:
            return command_line
        first, rest = stripped[1:end], stripped[end + 1 :]
    else:
        space = stripped.find(" ")
        if space == -1:
            first, rest = stripped, ""
        else:
            first, rest = stripped[:space], stripped[space:]
    if Path(first).name.lower() in ("python", "python.exe", "python3", "python3.exe"):
        return subprocess.list2cmdline([sys.executable]) + rest
    return command_line


def _child_env(config: dict, bootstrap_dir: Path, sandbox: dict) -> dict[str, str]:
    env = dict(os.environ)
    for entry in config.get("process", {}).get("env", []):
        key, _, value = str(entry).partition("=")
        if key:
            env[key] = value
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(bootstrap_dir) + (os.pathsep + existing if existing else "")
    )
    env["FAKE_WXC_SANDBOX"] = json.dumps(sandbox)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run(config: dict) -> int:
    if _infra_requested() and not _is_probe_container(config):
        sys.stderr.write(_INFRA_MESSAGE + "\n")
        return 1

    process = config.get("process", {})
    command_line = str(process.get("commandLine", ""))
    cwd = process.get("cwd") or None
    timeout_ms = process.get("timeout")
    fs = config.get("filesystem", {})
    readonly = [str(p) for p in fs.get("readonlyPaths", [])]
    readwrite = [str(p) for p in fs.get("readwritePaths", [])]
    root = _container_root(readonly, readwrite)

    # Stage the audit-hook policy shim inside the writable dir so it is readable
    # by the sandboxed child and cleaned up with the caller's work tree.
    boot_parent = Path(readwrite[0]) if readwrite else Path(tempfile.mkdtemp())
    bootstrap_dir = boot_parent / "__wxc_bootstrap__"
    try:
        bootstrap_dir.mkdir(parents=True, exist_ok=True)
        (bootstrap_dir / "sitecustomize.py").write_text(
            _SANDBOX_BOOTSTRAP, encoding="utf-8"
        )
    except Exception:
        bootstrap_dir = Path(tempfile.mkdtemp())
        (bootstrap_dir / "sitecustomize.py").write_text(
            _SANDBOX_BOOTSTRAP, encoding="utf-8"
        )

    sandbox = {
        "readonly": readonly,
        "readwrite": readwrite,
        "container_root": root,
    }
    env = _child_env(config, bootstrap_dir, sandbox)
    command = _swap_interpreter(command_line)

    job = _new_kill_on_close_job()
    try:
        proc = subprocess.Popen(command, cwd=cwd, env=env)
    except Exception as exc:
        sys.stderr.write(f"wxc-exec: container failed to start: {exc}\n")
        return 1
    if job is not None:
        _assign_to_job(job, proc.pid)

    timeout_s = None
    if isinstance(timeout_ms, (int, float)) and timeout_ms > 0:
        # Generous backstop; the OneCompute side owns yield/timeout by killing us.
        timeout_s = float(timeout_ms) / 1000.0 + 5.0
    try:
        return proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        sys.stderr.write("wxc-exec: container runtime timed out\n")
        return 1


def main(argv: list[str]) -> int:
    args = argv[1:]
    if "--probe" in args:
        sys.stdout.write(json.dumps(_PROBE_HEALTH))
        return 0
    if "--teardown" in args or "--stop" in args:
        return 0
    if "--dry-run" in args:
        try:
            _load_config(args)
        except Exception as exc:
            sys.stderr.write(f"wxc-exec: invalid config: {exc}\n")
            return 1
        return 0
    if "--config-base64" in args or "--config" in args:
        try:
            config = _load_config(args)
        except Exception as exc:
            sys.stderr.write(f"wxc-exec: container failed to start: {exc}\n")
            return 1
        return _run(config)
    sys.stderr.write("wxc-exec: unrecognized invocation\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
