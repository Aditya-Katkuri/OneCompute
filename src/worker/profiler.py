"""Rolling per-machine usage profiler: the "learn the envelope" half of the demand-adaptive
governor (idea.md §5 "Demand-adaptive headroom harvesting").

It samples how much CPU / GPU / RAM the *machine* actually uses and maintains, per
**hour-of-week bucket** (168 buckets), a rolling min / average / peak. From that envelope the
governor sizes a right-sized background allocation that lives in the spare headroom, and a
time-aware threshold above which it must yield.

Privacy (idea.md §8): this is **on-device only**, raw activity never leaves the machine; the
profile is persisted locally and only a derived spare-capacity number is ever advertised.

Conventions: pure `ctypes` + `pynvml` (no heavy deps, per architecture.md §11); every OS call
is guarded so importing or calling this module never raises.
"""

from __future__ import annotations

import ctypes
import json
import logging
import math
import os
import uuid
from ctypes import wintypes
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from measurement.availability import AvailabilityTracker

BUCKETS = 168  # hours in a week: day-of-week (0=Mon) * 24 + hour-of-day
# EWMA weight per update. Buckets only update during their own hour-of-week slot, so at a
# ~30 s cadence a bucket sees a few hundred samples per occurrence; 0.05 gives a rolling feel
# over roughly the last month of occurrences without storing raw history.
_EWMA_ALPHA = 0.05
# Peaks/troughs relax slowly toward the mean so a one-off spike from weeks ago fades (rolling
# max/min, not all-time): new_extreme = decayed_old_extreme blended with the live sample.
_EXTREME_DECAY = 0.02
_STALE_DAYS = 35.0  # a bucket not seen in >35 days is reset on next record (rolling window)
MAX_PROFILE_BYTES = 1_000_000
MAX_BUCKET_SAMPLES = 10_000_000

logger = logging.getLogger(__name__)


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
    gpu_n: int = 0          # valid GPU samples, separate from CPU/RAM sample count
    ram_mean: float = 0.0
    ram_max: float = 0.0
    ac_mean: float = 0.0    # % of samples the machine was on AC power (harvestable-window signal)
    idle_mean: float = 0.0  # % of samples the human was idle/away (prime-harvest-window signal)
    ac_n: int = 0           # valid AC samples, separate from CPU/RAM sample count
    idle_n: int = 0         # valid idle samples, separate from CPU/RAM sample count
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


