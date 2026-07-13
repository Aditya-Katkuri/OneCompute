"""Proactive reroute-in: a freed-up worker must pull work within ~step seconds of headroom
returning (not a full poll tick), and the worker must tell the governor when its OWN job uses the
GPU so the demand-yield signal never fires on the compute our job itself creates."""
from __future__ import annotations

import worker.__main__ as m
from contracts import Capability, JobAssignment, JobManifest, Requires, SignedManifest
from worker.agent import WorkerAgent
from worker.governor import AdaptiveGovernor


def _assignment(needs_gpu: bool) -> JobAssignment:
    manifest = JobManifest(job_id="j1", kind="data.transform", requires=Requires(needs_gpu=needs_gpu))
    return JobAssignment(signed_manifest=SignedManifest(manifest=manifest), input={"items": [1]})


def test_wait_for_headroom_wakes_early_when_freed(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(m.time, "sleep", lambda s: sleeps.append(s))
    g = object.__new__(AdaptiveGovernor)  # instance without full init; isinstance() still holds
    calls = {"n": 0}

    def avail(when=None):
        calls["n"] += 1
        return calls["n"] >= 2  # busy on the first check, freed on the second

    g.available_now = avail
    m._wait_for_headroom(g, adaptive=True, base_interval=1.5, step=0.5)
    assert calls["n"] == 2      # returned the instant headroom appeared
    assert len(sleeps) == 2     # only two 0.5s naps, not a full 1.5s tick


def test_wait_for_headroom_sleeps_full_interval_when_still_busy(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(m.time, "sleep", lambda s: sleeps.append(s))
    g = object.__new__(AdaptiveGovernor)
    g.available_now = lambda when=None: False  # never frees up this tick
    m._wait_for_headroom(g, adaptive=True, base_interval=1.5, step=0.5)
    assert len(sleeps) == 3     # 3 x 0.5s == the full 1.5s, then it gives up for this tick


def test_wait_for_headroom_non_adaptive_uses_should_run(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(m.time, "sleep", lambda s: sleeps.append(s))

    class Gate:
        def __init__(self) -> None:
            self.n = 0

        def should_run(self) -> bool:
            self.n += 1
            return self.n >= 2

    g = Gate()
    m._wait_for_headroom(g, adaptive=False, base_interval=1.5, step=0.5)
    assert g.n == 2
    assert len(sleeps) == 2


class _RecordingGate:
    def __init__(self) -> None:
        self.notes: list[bool] = []

    def note_job(self, needs_gpu: bool) -> None:
        self.notes.append(needs_gpu)


def test_run_job_forwards_gpu_use_to_active_gate_and_resets(monkeypatch):
    # A GPU job must tell the governor (note_job(True)) BEFORE running, then reset to False in the
    # finally, so the governor suppresses the GPU yield only while our GPU job is actually running.
    def fake_run_in_isolation(kind, inp, limits, should_yield=None, host_side=False,
                              allow_unsandboxed=True):
        return {"results": {}, "yielded": False}

    monkeypatch.setattr("worker.agent.run_in_isolation", fake_run_in_isolation)
    worker = WorkerAgent("http://x", Capability(worker_id="g1", has_gpu=True), isolated=True)
    gate = _RecordingGate()
    worker._active_gate = gate
    worker.run_job(_assignment(needs_gpu=True))
    worker.close()
    assert gate.notes == [True, False]  # noted on entry, reset after the job


def test_note_job_gpu_is_a_noop_without_an_active_gate():
    worker = WorkerAgent("http://x", Capability(worker_id="c1"))
    worker._active_gate = None
    worker._note_job_gpu(True)  # must not raise when running ungoverned
    worker.close()
