"""A survey (one-energy plate map): per-pixel I0, TEY, total XRF and one ROI per fixed line and detector, and the
per-point XRF spectra (kept for the emission of a zone, John Bussey's "XRF from survey, only inside region").

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
    per_point: np.ndarray | None = None  # (n, 4, 256) float32 per-point spectra; None when not kept or released
    source: object = None               # the ScanInfo it was read from (reload_spectra)

    @property
    def n(self) -> int:
        return int(len(self.x))

    @property
    def channels(self) -> list[str]:
        return list(self.lines) + ["total", "TEY", "I0"]

    @property
    def has_spectra(self) -> bool:
        return self.per_point is not None

    @classmethod
    def load(cls, scan: ScanInfo, bins: RoiBins | None, keep_spectra: bool = True) -> "SurveyData":
        """Read a survey. keep_spectra: keep the per-point spectra (n x 4 x 256 float32, 25 MB for 6 000 points) for
        zone_spectrum; they are read in the same pass either way."""
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
                 spec=spec, lines=lines, source=scan)
        if keep_spectra:
            sv.per_point = _per_point(pd.sdd, n)
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

    # ------------------------------------------------------------------ per-point spectra
    def zone_spectrum(self, pix) -> np.ndarray:
        """(4, 256) XRF counts per SDD summed over the survey points `pix` (indices; repeats count once): a 0/1 mask
        times the per-point spectra, the same time for any zone size. Divide by the number of points for a mean per
        point. ValueError when the spectra were not kept or were released (reload_spectra)."""
        if self.per_point is None:
            raise ValueError(f"{self.name}: the per-point spectra are not in memory (reload_spectra)")
        n = self.per_point.shape[0]
        mask = np.zeros(n, np.float32)
        pix = np.asarray(pix, int)
        mask[pix[(pix >= 0) & (pix < n)]] = 1.0
        return (mask @ self.per_point.reshape(n, -1)).reshape(self.per_point.shape[1:]).astype(float)

    def release_spectra(self) -> None:
        """Drop the per-point spectra (memory); the maps stay."""
        self.per_point = None

    def reload_spectra(self) -> bool:
        """Read the per-point spectra again from the survey's folder; False when it cannot be read."""
        if self.per_point is not None:
            return True
        if self.source is None:
            return False
        try:
            pd = read_point_dir(self.source.path)
        except (OSError, ValueError) as err:
            log.warning("%s: per-point spectra not read again (%s)", self.name, err)
            return False
        if pd.n_pix < self.n:
            return False
        self.per_point = _per_point(pd.sdd, self.n)
        return True


def _per_point(sdd, n: int) -> np.ndarray:
    """(n, 4, 256) float32 from the four (n_raw, 256) SDD arrays."""
    return np.stack([np.asarray(a[:n], np.float32) for a in sdd], axis=1)
