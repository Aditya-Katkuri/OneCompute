"""Conservative local idle gate for OneCompute workers."""

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

    def input_idle_seconds_sample(self) -> float | None:
        """Return seconds since last input in this session, or None when unavailable.

        GetLastInputInfo is session-specific. Do not run this detector only from session 0:
        it can make an interactive machine look idle forever.
        """
        try:
            last_input = _LastInputInfo()
            last_input.cbSize = ctypes.sizeof(_LastInputInfo)
            if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(last_input)):  # type: ignore[attr-defined]
                return None
            tick_count = ctypes.windll.kernel32.GetTickCount()  # type: ignore[attr-defined]
            elapsed_ms = (int(tick_count) - int(last_input.dwTime)) & 0xFFFFFFFF
            return max(0.0, elapsed_ms / 1000.0)
        except Exception:
            return None

    def input_idle_seconds(self) -> float:
        """Return seconds since last input; unknown preserves the existing fail-open behavior."""
        value = self.input_idle_seconds_sample()
        return value if value is not None else 1_000_000.0

    def on_ac_state(self) -> bool | None:
        """Return the sampled AC state, or None when Windows cannot determine it."""
        try:
            status = _SystemPowerStatus()
            if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):  # type: ignore[attr-defined]
                return None
            value = int(status.ACLineStatus)
            return value == 1 if value in {0, 1} else None
        except Exception:
            return None

    def on_ac(self) -> bool:
        """Return True when AC is detected; unknown preserves the existing fail-open behavior."""
        value = self.on_ac_state()
        return value if value is not None else True

    def user_idle_state(self) -> bool | None:
        """Return whether the user appears idle, or None when input timing is unavailable."""
        try:
            if self.locked():
                return True
            seconds = self.input_idle_seconds_sample()
            return seconds >= self.input_idle_threshold_s if seconds is not None else None
        except Exception:
            return None

    def user_idle(self) -> bool:
        """True when the human appears away: the session is locked, or there has been no keyboard/
        mouse input for at least the idle threshold. Uses GetLastInputInfo only (a timestamp, never
        keystroke content -- privacy/EDR clean). Never raises."""
        value = self.user_idle_state()
        return value if value is not None else True

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
