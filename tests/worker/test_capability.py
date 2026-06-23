from __future__ import annotations

from contracts import Capability
from worker.capability import detect_capability


def test_detect_capability_never_raises() -> None:
    cap = detect_capability()

    assert isinstance(cap, Capability)
    assert cap.cpus >= 1
    assert cap.has_gpu in (True, False)
