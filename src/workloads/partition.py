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


def weighted_partition(total_units: int, weights: list[float]) -> list[int]:
    """Split ``total_units`` into ``len(weights)`` integer shares proportional to ``weights``.

    This is the count-only sibling of :func:`weighted_ranges`: it returns how many units each
    share gets (not ``(start, end)`` ranges), which is what the launcher needs to hand more
    tiles to more capable / more idle machines. Properties (all deterministic for a fixed input):

    - ``sum(result) == total_units`` exactly (largest-remainder apportionment).
    - Each share is ``>= 1`` when ``total_units >= len(weights)`` (every worker gets work);
      when ``total_units < len(weights)`` there simply is not enough to go around, so the
      lowest-weight shares are left at ``0``.
    - Shares track ``weights`` proportionally; ties in the leftover go to the lowest index.

    When all weights are equal this reduces to the same near-even split as :func:`even_ranges`,
    so homogeneous fleets keep the original uniform behavior.
    """
    if not weights:
        raise ValueError("weights must be non-empty")
    if total_units < 0:
        raise ValueError("total_units must be non-negative")
    if any(w < 0 for w in weights):
        raise ValueError("weights must be non-negative")
    weight_sum = float(sum(weights))
    if weight_sum <= 0:
        raise ValueError("weights must sum to a positive value")

    n = len(weights)
    shares = [total_units * w / weight_sum for w in weights]
    counts = [int(s) for s in shares]
    leftover = total_units - sum(counts)
    # Hand the flooring leftover to the largest fractional remainders (stable on ties by index).
    order = sorted(range(n), key=lambda i: (shares[i] - counts[i], -i), reverse=True)
    for i in order[:leftover]:
        counts[i] += 1

    # Guarantee every share gets at least one unit once there is enough to cover all shares.
    # Borrow single units from the largest holders (deterministic) to fill any zero shares;
    # this keeps the exact sum while ensuring no capable worker is ever handed nothing.
    if total_units >= n:
        while True:
            zeros = [i for i in range(n) if counts[i] == 0]
            if not zeros:
                break
            donor = max(range(n), key=lambda i: (counts[i], -i))
            if counts[donor] <= 1:
                break  # nothing to borrow without creating a new zero (unreachable when total >= n)
            counts[donor] -= 1
            counts[min(zeros)] += 1
    return counts


def oversubscribed_tiles(worker_count: int, factor: int, cap: int) -> int:
    """Tile count for over-decomposition: ``~factor`` tiles per worker, clamped to ``[1, cap]``.

    This is the count-only knob behind work-stealing (docs/work-stealing.md): splitting a workload
    into MORE, SMALLER tiles than there are workers lets idle/fast machines keep pulling the next
    tile off the queue while a slow machine only ever holds one small tile. Pure and deterministic;
    an empty fleet is treated as one worker so a launch still produces ``factor`` tiles, and the cap
    bounds the queue so a huge fleet or factor cannot explode the tile count.
    """
    f = max(int(factor), 1)
    workers = max(int(worker_count), 1)
    bound = max(int(cap), 1)
    return max(1, min(workers * f, bound))


# Weighting model knobs (documented in docs/partitioning.md). Each factor is bounded so that no
# single dimension can zero out a worker's share: an approved worker always earns some work.
_IDLE_FLOOR = 0.1   # a fully-loaded worker still earns 10% of an idle worker's utilization share
_RAM_FLOOR = 0.5    # a memory-starved worker still earns 50% of a RAM-rich worker's memory share
_RAM_REF_GB = 8.0   # free RAM at/above this counts as full memory headroom


def worker_weight(
    class_weight: float,
    free_ram_gb: float | None = None,
    load_pct: float = 0.0,
) -> float:
    """Map a worker's capability + live utilization to a positive partitioning weight.

    Favors machines that are both **more capable** and **more idle**, so they are handed
    proportionally more of a launched workload. The inputs mirror the orchestrator's live worker
    row (``class_weight`` is the server-assigned capability tier: GPU=5, CPU=1; ``free_ram_gb`` is
    the last-known free RAM; ``load_pct`` is the busier of the worker's CPU/GPU percent, 0..100).

    weight = class_weight x idle_factor x ram_factor, where each factor is bounded away from zero:

    - ``idle_factor`` scales linearly from ``_IDLE_FLOOR`` (fully loaded) to ``1.0`` (fully idle).
    - ``ram_factor`` scales linearly from ``_RAM_FLOOR`` (no free RAM) to ``1.0`` at/above
      ``_RAM_REF_GB`` of free RAM; unknown free RAM is treated as full (``1.0``).

    Pure and deterministic given its inputs; the result is always strictly positive so every
    approved worker earns at least a minimal share.
    """
    cw = float(class_weight)
    if cw <= 0:
        cw = 1.0  # every approved worker counts at least as one CPU-class machine
    load = float(load_pct or 0.0)
    load = min(max(load, 0.0), 100.0)
    idle_factor = _IDLE_FLOOR + (1.0 - _IDLE_FLOOR) * (1.0 - load / 100.0)
    if free_ram_gb is None:
        ram_factor = 1.0
    else:
        avail = max(float(free_ram_gb), 0.0)
        ram_factor = _RAM_FLOOR + (1.0 - _RAM_FLOOR) * min(avail / _RAM_REF_GB, 1.0)
    return cw * idle_factor * ram_factor
