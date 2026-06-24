"""The `fractal` executor renders Mandelbrot escape counts per row-band (pure stdlib)."""
from __future__ import annotations

from jobkit.execute import execute


def test_in_set_point_reaches_max_iter():
    # The origin c=0 never escapes -> count == max_iter. Map a 1x1 window onto c=0.
    out = execute(
        "fractal",
        {
            "width": 1,
            "height": 1,
            "row_start": 0,
            "row_end": 1,
            "max_iter": 50,
            "x_min": 0.0,
            "x_max": 0.0001,
            "y_min": 0.0,
            "y_max": 0.0001,
        },
    )
    assert out["rows"][0][0] == 50  # in-set pixel hits the cap
    assert out["yielded"] is False


def test_escaping_point_has_small_count():
    # c well outside the set (far positive real axis) escapes almost immediately.
    out = execute(
        "fractal",
        {
            "width": 1,
            "height": 1,
            "row_start": 0,
            "row_end": 1,
            "max_iter": 120,
            "x_min": 4.0,
            "x_max": 4.0001,
            "y_min": 4.0,
            "y_max": 4.0001,
        },
    )
    assert out["rows"][0][0] < 5  # escapes fast -> small count


def test_two_row_tile_shape():
    out = execute(
        "fractal",
        {"width": 8, "height": 10, "row_start": 3, "row_end": 5, "max_iter": 30},
    )
    assert out["row_start"] == 3
    assert out["row_end"] == 5
    assert out["max_iter"] == 30
    assert out["width"] == 8
    assert len(out["rows"]) == 2  # two rows in the band
    assert all(len(row) == 8 for row in out["rows"])  # width pixels each
    assert all(isinstance(px, int) for row in out["rows"] for px in row)


def test_yield_returns_partial_and_flag():
    out = execute(
        "fractal",
        {"width": 50, "height": 50, "row_start": 0, "row_end": 50, "max_iter": 200},
        should_yield=lambda: True,
    )
    assert out["yielded"] is True
    assert out["rows"] == []  # yielded before the first row
