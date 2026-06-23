"""Best-effort Windows Job Object wrapper.

Job Objects provide resource governance and kill-on-close for a process tree. They
are not a filesystem or confidentiality boundary. Every Win32 call is guarded so
this module remains importable and usable on non-Windows machines.
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any

from contracts import Limits

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JobObjectExtendedLimitInformation = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


@dataclass
class NoopJobObjectHandle:
    """Fallback handle that can terminate the tracked root process only."""

    pid: int | None = None
    process: Any | None = None
    closed: bool = False

    def kill(self) -> None:
        self.closed = True
        if self.process is not None:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
            except Exception:
                pass
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except Exception:
                pass

    def close(self) -> None:
        self.kill()


@dataclass
class WindowsJobObjectHandle:
    raw: int
    pid: int | None = None
    process: Any | None = None
    closed: bool = False
    _kernel32: Any = field(default=None, repr=False)

    def kill(self) -> None:
        close(self)

    def close(self) -> None:
        close(self)


def _kernel32() -> Any | None:
    try:
        return ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return None


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
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


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def create_job_object(limits: Limits | None = None) -> WindowsJobObjectHandle | NoopJobObjectHandle:
    """Create a Job Object handle, or a no-op fallback if unsupported."""
    if sys.platform != "win32":
        return NoopJobObjectHandle()
    kernel32 = _kernel32()
    if kernel32 is None:
        return NoopJobObjectHandle()
    try:
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        raw = kernel32.CreateJobObjectW(None, None)
        if not raw:
            return NoopJobObjectHandle()
        handle = WindowsJobObjectHandle(raw=int(raw), _kernel32=kernel32)
        _apply_limits(handle, limits)
        return handle
    except Exception:
        return NoopJobObjectHandle()


def _apply_limits(handle: WindowsJobObjectHandle, limits: Limits | None) -> None:
    try:
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if limits is not None and limits.mem_gb and limits.mem_gb > 0:
            info.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
            info.ProcessMemoryLimit = int(limits.mem_gb * 1024 * 1024 * 1024)
        kernel32 = handle._kernel32 or _kernel32()
        if kernel32 is None:
            return
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject(
            wintypes.HANDLE(handle.raw),
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
    except Exception:
        return


def assign_process(handle: WindowsJobObjectHandle | NoopJobObjectHandle, pid: int) -> None:
    """Assign a process ID to a Job Object; never raise."""
    handle.pid = pid
    if isinstance(handle, NoopJobObjectHandle) or sys.platform != "win32":
        return
    try:
        kernel32 = handle._kernel32 or _kernel32()
        if kernel32 is None:
            return
        access = PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        proc_handle = kernel32.OpenProcess(access, False, pid)
        if not proc_handle:
            return
        try:
            kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel32.AssignProcessToJobObject(wintypes.HANDLE(handle.raw), proc_handle)
        finally:
            try:
                kernel32.CloseHandle(proc_handle)
            except Exception:
                pass
    except Exception:
        return


def close(handle: WindowsJobObjectHandle | NoopJobObjectHandle | None) -> None:
    """Close the handle. For a real Job Object this kills the process tree."""
    if handle is None or getattr(handle, "closed", False):
        return
    handle.closed = True
    if isinstance(handle, NoopJobObjectHandle) or sys.platform != "win32":
        handle.kill()
        return
    try:
        kernel32 = handle._kernel32 or _kernel32()
        if kernel32 is not None and handle.raw:
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            kernel32.CloseHandle(wintypes.HANDLE(handle.raw))
    except Exception:
        if handle.process is not None:
            try:
                if handle.process.poll() is None:
                    handle.process.terminate()
            except Exception:
                pass
