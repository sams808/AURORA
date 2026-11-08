"""Zone spectra: from the channel sums of a zone (aurora.core.zones.zone_sums) to its traces. De-spiking, the I0
source, normalisation, the per-detector traces on the sum's scale, TEY, and the I0 scores of the good-I0 picker.

Ported function for function from the zone tool (`despike`, `normalise`, `zoneTraces`, `i0Score`, `goodI0Curve`;
docs/HANDOFF.md sections 3.2 and 5). Membership and channel sums stay in aurora.core.zones, so the geometry and the
trace maths live in different files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from aurora.core.constants import DESPIKE_K, DETS
from aurora.core.io.config import ScanSettings

if TYPE_CHECKING:                                   # zones imports this module; the annotation only needs the name
    from aurora.core.zones import ZoneSums

NORM_MODES = ("raw", "i0", "i0pre", "i0jump", "area", "max")


# ----------------------------------------------------------------------------- de-spiking
def run_median(y: np.ndarray, w: int = 5) -> np.ndarray:
    """Running median over a window of w points (edges shrink), ignoring NaN; NaN where the window is empty."""
    y = np.asarray(y, float); n = len(y); h = w // 2
    out = np.full(n, np.nan)
    for i in range(n):
        seg = y[max(0, i - h): i + h + 1]
        seg = seg[np.isfinite(seg)]
        if seg.size:
            out[i] = np.sort(seg)[seg.size // 2]       # the tool's "upper" median, kept for bit-equality
    return out


def despike(y: np.ndarray, k: float = DESPIKE_K) -> tuple[np.ndarray, list[int]]:
    """Loose-electron spikes: |y - running median 5| > k * 1.4826 * MAD, replaced by linear interpolation between the
    nearest non-spike finite neighbours. Returns (cleaned copy, spike indices)."""
    y = np.asarray(y, float); n = len(y)
    med = run_median(y, 5)
    res = y - med
    ar = np.abs(res[np.isfinite(res)])
    mad = float(np.sort(ar)[ar.size // 2]) if ar.size else 0.0
    thr = k * 1.4826 * mad
    out = y.copy()
    if not thr > 0:
        return out, []
    spikes = [int(i) for i in np.where(np.isfinite(res) & (np.abs(res) > thr))[0]]
    sp = set(spikes)
    for i in spikes:
        a, b = i - 1, i + 1
        while a >= 0 and (a in sp or not np.isfinite(y[a])):
            a -= 1
        while b < n and (b in sp or not np.isfinite(y[b])):
            b += 1
        if a >= 0 and b < n:
            out[i] = y[a] + (y[b] - y[a]) * (i - a) / (b - a)
        elif a >= 0:
            out[i] = y[a]
        elif b < n:
            out[i] = y[b]
    return out, spikes


# ----------------------------------------------------------------------------- normalisation
@dataclass
class Normalised:
    y: np.ndarray
    pre: tuple[float, float]
    post: tuple[float, float]
    scale: float = 1.0


def normalise(E: np.ndarray, y: np.ndarray, mode: str, i0: np.ndarray | None, pre=None, post=None) -> Normalised:
    """raw | i0 (y / I0) | i0pre (minus a line fitted on `pre`) | i0jump (i0pre / mean over `post`, edge jump = 1) |
    area (y/I0 / integral) | max (y/I0 / maximum). Default pre = first 15 % of the range, post = last 20 %."""
    E = np.asarray(E, float); y = np.asarray(y, float); n = len(E)
    out = y.copy(); scale = 1.0
    if mode != "raw":
        if i0 is None:
            out[:] = np.nan
        else:
            i0 = np.asarray(i0, float)
            with np.errstate(all="ignore"):
                out = np.where(i0 > 0, y / i0, np.nan)
    r0, r1 = E[0], E[-1]
    PRE = tuple(pre) if pre else (r0, r0 + 0.15 * (r1 - r0))
    POST = tuple(post) if post else (r1 - 0.2 * (r1 - r0), r1)
    if mode in ("i0pre", "i0jump"):
        sel = (E >= PRE[0]) & (E <= PRE[1]) & np.isfinite(out)
        m = int(sel.sum())
        if m >= 2:
            ex, oy = E[sel], out[sel]
            sx, sy, sxx, sxy = ex.sum(), oy.sum(), (ex * ex).sum(), (ex * oy).sum()
            den = m * sxx - sx * sx
            b = (m * sxy - sx * sy) / (den if den else 1.0)
            a = (sy - b * sx) / m
            out = out - (a + b * E)
        elif m == 1:
            out = out - out[sel][0]
        if mode == "i0jump":
            sel = (E >= POST[0]) & (E <= POST[1]) & np.isfinite(out)
            if sel.any() and out[sel].sum() != 0:
                scale = float(out[sel].mean())
                out = out / scale
    elif mode == "area":
        fin = np.isfinite(out)
        A = 0.0
        for i in range(1, n):
            if fin[i] and fin[i - 1]:
                A += 0.5 * (out[i] + out[i - 1]) * (E[i] - E[i - 1])
        if A:
            scale = float(A); out = out / A
    elif mode == "max":
        fin = np.isfinite(out)
        if fin.any() and np.nanmax(out) > 0:
            scale = float(np.nanmax(out)); out = out / scale
    return Normalised(y=out, pre=(float(PRE[0]), float(PRE[1])), post=(float(POST[0]), float(POST[1])), scale=scale)


# ----------------------------------------------------------------------------- good I0
def interp(E: np.ndarray, y: np.ndarray, e: float) -> float:
    """Linear interpolation inside [E0, E-1], NaN outside (the tool's `interp`)."""
    if e < E[0] or e > E[-1]:
        return float("nan")
    i = 0
    while i < len(E) - 2 and E[i + 1] < e:
        i += 1
    t = (e - E[i]) / ((E[i + 1] - E[i]) or 1.0)
    return float(y[i] + (y[i + 1] - y[i]) * t)


def good_i0_curve(E: np.ndarray, good_curves: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray | None:
    """Mean of the good scans' per-pixel-mean I0 curves at the energies E (curves whose range does not overlap are
    left out); None when no curve applies."""
    E = np.asarray(E, float)
    curves = [(np.asarray(Ei, float), np.asarray(yi, float)) for Ei, yi in good_curves
              if not (Ei[0] > E[-1] or Ei[-1] < E[0])]
    if not curves:
        return None
    out = np.full(len(E), np.nan)
    for i, e in enumerate(E):
        vals = [interp(Ei, yi, e) for Ei, yi in curves]
        vals = [v for v in vals if np.isfinite(v)]
        if vals:
            out[i] = float(np.mean(vals))
    return out


@dataclass
class I0Score:
    level: float
    max_step: float
    rough: float
    nonpos: int
    n: int

    def looks_good(self, pool_median: float) -> bool:
        return self.nonpos == 0 and self.level >= 0.5 * pool_median and self.max_step < 0.15 and self.rough < 0.03


def i0_score(i0_mean: np.ndarray) -> I0Score | None:
    """Level = median; max_step = max |ΔI0| / level between consecutive energies; rough = RMS of (I0 - runmed7) / level;
    nonpos = points <= 0."""
    m = np.asarray(i0_mean, float)
    y = m[np.isfinite(m)]
    if not y.size:
        return None
    med = float(np.sort(y)[y.size // 2])
    max_step = 0.0
    if med > 0:
        d = np.abs(np.diff(m)) / med
        d = d[np.isfinite(d)]
        max_step = float(d.max()) if d.size else 0.0
    rm = run_median(m, 7)
    ok = np.isfinite(m) & np.isfinite(rm)
    rough = float(np.sqrt(np.mean(((m[ok] - rm[ok]) / (med or 1.0)) ** 2))) if ok.any() else float("nan")
    return I0Score(level=med, max_step=max_step, rough=rough, nonpos=int((y <= 0).sum()), n=int(y.size))


# ----------------------------------------------------------------------------- zone traces
@dataclass
class ZoneTraces:
    E: np.ndarray
    n_pix: int
    line: str
    det: list[np.ndarray | None]            # per SDD normalised traces (None when the line has no per-SDD data)
    det_raw: list[np.ndarray | None]        # per SDD de-spiked raw counts
    sum: np.ndarray | None                  # sum of the ticked SDDs, normalised
    sum_raw: np.ndarray | None
    tey: np.ndarray
    tey_raw: np.ndarray
    i0: np.ndarray                          # zone I0 (de-spiked when asked)
    i0_used: np.ndarray                     # what the traces were divided by (own or good-I0 x n_pix)
    i0_raw: np.ndarray
    spikes: dict = field(default_factory=dict)
    pre: tuple[float, float] | None = None
    post: tuple[float, float] | None = None
    sum_scale: float = 1.0
    settings: ScanSettings | None = None
    err: dict[str, np.ndarray] = field(default_factory=dict)       # trace name -> 1-sigma uncertainty, when computed
    notes: list[str] = field(default_factory=list)                 # what was done to the data, for Check and the report
    masked: dict[str, np.ndarray] = field(default_factory=dict)    # trace name -> bool mask of the energies left out


def build_traces(sums: "ZoneSums", settings: ScanSettings, good_curve: np.ndarray | None = None) -> ZoneTraces | None:
    """The zone tool's `zoneTraces` on precomputed zone sums: de-spike, I0 source, normalisation of the sum of the
    ticked SDDs, per-SDD traces on the sum's scale / number of ticked SDDs, TEY. None for a zone without pixels."""
    if sums.n_pix == 0:
        return None
    E = sums.E; nE = len(E)
    i0_raw, tey_raw = sums.i0, sums.tey
    line = settings.line or sums.main
    sp: dict = {}
    i0 = i0_raw
    if settings.despike:
        i0, sp["i0"] = despike(i0_raw, settings.k)
    i0n = i0
    if settings.i0_source == "good" and good_curve is not None:
        i0n = np.asarray(good_curve, float) * sums.n_pix
        sp["i0good"] = True
    det_arr = sums.det.get(line)                    # (nE, 4) or None
    det_raw: list = [None] * len(DETS)
    det_norm: list = [None] * len(DETS)
    sum_raw = np.zeros(nE); any_det = False; n_ticked = 0
    if det_arr is not None:
        for i in range(len(DETS)):
            y = det_arr[:, i].astype(float)
            if settings.despike:
                y, sp[f"sdd{i + 1}"] = despike(y, settings.k)
            else:
                sp[f"sdd{i + 1}"] = []
            det_raw[i] = y
            if settings.sdd[i]:
                sum_raw = sum_raw + y; any_det = True; n_ticked += 1
    out = ZoneTraces(E=E, n_pix=int(sums.n_pix), line=line, det=det_norm, det_raw=det_raw, sum=None, sum_raw=None,
                     tey=np.full(nE, np.nan), tey_raw=tey_raw, i0=i0, i0_used=i0n, i0_raw=i0_raw, spikes=sp, settings=settings)
    if any_det:
        nr = normalise(E, sum_raw, settings.fluo_norm, i0n, settings.pre, settings.post)
        out.sum, out.sum_raw, out.pre, out.post, out.sum_scale = nr.y, sum_raw, nr.pre, nr.post, nr.scale
    for i, y in enumerate(det_raw):
        if y is None:
            continue
        mode = "raw" if settings.fluo_norm == "raw" else ("i0" if settings.fluo_norm == "i0" else "i0pre")
        nr = normalise(E, y, mode, i0n, settings.pre, settings.post)
        sc = (out.sum_scale / n_ticked) if (out.sum_scale and n_ticked) else 1.0
        det_norm[i] = nr.y / sc
    ty = tey_raw
    if settings.despike:
        ty, sp["tey"] = despike(tey_raw, settings.k)
    out.tey = normalise(E, ty, settings.tey_norm, i0n, settings.pre, settings.post).y
    return out
