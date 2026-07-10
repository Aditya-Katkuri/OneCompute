"""Capability detection for OneCompute workers."""

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


# A DirectML execution provider surfaces any DirectX 12 accelerator (NPU or iGPU/dGPU), so it is
# a weak NPU signal on its own. QNN is Qualcomm's NPU-specific provider (Snapdragon X Copilot+).
_NPU_PROVIDERS = ("QNNExecutionProvider", "DmlExecutionProvider")


def detect_npu() -> tuple[bool, float | None]:
    """Best-effort NPU detection for the fleet picture. NEVER raises.

    Returns (has_npu, npu_tops) where npu_tops is a NAMEPLATE INT8 peak (spec sheet) when the
    device family is recognizable, else None. This advertises an NPU / DirectML execution
    provider; it does NOT run NPU jobs (see docs/npu-harvesting.md -- execution is roadmap and
    needs onnxruntime-directml + real Copilot+ hardware).

    Detection order, each guarded independently:
      1. ONNX Runtime execution providers (QNN NPU provider, or DirectML) if importable.
      2. A cheap Windows environment hint (ONECOMPUTE_NPU / ONECOMPUTE_NPU_TOPS) for pilots that
         want to declare a known NPU out-of-band without a heavy runtime.
    """
    # (1) ONNX Runtime execution providers.
    try:
        import onnxruntime  # type: ignore[import-not-found]

        providers = list(onnxruntime.get_available_providers())
        if "QNNExecutionProvider" in providers:
            return True, None
        if "DmlExecutionProvider" in providers:
            return True, None
    except Exception:
        pass

    # (2) Cheap, opt-in Windows env hint (no heavy dependency, no registry scan by default).
    try:
        if os.environ.get("ONECOMPUTE_NPU", "").strip().lower() in ("1", "true", "yes"):
            tops_raw = os.environ.get("ONECOMPUTE_NPU_TOPS", "").strip()
            tops = float(tops_raw) if tops_raw else None
            return True, tops
    except Exception:
        pass

    return False, None


def detect_capability(worker_id: str | None = None) -> Capability:
    """Detect worker capacity without ever raising to callers."""
    try:
        resolved_worker_id = worker_id or f"{socket.gethostname()}-{uuid4().hex[:6]}"
        has_gpu, gpu_model, gpu_vram_gb, accel = _gpu_info()
        has_npu, npu_tops = detect_npu()
        return Capability(
            worker_id=resolved_worker_id,
            cpus=os.cpu_count() or 1,
            ram_gb=_ram_gb(),
            free_ram_gb=_free_ram_gb(),
            has_gpu=has_gpu,
            gpu_model=gpu_model,
            gpu_vram_gb=gpu_vram_gb,
            accel=accel,
            has_npu=has_npu,
            npu_tops=npu_tops,
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
                has_npu=False,
                npu_tops=None,
                labels=[],
            )
