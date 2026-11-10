"""Zone spectra: from the channel sums of a zone (aurora.core.zones.zone_sums) to its traces. De-spiking, the I0
source, normalisation, the per-detector traces on the sum's scale, TEY, and the I0 scores of the good-I0 picker.

Ported function for function from the zone tool (`despike`, `normalise`, `zoneTraces`, `i0Score`, `goodI0Curve`;
docs/HANDOFF.md sections 3.2 and 5). Membership and channel sums stay in aurora.core.zones, so the geometry and the
trace maths live in different files.

Beyond the zone tool (ScanSettings fields added by AURORA):
- despike_mode 'classic' is the zone tool's rule, bit for bit; 'coherent' looks for spikes in each SDD against the
  median of the other three (a white line is common to all four and leaves no residual), keeps a sum or TEY outlier
  that the SDDs show as well and an I0 outlier that the signals divided by it do not show (despike_coherent,
  coherent_despike, i0_despike); its noise scale never goes below the point-to-point noise of the trace
  (noise_sigma), where the classic MAD collapses on smooth, high-S/N traces (a whole pad);
- energies whose I0 is below I0_LOW_FRAC of its median are left out of every / I0 trace (docs/HANDOFF.md 5.2);
- signal 'ipfy' divides I0 by the counts of the (non-resonant) line, aurora.core.yields.ipfy; with the resonant line
  of the edge (F Kα on an F K stack) that is 1 / PFY, and a note says so;
- ZoneTraces.notes says what was done in words and in eV (spike energies, masked energies, the signal).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from aurora.core.constants import COHERENT_MIN_SDDS, DESPIKE_K, DETS, EDGE_SPIKE_STEPS, I0_LOW_FRAC
from aurora.core.io.config import ScanSettings
from aurora.core.lines import line_element
from aurora.core.yields import describe as describe_ipfy
from aurora.core.yields import ipfy

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


DESPIKE_MODES = ("classic", "coherent")


def _detect(y: np.ndarray, k: float, floor: float = 0.0) -> list[int]:
    """The classic detection alone: indices where |y - running median 5| > k * max(1.4826 * MAD, floor)."""
    y = np.asarray(y, float)
    res = y - run_median(y, 5)
    ar = np.abs(res[np.isfinite(res)])
    mad = float(np.sort(ar)[ar.size // 2]) if ar.size else 0.0
    thr = k * max(1.4826 * mad, floor)
    if not thr > 0:
        return []
    return [int(i) for i in np.where(np.isfinite(res) & (np.abs(res) > thr))[0]]


def fill_spikes(y: np.ndarray, spikes) -> np.ndarray:
    """A copy of y with the points `spikes` replaced by linear interpolation between the nearest non-spike finite
    neighbours (the classic rule's interpolation)."""
    y = np.asarray(y, float); n = len(y)
    out = y.copy()
    sp = set(int(i) for i in spikes)
    for i in sorted(sp):
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
    return out


def noise_sigma(y: np.ndarray) -> float:
    """Point-to-point noise of a trace from its second differences (robust: 1.4826 x their MAD / sqrt(6), as
    aurora.core.fit.estimate_noise): white noise of standard deviation s gives y[i-1] - 2 y[i] + y[i+1] a standard
    deviation s * sqrt(6), a smooth curve adds little. 0 with fewer than 5 finite points."""
    y = np.asarray(y, float)
    y = y[np.isfinite(y)]
    if y.size < 5:
        return 0.0
    d2 = y[:-2] - 2 * y[1:-1] + y[2:]
    return float(1.4826 * np.median(np.abs(d2 - np.median(d2))) / np.sqrt(6.0))


def hampel(y: np.ndarray, k: float) -> list[int]:
    """Indices where |y - running median 5| > k * 1.4826 * S, S being the median absolute deviation of the same
    5-point window from its median (the Hampel identifier), never below the scan-wide MAD of the classic rule nor
    the point-to-point noise of the trace (noise_sigma). The window scale follows the noise where it changes along
    the scan (few counts below the edge, many above), where one scan-wide MAD would call the noisier part spikes. The
    noise floor matters on smooth, high-S/N traces (a whole pad): there the point is the median of its own window at
    most energies, the residual MAD collapses towards 0, and ordinary noise of 1-5 sigma would pass for spikes."""
    y = np.asarray(y, float); n = len(y)
    med = run_median(y, 5)
    res = y - med
    ar = np.abs(res[np.isfinite(res)])
    g = float(np.sort(ar)[ar.size // 2]) if ar.size else 0.0
    g = max(g, noise_sigma(y) / 1.4826)                 # as a MAD: the threshold is then at least k x the noise
    out = []
    for i in range(n):
        if not np.isfinite(res[i]):
            continue
        seg = y[max(0, i - 2): i + 3]
        seg = seg[np.isfinite(seg)]
        loc = float(np.sort(np.abs(seg - med[i]))[seg.size // 2]) if seg.size else 0.0
        s = max(loc, g)
        if s > 0 and abs(res[i]) > k * 1.4826 * s:
            out.append(i)
    return out


def despike_hampel(y: np.ndarray, k: float) -> tuple[np.ndarray, list[int]]:
    """One trace on its own in the coherent mode (no detector to compare with): hampel's spikes, interpolated as in
    the classic rule. Returns (cleaned copy, spike indices)."""
    sp = hampel(y, k)
    return (fill_spikes(y, sp) if sp else np.asarray(y, float).copy()), sp


def coherent_count(dets, i: int, sign: float) -> int:
    """How many SDD traces show a local maximum (sign > 0) or minimum (sign < 0) at index i: strictly above (below)
    every finite neighbour; the first and last energies have one neighbour."""
    n_ok = 0
    for d in dets:
        if d is None:
            continue
        d = np.asarray(d, float)
        if not 0 <= i < len(d) or not np.isfinite(d[i]):
            continue
        nbs = [j for j in (i - 1, i + 1) if 0 <= j < len(d) and np.isfinite(d[j])]
        if nbs and all(sign * (d[i] - d[j]) > 0 for j in nbs):
            n_ok += 1
    return n_ok


def despike_coherent(dets, k: float = DESPIKE_K) -> tuple[list, list[list[int]]]:
    """De-spike the four SDD traces together. For SDD j the spikes are looked for on the ratio y_j / m_j, m_j being the
    median of the other detectors, each scaled to SDD j's level (its median over the energies), against its running
    median (hampel: the 5-point window's own MAD, never below the scan-wide one). A white line, common to all four
    detectors, leaves the ratio flat; a loose-electron spike on one detector does not. A candidate that at least
    COHERENT_MIN_SDDS detectors in all show as the same local maximum (or minimum), each other one with at least 1/k
    of this detector's relative excess or as an outlier of its own, is a real feature whose height differs between
    the detectors (geometry, self-absorption) and stays. Spikes are
    interpolated as in the classic rule. A detector with
    fewer than two usable references (dead or missing detectors) is de-spiked on its own (despike_hampel).
    Returns (cleaned traces, spike indices per detector); None entries stay None."""
    arrs = [None if d is None else np.asarray(d, float) for d in dets]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        levels = [float(np.nanmedian(a)) if a is not None and np.isfinite(a).any() else float("nan") for a in arrs]
    out: list = []
    spikes: list[list[int]] = []
    for j, a in enumerate(arrs):
        if a is None:
            out.append(None); spikes.append([])
            continue
        refs = [i for i, b in enumerate(arrs) if i != j and b is not None and levels[i] > 0]
        if len(refs) < 2 or not levels[j] > 0:
            y, sp = despike_hampel(a, k)
            out.append(y); spikes.append(sp)
            continue
        scaled = np.column_stack([arrs[i] * (levels[j] / levels[i]) for i in refs])
        with warnings.catch_warnings(), np.errstate(all="ignore"):
            warnings.simplefilter("ignore", RuntimeWarning)
            ref = np.nanmedian(scaled, axis=1)
            ratio = np.where(ref > 0, a / ref, np.nan)
        med = run_median(a, 5)
        res = a - med
        others = [arrs[i] for i in refs]
        omed = [run_median(b, 5) for b in others]
        oout = [set(hampel(b, k)) for b in others]

        def common(i: int) -> bool:
            """At least COHERENT_MIN_SDDS detectors in all (this one and the others) show the same local extremum here,
            each other one with either at least 1/k of this one's relative excess over the running median or an
            outlier of its own trace: a real feature (of another height), not a noise coincidence next to a spike."""
            if not (np.isfinite(res[i]) and med[i]):
                return False
            sign = 1.0 if res[i] > 0 else -1.0
            rel = abs(res[i] / med[i])
            n_ok = sum(1 for b, m, o in zip(others, omed, oout)
                       if coherent_count([b], i, sign) == 1
                       and (i in o or (m[i] and sign * (b[i] - m[i]) / abs(m[i]) >= rel / k)))
            return n_ok >= min(COHERENT_MIN_SDDS - 1, len(others))

        sp = [i for i in hampel(ratio, k) if not common(i)]
        out.append(fill_spikes(a, sp) if sp else a.copy())
        spikes.append(sp)
    return out, spikes


def coherent_despike(y: np.ndarray, k: float, dets) -> tuple[np.ndarray, list[int]]:
    """The sum or TEY in the coherent mode: an outlier (hampel) counts as a spike only when it is not a local maximum
    (a white line) or, for a dip, a local minimum, in at least COHERENT_MIN_SDDS of the SDD traces. Without enough
    SDD traces it is despike_hampel."""
    if dets is None or sum(d is not None for d in dets) < COHERENT_MIN_SDDS:
        return despike_hampel(y, k)
    y = np.asarray(y, float)
    res = y - run_median(y, 5)
    keep = [i for i in hampel(y, k) if coherent_count(dets, i, 1.0 if res[i] > 0 else -1.0) < COHERENT_MIN_SDDS]
    return (fill_spikes(y, keep) if keep else y.copy()), keep


def i0_despike(i0: np.ndarray, k: float, signals) -> tuple[np.ndarray, list[int]]:
    """I0 in the coherent mode. An outlier of I0 (the classic rule with the noise floor: I0 is smooth, without an edge
    whose slope the Hampel window would need) is a spike only when a signal divided by I0 (the fluorescence sum,
    TEY) is an outlier there too, the other way: a spike of I0 alone is, a beam dip that the signals follow is not and
    stays measured, since the division by I0 takes it out. Without a usable signal it is despike_hampel."""
    i0 = np.asarray(i0, float)
    ratios = []
    for sig in signals:
        if sig is None:
            continue
        with np.errstate(all="ignore"):
            r = np.where(i0 > 0, np.asarray(sig, float) / i0, np.nan)
        if np.isfinite(r).sum() >= 5 and np.nanmax(np.abs(r)) > 0:
            ratios.append(r)
    if not ratios:
        return despike_hampel(i0, k)
    cand = _detect(i0, k, noise_sigma(i0))
    if not cand:
        return i0.copy(), []
    res = i0 - run_median(i0, 5)
    shown = [(r - run_median(r, 5), set(_detect(r, k, noise_sigma(r)))) for r in ratios]
    keep = [i for i in cand if any(i in out and rres[i] * res[i] < 0 for rres, out in shown)]
    return (fill_spikes(i0, keep) if keep else i0.copy()), keep


def i0_low_mask(i0, frac: float = I0_LOW_FRAC) -> np.ndarray:
    """Energies whose I0 is below `frac` x its median (finite values only): beam loss, never divided by
    (docs/HANDOFF.md 5.2, the sgm_report rule)."""
    i0 = np.asarray(i0, float)
    fin = np.isfinite(i0)
    if not fin.any():
        return np.zeros(i0.shape, bool)
    med = float(np.median(i0[fin]))
    if not med > 0:
        return np.zeros(i0.shape, bool)
    return fin & (i0 < frac * med)


# ----------------------------------------------------------------------------- normalisation
@dataclass
class Normalised:
    y: np.ndarray
    pre: tuple[float, float]
    post: tuple[float, float]
    scale: float = 1.0


def normalise(E: np.ndarray, y: np.ndarray, mode: str, i0: np.ndarray | None, pre=None, post=None,
              pre_divided: bool = False) -> Normalised:
    """raw | i0 (y / I0) | i0pre (minus a line fitted on `pre`) | i0jump (i0pre / mean over `post`, edge jump = 1) |
    area (y/I0 / integral) | max (y/I0 / maximum). Default pre = first 15 % of the range, post = last 20 %.
    pre_divided=True: y is already a ratio to I0 (IPFY), so it is never divided again; the modes apply to y itself."""
    E = np.asarray(E, float); y = np.asarray(y, float); n = len(E)
    out = y.copy(); scale = 1.0
    if mode != "raw" and not pre_divided:
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
    spikes: dict = field(default_factory=dict)      # trace -> spike indices ('sdd1'..'sdd4', 'sum', 'tey', 'i0')
    pre: tuple[float, float] | None = None
    post: tuple[float, float] | None = None
    sum_scale: float = 1.0
    settings: ScanSettings | None = None
    err: dict[str, np.ndarray] = field(default_factory=dict)       # trace name -> 1-sigma uncertainty, when computed
    notes: list[str] = field(default_factory=list)                 # what was done to the data, for Check and the report
    masked: dict[str, np.ndarray] = field(default_factory=dict)    # trace name -> bool mask of the energies left out
    signal: str = "pfy"                                            # 'pfy' | 'ipfy': what sum and det hold
    spikes_ev: dict[str, list[float]] = field(default_factory=dict)  # trace -> spike energies (eV)

    def trace_names(self) -> dict[str, str]:
        """Column names of the normalised traces: fluo_sum_norm / sdd1_norm for PFY, ipfy_sum_norm / ipfy_sdd1_norm
        for IPFY; TEY_norm either way."""
        pre = "ipfy_" if self.signal == "ipfy" else ""
        out = {"sum": f"{pre or 'fluo_'}sum_norm", "tey": "TEY_norm"}
        out.update({d: f"{pre}{d}_norm" for d in DETS})
        return out

    def bad_mask(self, include_i0: bool = True) -> np.ndarray:
        """Energies flagged bad: spikes of the ticked SDDs, the sum, TEY (when used) and I0, plus masked energies."""
        nE = len(self.E)
        bad = np.zeros(nE, bool)
        st = self.settings
        keys = [d for k, d in enumerate(DETS) if st is None or st.sdd[k]] + ["sum"]
        if st is None or st.tey:
            keys.append("tey")
        if include_i0:
            keys.append("i0")
        for key in keys:
            for i in self.spikes.get(key) or []:
                if isinstance(i, (int, np.integer)) and 0 <= int(i) < nE:
                    bad[int(i)] = True
        for m in self.masked.values():
            m = np.asarray(m, bool)
            if m.shape == bad.shape:
                bad |= m
        return bad


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def _ev_list(E: np.ndarray, idx) -> str:
    return ", ".join(f"{float(E[i]):.2f}" for i in idx)


def measured_edge(E: np.ndarray, y: np.ndarray | None, edge_ev: float | None) -> float | None:
    """The edge of a measured trace: the maximum of its smoothed derivative (aurora.core.compare.derivative_e0) within
    the E0 search window around the tabulated edge; the tabulated edge when the trace gives none."""
    if y is None or edge_ev is None or not np.isfinite(edge_ev) or np.isfinite(np.asarray(y, float)).sum() < 5:
        return edge_ev
    from aurora.core.compare import _search_window, derivative_e0
    e0 = derivative_e0(np.asarray(E, float), np.asarray(y, float), window=_search_window(edge_ev))
    return float(e0) if np.isfinite(e0) else edge_ev


def _spike_notes(out: "ZoneTraces", settings: ScanSettings, mode: str, edge_ev: float | None, edge_trace=None) -> None:
    """Spike energies in eV (ZoneTraces.spikes_ev and a sentence), and the calm note for spikes next to the edge: the
    edge of `edge_trace` (fluorescence or TEY / I0, measured_edge) when given, else the tabulated one."""
    E = out.E
    names = {**{d: d.upper() for d in DETS}, "sum": "sum", "tey": "TEY", "i0": "I0"}
    found = []
    for key in (*DETS, "sum", "tey", "i0"):
        idx = out.spikes.get(key)
        if isinstance(idx, list) and idx:
            out.spikes_ev[key] = [float(E[i]) for i in idx]
            found.append(f"{names[key]} at {_ev_list(E, idx)} eV")
    if not settings.despike:
        out.notes.append("de-spike off")
        return
    head = f"de-spike ({mode}, k = {settings.k:g})"
    out.notes.append(f"{head}: " + ("; ".join(found) + " interpolated" if found else "no spikes found"))
    if out.spikes_ev and edge_trace is not None:
        edge_ev = measured_edge(E, edge_trace, edge_ev)
    if edge_ev is not None and len(E) > 1 and np.isfinite(edge_ev):
        step = float(np.median(np.abs(np.diff(E)))) or 0.0
        near = sorted({round(e, 6) for evs in out.spikes_ev.values() for e in evs
                       if abs(e - edge_ev) <= EDGE_SPIKE_STEPS * step})
        if near:
            out.notes.append(f"{_plural(len(near), 'point')} near the edge {'was' if len(near) == 1 else 'were'} treated "
                             f"as {'a spike' if len(near) == 1 else 'spikes'}, check the preview")


def build_traces(sums: "ZoneSums", settings: ScanSettings, good_curve: np.ndarray | None = None) -> ZoneTraces | None:
    """The zone tool's `zoneTraces` on precomputed zone sums: de-spike, I0 source, normalisation of the sum of the
    ticked SDDs, per-SDD traces on the sum's scale / number of ticked SDDs, TEY. None for a zone without pixels.

    despike_mode 'classic' (and every file without 'dsm') is the zone tool's rule, unchanged; 'coherent' uses
    despike_coherent on the four SDDs and coherent_despike on the sum and TEY. Energies with I0 below I0_LOW_FRAC x its
    median are NaN in every / I0 trace. signal 'ipfy': the sum and each SDD become I0 / counts (yields.ipfy), then
    normalised without a second division by I0."""
    if sums.n_pix == 0:
        return None
    E = sums.E; nE = len(E)
    i0_raw, tey_raw = sums.i0, sums.tey
    line = settings.line or sums.main
    mode = getattr(settings, "despike_mode", "classic") or "classic"
    coherent = bool(settings.despike) and mode == "coherent"
    signal = getattr(settings, "signal", "pfy") or "pfy"
    is_ipfy = signal == "ipfy"
    sp: dict = {}
    i0 = i0_raw
    if settings.despike:
        if coherent:
            sp["i0"] = []                           # below, once the SDD traces are de-spiked
        else:
            i0, sp["i0"] = despike(i0_raw, settings.k)
    det_arr = sums.det.get(line)                    # (nE, 4) or None
    det_raw: list = [None] * len(DETS)
    det_norm: list = [None] * len(DETS)
    sum_raw = np.zeros(nE); any_det = False; n_ticked = 0
    if det_arr is not None:
        cleaned, cspikes = (despike_coherent([det_arr[:, i].astype(float) for i in range(len(DETS))], settings.k)
                            if coherent else (None, None))
        for i in range(len(DETS)):
            y = det_arr[:, i].astype(float)
            if settings.despike:
                if coherent:
                    y, sp[f"sdd{i + 1}"] = cleaned[i], cspikes[i]
                else:
                    y, sp[f"sdd{i + 1}"] = despike(y, settings.k)
            else:
                sp[f"sdd{i + 1}"] = []
            det_raw[i] = y
            if settings.sdd[i]:
                sum_raw = sum_raw + y; any_det = True; n_ticked += 1
        if coherent and any_det:
            sum_raw, sp["sum"] = coherent_despike(sum_raw, settings.k, det_raw)
    if coherent:
        # an I0 outlier that the fluorescence and TEY follow is the beam's own (the division takes it out)
        i0, sp["i0"] = i0_despike(i0_raw, settings.k, [sum_raw if any_det else None, tey_raw])
    i0n = i0
    use_good = settings.i0_source == "good" and good_curve is not None
    if use_good:
        i0n = np.asarray(good_curve, float) * sums.n_pix
        sp["i0good"] = True
    # I0 below I0_LOW_FRAC x its median (beam loss): left out of every / I0 trace. Judged on the measured I0 (before
    # de-spiking, which would interpolate a dip away), or on the good-I0 curve when that is what the traces use.
    low = i0_low_mask(i0n if use_good else i0_raw)
    i0d = i0n
    if low.any():
        i0d = np.array(i0n, float, copy=True)
        i0d[low] = np.nan
    out = ZoneTraces(E=E, n_pix=int(sums.n_pix), line=line, det=det_norm, det_raw=det_raw, sum=None, sum_raw=None,
                     tey=np.full(nE, np.nan), tey_raw=tey_raw, i0=i0, i0_used=i0n, i0_raw=i0_raw, spikes=sp, settings=settings,
                     signal="ipfy" if is_ipfy else "pfy")
    n_nonpos = 0
    if any_det:
        if is_ipfy:
            r = ipfy(sum_raw, i0d, getattr(settings, "ipfy_mode", "ratio"))
            n_nonpos = r.n_nonpos
            nr = normalise(E, r.y, settings.fluo_norm, None, settings.pre, settings.post, pre_divided=True)
        else:
            nr = normalise(E, sum_raw, settings.fluo_norm, i0d, settings.pre, settings.post)
        out.sum, out.sum_raw, out.pre, out.post, out.sum_scale = nr.y, sum_raw, nr.pre, nr.post, nr.scale
    for i, y in enumerate(det_raw):
        if y is None:
            continue
        mode_i = "raw" if settings.fluo_norm == "raw" else ("i0" if settings.fluo_norm == "i0" else "i0pre")
        if is_ipfy:
            # I0 / (n_ticked x S_k): equal detectors give the sum's trace, on the sum's scale
            r = ipfy(y * max(n_ticked, 1), i0d, getattr(settings, "ipfy_mode", "ratio"))
            nr = normalise(E, r.y, mode_i, None, settings.pre, settings.post, pre_divided=True)
            sc = out.sum_scale if out.sum_scale else 1.0
        else:
            nr = normalise(E, y, mode_i, i0d, settings.pre, settings.post)
            sc = (out.sum_scale / n_ticked) if (out.sum_scale and n_ticked) else 1.0
        det_norm[i] = nr.y / sc
    ty = tey_raw
    if settings.despike:
        if coherent:
            ty, sp["tey"] = coherent_despike(tey_raw, settings.k, det_raw if det_arr is not None else None)
        else:
            ty, sp["tey"] = despike(tey_raw, settings.k)
    out.tey = normalise(E, ty, settings.tey_norm, i0d, settings.pre, settings.post).y
    # notes, in words and in eV; the edge of the spike note is the measured one (fluorescence / I0, else TEY / I0)
    with np.errstate(all="ignore"):
        edge_trace = np.where(i0d > 0, (sum_raw if any_det else ty) / i0d, np.nan)
    _spike_notes(out, settings, mode if settings.despike else "off", getattr(sums, "edge_ev", None), edge_trace)
    if low.any():
        out.masked["i0_low"] = low
        idx = np.where(low)[0]
        out.notes.append(f"I0 below {I0_LOW_FRAC:.0%} of its median at {_plural(len(idx), 'energy', 'energies')} "
                         f"({_ev_list(E, idx)} eV): left out of the / I0 traces")
    if is_ipfy:
        out.notes.append(describe_ipfy(line, getattr(settings, "ipfy_mode", "ratio")))
        edge = getattr(sums, "edge", None)
        if edge and line_element(line) == edge[0]:
            out.notes.append(f"IPFY of the resonant line: {line} is the line of the {edge[0]} {edge[1]} edge, so I0 / "
                             f"{line} is 1 / PFY, not an inverse partial fluorescence yield; choose a line of another "
                             "element (O Kα across the F K edge)")
        if n_nonpos:
            out.notes.append(f"IPFY: {_plural(n_nonpos, 'energy', 'energies')} without {line} counts left out")
    return out
