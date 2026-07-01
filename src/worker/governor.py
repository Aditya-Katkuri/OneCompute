"""Demand-adaptive governor: the decision half of headroom harvesting (idea.md §5).

A **drop-in replacement for `IdleGate`**: it exposes the same `should_run()` / `active_now()`
the worker already uses, but instead of a binary "fully idle?" gate it runs work in the
machine's learned **spare headroom** and yields on the *employee's* real compute demand.

The key trick is **attribution**: estimate the employee's own demand as
``user_cpu = system_cpu − our_job_tree_cpu`` (via ``psutil`` per-process accounting), so the
governor reasons about *their* load, never its own.

Two thresholds for two phases:

* **Admission** (`should_run`, between jobs, when our job isn't running so ``user_cpu`` ≈
  ``system_cpu``): admit only if ``user_cpu`` is below a **time-aware threshold**
  (`profiled_mean + margin`, capped) and the hour-of-week bucket has headroom. This runs during
  *light* foreground use, not only full idle. The sample is folded into the profile **after**
  the decision, so a cold/just-reset bucket can't authorize itself.
* **Yield** (`active_now`, polled during our job): yield once ``user_cpu`` (system minus our own
  job tree) stays above a **yield threshold** (admission + hysteresis) for several samples. The
  employee now wants more than the margin we left them. It deliberately does **not** yield on
  mere input (typing while we use spare headroom is fine) and does **not** record into the
  profile (our job is running, so the sample isn't the employee's baseline).

> PoC scope: host-side job subprocesses are attributed via ``psutil``; a Docker job's container
> runs in the WSL VM (not a child process), so its CPU isn't subtracted yet: container-level
> accounting (``docker stats``) or a Job Object ``CpuRate`` self-cap is the next refinement. The
> hard "mouse-touch → instant yield" reflex remains available via the binary ``IdleGate``
> (`--governor idle`) and the demo's explicit trigger; the adaptive governor is purely
> compute-demand-driven, matching "stays on while you work, yields when your usage spikes."
"""

from __future__ import annotations

import ctypes
import os
from datetime import datetime

from worker.idle import IdleGate
from worker.profiler import SystemCpuSampler, UsageProfiler

try:  # psutil powers per-process CPU attribution (system minus our own job tree)
    import psutil
except Exception:  # pragma: no cover - psutil is a declared dependency; guard for safety
    psutil = None


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),  # % of physical memory in use (0-100)
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def system_ram_load_pct() -> float:
    """Percent of physical RAM in use (0-100), or 0.0 if undetectable. Never raises."""
    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
            return float(status.dwMemoryLoad)
    except Exception:
        pass
    return 0.0


def system_gpu_load_pct() -> float:
    """Current NVIDIA GPU utilization % (0-100), or 0.0 if no device/driver. Never raises."""
    try:
        import pynvml  # type: ignore[import-not-found]

        pynvml.nvmlInit()
        try:
            if pynvml.nvmlDeviceGetCount() < 1:
                return 0.0
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            return float(getattr(pynvml.nvmlDeviceGetUtilizationRates(handle), "gpu", 0.0))
        finally:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
    except Exception:
        return 0.0


