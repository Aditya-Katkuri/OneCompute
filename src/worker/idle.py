"""Conservative local idle gate for NightShift workers."""

from __future__ import annotations

import ctypes


class _LastInputInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("dwTime", ctypes.c_uint),
    ]


class _SystemPowerStatus(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", ctypes.c_ulong),
        ("BatteryFullLifeTime", ctypes.c_ulong),
    ]


class IdleGate:
    def __init__(
        self,
        input_idle_threshold_s: float = 60.0,
        require_ac: bool = True,
        gpu_busy_pct: float = 20.0,
    ) -> None:
        self.input_idle_threshold_s = input_idle_threshold_s
        self.require_ac = require_ac
        self.gpu_busy_pct = gpu_busy_pct

    def input_idle_seconds(self) -> float:
        """Return seconds since last input in this session; fail open for demo continuity.

        GetLastInputInfo is session-specific. Do not run this detector only from session 0:
        it can make an interactive machine look idle forever.
        """
        try:
            last_input = _LastInputInfo()
            last_input.cbSize = ctypes.sizeof(_LastInputInfo)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)):  # type: ignore[attr-defined]
                return 1_000_000.0
            tick_count = ctypes.windll.kernel32.GetTickCount()  # type: ignore[attr-defined]
            elapsed_ms = (int(tick_count) - int(last_input.dwTime)) & 0xFFFFFFFF
            return max(0.0, elapsed_ms / 1000.0)
        except Exception:
            return 1_000_000.0

    def on_ac(self) -> bool:
        """Return True when AC power is detected; unknown defaults to True."""
        try:
            status = _SystemPowerStatus()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):  # type: ignore[attr-defined]
                return True
            return int(status.ACLineStatus) == 1
        except Exception:
            return True

    def locked(self) -> bool:
        """Best-effort lock detection. Unknown defaults to unlocked."""
        return False

    def gpu_busy(self) -> bool:
        """Return True when NVIDIA GPU utilization is above the threshold.

        Keyboard idle is not GPU idle: rendering, video effects, or local ML can keep a GPU busy.
        """
        try:
            import pynvml  # type: ignore[import-not-found]

            pynvml.nvmlInit()
            try:
                if pynvml.nvmlDeviceGetCount() < 1:
                    return False
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
                return float(getattr(utilization, "gpu", 0.0)) > self.gpu_busy_pct
            finally:
                try:
                    pynvml.nvmlShutdown()
                except Exception:
                    pass
        except Exception:
            return False

    def should_run(self) -> bool:
        """Conservative admission decision that never raises."""
        try:
            idle_enough = self.input_idle_seconds() >= self.input_idle_threshold_s
            power_ok = self.on_ac() or not self.require_ac
            return bool(idle_enough and power_ok and not self.locked() and not self.gpu_busy())
        except Exception:
            return False

    def active_now(self) -> bool:
        """Return True when the human has just touched the machine."""
        try:
            return bool(self.input_idle_seconds() < 1.0)
        except Exception:
            return False
