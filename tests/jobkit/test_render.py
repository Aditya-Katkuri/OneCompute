"""Tests for the GPU-capable `render` executor: honest CPU fallback (the reality on a
no-CUDA box), a mocked-CUDA path, and yield preemption."""
from __future__ import annotations

import sys
import types

import numpy as np

from jobkit.execute import execute


def _fake_cupy():
    m = types.ModuleType("cupy")
    m.full = np.full
    m.float32 = np.float32
    cuda = types.ModuleType("cupy.cuda")
    cuda.runtime = types.SimpleNamespace(
        getDeviceCount=lambda: 1,
        getDeviceProperties=lambda i: {"name": b"FakeGPU-9000"},
    )

    class _Dev:
        def __init__(self, index):
            self.index = index

        def synchronize(self):
            return None

    cuda.Device = _Dev
    m.cuda = cuda
    return m


def _fake_pynvml(util=77):
    m = types.ModuleType("pynvml")
    m.nvmlInit = lambda: None
    m.nvmlShutdown = lambda: None
    m.nvmlDeviceGetHandleByIndex = lambda i: object()
    m.nvmlDeviceGetUtilizationRates = lambda h: types.SimpleNamespace(gpu=util)
    return m


def test_render_cpu_fallback_is_honest():
    # No cupy is installed on this box -> the executor must run on CPU and SAY SO.
    out = execute("render", {"size": 16, "iters": 4})
    assert out["accelerator"] == "cpu-fallback"
    assert out["gpu_available"] is False
    assert out["gpu_util_peak"] is None
    assert out["results"]["iters_done"] == 4
    assert isinstance(out["results"]["checksum"], float)
    assert out["yielded"] is False


def test_render_cuda_path_when_device_present(monkeypatch):
    monkeypatch.setitem(sys.modules, "cupy", _fake_cupy())
    monkeypatch.setitem(sys.modules, "pynvml", _fake_pynvml(util=77))
    out = execute("render", {"size": 16, "iters": 3})
    assert out["accelerator"] == "cuda"
    assert out["gpu_available"] is True
    assert out["device"] == "FakeGPU-9000"
    assert out["gpu_util_peak"] == 77.0
    assert out["results"]["iters_done"] == 3


def test_render_preempts_on_yield():
    out = execute("render", {"size": 32, "iters": 1000}, should_yield=lambda: True)
    assert out["yielded"] is True
    assert out["results"]["iters_done"] == 0
