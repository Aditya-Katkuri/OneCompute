from __future__ import annotations

from worker.idle import IdleGate


def test_idle_gate_methods_never_raise() -> None:
    gate = IdleGate()

    idle_seconds = gate.input_idle_seconds()

    assert idle_seconds >= 0
    assert isinstance(gate.on_ac(), bool)
    assert isinstance(gate.locked(), bool)
    assert isinstance(gate.gpu_busy(), bool)
    assert isinstance(gate.should_run(), bool)
    assert isinstance(gate.active_now(), bool)
