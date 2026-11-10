"""Fixtures of the zone-drawing tests. A fake beamtime from aurora.core.testdata (never the demo beamtime, whose
names and survey another line changes) plus one wider stack, so a brush can move several points at once; and the
window helpers of the WP3 tests (open_window, qdrag)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from aurora.core import testdata

WIDE = "093000_Wide_f_k_stack"          # a second complete stack of the holder, 12 x 4 points
WIDE_GRID = {"nx": 12, "ny": 4, "pitch": 0.25, "x0": 2.0, "y0": 5.0}


def zone_project(root: Path) -> Path:
    """testdata.make_fake_project(root) plus the WIDE stack (written after the first stack, same holder)."""
    root = testdata.make_fake_project(Path(root))
    x, y = testdata.snake_grid(**WIDE_GRID)
    d = root / "raw" / testdata.DAY / WIDE
    testdata._write_stack(d, WIDE[7:-6], testdata.ENERGIES, len(testdata.ENERGIES), x, y, np.random.default_rng(21),
                          created="Thu Nov  5 09:30:00 2026")
    return root


def pixel_xy(sd, i: int) -> tuple[float, float]:
    return float(sd.x[i]), float(sd.y[i])


def row_points(sd, row: int) -> np.ndarray:
    """Indices of the stack's pixels on one row (y plateau), left to right."""
    ys = np.unique(np.round(sd.y, 6))
    idx = np.where(np.isclose(sd.y, ys[row]))[0]
    return idx[np.argsort(sd.x[idx])]


def inner_box(sd, frac: float = 0.25):
    """A rectangle (x0, x1, y0, y1) inside the stack's footprint, `frac` of the size in from each side."""
    x0, x1, y0, y1 = sd.bbox
    dx, dy = (x1 - x0) * frac, (y1 - y0) * frac
    return x0 + dx, x1 - dx, y0 + dy, y1 - dy
