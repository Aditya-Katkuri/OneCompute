from __future__ import annotations

import types

import pytest

from contracts import Capability
from worker import capability as capmod
from worker.capability import detect_capability, detect_npu


def test_detect_npu_never_raises_and_returns_bool_optional_float() -> None:
    has_npu, tops = detect_npu()

    assert isinstance(has_npu, bool)
    assert tops is None or isinstance(tops, float)


def test_detect_npu_true_on_qnn_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ort = types.SimpleNamespace(
        get_available_providers=lambda: ["CPUExecutionProvider", "QNNExecutionProvider"],
    )
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", fake_ort)

    has_npu, tops = detect_npu()

    assert has_npu is True
    assert tops is None or isinstance(tops, float)


def test_detect_npu_true_on_directml_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ort = types.SimpleNamespace(
        get_available_providers=lambda: ["DmlExecutionProvider", "CPUExecutionProvider"],
    )
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", fake_ort)

    has_npu, _tops = detect_npu()

    assert has_npu is True


def test_detect_npu_false_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_ort = types.SimpleNamespace(
        get_available_providers=lambda: ["CPUExecutionProvider"],
    )
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", fake_ort)
    monkeypatch.delenv("ONECOMPUTE_NPU", raising=False)

    assert detect_npu() == (False, None)


def test_detect_npu_false_on_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", None)
    monkeypatch.delenv("ONECOMPUTE_NPU", raising=False)

    assert detect_npu() == (False, None)


def test_detect_npu_env_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", None)
    monkeypatch.setenv("ONECOMPUTE_NPU", "1")
    monkeypatch.setenv("ONECOMPUTE_NPU_TOPS", "45")

    assert detect_npu() == (True, 45.0)


def test_detect_capability_carries_npu_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capmod, "detect_npu", lambda: (True, 45.0))

    cap = detect_capability()

    assert isinstance(cap, Capability)
    assert cap.has_npu is True
    assert cap.npu_tops == 45.0


def test_capability_npu_defaults() -> None:
    cap = Capability(worker_id="w1")

    assert cap.has_npu is False
    assert cap.npu_tops is None


def test_capability_npu_roundtrip() -> None:
    cap = Capability(
        worker_id="w-npu",
        cpus=8,
        ram_gb=32.0,
        has_gpu=True,
        gpu_model="RTX 4090",
        gpu_vram_gb=24.0,
        accel=["cuda", "directml"],
        has_npu=True,
        npu_tops=45.0,
    )

    restored = Capability.model_validate(cap.model_dump())

    assert restored == cap
    assert restored.has_npu is True
    assert restored.npu_tops == 45.0


def test_capability_npu_json_roundtrip() -> None:
    cap = Capability(worker_id="w-json", has_npu=True, npu_tops=50.0)

    restored = Capability.model_validate_json(cap.model_dump_json())

    assert restored.has_npu is True
    assert restored.npu_tops == 50.0
