"""Challenge/ringer helpers for deterministic integrity checks."""

from __future__ import annotations

import secrets


def make_challenge() -> tuple[dict, dict]:
    x = secrets.randbelow(9998) + 2
    return {"x": x}, {"y": x * x + 1}


def _exact_int(value: object) -> bool:
    return type(value) is int


def check_challenge(output: dict, expected: dict) -> bool:
    try:
        if not isinstance(output, dict) or not isinstance(expected, dict):
            return False
        actual_y = output["y"]
        expected_y = expected["y"]
        return _exact_int(actual_y) and _exact_int(expected_y) and actual_y == expected_y
    except Exception:
        return False
