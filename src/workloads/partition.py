"""Hardcoded work-splitting helpers for the OneCompute fleet demo.

The demo fans ONE big workload across N physical machines by carving the total
work into N contiguous tiles. These helpers are pure (no I/O, stdlib-only) so
they can be reused by every workload builder (fractal rows, optimize candidate
indices, synth row slices). Each helper returns a list of ``(start, end)``
half-open ranges that together cover ``[0, total)`` exactly -- no gaps, no
overlaps -- so ``sum(end - start) == total`` always holds.
"""

from __future__ import annotations


def even_ranges(total: int, n: int) -> list[tuple[int, int]]:
    """Split ``[0, total)`` into ``n`` contiguous, near-equal half-open ranges.

    The remainder is spread one-per-tile across the leading tiles, so sizes differ
    by at most 1 and the ranges cover ``[0, total)`` exactly. Empty ranges
    ``(k, k)`` are emitted when ``total < n`` (so the result always has length
    ``n``), which is harmless: a builder simply produces a zero-unit tile.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if total < 0:
        raise ValueError("total must be non-negative")

    base, extra = divmod(total, n)
    ranges: list[tuple[int, int]] = []
    start = 0
    for i in range(n):
        size = base + (1 if i < extra else 0)
        ranges.append((start, start + size))
        start += size
    return ranges


def weighted_ranges(total: int, weights: list[float]) -> list[tuple[int, int]]:
    """Split ``[0, total)`` into contiguous ranges sized proportional to ``weights``.

    The number of tiles equals ``len(weights)``. Sizes are the floor of each
    proportional share; the leftover (from flooring) is handed out one-per-tile to
    the tiles with the largest fractional remainders, so the ranges still cover
    ``[0, total)`` exactly and ``sum(end - start) == total``. Deterministic for a
    fixed ``weights`` list.
    """
    if not weights:
        raise ValueError("weights must be non-empty")
    if total < 0:
        raise ValueError("total must be non-negative")
    if any(w < 0 for w in weights):
        raise ValueError("weights must be non-negative")
    weight_sum = float(sum(weights))
    if weight_sum <= 0:
        raise ValueError("weights must sum to a positive value")

    # Floor each share, remember the fractional remainder to allocate the leftover.
    shares = [total * w / weight_sum for w in weights]
    floors = [int(s) for s in shares]
    leftover = total - sum(floors)
    # Hand the leftover to the largest fractional remainders (stable on ties by index).
    order = sorted(range(len(weights)), key=lambda i: (shares[i] - floors[i], -i), reverse=True)
    for i in order[:leftover]:
        floors[i] += 1

    ranges: list[tuple[int, int]] = []
    start = 0
    for size in floors:
        ranges.append((start, start + size))
        start += size
    return ranges
