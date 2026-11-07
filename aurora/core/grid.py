"""Snake / fly-scan points -> regular image. Ported from sgm_report.py v2.5 `to_grid`.

Rows are plateaus of y: the hexapod moves y gradually at the row turnarounds and those transition points are dropped
(ix / iy = -1). Columns are the within-row x step. Do not estimate the pitch from unique coordinate values (jitter).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Grid:
    img: np.ndarray         # (ny, nx), NaN where nothing was measured
    xs: np.ndarray          # column centres (mm)
    ys: np.ndarray          # row centres (mm)
    ix: np.ndarray          # column index per point, -1 for dropped turnaround points
    iy: np.ndarray          # row index per point, -1 for dropped points

    @property
    def dx(self) -> float:
        return float(self.xs[1] - self.xs[0]) if len(self.xs) > 1 else float("nan")

    @property
    def dy(self) -> float:
        return float(np.median(np.diff(self.ys))) if len(self.ys) > 1 else float("nan")

    @property
    def n_dropped(self) -> int:
        return int((self.ix < 0).sum())


def to_grid(x: np.ndarray, y: np.ndarray, vals: np.ndarray, tol: float = 1e-3) -> Grid:
    x = np.asarray(x, float); y = np.asarray(y, float); vals = np.asarray(vals, float)
    yr = np.round(y, 3)
    vals_u, counts = np.unique(yr, return_counts=True)
    plateaus = vals_u[counts >= max(3, 0.25 * counts.max())]
    if len(plateaus) < 2:
        plateaus = vals_u
    dy = float(np.median(np.diff(plateaus))) if len(plateaus) > 1 else max(float(y.max() - y.min()), 1.0)
    iy = np.abs(y[:, None] - plateaus[None, :]).argmin(1)
    on_row = np.abs(y - plateaus[iy]) <= 0.3 * dy
    rowchg = np.r_[False, np.diff(iy) != 0] | ~on_row
    dx_all = np.abs(np.diff(x))[~rowchg[1:]]
    dx_all = dx_all[dx_all > tol]
    dx = float(np.median(dx_all)) if dx_all.size else max(float(x.max() - x.min()), 1.0)
    nx = max(1, min(int(round((x.max() - x.min()) / dx)) + 1, 4000))
    ny = len(plateaus)
    ix = np.clip(np.round((x - x.min()) / dx).astype(int), 0, nx - 1)
    cnt = np.zeros((ny, nx)); acc = np.zeros((ny, nx))
    np.add.at(cnt, (iy[on_row], ix[on_row]), 1)
    np.add.at(acc, (iy[on_row], ix[on_row]), np.nan_to_num(vals[on_row]))
    img = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
    xs = x.min() + dx * np.arange(nx)
    ys = plateaus.astype(float)
    return Grid(img=img, xs=xs, ys=ys, ix=np.where(on_row, ix, -1), iy=np.where(on_row, iy, -1))
