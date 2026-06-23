"""Capability detection for NightShift workers."""

from __future__ import annotations

import ctypes
import os
import socket
from uuid import uuid4

from contracts import Capability


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _ram_gb() -> float:
    try:
        if os.name == "nt":
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return round(float(status.ullTotalPhys) / (1024**3), 2)
    except Exception:
        pass
    return 8.0


def _free_ram_gb() -> float | None:
    try:
        if os.name == "nt":
            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                return round(float(status.ullAvailPhys) / (1024**3), 2)
    except Exception:
        pass
    return None


def free_ram_gb() -> float | None:
    """Currently-available physical RAM in GB (None if undetectable). Re-read each heartbeat."""
    return _free_ram_gb()


def _gpu_info() -> tuple[bool, str | None, float | None, list[str]]:
    try:
        import pynvml  # type: ignore[import-not-found]

        pynvml.nvmlInit()
        try:
            if pynvml.nvmlDeviceGetCount() < 1:
                return False, None, None, []
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            return True, str(name), round(float(mem.total) / (1024**3), 2), ["cuda"]
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception:
        return False, None, None, []


def detect_capability(worker_id: str | None = None) -> Capability:
    """Detect worker capacity without ever raising to callers."""
    try:
        resolved_worker_id = worker_id or f"{socket.gethostname()}-{uuid4().hex[:6]}"
        has_gpu, gpu_model, gpu_vram_gb, accel = _gpu_info()
        return Capability(
            worker_id=resolved_worker_id,
            cpus=os.cpu_count() or 1,
            ram_gb=_ram_gb(),
            free_ram_gb=_free_ram_gb(),
            has_gpu=has_gpu,
            gpu_model=gpu_model,
            gpu_vram_gb=gpu_vram_gb,
            accel=accel,
        )
    except Exception:
        try:
            fallback_id = worker_id or f"worker-{uuid4().hex[:6]}"
            return Capability(worker_id=fallback_id, cpus=1, ram_gb=8.0, has_gpu=False)
        except Exception:
            return Capability.model_construct(
                worker_id=worker_id or "worker-unknown",
                cpus=1,
                ram_gb=8.0,
                has_gpu=False,
                gpu_model=None,
                gpu_vram_gb=None,
                accel=[],
                benchmarked_tops=None,
                labels=[],
            )
