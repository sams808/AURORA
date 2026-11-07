"""A survey (one-energy plate map): per-pixel I0, TEY, total XRF and one ROI per fixed line and detector.

Port of build_zone_tool.read_survey and the zone tool's survey map options (channel, per SDD or sum, / I0, log,
invert, percentile clip).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from aurora.core.constants import DETS, I0_COL, TEY_COL
from aurora.core.grid import Grid, to_grid
from aurora.core.io.config import RoiBins
from aurora.core.io.h5 import find_h5, xy
from aurora.core.io.raw import read_point_dir
from aurora.core.model import ScanInfo
from aurora.core.zones import grid_pitch

log = logging.getLogger("aurora.survey")


@dataclass
class SurveyData:
    name: str
    holder: str
    energy: float
    x: np.ndarray
    y: np.ndarray
    i0: np.ndarray                      # (n,)
    tey: np.ndarray
    roi: np.ndarray                     # (n_lines, 4, n)
    total: np.ndarray                   # (4, n)
    spec: np.ndarray                    # (4, 256) summed spectra
    lines: list[str]
    pitch: tuple[float, float] = (0.2, 0.2)
    i0_usable: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return int(len(self.x))

    @property
    def channels(self) -> list[str]:
        return list(self.lines) + ["total", "TEY", "I0"]

    @classmethod
    def load(cls, scan: ScanInfo, bins: RoiBins | None) -> "SurveyData":
        h5 = scan.h5 or find_h5(scan.path)
        if h5 is None:
            raise FileNotFoundError(f"{scan.path}: no .h5 file")
        x, y = xy(h5)
        pd = read_point_dir(scan.path)
        n = min(len(x), pd.n_pix)
        if n < len(x):
            log.info("%s: %d coordinates but %d measured points; truncated", scan.name, len(x), pd.n_pix)
        x, y = x[:n], y[:n]
        lines = [l for l in (bins.labels if bins else []) if bins.has(l)]
        roi = np.zeros((len(lines), len(DETS), n), np.float32)
        for li, lab in enumerate(lines):
            b = bins.bounds(lab)
            for di, d in enumerate(DETS):
                lo, hi = b[d]
                roi[li, di] = pd.sdd[di][:n, lo:hi].sum(axis=1)
        total = np.stack([s[:n].sum(axis=1) for s in pd.sdd]).astype(np.float32)
        spec = np.stack([s[:n].sum(axis=0) for s in pd.sdd]).astype(np.float32)
        i0 = pd.mcc[:n, I0_COL].astype(float); tey = pd.mcc[:n, TEY_COL].astype(float)
        sv = cls(name=scan.name, holder=scan.holder, energy=scan.e_min, x=x, y=y, i0=i0, tey=tey, roi=roi, total=total,
                 spec=spec, lines=lines)
        sv.pitch = grid_pitch(x, y)
        # I0 usable for the map when > 95 % of the points are positive and the median is above 0.2 (zone tool rule)
        pos = float(np.mean(i0 > 0)) if n else 0.0
        med = float(np.nanmedian(i0)) if n else 0.0
        sv.i0_usable = pos > 0.95 and med > 0.2
        if not sv.i0_usable:
            sv.notes.append(f"survey I0 unusable (median {med:.3g}, {100 * (1 - pos):.0f}% of the points <= 0): maps are raw, not / I0")
        log.info("%s: %d points at %.1f eV, pitch %.3f x %.3f mm, %d lines", scan.name, n, sv.energy, sv.pitch[0], sv.pitch[1], len(lines))
        return sv

    def values(self, channel: str, det: int | None = None, per_i0: bool = True, log10: bool = False,
               invert: bool = False, clip: tuple[float, float] = (0.5, 99.5)) -> np.ndarray:
        """Per-pixel map values for the display: `channel` = a line label, 'total', 'TEY' or 'I0'; `det` = one SDD
        index or None for the sum; / I0 when usable; log10; inverted (samples darker than the plate); percentile clip."""
        if channel == "I0":
            v = self.i0.astype(float); per_i0 = False
        elif channel == "TEY":
            v = self.tey.astype(float)
        elif channel == "total":
            v = (self.total.sum(axis=0) if det is None else self.total[det]).astype(float)
        else:
            li = self.lines.index(channel)
            v = (self.roi[li].sum(axis=0) if det is None else self.roi[li, det]).astype(float)
        if per_i0 and self.i0_usable:
            with np.errstate(all="ignore"):
                v = np.where(self.i0 > 0, v / self.i0, np.nan)
        if log10:
            with np.errstate(all="ignore"):
                v = np.log10(np.maximum(v, 0) + 1.0) if channel != "I0" else v
        fin = np.isfinite(v)
        if fin.any() and clip is not None:
            lo, hi = np.nanpercentile(v[fin], clip)
            v = np.clip(v, lo, hi)
        if invert and fin.any():
            v = np.nanmax(v) - v
        return v

    def grid(self, vals: np.ndarray) -> Grid:
        return to_grid(self.x, self.y, vals)
