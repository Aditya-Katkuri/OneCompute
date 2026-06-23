"""Shared, canonical job execution kit (used in-process and inside the sandbox)."""

from jobkit.execute import EXECUTORS, execute

__all__ = ["EXECUTORS", "execute"]
