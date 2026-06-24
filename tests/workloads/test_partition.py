"""The hardcoded split helpers cover [0, total) exactly: no gaps, no overlaps."""
from __future__ import annotations

import pytest

from workloads.partition import even_ranges, weighted_ranges


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