def _finite_number(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _percent(value: object, default: float = 0.0) -> float:
    return max(0.0, min(100.0, _finite_number(value, default)))


def _bucket_from_dict(raw: dict, *, gpu_supported: bool | None = None) -> BucketStat:
    try:
        n = min(MAX_BUCKET_SAMPLES, max(0, int(raw.get("n", 0))))
    except (TypeError, ValueError, OverflowError):
        n = 0
    cpu_mean = _percent(raw.get("cpu_mean"))
    gpu_mean = _percent(raw.get("gpu_mean"))
    ram_mean = _percent(raw.get("ram_mean"))
    try:
        if "gpu_n" in raw:
            gpu_n = max(0, int(raw.get("gpu_n", 0)))
        elif gpu_supported is True:
            gpu_n = n
        elif gpu_supported is False:
            gpu_n = 0
        else:
            gpu_n = n if gpu_mean > 0.0 or _percent(raw.get("gpu_max")) > 0.0 else 0
    except (TypeError, ValueError, OverflowError):
        gpu_n = 0
    try:
        ac_n = max(0, int(raw.get("ac_n", n if "ac_mean" in raw else 0)))
    except (TypeError, ValueError, OverflowError):
        ac_n = 0
    try:
        idle_n = max(0, int(raw.get("idle_n", n if "idle_mean" in raw else 0)))
    except (TypeError, ValueError, OverflowError):
        idle_n = 0
    return BucketStat(
        n=n,
        cpu_mean=cpu_mean,
        cpu_max=max(cpu_mean, _percent(raw.get("cpu_max"))),
        cpu_min=min(cpu_mean, _percent(raw.get("cpu_min"), 100.0)),
        gpu_mean=gpu_mean,
        gpu_max=max(gpu_mean, _percent(raw.get("gpu_max"))),
        gpu_n=min(gpu_n, n),
        ram_mean=ram_mean,
        ram_max=max(ram_mean, _percent(raw.get("ram_max"))),
        ac_mean=_percent(raw.get("ac_mean")),
        idle_mean=_percent(raw.get("idle_mean")),
        ac_n=min(ac_n, n),
        idle_n=min(idle_n, n),
        updated_at=max(0.0, _finite_number(raw.get("updated_at"))),
    )


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


def default_profile_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "OneCompute" / "usage_profile.json"


class UsageProfiler:
    """Learns and persists a machine's rolling hour-of-week usage envelope (on-device only)."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else default_profile_path()
        self.buckets: list[BucketStat] = [BucketStat() for _ in range(BUCKETS)]
        self.availability = AvailabilityTracker()
        self.gpu_supported: bool | None = None
        self.load_warning: str | None = None
        self.recovered_profile_path: Path | None = None
        self.recovery_blocked = False
        self.last_save_error: str | None = None
        self._load()

    def _load(self) -> None:
        try:
            if self.path.exists():
                if not self.path.is_file():
                    raise ValueError("profile path is not a file")
                if self.path.stat().st_size > MAX_PROFILE_BYTES:
                    raise ValueError("profile exceeds the maximum expected size")
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("profile root must be a JSON object")
                raw_buckets = data.get("buckets", [])
                if not isinstance(raw_buckets, list):
                    raise ValueError("profile buckets must be a JSON list")
                if isinstance(data.get("gpu_supported"), bool):
                    self.gpu_supported = data["gpu_supported"]
                for i, raw in enumerate(raw_buckets[:BUCKETS]):
                    if isinstance(raw, dict):
                        self.buckets[i] = _bucket_from_dict(
                            raw,
                            gpu_supported=self.gpu_supported,
                        )
                self.availability = AvailabilityTracker.from_dict(data.get("availability"))
        except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
            self.load_warning = str(exc)
            self.recovered_profile_path = self._preserve_corrupt_profile()
            self.recovery_blocked = (
                self.recovered_profile_path is None and self.path.exists()
            )
            logger.warning(
                "usage profile could not be loaded%s: %s",
                (
                    f"; preserved at {self.recovered_profile_path}"
                    if self.recovered_profile_path is not None
                    else ""
                ),
                exc,
            )
            self.buckets = [BucketStat() for _ in range(BUCKETS)]
            self.availability = AvailabilityTracker()
            self.gpu_supported = None

    def _preserve_corrupt_profile(self) -> Path | None:
        if not self.path.is_file():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = self.path.with_name(
            f"{self.path.stem}.corrupt-{stamp}-{uuid.uuid4().hex[:8]}{self.path.suffix}"
        )
        try:
            os.replace(self.path, destination)
            return destination
        except OSError:
            return None

    def assert_writable(self) -> None:
        """Fail early if the profile directory cannot safely persist pilot data."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and not self.path.is_file():
            raise OSError(f"profile path is not a file: {self.path}")
        if self.recovery_blocked:
            raise OSError(f"invalid profile could not be preserved: {self.path}")
        probe = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.probe")
        try:
            with probe.open("x", encoding="utf-8") as handle:
                handle.write("ok")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            try:
                probe.unlink()
            except FileNotFoundError:
                pass

    def save(self) -> bool:
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        if self.recovery_blocked:
            self.last_save_error = f"invalid profile could not be preserved: {self.path}"
            logger.error("usage profile save blocked for %s: %s", self.path, self.last_save_error)
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 3,
                "gpu_supported": self.gpu_supported,
                "buckets": [asdict(b) for b in self.buckets],
                "availability": self.availability.to_dict(),
            }
            encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False)
            with tmp.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            self.last_save_error = None
            return True
        except (OSError, TypeError, ValueError) as exc:
            self.last_save_error = str(exc)
            logger.error("usage profile save failed for %s: %s", self.path, exc)
            return False
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning("could not remove usage profile temporary file %s: %s", tmp, exc)

    def record_availability(
        self,
        sampled_at: float,
        expected_interval_seconds: float = 30.0,
    ) -> None:
        """Persist successful-sample timing so sleep/off/observer gaps survive restarts."""
        self.availability.record(sampled_at, expected_interval_seconds)

    def record(
        self,
        cpu: float,
        gpu: float | None,
        ram: float,
        when: datetime | None = None,
        on_ac: bool | None = None,
        idle: bool | None = None,
    ) -> None:
        """Fold one (cpu%, gpu%, ram%) observation into the current hour-of-week bucket.

        ``on_ac`` (machine plugged in) and ``idle`` (human away) are optional 0/1 indicators folded
        as percentages, so their bucket means become the % of time on AC and the % of time idle --
        the harvestable-window signals. When omitted (e.g. a caller that doesn't sample them) the
        AC/idle means are left unchanged.
        """
        cpu = _percent(cpu)
        gpu = _percent(gpu) if gpu is not None else None
        ram = _percent(ram)
        now = when or datetime.now()
        b = self.buckets[bucket_index(now)]
        ts = now.timestamp()
        if b.n > 0 and (ts - b.updated_at) > _STALE_DAYS * 86400:
            b = BucketStat()  # bucket aged out of the rolling window -> reset
        b.cpu_mean = _ewma(b.cpu_mean, cpu, b.n)
        b.cpu_max = _roll_max(b.cpu_max, cpu, b.cpu_mean) if b.n else cpu
        b.cpu_min = _roll_min(b.cpu_min, cpu, b.cpu_mean) if b.n else cpu
        if gpu is not None:
            b.gpu_mean = _ewma(b.gpu_mean, gpu, b.gpu_n)
            b.gpu_max = _roll_max(b.gpu_max, gpu, b.gpu_mean) if b.gpu_n else gpu
            b.gpu_n += 1
        b.ram_mean = _ewma(b.ram_mean, ram, b.n)
        b.ram_max = _roll_max(b.ram_max, ram, b.ram_mean) if b.n else ram
        if on_ac is not None:
            b.ac_mean = _ewma(b.ac_mean, 100.0 if on_ac else 0.0, b.ac_n)
            b.ac_n += 1
        if idle is not None:
            b.idle_mean = _ewma(b.idle_mean, 100.0 if idle else 0.0, b.idle_n)
            b.idle_n += 1
        b.n += 1
        b.updated_at = ts
        self.buckets[bucket_index(now)] = b

    def profile_now(self, when: datetime | None = None) -> BucketStat:
        """The learned usage stats for the current hour-of-week bucket."""
        return self.buckets[bucket_index(when or datetime.now())]