class AdaptiveGovernor:
    """Headroom-aware admission + demand-aware yield. Drop-in for ``IdleGate``.

    Conservative-harvest posture (design intent, documented so the code is self-explaining):
    the governor harvests only a machine's **spare headroom** -- by design roughly 20-40% of its
    compute -- and always reserves a comfort ``margin_pct`` above the employee's *learned* demand
    before admitting anything. The ceilings below are **safety maxima, never targets**:
    ``hard_ceiling_pct`` (80, admission) and ``yield_ceiling_pct`` (95, the yield cap) exist so the
    derived thresholds can't wander into territory a user would feel; in practice the governor
    should rarely approach them, because people only begin to perceive slowdown around ~50%+
    sustained CPU. The goal is to stay invisible: run in the slack, yield early, and always leave
    the employee more compute than they are currently using.
    """

    def __init__(
        self,
        profiler: UsageProfiler | None = None,
        idle_gate: IdleGate | None = None,
        *,
        margin_pct: float = 25.0,         # comfort headroom reserved above profiled demand
        hard_ceiling_pct: float = 80.0,   # never admit when the user is already this busy
        yield_ceiling_pct: float = 95.0,  # absolute cap on the yield threshold
        hysteresis_pct: float = 10.0,     # yield threshold sits this far above admission
        min_headroom_pct: float = 15.0,   # only run where at least this much headroom exists
        sustained_samples: int = 3,       # consecutive over-threshold samples before yielding
        require_ac: bool = True,
    ) -> None:
        self.profiler = profiler if profiler is not None else UsageProfiler()
        self.gate = idle_gate if idle_gate is not None else IdleGate(require_ac=require_ac)
        self.margin_pct = margin_pct
        self.hard_ceiling_pct = hard_ceiling_pct
        self.yield_ceiling_pct = yield_ceiling_pct
        self.hysteresis_pct = hysteresis_pct
        self.min_headroom_pct = min_headroom_pct
        self.sustained_samples = sustained_samples
        self.require_ac = require_ac
        self._over = 0  # consecutive samples with user demand over the yield threshold
        self.last_decision: dict = {}  # readings from the last should_run(), for telemetry
        self._cpu = SystemCpuSampler()  # ctypes fallback when psutil is unavailable
        self._cpu.sample()
        self._proc = None
        if psutil is not None:
            try:
                self._proc = psutil.Process()
                psutil.cpu_percent(interval=None)      # prime the system delta
                self._proc.cpu_percent(interval=None)  # prime our own delta
            except Exception:
                self._proc = None

    # --- live sampling + attribution ---------------------------------------

    def _system_cpu(self) -> float:
        """System-wide CPU% (0-100). psutil when available, else GetSystemTimes deltas."""
        if psutil is not None:
            try:
                return float(psutil.cpu_percent(interval=None))
            except Exception:
                pass
        value = self._cpu.sample()
        return value if value is not None else 0.0

    def _our_cpu(self) -> float:
        """CPU% (normalized 0-100) used by THIS worker's own job process tree.

        Subtracting this from the system total isolates the *employee's* demand from the
        agent's own load, so the governor never yields on its own work. Host-side job
        subprocesses are attributed; a Docker job's container runs in the WSL VM (not a child
        process), so its CPU isn't subtracted yet -- ``docker stats`` accounting is the refinement.
        """
        if self._proc is None:
            return 0.0
        total = 0.0
        try:
            for child in self._proc.children(recursive=True):
                try:
                    total += float(child.cpu_percent(interval=None))
                except Exception:
                    continue
        except Exception:
            return 0.0
        return min(100.0, total / float(os.cpu_count() or 1))

    def user_cpu(self) -> float:
        """Estimated CPU the EMPLOYEE is using = system minus our job tree (0-100)."""
        return max(0.0, min(100.0, self._system_cpu() - self._our_cpu()))

    # --- thresholds derived from the learned profile -----------------------

    def admission_threshold(self, when: datetime | None = None) -> float:
        """User-CPU level below which we admit work this hour-of-week (time-aware)."""
        return min(
            self.hard_ceiling_pct, self.profiler.profile_now(when).cpu_mean + self.margin_pct
        )

    def yield_threshold(self, when: datetime | None = None) -> float:
        """User-CPU level above which we yield -- a hysteresis gap above admission so a machine
        hovering near the line doesn't flap between admit and yield."""
        return min(self.yield_ceiling_pct, self.admission_threshold(when) + self.hysteresis_pct)

    def headroom_now(self, when: datetime | None = None) -> float:
        """Estimated spare CPU% to harvest right now (capacity - profiled demand - margin)."""
        return max(0.0, 100.0 - self.profiler.profile_now(when).cpu_mean - self.margin_pct)

    # --- learning + decisions ----------------------------------------------

    def observe(self, when: datetime | None = None) -> None:
        """Fold the employee's current demand into the rolling profile. Call ONLY when our job
        is not running, so the envelope reflects the employee's usage and not the agent's."""
        self.profiler.record(
            self.user_cpu(), system_gpu_load_pct(), system_ram_load_pct(), when=when
        )

    def should_run(self, when: datetime | None = None) -> bool:
        """Admit work into the spare headroom -- runs during LIGHT foreground use, not only at
        full idle. Called between jobs (no job running) so user demand ~= system CPU. Decides
        against the CURRENT profile, THEN folds the sample in, so a cold/just-reset bucket can't
        authorize itself. Records the readings in ``last_decision`` for telemetry. Never raises."""
        try:
            if self.require_ac and not self.gate.on_ac():
                self.last_decision = {"admitted": False, "reason": "on_battery"}
                return False
            if self.gate.locked() or self.gate.gpu_busy():
                self.last_decision = {"admitted": False, "reason": "locked_or_gpu"}
                return False
            headroom = self.headroom_now(when)          # pre-update profile
            threshold = self.admission_threshold(when)  # pre-update profile
            demand = self.user_cpu()
            self.profiler.record(demand, system_gpu_load_pct(), system_ram_load_pct(), when=when)
            admitted = headroom >= self.min_headroom_pct and demand < threshold
            self.last_decision = {
                "admitted": admitted,
                "user_cpu": round(demand, 1),
                "headroom": round(headroom, 1),
                "admission_threshold": round(threshold, 1),
            }
            return admitted
        except Exception:
            return False

    def active_now(self, when: datetime | None = None) -> bool:
        """Yield signal polled during a job. True once the EMPLOYEE'S OWN demand (system minus
        our job tree) stays above the time-aware yield threshold for several samples -- they now
        want more than the margin we left them. Deliberately does NOT yield on mere input (typing
        while we use spare headroom is fine) and does NOT record into the profile (our job is
        running, so the sample isn't the employee's baseline). Never raises."""
        try:
            if self.user_cpu() > self.yield_threshold(when):
                self._over += 1
            else:
                self._over = 0
            return self._over >= self.sustained_samples
        except Exception:
            return False
