from __future__ import annotations

from worker.idle import IdleGate


def test_idle_gate_methods_never_raise() -> None:
    gate = IdleGate()

    idle_seconds = gate.input_idle_seconds()
    idle_sample = gate.input_idle_seconds_sample()
    ac_state = gate.on_ac_state()
    user_idle_state = gate.user_idle_state()

    assert idle_seconds >= 0
    assert idle_sample is None or idle_sample >= 0
    assert ac_state is None or isinstance(ac_state, bool)
    assert user_idle_state is None or isinstance(user_idle_state, bool)
    assert isinstance(gate.on_ac(), bool)
    assert isinstance(gate.locked(), bool)
    assert isinstance(gate.gpu_busy(), bool)
    assert isinstance(gate.should_run(), bool)
    assert isinstance(gate.active_now(), bool)
