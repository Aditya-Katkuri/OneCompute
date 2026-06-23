"""Server-authoritative credit metering."""

from __future__ import annotations


def credits(accepted_units: int, class_weight: float) -> float:
    return accepted_units * class_weight
