"""Spectra library for the Compare perspective: readers for the CSV layouts in use (AURORA zone CSVs, sgm_report
`_xanes.csv`, John Bussey's `_full_reduced.csv` / `_absorption.csv`, generic column CSVs), the three normalisations of
John's Compare tab (larch-style two-line, legacy constant pre/post, TEY pre-edge line), the E0 choices, shifts,
stacking, peaks, and the library files (config JSON with relative and absolute paths, the sidecar of arrays).
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

log = logging.getLogger("aurora.compare")

NORMS = ("larch", "legacy", "tey_line", "none")
DEFAULT_PRE = (-20.0, -5.0)      # eV from E0, larch-style pre-edge line
DEFAULT_POST = (15.0, 35.0)      # eV from E0, larch-style post-edge line
LEGACY_PRE_FRACTION = 0.15       # John Bussey's legacy defaults: the first 15 % of the scan ...
LEGACY_POST_FRACTION = 0.20      # ... and the last 20 %

# E0 choices of a row: 'deriv', 'half', 'table', 'fixed', or 'row:<label [signal]>' (the E0 of another row)
E0_MODES = ("deriv", "half", "table", "fixed")
E0_MODE_TEXT = {"deriv": "maximum of the smoothed derivative", "half": "half of the edge step",
                "table": "tabulated edge (xraydb)", "fixed": "fixed value"}
# DEFAULT, to be confirmed on beamtime data: Savitzky-Golay width of the derivative used for E0. On a 1 eV wide
# synthetic edge sampled every 0.2 eV with 2 % noise it keeps the E0 scatter below 0.5 eV (the unsmoothed derivative
# scatters by 0.7 eV, and by 4.4 eV when the data ends may win); tests/test_wp5_e0.py re-runs that probe.
E0_SMOOTH_EV = 1.5
# DEFAULT, to be confirmed: half-width of the window around the tabulated edge in which E0 is searched. It equals the
# start of the default pre-edge window (|DEFAULT_PRE[0]|), so the pre-edge line still lies below the tabulated edge.
E0_SEARCH_EV = 20.0

LIBRARY_KIND = "aurora-compare-library"
LIBRARY_SCHEMA = 1
_CONFIG_KEYS = ("rows", "sources", "sources_rel", "stored", "kind", "schema", "app", "default_shift")


@dataclass
class Spectrum:
    label: str
    signal: str
    E: np.ndarray
    y: np.ndarray
    source: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.label} [{self.signal}]"


@dataclass
class ZoneRef:
    """Where a zone trace sent from Reduce came from (it has no file of its own)."""
    holder: str
    zone: int
    sid: str
    signal: str

    def to_json(self) -> dict:
        return {"holder": self.holder, "zone": self.zone, "sid": self.sid, "signal": self.signal}

    @classmethod
    def from_json(cls, d: dict) -> "ZoneRef":
        return cls(holder=str(d.get("holder", "")), zone=int(d.get("zone", 0)), sid=str(d.get("sid", "")),
                   signal=str(d.get("signal", "")))


_ZONE_SOURCE = re.compile(r"^zone (\d+) of (.+)$")


def parse_zone_source(source: str) -> tuple[int, str] | None:
    """'zone 3 of 2025-03-14_092758_x_stack' (the source Reduce gives a zone trace) -> (3, sid); None otherwise."""
    m = _ZONE_SOURCE.match(str(source or ""))
    return (int(m.group(1)), m.group(2)) if m else None


def _pair(v, default):
    if v is None:
        return default
    try:
        a, b = v
        return (float(a), float(b))
    except (TypeError, ValueError):
        return default


@dataclass
class TraceRequest:
    """One row of the Compare table (John's PLOT_ORDER request).

    John Bussey's keys 'color' and 'mcr' are read as aliases of 'colour' and 'fit' and written as well. When norm is
    'legacy', the windows are absolute energies in eV (legacy_pre / legacy_post, None = automatic: the first 15 % and
    the last 20 % of the scan); otherwise pre / post are eV from E0."""
    label: str
    signal: str
    shift: float = 0.0
    colour: str = ""
    norm: str = "larch"
    pre: tuple[float, float] = DEFAULT_PRE
    post: tuple[float, float] = DEFAULT_POST
    flatten: bool = True
    spacing_after: float | None = None
    scale: float = 1.0
    show: bool = True
    fit: bool = False
    diagnostics: bool = False
    e0_mode: str = "deriv"
    e0: float | None = None                     # the value of a 'fixed' E0, eV
    e0_smooth: float = E0_SMOOTH_EV             # derivative smoothing width, eV
    legacy_pre: tuple[float, float] | None = None
    legacy_post: tuple[float, float] | None = None
    extra: dict = field(default_factory=dict)   # keys this version does not know, written back unchanged

    @property
    def key(self) -> str:
        return f"{self.label} [{self.signal}]"

    def to_json(self) -> dict:
        d = {"label": self.label, "signal": self.signal, "shift": self.shift, "colour": self.colour, "color": self.colour,
             "norm": self.norm, "pre": list(self.pre), "post": list(self.post), "flatten": self.flatten,
             "spacing_after": self.spacing_after, "scale": self.scale, "show": self.show, "fit": self.fit,
             "mcr": self.fit, "diagnostics": self.diagnostics, "e0_mode": self.e0_mode, "e0": self.e0,
             "e0_smooth": self.e0_smooth,
             "legacy_pre": None if self.legacy_pre is None else list(self.legacy_pre),
             "legacy_post": None if self.legacy_post is None else list(self.legacy_post)}
        return {**self.extra, **d}

    @classmethod
    def from_json(cls, d: dict) -> "TraceRequest":
        known = {"label", "signal", "shift", "colour", "color", "norm", "pre", "post", "flatten", "spacing_after",
                 "scale", "show", "fit", "mcr", "diagnostics", "e0_mode", "e0", "e0_smooth", "legacy_pre",
                 "legacy_post"}
        colour = d.get("colour") if d.get("colour") not in (None, "") else d.get("color", "")
        fit = d["fit"] if "fit" in d else d.get("mcr", False)
        sp = d.get("spacing_after")
        e0 = d.get("e0")
        lp, lq = d.get("legacy_pre"), d.get("legacy_post")
        mode = str(d.get("e0_mode") or "deriv")
        if mode not in E0_MODES and not mode.startswith("row:"):
            log.info("row %s: unknown E0 mode %r read as 'deriv'", d.get("label"), mode)
            mode = "deriv"
        return cls(label=d["label"], signal=d.get("signal", ""), shift=float(d.get("shift", 0) or 0),
                   colour=str(colour or ""), norm=d.get("norm", "larch") if d.get("norm", "larch") in NORMS else "larch",
                   pre=_pair(d.get("pre"), DEFAULT_PRE), post=_pair(d.get("post"), DEFAULT_POST),
                   flatten=bool(d.get("flatten", True)), spacing_after=None if sp is None else float(sp),
                   scale=float(d.get("scale", 1) or 1), show=bool(d.get("show", True)), fit=bool(fit),
                   diagnostics=bool(d.get("diagnostics", False)), e0_mode=mode,
                   e0=None if e0 is None else float(e0), e0_smooth=float(d.get("e0_smooth", E0_SMOOTH_EV) or E0_SMOOTH_EV),
                   legacy_pre=None if lp is None else _pair(lp, None), legacy_post=None if lq is None else _pair(lq, None),
                   extra={k: v for k, v in d.items() if k not in known})


# ----------------------------------------------------------------------------- readers
def read_csv_table(path: Path) -> tuple[list[str], list[str], np.ndarray]:
    """(# header lines, column names, data) of a comma-separated table with '#' comments; non-numeric cells -> NaN."""
    path = Path(path)
    header, cols, rows = [], None, []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                header.append(s)
                continue
            parts = [p.strip() for p in re.split(r"[,\t;]", s)]
            if cols is None:
                try:
                    [float(p) for p in parts]
                    cols = [f"col{i}" for i in range(len(parts))]       # no header line: numeric from the start
                except ValueError:
                    cols = parts
                    continue
            vals = []
            for p in parts:
                try:
                    vals.append(float(p))
                except ValueError:
                    vals.append(np.nan)
            if len(vals) == len(cols):
                rows.append(vals)
    data = np.array(rows, float) if rows else np.zeros((0, len(cols or [])))
    return header, cols or [], data


def _minmax(y: np.ndarray) -> np.ndarray:
    fin = np.isfinite(y)
    if not fin.any():
        return y
    lo, hi = np.nanmin(y), np.nanmax(y)
    return (y - lo) / (hi - lo) if hi > lo else y - lo


def layout_of(path: Path) -> str:
    """'john' (John Bussey's _full_reduced / _absorption), 'xanes' (sgm_report), 'zone' (AURORA zone CSV) or 'other',
    from the file name and, for zone CSVs, the header."""
    name = Path(path).name
    if name.endswith(("_full_reduced.csv", "_absorption.csv")):
        return "john"
    if name.endswith("_xanes.csv"):
        return "xanes"
    return "other"


def load_spectra(path: Path, label: str | None = None) -> list[Spectrum]:
    """Every usable signal of one CSV as Spectrum objects.

    AURORA zone CSV: fluo_sum_norm, fluo_sum_raw, TEY_norm, TEY_raw, sdd1..4_norm.
    sgm_report _xanes.csv: PFY_<line>_over_I0, TEY_over_I0, RAW_<line>.
    John's _full_reduced.csv / _absorption.csv: 'sdd-scaled' (sum of the four min-max scaled detectors) / I0, 'sdd_total' / I0,
    TEY / I0 (division at compare time, as John's notebook does).
    Generic: first column = energy, every other numeric column = a signal.
    """
    path = Path(path)
    header, cols, data = read_csv_table(path)
    if data.size == 0 or len(cols) < 2:
        raise ValueError(f"{path}: no numeric table found")
    lab = label or _label_from(path, header)
    meta = {"header": header, "columns": cols}
    out: list[Spectrum] = []
    c = {name: data[:, i] for i, name in enumerate(cols)}
    e_col = next((n for n in cols if n.lower() in ("energy_ev", "energy", "e", "energy (ev)", "incident_energy", "col0")), cols[0])
    E = c[e_col]
    if "fluo_sum_norm" in c:                                             # AURORA zone CSV
        meta["layout"] = "zone"
        for n in ("fluo_sum_norm", "fluo_sum_raw", "TEY_norm", "TEY_raw") + tuple(f"sdd{k}_norm" for k in range(1, 5)):
            if n in c:
                out.append(Spectrum(lab, n, E, c[n], str(path), meta))
    elif any(n.startswith("PFY_") and n.endswith("_over_I0") for n in cols):   # sgm_report zone CSV
        meta["layout"] = "xanes"
        for n in cols:
            if (n.startswith("PFY_") and n.endswith("_over_I0") and not re.search(r"_SDD\d_over_I0$", n)) or n == "TEY_over_I0":
                out.append(Spectrum(lab, n, E, c[n], str(path), meta))
    elif "sdd_total" in c and "I0" in c:                                   # John's reduced CSVs
        meta["layout"] = "john"
        i0 = c["I0"]
        with np.errstate(all="ignore"):
            if all(f"sdd{k}" in c for k in range(1, 5)):
                scaled = sum(_minmax(c[f"sdd{k}"]) for k in range(1, 5))
                out.append(Spectrum(lab, "sdd-scaled", E, np.where(i0 > 0, scaled / i0, np.nan), str(path), meta))
            out.append(Spectrum(lab, "sdd_total", E, np.where(i0 > 0, c["sdd_total"] / i0, np.nan), str(path), meta))
            if "TEY" in c:
                out.append(Spectrum(lab, "TEY", E, np.where(i0 > 0, c["TEY"] / i0, np.nan), str(path), meta))
    else:
        meta["layout"] = "other"
        for n in cols:
            if n != e_col and np.isfinite(c[n]).any():
                out.append(Spectrum(lab, n, E, c[n], str(path), meta))
    for s in out:
        order = np.argsort(s.E, kind="stable")
        s.E, s.y = s.E[order], s.y[order]
    log.info("%s: %d signals (%s), %d points", path.name, len(out), ", ".join(s.signal for s in out)[:80], len(E))
    return out


def _label_from(path: Path, header: list[str]) -> str:
    for h in header:
        m = re.match(r"#\s*(?:sample_label|zone):\s*(.+?)(?:\s*\(|$)", h)
        if m:
            return m.group(1).strip()
    name = path.stem
    for suf in ("_full_reduced", "_absorption", "_xanes"):
        if name.endswith(suf):
            name = name[: -len(suf)]
    return name


def find_reduced_csvs(folder: Path) -> list[Path]:
    """John's rule: *_full_reduced.csv of a folder (else *_absorption.csv), looking one level down too; AURORA zone
    CSVs and sgm_report *_xanes.csv are taken as well."""
    folder = Path(folder)
    pats = ("*_full_reduced.csv", "*_absorption.csv", "*_xanes.csv")
    found: list[Path] = []
    for depth in ("", "*/", "*/*/"):
        for pat in pats:
            found += sorted(folder.glob(depth + pat))
        if found:
            break
    full = {p.name[:-len("_full_reduced.csv")] for p in found if p.name.endswith("_full_reduced.csv")}
    return [p for p in found if not (p.name.endswith("_absorption.csv") and p.name[:-len("_absorption.csv")] in full)]


# ----------------------------------------------------------------------------- E0
@dataclass
class NormResult:
    y: np.ndarray
    e0: float
    pre_line: np.ndarray | None = None
    post_line: np.ndarray | None = None
    step: float = float("nan")
    note: str = ""
    e0_mode: str = "deriv"
    e0_note: str = ""                            # a calm note about the E0 found (far from the tabulated edge, ...)
    pre_window: tuple[float, float] | None = None    # absolute eV of the windows used
    post_window: tuple[float, float] | None = None
    err: np.ndarray | None = None                # the uncertainty of y, when the spectrum carries one


def _finite_sorted(E, y):
    E = np.asarray(E, float); y = np.asarray(y, float)
    fin = np.isfinite(E) & np.isfinite(y)
    e, v = E[fin], y[fin]
    order = np.argsort(e, kind="stable")
    return e[order], v[order]


def smoothed_derivative(E: np.ndarray, y: np.ndarray, width_ev: float = E0_SMOOTH_EV) -> tuple[np.ndarray, np.ndarray]:
    """(uniform grid, dy/dE) with a Savitzky-Golay filter (order 2) about `width_ev` wide. The spectrum is put on a
    uniform grid at its median step first, so uneven energy regions are handled. Empty arrays when < 5 points."""
    from scipy.signal import savgol_filter
    e, v = _finite_sorted(E, y)
    if e.size < 5:
        return np.array([]), np.array([])
    steps = np.diff(e)
    steps = steps[steps > 0]
    if steps.size == 0:
        return np.array([]), np.array([])
    h = float(np.median(steps))
    grid = np.arange(e[0], e[-1] + h / 2, h)
    if grid.size < 5:
        return np.array([]), np.array([])
    vi = np.interp(grid, e, v)
    n = int(round(max(float(width_ev), 0.0) / h))
    n = max(3, n + (1 - n % 2))                             # odd, at least 3
    n = min(n, grid.size - (1 - grid.size % 2))             # odd and no longer than the grid
    order = 2 if n >= 5 else min(2, n - 1)
    d = savgol_filter(vi, n, order, deriv=1, delta=h, mode="interp")
    return grid, d


def derivative_e0(E: np.ndarray, y: np.ndarray, width_ev: float | None = E0_SMOOTH_EV,
                  window: tuple[float, float] | None = None) -> float:
    """E0 = the largest local maximum of the derivative, after Savitzky-Golay smoothing `width_ev` wide (0 or None:
    the plain gradient), inside `window` (eV) when it holds enough points; refined by a parabola through the maximum.

    Points within one smoothing width of the data ends and of the window ends are not candidates: a start-up
    transient of the first points, or a derivative still rising where the window stops, is not an edge (larch's
    find_e0 skips the ends too). Without a local maximum inside, the largest value inside is taken."""
    if not width_ev:
        e, v = _finite_sorted(E, y)
        if e.size < 5:
            return float("nan")
        d = np.gradient(v, e)
        grid = e
    else:
        grid, d = smoothed_derivative(E, y, width_ev)
        if grid.size == 0:
            return float("nan")
    sel = np.ones(grid.size, bool)
    lo, hi = grid[0], grid[-1]
    if window is not None:
        w = (grid >= window[0]) & (grid <= window[1])
        if w.sum() >= 3:
            sel = w
            lo, hi = max(lo, window[0]), min(hi, window[1])
    margin = float(width_ev or 0.0)
    inner = sel & (grid > lo + margin) & (grid < hi - margin)
    inner[[0, -1]] = False                                  # an end point is never a local maximum
    peaks, _ = find_peaks(d)
    cand = np.intersect1d(peaks, np.flatnonzero(inner))
    idx = cand if cand.size else (np.flatnonzero(inner) if inner.any() else np.flatnonzero(sel))
    k = int(idx[int(np.argmax(d[idx]))])
    e0 = float(grid[k])
    if 0 < k < grid.size - 1 and sel[k - 1] and sel[k + 1]:
        a, b, c = d[k - 1], d[k], d[k + 1]
        den = a - 2 * b + c
        if den < 0:                                         # a real maximum: vertex of the parabola
            off = 0.5 * (a - c) / den
            if abs(off) <= 1:
                e0 = float(grid[k] + off * (grid[k + 1] - grid[k]))
    return e0


def _search_window(edge_ev: float | None) -> tuple[float, float] | None:
    return None if edge_ev is None or not np.isfinite(edge_ev) else (edge_ev - E0_SEARCH_EV, edge_ev + E0_SEARCH_EV)


def half_step_e0(E: np.ndarray, y: np.ndarray, req: "TraceRequest", start: float,
                 edge_ev: float | None = None) -> tuple[float, str]:
    """E0 = the first crossing of 0.5 of the normalised spectrum, iterated twice (the windows move with E0)."""
    E = np.asarray(E, float); y = np.asarray(y, float)
    e0 = float(start)
    lo = (edge_ev if edge_ev is not None and np.isfinite(edge_ev) else e0) - E0_SEARCH_EV
    for _ in range(2):
        r = _normalise(E, y, req, e0, "larch" if req.norm == "none" else req.norm)
        yn = r.y
        fin = np.isfinite(yn) & (E >= lo)
        idx = np.flatnonzero(fin)
        hit = None
        for a, b in zip(idx[:-1], idx[1:]):
            if yn[a] < 0.5 <= yn[b]:
                hit = (a, b)
                break
        if hit is None:
            return e0, "no crossing of the half step found: E0 from the derivative"
        a, b = hit
        e0 = float(E[a] + (0.5 - yn[a]) * (E[b] - E[a]) / (yn[b] - yn[a]))
    return e0, ""


def resolve_e0(E: np.ndarray, y: np.ndarray, req: "TraceRequest", edge_ev: float | None = None,
               e0_ref: float | None = None) -> tuple[float, str]:
    """(E0 in eV, note) of a row on its shifted energies, per its e0_mode:
    'deriv' smoothed derivative maximum inside a window around the tabulated edge; 'half' half step, iterated twice;
    'table' the tabulated edge; 'fixed' req.e0; 'row:<label>' e0_ref, the E0 of the named row."""
    mode = req.e0_mode or "deriv"
    deriv = lambda: derivative_e0(E, y, req.e0_smooth, _search_window(edge_ev))     # noqa: E731
    if mode == "table":
        if edge_ev is not None and np.isfinite(edge_ev):
            return float(edge_ev), ""
        return deriv(), "no tabulated edge is known for this row: E0 from the derivative"
    if mode == "fixed":
        if req.e0 is not None and np.isfinite(req.e0):
            return float(req.e0), ""
        return deriv(), "no fixed E0 value: E0 from the derivative"
    if mode.startswith("row:"):
        if e0_ref is not None and np.isfinite(e0_ref):
            return float(e0_ref), ""
        return deriv(), f"row {mode[4:]} has no E0: E0 from the derivative"
    if mode == "half":
        d = deriv()
        if not np.isfinite(d):
            return d, "no finite E0"
        e0, note = half_step_e0(E, y, req, d, edge_ev)
        return (e0, note) if not note else (d, note)
    return deriv(), ""


# ----------------------------------------------------------------------------- normalisation
def negative_step_note(step: float) -> str:
    """The calm note of a negative edge step: the trace keeps the orientation of the data (larch's pre_edge divides
    by |step| too), so a signal that falls across the edge is never shown as a rising absorption edge."""
    return (f"edge step negative ({step:.3g}): the signal falls across the edge; kept as measured, divided by |step|"
            if np.isfinite(step) and step < 0 else "")


def _linefit(E, y, lo, hi):
    sel = (E >= lo) & (E <= hi) & np.isfinite(y)
    if sel.sum() >= 2:
        b, a = np.polyfit(E[sel], y[sel], 1)
        return a + b * E, int(sel.sum())
    if sel.sum() == 1:
        return np.full(len(E), float(y[sel][0])), 1
    return None, 0


def normalise_larch(E: np.ndarray, y: np.ndarray, pre=DEFAULT_PRE, post=DEFAULT_POST, flatten: bool = True,
                    e0: float | None = None) -> NormResult:
    """John's larch-style normalisation: E0 from the derivative maximum (unless given), a line through [E0+pre] and
    one through [E0+post], edge step = post(E0) - pre(E0), (y - pre) / |step|, and flattening above E0 (larch's
    pre_edge: flat = norm - (post - pre) / |step| + its value at E0). A negative step is kept as measured, with a
    note."""
    E = np.asarray(E, float); y = np.asarray(y, float)
    e0 = derivative_e0(E, y) if e0 is None else float(e0)
    if not np.isfinite(e0):
        return NormResult(np.full(len(E), np.nan), e0, note="no finite E0")
    pw, qw = (e0 + pre[0], e0 + pre[1]), (e0 + post[0], e0 + post[1])
    pre_line, n1 = _linefit(E, y, *pw)
    post_line, n2 = _linefit(E, y, *qw)
    if pre_line is None or post_line is None:
        return NormResult(np.full(len(E), np.nan), e0, note=f"pre ({n1} pts) or post ({n2} pts) window empty: adjust the ranges",
                          pre_window=pw, post_window=qw)
    i0 = int(np.argmin(np.abs(E - e0)))
    step = float(post_line[i0] - pre_line[i0])
    if not step or not np.isfinite(step):
        return NormResult(np.full(len(E), np.nan), e0, pre_line, post_line, step, note="edge step is zero",
                          pre_window=pw, post_window=qw)
    out = (y - pre_line) / abs(step)
    if flatten:
        flat = (post_line - pre_line) / abs(step)
        out = np.where(E >= e0, out - flat + flat[i0], out)
    return NormResult(out, e0, pre_line, post_line, step, note=negative_step_note(step), pre_window=pw, post_window=qw)


def legacy_auto_windows(E: np.ndarray) -> tuple[tuple[float, float], tuple[float, float]]:
    """John's automatic legacy windows: the first 15 % and the last 20 % of the scan (absolute eV)."""
    E = np.asarray(E, float)
    fin = E[np.isfinite(E)]
    r0, r1 = (float(fin.min()), float(fin.max())) if fin.size else (0.0, 0.0)
    return (r0, r0 + LEGACY_PRE_FRACTION * (r1 - r0)), (r1 - LEGACY_POST_FRACTION * (r1 - r0), r1)


def normalise_legacy(E: np.ndarray, y: np.ndarray, pre_range: tuple[float, float] | None = None,
                     post_range: tuple[float, float] | None = None, e0: float | None = None) -> NormResult:
    """John's legacy normalisation: subtract the mean over an absolute pre-edge range, divide by the step (mean over
    an absolute post-edge range minus the pre-edge mean). Defaults: the first 15 % and the last 20 % of the scan."""
    E = np.asarray(E, float); y = np.asarray(y, float)
    auto_pre, auto_post = legacy_auto_windows(E)
    pre_range = tuple(pre_range) if pre_range else auto_pre
    post_range = tuple(post_range) if post_range else auto_post
    a = (E >= pre_range[0]) & (E <= pre_range[1]) & np.isfinite(y)
    b = (E >= post_range[0]) & (E <= post_range[1]) & np.isfinite(y)
    e0 = derivative_e0(E, y) if e0 is None else float(e0)
    if not a.any() or not b.any():
        return NormResult(np.full(len(E), np.nan), e0, note="pre or post range empty", pre_window=pre_range,
                          post_window=post_range)
    base = float(y[a].mean()); top = float(y[b].mean())
    step = top - base
    if not step:
        return NormResult(np.full(len(E), np.nan), e0, note="edge step is zero", pre_window=pre_range, post_window=post_range)
    return NormResult((y - base) / abs(step), e0, np.full(len(E), base), np.full(len(E), top), step,
                      note=negative_step_note(step), pre_window=pre_range, post_window=post_range)


def normalise_tey_line(E: np.ndarray, y: np.ndarray, pre=DEFAULT_PRE, post=DEFAULT_POST,
                       e0: float | None = None) -> NormResult:
    """TEY pre-edge line: remove a line fitted on [E0+pre], scale to the mean over [E0+post] (no flattening)."""
    E = np.asarray(E, float); y = np.asarray(y, float)
    e0 = derivative_e0(E, y) if e0 is None else float(e0)
    if not np.isfinite(e0):
        return NormResult(np.full(len(E), np.nan), e0, note="no finite E0")
    pw, qw = (e0 + pre[0], e0 + pre[1]), (e0 + post[0], e0 + post[1])
    pre_line, n1 = _linefit(E, y, *pw)
    if pre_line is None:
        return NormResult(np.full(len(E), np.nan), e0, note="pre window empty", pre_window=pw, post_window=qw)
    out = y - pre_line
    sel = (E >= qw[0]) & (E <= qw[1]) & np.isfinite(out)
    step = float(out[sel].mean()) if sel.any() else float("nan")
    if step and np.isfinite(step):
        out = out / abs(step)
    return NormResult(out, e0, pre_line, None, step, note=negative_step_note(step), pre_window=pw, post_window=qw)


def _normalise(E, y, req: "TraceRequest", e0: float, norm: str) -> NormResult:
    if norm == "larch":
        return normalise_larch(E, y, req.pre, req.post, req.flatten, e0=e0)
    if norm == "legacy":
        return normalise_legacy(E, y, req.legacy_pre, req.legacy_post, e0=e0)
    if norm == "tey_line":
        return normalise_tey_line(E, y, req.pre, req.post, e0=e0)
    return NormResult(np.asarray(y, float).copy(), e0)


def process(spec: Spectrum, req: TraceRequest, edge_ev: float | None = None, e0_ref: float | None = None) -> NormResult:
    """Shift, choose E0 (req.e0_mode), normalise per the request. req.scale is not applied: it multiplies the drawn
    trace only (John Bussey's 'Scale (plot only)'), so Fit, Difference and the CSV export read the normalised values.

    edge_ev: the tabulated edge of the row (search window of 'deriv', value of 'table'); e0_ref: the E0 of the row a
    'row:<label>' mode points at. A calm e0_note says when E0 lies further from the tabulated edge than the width of
    the pre-edge window, since the E0-relative windows move with it."""
    E = spec.E + req.shift
    e0, note = resolve_e0(E, spec.y, req, edge_ev, e0_ref)
    r = _normalise(E, spec.y, req, e0, req.norm)
    r.e0_mode = req.e0_mode
    width = abs(req.pre[1] - req.pre[0])
    if req.norm != "none" and edge_ev is not None and np.isfinite(edge_ev) and np.isfinite(r.e0) \
            and abs(r.e0 - edge_ev) > width:
        r.e0_note = (f"E0 {r.e0:.1f} eV lies {abs(r.e0 - edge_ev):.1f} eV from the tabulated edge ({edge_ev:.1f} eV), "
                     f"more than the pre-edge window width ({width:g} eV): check the windows")
    if note:
        r.e0_note = note + (f"; {r.e0_note}" if r.e0_note else "")
    err = spec.meta.get("err") if isinstance(spec.meta, dict) else None
    if err is not None and np.shape(err) == np.shape(spec.y):
        div = abs(r.step) if req.norm != "none" and np.isfinite(r.step) and r.step else 1.0
        r.err = np.asarray(err, float) / div
    return r


def stack_offsets(reqs: list[TraceRequest], default_spacing: float) -> list[float]:
    """Vertical offset of each shown trace: cumulative spacing_after (John's notebook)."""
    out, off = [], 0.0
    for r in reqs:
        out.append(off)
        off += default_spacing if r.spacing_after is None else float(r.spacing_after)
    return out


def derivative(E: np.ndarray, y: np.ndarray, smooth_points: int = 1) -> np.ndarray:
    """First derivative dy/dE on the (possibly uneven) energy grid, after an optional running mean of `smooth_points`."""
    E = np.asarray(E, float); y = np.asarray(y, float)
    if smooth_points > 1:
        k = np.ones(int(smooth_points)) / int(smooth_points)
        fin = np.isfinite(y)
        ys = y.copy(); ys[~fin] = 0.0
        num = np.convolve(ys, k, mode="same"); den = np.convolve(fin.astype(float), k, mode="same")
        with np.errstate(all="ignore"):
            y = np.where(den > 0, num / den, np.nan)
    out = np.full(len(E), np.nan)
    fin = np.isfinite(y)
    if fin.sum() >= 3:
        out[fin] = np.gradient(y[fin], E[fin])
    return out


def difference(a: Spectrum, b: Spectrum, ya: np.ndarray, yb: np.ndarray, shift_a: float = 0.0, shift_b: float = 0.0) -> Spectrum:
    """a - b on a's (shifted) energy grid, b interpolated; NaN outside the overlap."""
    Ea = a.E + shift_a; Eb = b.E + shift_b
    fin = np.isfinite(yb) & np.isfinite(Eb)
    yb_i = np.interp(Ea, Eb[fin], yb[fin], left=np.nan, right=np.nan) if fin.sum() >= 2 else np.full(len(Ea), np.nan)
    return Spectrum(f"{a.label} − {b.label}", "difference", Ea, ya - yb_i, source=f"difference of {a.key} and {b.key}")


def local_peaks(E: np.ndarray, y: np.ndarray, min_prominence: float = 0.05, min_distance_points: int = 5) -> list[tuple[float, float]]:
    fin = np.isfinite(y)
    if fin.sum() < 3:
        return []
    idx, _ = find_peaks(y[fin], prominence=min_prominence, distance=max(1, int(min_distance_points)))
    e, v = E[fin], y[fin]
    return [(float(e[i]), float(v[i])) for i in idx]


def wide_columns(traces: list[tuple[TraceRequest, np.ndarray, np.ndarray]], quantity: str = "") -> list[str]:
    """The column names of export_wide_csv: <label>-energy (eV), <label>-<quantity><signal> per trace (quantity
    'd/dE ' for the first derivative, empty for the values themselves)."""
    heads = []
    for req, _E, _y in traces:
        heads += [f"{req.label}-energy (eV)", f"{req.label}-{quantity}{req.signal}"]
    return heads


def export_wide_csv(path: Path, traces: list[tuple[TraceRequest, np.ndarray, np.ndarray]], quantity: str = "") -> None:
    """John's wide layout: <label>-energy (eV), <label>-<quantity><signal> per trace, rows padded with blanks.
    Written in one step, and refused in raw/ or a protected folder before anything is written."""
    from aurora.core.io.textio import open_atomic
    n = max((len(E) for _, E, _ in traces), default=0)
    heads, columns = wide_columns(traces, quantity), []
    for _req, E, y in traces:
        columns += [E, y]
    with open_atomic(path) as f:
        f.write(",".join(heads) + "\n")
        for i in range(n):
            f.write(",".join(("" if i >= len(c) or not np.isfinite(c[i]) else f"{c[i]:.6g}") for c in columns) + "\n")


# ----------------------------------------------------------------------------- library files
def sidecar_path(path: Path) -> Path:
    """<name>_spectra.npz next to <name>.json: the arrays of the spectra that have no file of their own."""
    path = Path(path)
    return path.with_name(path.stem + "_spectra.npz")


def _rel(src: str, base: Path | None) -> str | None:
    if base is None or not src:
        return None
    try:
        return Path(os.path.relpath(Path(src).resolve(), Path(base).resolve())).as_posix()
    except (ValueError, OSError):           # another drive on Windows
        return None


def resolve_source(src: str, rel: str | None, base: Path | None) -> Path | None:
    """The file of a source: the absolute path when it exists, else the relative one against `base`; None if neither."""
    if src:
        p = Path(src)
        if p.is_file():
            return p
    if rel and base is not None:
        q = Path(base) / rel
        if q.is_file():
            return q
    if src and base is not None and not Path(src).is_absolute():
        q = Path(base) / src
        if q.is_file():
            return q
    return None


def _write_bytes_atomic(path: Path, data: bytes) -> Path:
    from aurora.core.io.safety import assert_writable
    from aurora.core.io.textio import REPLACE_RETRIES, REPLACE_WAIT_S
    path = Path(path)
    assert_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    last: Exception | None = None
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(tmp, path)
            return path
        except PermissionError as err:
            last = err
            time.sleep(REPLACE_WAIT_S * (attempt + 1))
    try:
        tmp.unlink()
    except OSError:
        pass
    raise last if last is not None else OSError(f"{path}: could not be replaced")


def spectra_arrays(spectra: list[Spectrum]) -> tuple[list[dict], bytes]:
    """(index for the JSON, npz bytes) of spectra kept as arrays: s<i>_E, s<i>_y and s<i>_err when present."""
    index, arrays = [], {}
    for i, s in enumerate(spectra):
        sid = f"s{i}"
        arrays[f"{sid}_E"] = np.asarray(s.E, float)
        arrays[f"{sid}_y"] = np.asarray(s.y, float)
        meta = {k: v for k, v in (s.meta or {}).items() if k not in ("header", "columns", "err")}
        if isinstance(s.meta, dict) and s.meta.get("err") is not None:
            arrays[f"{sid}_err"] = np.asarray(s.meta["err"], float)
        meta = {k: (v.to_json() if isinstance(v, ZoneRef) else v) for k, v in meta.items()}
        index.append({"id": sid, "label": s.label, "signal": s.signal, "source": s.source,
                      "meta": json.loads(json.dumps(meta, default=str))})
    buf = io.BytesIO()
    np.savez_compressed(buf, **arrays) if arrays else np.savez_compressed(buf, empty=np.zeros(0))
    return index, buf.getvalue()


def read_spectra_arrays(npz_path: Path, index: list[dict]) -> list[Spectrum]:
    """The spectra of a sidecar npz, from the JSON index; entries whose arrays are missing are skipped (logged)."""
    out = []
    try:
        with np.load(Path(npz_path), allow_pickle=False) as z:
            for d in index or []:
                sid = d.get("id", "")
                if f"{sid}_E" not in z.files or f"{sid}_y" not in z.files:
                    log.warning("%s: arrays of %s missing; dropped", npz_path, d.get("label"))
                    continue
                meta = dict(d.get("meta") or {})
                if "zone" in meta and isinstance(meta["zone"], dict):
                    meta["zone"] = ZoneRef.from_json(meta["zone"])
                if f"{sid}_err" in z.files:
                    meta["err"] = np.array(z[f"{sid}_err"], float)
                out.append(Spectrum(str(d.get("label", "")), str(d.get("signal", "")), np.array(z[f"{sid}_E"], float),
                                    np.array(z[f"{sid}_y"], float), str(d.get("source", "")), meta))
    except (OSError, ValueError) as err:
        log.warning("%s unreadable (%s); the spectra kept as arrays are not restored", npz_path, err)
    return out


def config_dict(reqs: list[TraceRequest], sources: dict[str, str], base: Path | None, extra: dict | None = None,
                stored: list[dict] | None = None, default_shift: float = 0.0) -> dict:
    """The library as JSON: rows, sources (absolute) and sources_rel (relative to `base`), the index of the spectra
    kept as arrays in the sidecar, the default shift for new rows, plus the caller's extra keys."""
    from aurora import __version__
    rel = {lab: r for lab, r in ((lab, _rel(src, base)) for lab, src in sources.items()) if r}
    absolute = {}
    for lab, src in sources.items():
        try:
            absolute[lab] = str(Path(src).resolve()) if src and Path(src).exists() else src
        except OSError:
            absolute[lab] = src
    return {"kind": LIBRARY_KIND, "schema": LIBRARY_SCHEMA, "app": f"AURORA {__version__}",
            "rows": [r.to_json() for r in reqs], "sources": absolute, "sources_rel": rel, "stored": stored or [],
            "default_shift": float(default_shift), **(extra or {})}


def save_config(path: Path, reqs: list[TraceRequest], sources: dict[str, str], extra: dict | None = None,
                spectra: list[Spectrum] | None = None, default_shift: float = 0.0) -> list[Path]:
    """Write the config JSON (atomic) and, when some spectra have no file, the sidecar <name>_spectra.npz.
    Returns the files written."""
    from aurora.core.io.textio import write_text_atomic
    path = Path(path)
    stored, data = spectra_arrays(spectra) if spectra else ([], b"")
    d = config_dict(reqs, sources, path.parent, extra, stored, default_shift)
    out = []
    if stored:
        out.append(_write_bytes_atomic(sidecar_path(path), data))
    write_text_atomic(path, json.dumps(d, indent=1, ensure_ascii=False))
    out.insert(0, path)
    return out


def read_config(path: Path) -> dict:
    """The parsed config JSON (ValueError when it is not a library or config file); a leading BOM is accepted."""
    d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(d, dict) or not isinstance(d.get("rows", []), list):
        raise ValueError(f"{Path(path).name} is not a Compare config")
    return d


def load_config(path: Path) -> tuple[list[TraceRequest], dict[str, str], dict]:
    """(rows, sources as written, extra keys) of a config JSON; John Bussey's keys are read as well."""
    d = read_config(path)
    reqs = [TraceRequest.from_json(r) for r in d.get("rows", [])]
    extra = {k: v for k, v in d.items() if k not in _CONFIG_KEYS}
    return reqs, dict(d.get("sources", {})), extra
