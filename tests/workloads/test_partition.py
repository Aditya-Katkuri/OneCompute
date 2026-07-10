"""The hardcoded split helpers cover [0, total) exactly: no gaps, no overlaps."""
from __future__ import annotations

import pytest

from workloads.partition import (
    even_ranges,
    weighted_partition,
    weighted_ranges,
    worker_weight,
)


def _covers_exactly(ranges, total):
    """Assert ranges are contiguous, ordered, and cover [0, total) with no gaps/overlap."""
    cursor = 0
    covered = 0
    for start, end in ranges:
        assert start == cursor, f"gap/overlap at {start} (expected {cursor})"
        assert end >= start
        covered += end - start
        cursor = end
    assert cursor == total
    assert covered == total


def test_even_ranges_exact_division():
    ranges = even_ranges(12, 3)
    assert ranges == [(0, 4), (4, 8), (8, 12)]
    _covers_exactly(ranges, 12)


def test_even_ranges_remainder_spread_to_leading_tiles():
    # 10 across 3 -> sizes 4,3,3 (remainder on the first tile), still covers exactly.
    ranges = even_ranges(10, 3)
    sizes = [e - s for s, e in ranges]
    assert sizes == [4, 3, 3]
    assert max(sizes) - min(sizes) <= 1
    _covers_exactly(ranges, 10)


def test_even_ranges_more_tiles_than_total_emits_empty_tiles():
    ranges = even_ranges(2, 5)
    assert len(ranges) == 5
    _covers_exactly(ranges, 2)
    # First two tiles carry the work, the rest are empty (k, k).
    assert sum(1 for s, e in ranges if e > s) == 2


def test_even_ranges_zero_total():
    ranges = even_ranges(0, 4)
    assert ranges == [(0, 0), (0, 0), (0, 0), (0, 0)]
    _covers_exactly(ranges, 0)


def test_even_ranges_validates():
    with pytest.raises(ValueError):
        even_ranges(10, 0)
    with pytest.raises(ValueError):
        even_ranges(10, -1)
    with pytest.raises(ValueError):
        even_ranges(-1, 3)


def test_weighted_ranges_proportional():
    # 100 split 1:2:1 -> 25/50/25.
    ranges = weighted_ranges(100, [1.0, 2.0, 1.0])
    sizes = [e - s for s, e in ranges]
    assert sizes == [25, 50, 25]
    _covers_exactly(ranges, 100)


def test_weighted_ranges_leftover_goes_to_largest_remainder():
    # 10 split 1:1:1 -> shares 3.33 each; leftover 1 goes to one tile, still covers 10.
    ranges = weighted_ranges(10, [1.0, 1.0, 1.0])
    _covers_exactly(ranges, 10)
    assert sum(e - s for s, e in ranges) == 10
    assert len(ranges) == 3


def test_weighted_ranges_uneven_weights_cover_exactly():
    ranges = weighted_ranges(97, [0.5, 1.5, 3.0, 1.0])
    _covers_exactly(ranges, 97)
    assert len(ranges) == 4


def test_weighted_ranges_validates():
    with pytest.raises(ValueError):
        weighted_ranges(10, [])
    with pytest.raises(ValueError):
        weighted_ranges(-1, [1.0])
    with pytest.raises(ValueError):
        weighted_ranges(10, [1.0, -1.0])
    with pytest.raises(ValueError):
        weighted_ranges(10, [0.0, 0.0])


# --- weighted_partition (integer share counts, the launcher's tile budget) -----------------


def test_weighted_partition_sums_exactly_and_is_proportional():
    # 100 units split 1:2:1 -> 25/50/25, sum is exact.
    counts = weighted_partition(100, [1.0, 2.0, 1.0])
    assert counts == [25, 50, 25]
    assert sum(counts) == 100


def test_weighted_partition_leftover_to_largest_remainder_sums_exactly():
    # 10 units, equal weights -> shares 3.33 each; the leftover unit keeps the sum exact.
    counts = weighted_partition(10, [1.0, 1.0, 1.0])
    assert sum(counts) == 10
    assert max(counts) - min(counts) <= 1  # near-even, like even_ranges


def test_weighted_partition_heavier_weight_gets_more():
    counts = weighted_partition(40, [1.0, 5.0])  # CPU vs GPU class weights
    assert counts[1] > counts[0]
    assert sum(counts) == 40


