"""Rolling per-machine usage profiler - the "learn the envelope" half of the demand-adaptive
governor (idea.md §5 "Demand-adaptive headroom harvesting").

It samples how much CPU / GPU / RAM the *machine* actually uses and maintains, per
**hour-of-week bucket** (168 buckets), a rolling min / average / peak. From that envelope the
governor sizes a right-sized background allocation that lives in the spare headroom, and a
time-aware threshold above which it must yield.

Privacy (idea.md §8): this is **on-device only** - raw activity never leaves the machine; the
profile is persisted locally and only a derived spare-capacity number is ever advertised.

Conventions: pure `ctypes` + `pynvml` (no heavy deps, per architecture.md §11); every OS call
is guarded so importing or calling this module never raises.
"""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

BUCKETS = 168  # hours in a week: day-of-week (0=Mon) * 24 + hour-of-day
# EWMA weight per update. Buckets only update during their own hour-of-week slot, so at a
# ~30 s cadence a bucket sees a few hundred samples per occurrence; 0.05 gives a rolling feel
# over roughly the last month of occurrences without storing raw history.
_EWMA_ALPHA = 0.05
# Peaks/troughs relax slowly toward the mean so a one-off spike from weeks ago fades (rolling
# max/min, not all-time): new_extreme = decayed_old_extreme blended with the live sample.
_EXTREME_DECAY = 0.02
_STALE_DAYS = 35.0  # a bucket not seen in >35 days is reset on next record (rolling window)


def bucket_index(when: datetime) -> int:
    """Hour-of-week bucket for ``when`` (local time): 0 = Mon 00:00 ... 167 = Sun 23:00."""
    return (when.weekday() * 24 + when.hour) % BUCKETS


@dataclass
class BucketStat:
    """Rolling CPU/GPU/RAM usage stats for one hour-of-week bucket (all percentages 0-100)."""

    n: int = 0
    cpu_mean: float = 0.0
    cpu_max: float = 0.0
    cpu_min: float = 100.0
    gpu_mean: float = 0.0
    gpu_max: float = 0.0
    ram_mean: float = 0.0
    ram_max: float = 0.0
    updated_at: float = 0.0  # epoch seconds of the last record


def _ewma(prev: float, sample: float, n: int) -> float:
    if n <= 0:
        return sample
    return (1.0 - _EWMA_ALPHA) * prev + _EWMA_ALPHA * sample


def _roll_max(prev: float, sample: float, mean: float) -> float:
    decayed = prev - _EXTREME_DECAY * (prev - mean)  # relax toward mean
    return max(decayed, sample)


def _roll_min(prev: float, sample: float, mean: float) -> float:
    decayed = prev + _EXTREME_DECAY * (mean - prev)  # relax toward mean
    return min(decayed, sample)


# --- system CPU% via GetSystemTimes (no psutil; matches the ctypes/pynvml convention) -------


class _Filetime(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    def as_int(self) -> int:
        return (int(self.high) << 32) | int(self.low)


class SystemCpuSampler:
    """System-wide CPU utilization % from successive GetSystemTimes deltas.

    The first ``sample()`` primes the baseline and returns ``None``; subsequent calls return
    the busy fraction since the previous call. Never raises (returns ``None`` on any failure).
    """

    def __init__(self) -> None:
        self._prev: tuple[int, int] | None = None  # (idle, total)

    def _read(self) -> tuple[int, int] | None:
        try:
            idle, kernel, user = _Filetime(), _Filetime(), _Filetime()
            ok = ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            )
            if not ok:
                return None
            # On Windows kernel time INCLUDES idle time, so total = kernel + user.
            return idle.as_int(), kernel.as_int() + user.as_int()
        except Exception:
            return None

    def sample(self) -> float | None:
        cur = self._read()
        if cur is None:
            return None
        if self._prev is None:
            self._prev = cur
            return None
        (idle0, total0), (idle1, total1) = self._prev, cur
        self._prev = cur
        d_total = total1 - total0
        d_idle = idle1 - idle0
        if d_total <= 0:
            return None
        busy = 1.0 - (d_idle / d_total)
        return max(0.0, min(100.0, busy * 100.0))


def _default_profile_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "OneCompute" / "usage_profile.json"


class UsageProfiler:
    """Learns and persists a machine's rolling hour-of-week usage envelope (on-device only)."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else _default_profile_path()
        self.buckets: list[BucketStat] = [BucketStat() for _ in range(BUCKETS)]
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                for i, raw in enumerate(data.get("buckets", [])[:BUCKETS]):
                    self.buckets[i] = BucketStat(**raw)
        except Exception:
            # Corrupt/unreadable profile -> start fresh; never raise.
            self.buckets = [BucketStat() for _ in range(BUCKETS)]

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"buckets": [asdict(b) for b in self.buckets]}
            self.path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        except Exception:
            pass  # best-effort persistence; never raise on the demo path

    def record(self, cpu: float, gpu: float, ram: float, when: datetime | None = None) -> None:
        """Fold one (cpu%, gpu%, ram%) observation into the current hour-of-week bucket."""
        now = when or datetime.now()
        b = self.buckets[bucket_index(now)]
        ts = now.timestamp()
        if b.n > 0 and (ts - b.updated_at) > _STALE_DAYS * 86400:
            b = BucketStat()  # bucket aged out of the rolling window -> reset
        b.cpu_mean = _ewma(b.cpu_mean, cpu, b.n)
        b.cpu_max = _roll_max(b.cpu_max, cpu, b.cpu_mean) if b.n else cpu
        b.cpu_min = _roll_min(b.cpu_min, cpu, b.cpu_mean) if b.n else cpu
        b.gpu_mean = _ewma(b.gpu_mean, gpu, b.n)
        b.gpu_max = _roll_max(b.gpu_max, gpu, b.gpu_mean) if b.n else gpu
        b.ram_mean = _ewma(b.ram_mean, ram, b.n)
        b.ram_max = _roll_max(b.ram_max, ram, b.ram_mean) if b.n else ram
        b.n += 1
        b.updated_at = ts
        self.buckets[bucket_index(now)] = b

    def profile_now(self, when: datetime | None = None) -> BucketStat:
        """The learned usage stats for the current hour-of-week bucket."""
        return self.buckets[bucket_index(when or datetime.now())]
