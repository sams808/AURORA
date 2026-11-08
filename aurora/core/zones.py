"""Zone geometry and channel sums: the zone tool's `gridPitch` and `members` (docs/HANDOFF.md section 3.2), the sums
over a zone's pixels, and `zone_traces`, which hands those sums to aurora.core.spectra.

De-spiking, normalisation and the trace arithmetic live in aurora.core.spectra; the names are re-exported here so
`from aurora.core.zones import despike, normalise, ZoneTraces, ...` keeps working.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np

from aurora.core.io.config import ScanSettings, Zone
from aurora.core.spectra import (NORM_MODES, I0Score, Normalised, ZoneTraces, build_traces, despike,  # noqa: F401
                                 good_i0_curve, i0_score, interp, normalise, run_median)

__all__ = ["NORM_MODES", "I0Score", "Normalised", "ZoneSums", "ZoneTraces", "build_traces", "despike", "export_ready",
           "good_i0_curve", "grid_pitch", "i0_score", "interp", "members", "normalise", "run_median", "zone_sums",
           "zone_traces"]


# ----------------------------------------------------------------------------- geometry
def grid_pitch(x: np.ndarray, y: np.ndarray | None) -> tuple[float, float]:
    """(pitch x, pitch y) of a snake / fly scan: rows are plateaus of y, columns the within-row x step."""
    x = np.asarray(x, float)
    n = len(x)
    if n == 0:
        return 0.1, 0.1
    py, on_row = 0.1, None
    if y is not None:
        y = np.asarray(y, float)
        yr = np.round(y, 3)
        vals, counts = np.unique(yr, return_counts=True)
        plateaus = vals[counts >= max(3, 0.25 * counts.max())]
        if len(plateaus) < 2:
            plateaus = vals
        d = np.diff(plateaus)
        py = float(np.median(d)) if d.size else max(1e-3, float(y.max() - y.min()))
        on_row = np.isin(yr, plateaus)
    dx = np.abs(np.diff(x))
    if on_row is not None:
        dx = dx[on_row[1:] & on_row[:-1]]
    dx = dx[dx > 1e-3]
    px = float(np.median(dx)) if dx.size else 0.1
    if y is None and not dx.size:
        px = max((x.max() - x.min()) / np.sqrt(n), 1e-3)
    return px, py


def members(zone: Zone, x: np.ndarray, y: np.ndarray, pitch: tuple[float, float]) -> np.ndarray:
    """Pixel indices of a stack inside the zone. A pixel counts when its square touches a rectangle (0.45 pitch
    tolerance) or sits in a painted square; erase rectangles and squares remove it again."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    hx, hy = pitch[0] * 0.45, pitch[1] * 0.45
    inside = np.zeros(len(x), bool)
    for r in zone.rects:
        inside |= (x >= r[0] - hx) & (x <= r[1] + hx) & (y >= r[2] - hy) & (y <= r[3] + hy)
    for s in zone.paint:
        inside |= (np.abs(x - s[0]) <= s[2] * 0.51) & (np.abs(y - s[1]) <= s[3] * 0.51)
    if inside.any():
        out = np.zeros(len(x), bool)
        for r in zone.erects:
            out |= (x >= r[0] - hx) & (x <= r[1] + hx) & (y >= r[2] - hy) & (y <= r[3] + hy)
        for s in zone.erase:
            out |= (np.abs(x - s[0]) <= s[2] * 0.51) & (np.abs(y - s[1]) <= s[3] * 0.51)
        inside &= ~out
    return np.where(inside)[0]


def export_ready(zone: Zone, pitches: dict[str, tuple[float, float]]) -> dict:
    """The zone as the HTML zone tool reads it (zones_all.json), for every stack pitch in `pitches` (sid -> pitch).

    Foundation stub: a copy of zone.to_json(). Polygon zones ('polys') get rasterised into paint squares at each pitch
    here, so the tool sees the same members; 'polys' itself is kept for AURORA."""
    return copy.deepcopy(zone.to_json())


# ----------------------------------------------------------------------------- channel sums
@dataclass
class ZoneSums:
    """Sums over a zone's pixels of one stack, the input of aurora.core.spectra.build_traces."""
    E: np.ndarray                               # (nE,) eV
    n_pix: int                                  # pixels in the zone
    i0: np.ndarray                              # (nE,) I0 summed over the pixels, NaN at skipped energies
    tey: np.ndarray                             # (nE,)
    det: dict[str, np.ndarray]                  # line label -> (nE, 4) counts per SDD
    main: str                                   # the stack's main line (used when the settings name none)
    edge_ev: float | None = None
    valid: np.ndarray | None = None             # (nE,) bool, energies with a usable map
    sid: str = ""
    windows: dict[str, list[int]] | None = None     # per-SDD channel windows [lo, hi) used, None = the fixed line bins
    notes: list[str] = field(default_factory=list)


def zone_sums(stack, pix, settings: ScanSettings | None = None) -> ZoneSums:
    """Channel sums over the zone pixels of a StackData: I0, TEY and every line of the stack per SDD. `settings`
    selects per-detector windows when ScanSettings.roi is used; the fixed line bins of the stack otherwise."""
    pix = np.asarray(pix, int)
    s = stack.sum_over(pix)
    return ZoneSums(E=stack.E, n_pix=int(s["n"]), i0=s["i0"], tey=s["tey"], det=s["det"], main=stack.main,
                    edge_ev=getattr(stack, "edge_ev", None), valid=getattr(stack, "valid", None),
                    sid=str(getattr(stack, "id", "")))


def zone_traces(stack, pix, settings: ScanSettings, good_curve: np.ndarray | None = None) -> ZoneTraces | None:
    """The zone tool's `zoneTraces`: sums over the zone pixels (zone_sums), then aurora.core.spectra.build_traces.
    `stack` is a StackData; None when the zone holds no pixel of it."""
    pix = np.asarray(pix, int)
    if pix.size == 0:
        return None
    return build_traces(zone_sums(stack, pix, settings), settings, good_curve)