def test_weighted_partition_each_gets_at_least_one_when_enough():
    # A tiny-weight worker still gets >= 1 tile when total >= number of shares.
    counts = weighted_partition(5, [1000.0, 1.0, 1.0, 1.0, 1.0])
    assert all(c >= 1 for c in counts)
    assert sum(counts) == 5


def test_weighted_partition_total_below_n_leaves_smallest_at_zero():
    # Only 2 units for 4 shares: the two heaviest win, the lightest are left at 0.
    counts = weighted_partition(2, [4.0, 3.0, 2.0, 1.0])
    assert sum(counts) == 2
    assert counts[0] >= 1 and counts[1] >= 1
    assert counts[3] == 0


def test_weighted_partition_single_worker_takes_all():
    assert weighted_partition(7, [3.0]) == [7]


def test_weighted_partition_equal_weights_matches_even_split():
    # Homogeneous fleet: same near-even shares as even_ranges (no regression in behavior).
    counts = weighted_partition(23, [1.0, 1.0, 1.0, 1.0])
    even = [e - s for s, e in even_ranges(23, 4)]
    assert sorted(counts) == sorted(even)
    assert sum(counts) == 23


def test_weighted_partition_deterministic():
    a = weighted_partition(97, [0.5, 1.5, 3.0, 1.0])
    b = weighted_partition(97, [0.5, 1.5, 3.0, 1.0])
    assert a == b
    assert sum(a) == 97


def test_weighted_partition_validates():
    with pytest.raises(ValueError):
        weighted_partition(10, [])
    with pytest.raises(ValueError):
        weighted_partition(-1, [1.0])
    with pytest.raises(ValueError):
        weighted_partition(10, [1.0, -1.0])
    with pytest.raises(ValueError):
        weighted_partition(10, [0.0, 0.0])


# --- worker_weight (capability x idle headroom x free RAM) ----------------------------------


def test_worker_weight_is_positive_and_capability_scales():
    # A GPU-class worker (5) outweighs a CPU-class worker (1) at equal utilization/RAM.
    gpu = worker_weight(5.0, free_ram_gb=8.0, load_pct=0.0)
    cpu = worker_weight(1.0, free_ram_gb=8.0, load_pct=0.0)
    assert gpu > cpu > 0.0


def test_worker_weight_favors_idle_machines():
    idle = worker_weight(1.0, free_ram_gb=8.0, load_pct=5.0)
    busy = worker_weight(1.0, free_ram_gb=8.0, load_pct=95.0)
    assert idle > busy > 0.0


def test_worker_weight_favors_more_free_ram():
    roomy = worker_weight(1.0, free_ram_gb=16.0, load_pct=0.0)
    tight = worker_weight(1.0, free_ram_gb=0.5, load_pct=0.0)
    assert roomy > tight > 0.0


def test_worker_weight_fully_loaded_worker_still_positive():
    # Even a saturated, RAM-starved worker keeps a small positive share (never zero).
    assert worker_weight(1.0, free_ram_gb=0.0, load_pct=100.0) > 0.0


def test_worker_weight_unknown_ram_treated_as_full_and_deterministic():
    assert worker_weight(1.0, free_ram_gb=None, load_pct=0.0) == worker_weight(
        1.0, free_ram_gb=None, load_pct=0.0
    )
    # Unknown RAM behaves like ample RAM (full memory factor).
    assert worker_weight(1.0, free_ram_gb=None, load_pct=0.0) == worker_weight(
        1.0, free_ram_gb=32.0, load_pct=0.0
    )


def test_worker_weight_clamps_out_of_range_load():
    # Loads outside 0..100 are clamped, so weights stay ordered and bounded.
    assert worker_weight(1.0, load_pct=-10.0) == worker_weight(1.0, load_pct=0.0)
    assert worker_weight(1.0, load_pct=150.0) == worker_weight(1.0, load_pct=100.0)


def test_worker_weight_idle_capable_beats_busy_weak():
    # The whole point: an idle GPU box should be handed more than a saturated CPU box.
    idle_gpu = worker_weight(5.0, free_ram_gb=16.0, load_pct=2.0)
    busy_cpu = worker_weight(1.0, free_ram_gb=1.0, load_pct=90.0)
    assert idle_gpu > busy_cpu
