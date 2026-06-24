"""NON-AI #1 -- distributed Mandelbrot fractal (host-side builder + assembler).

The fleet renders ONE Mandelbrot image by giving each machine a horizontal BAND
of rows (a ``fractal`` tile). The tiles come back as escape-count rows and are
stitched + colorized here into a single PIL image. The job builder is pure
stdlib; numpy/PIL are imported ONLY inside the assembler/save helpers (guarded),
so importing this module never requires them -- a clear error is raised only if
you actually call ``assemble_tiles``/``save_png`` without PIL installed.
"""

from __future__ import annotations

from typing import Any

from workloads.partition import even_ranges, weighted_ranges

# Default complex-plane window (classic full Mandelbrot view).
_X_MIN, _X_MAX = -2.5, 1.0
_Y_MIN, _Y_MAX = -1.25, 1.25


def build_fractal_jobs(
    n_tiles: int,
    width: int = 900,
    height: int = 600,
    max_iter: int = 120,
    weights: list[float] | None = None,
) -> list[dict]:
    """Build ``n_tiles`` ``fractal`` SubmitRequest-shaped jobs, one per row-band.

    Rows ``[0, height)`` are partitioned across tiles (evenly, or proportional to
    ``weights`` when given). ``units`` is the row count of the tile (server-metered
    credit). Empty bands are skipped, so ``sum(units) == height`` over the returned
    jobs and the bands cover the image with no gaps or overlaps.
    """
    if n_tiles <= 0:
        raise ValueError("n_tiles must be positive")
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if max_iter <= 0:
        raise ValueError("max_iter must be positive")

    if weights is None:
        bands = even_ranges(height, n_tiles)
    else:
        if len(weights) != n_tiles:
            raise ValueError("weights length must equal n_tiles")
        bands = weighted_ranges(height, weights)

    jobs: list[dict] = []
    for row_start, row_end in bands:
        if row_end <= row_start:
            continue  # zero-row band (height < n_tiles); skip so units stays honest
        jobs.append(
            {
                "kind": "fractal",
                "input": {
                    "width": width,
                    "height": height,
                    "row_start": row_start,
                    "row_end": row_end,
                    "max_iter": max_iter,
                    "x_min": _X_MIN,
                    "x_max": _X_MAX,
                    "y_min": _Y_MIN,
                    "y_max": _Y_MAX,
                },
                "units": row_end - row_start,
            }
        )
    return jobs


def _colorize(count: int, max_iter: int) -> tuple[int, int, int]:
    """Map an escape count to an RGB triple (in-set -> black, else a smooth ramp)."""
    if count >= max_iter:
        return (0, 0, 0)
    t = count / max_iter
    # A warm blue->cyan->white ramp that reads well on a projector.
    r = int(255 * (t**0.5))
    g = int(255 * (t**0.9))
    b = int(255 * (0.4 + 0.6 * t))
    return (min(r, 255), min(g, 255), min(b, 255))


def assemble_tiles(results: list[dict], width: int, height: int, max_iter: int) -> Any:
    """Stitch ``fractal`` tile results into ONE colorized ``PIL.Image`` (RGB).

    ``results`` is the list of executor outputs (each carries ``row_start`` and its
    ``rows`` of escape counts). Rows are placed at their absolute ``row_start`` so the
    image reassembles correctly regardless of tile arrival order. Raises a clear
    ``RuntimeError`` if PIL/numpy are unavailable.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - exercised only on a box without PIL
        raise RuntimeError(
            "assemble_tiles requires Pillow + numpy (host-side only); install them to render."
        ) from exc

    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    # Precompute the color ramp once per escape count (max_iter+1 entries).
    ramp = np.array([_colorize(c, max_iter) for c in range(max_iter + 1)], dtype=np.uint8)
    for tile in results:
        row_start = int(tile.get("row_start", 0))
        rows = tile.get("rows", [])
        for offset, row in enumerate(rows):
            y = row_start + offset
            if 0 <= y < height and row:
                idx = np.clip(np.asarray(row, dtype=np.int64), 0, max_iter)
                canvas[y, : len(row)] = ramp[idx]
    return Image.fromarray(canvas, mode="RGB")


def save_png(image: Any, path: str) -> str:
    """Save a PIL image to ``path`` as PNG and return ``path``. Clear error without PIL."""
    save = getattr(image, "save", None)
    if save is None:
        raise RuntimeError("save_png requires a PIL.Image (host-side only).")
    save(path, format="PNG")
    return path
