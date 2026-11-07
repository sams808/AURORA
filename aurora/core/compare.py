"""Spectra library for the Compare perspective: readers for the CSV layouts in use (AURORA zone CSVs, sgm_report
`_xanes.csv`, John Bussey's `_full_reduced.csv` / `_absorption.csv`, generic column CSVs), the three normalisations of
John's Compare tab (larch-style two-line, legacy constant pre/post, TEY pre-edge line), shifts, stacking and peaks.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

log = logging.getLogger("aurora.compare")

NORMS = ("larch", "legacy", "tey_line", "none")
DEFAULT_PRE = (-20.0, -5.0)      # eV from E0, larch-style pre-edge line
DEFAULT_POST = (15.0, 35.0)      # eV from E0, larch-style post-edge line


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
class TraceRequest:
    """One row of the Compare table (John's PLOT_ORDER request)."""
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

    def to_json(self) -> dict:
        return {"label": self.label, "signal": self.signal, "shift": self.shift, "colour": self.colour, "norm": self.norm,
                "pre": list(self.pre), "post": list(self.post), "flatten": self.flatten, "spacing_after": self.spacing_after,
                "scale": self.scale, "show": self.show, "fit": self.fit, "diagnostics": self.diagnostics}

    @classmethod
    def from_json(cls, d: dict) -> "TraceRequest":
        return cls(label=d["label"], signal=d.get("signal", ""), shift=float(d.get("shift", 0)), colour=d.get("colour", ""),
                   norm=d.get("norm", "larch"), pre=tuple(d.get("pre", DEFAULT_PRE)), post=tuple(d.get("post", DEFAULT_POST)),
                   flatten=bool(d.get("flatten", True)), spacing_after=d.get("spacing_after"), scale=float(d.get("scale", 1)),
                   show=bool(d.get("show", True)), fit=bool(d.get("fit", False)), diagnostics=bool(d.get("diagnostics", False)))


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
        for n in ("fluo_sum_norm", "fluo_sum_raw", "TEY_norm", "TEY_raw") + tuple(f"sdd{k}_norm" for k in range(1, 5)):
            if n in c:
                out.append(Spectrum(lab, n, E, c[n], str(path), meta))
    elif any(n.startswith("PFY_") and n.endswith("_over_I0") for n in cols):   # sgm_report zone CSV
        for n in cols:
            if (n.startswith("PFY_") and n.endswith("_over_I0") and not re.search(r"_SDD\d_over_I0$", n)) or n == "TEY_over_I0":
                out.append(Spectrum(lab, n, E, c[n], str(path), meta))
    elif "sdd_total" in c and "I0" in c:                                   # John's reduced CSVs
        i0 = c["I0"]
        with np.errstate(all="ignore"):
            if all(f"sdd{k}" in c for k in range(1, 5)):
                scaled = sum(_minmax(c[f"sdd{k}"]) for k in range(1, 5))
                out.append(Spectrum(lab, "sdd-scaled", E, np.where(i0 > 0, scaled / i0, np.nan), str(path), meta))
            out.append(Spectrum(lab, "sdd_total", E, np.where(i0 > 0, c["sdd_total"] / i0, np.nan), str(path), meta))
            if "TEY" in c:
                out.append(Spectrum(lab, "TEY", E, np.where(i0 > 0, c["TEY"] / i0, np.nan), str(path), meta))
    else:
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


# ----------------------------------------------------------------------------- normalisation
@dataclass
class NormResult:
    y: np.ndarray
    e0: float
    pre_line: np.ndarray | None = None
    post_line: np.ndarray | None = None
    step: float = float("nan")
    note: str = ""


def derivative_e0(E: np.ndarray, y: np.ndarray) -> float:
    fin = np.isfinite(y) & np.isfinite(E)
    if fin.sum() < 5:
        return float("nan")
    e, v = E[fin], y[fin]
    d = np.gradient(v, e)
    return float(e[int(np.argmax(d))])


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
    """John's larch-style normalisation: E0 from the derivative maximum, a line through [E0+pre] and one through
    [E0+post], edge step = post(E0) - pre(E0), (y - pre) / step, and flattening above E0."""
    E = np.asarray(E, float); y = np.asarray(y, float)
    e0 = derivative_e0(E, y) if e0 is None else float(e0)
    if not np.isfinite(e0):
        return NormResult(np.full(len(E), np.nan), e0, note="no finite E0")
    pre_line, n1 = _linefit(E, y, e0 + pre[0], e0 + pre[1])
    post_line, n2 = _linefit(E, y, e0 + post[0], e0 + post[1])
    if pre_line is None or post_line is None:
        return NormResult(np.full(len(E), np.nan), e0, note=f"pre ({n1} pts) or post ({n2} pts) window empty: adjust the ranges")
    i0 = int(np.argmin(np.abs(E - e0)))
    step = float(post_line[i0] - pre_line[i0])
    if not step or not np.isfinite(step):
        return NormResult(np.full(len(E), np.nan), e0, pre_line, post_line, step, note="edge step is zero")
    out = (y - pre_line) / step
    if flatten:
        flat = (post_line - pre_line) / step - 1.0
        out = np.where(E >= e0, out - flat, out)
    return NormResult(out, e0, pre_line, post_line, step)


def normalise_legacy(E: np.ndarray, y: np.ndarray, pre_range: tuple[float, float] | None = None,
                     post_range: tuple[float, float] | None = None) -> NormResult:
    """John's legacy normalisation: subtract the mean over an absolute pre-edge range, divide by the mean over an
    absolute post-edge range (defaults: first 15 % and last 20 % of the scan)."""
    E = np.asarray(E, float); y = np.asarray(y, float)
    r0, r1 = float(E[0]), float(E[-1])
    pre_range = pre_range or (r0, r0 + 0.15 * (r1 - r0))
    post_range = post_range or (r1 - 0.2 * (r1 - r0), r1)
    a = (E >= pre_range[0]) & (E <= pre_range[1]) & np.isfinite(y)
    b = (E >= post_range[0]) & (E <= post_range[1]) & np.isfinite(y)
    e0 = derivative_e0(E, y)
    if not a.any() or not b.any():
        return NormResult(np.full(len(E), np.nan), e0, note="pre or post range empty")
    base = float(y[a].mean()); top = float(y[b].mean())
    step = top - base
    if not step:
        return NormResult(np.full(len(E), np.nan), e0, note="edge step is zero")
    return NormResult((y - base) / step, e0, np.full(len(E), base), np.full(len(E), top), step)


def normalise_tey_line(E: np.ndarray, y: np.ndarray, pre=DEFAULT_PRE, post=DEFAULT_POST) -> NormResult:
    """TEY pre-edge line: remove a line fitted on [E0+pre], scale to the mean over [E0+post] (no flattening)."""
    E = np.asarray(E, float); y = np.asarray(y, float)
    e0 = derivative_e0(E, y)
    if not np.isfinite(e0):
        return NormResult(np.full(len(E), np.nan), e0, note="no finite E0")
    pre_line, n1 = _linefit(E, y, e0 + pre[0], e0 + pre[1])
    if pre_line is None:
        return NormResult(np.full(len(E), np.nan), e0, note="pre window empty")
    out = y - pre_line
    sel = (E >= e0 + post[0]) & (E <= e0 + post[1]) & np.isfinite(out)
    step = float(out[sel].mean()) if sel.any() else float("nan")
    if step and np.isfinite(step):
        out = out / step
    return NormResult(out, e0, pre_line, None, step)


def process(spec: Spectrum, req: TraceRequest) -> NormResult:
    """Shift, normalise per the request, scale (plot only)."""
    E = spec.E + req.shift
    if req.norm == "larch":
        r = normalise_larch(E, spec.y, req.pre, req.post, req.flatten)
    elif req.norm == "legacy":
        r = normalise_legacy(E, spec.y, None, None)
    elif req.norm == "tey_line":
        r = normalise_tey_line(E, spec.y, req.pre, req.post)
    else:
        r = NormResult(spec.y.copy(), derivative_e0(E, spec.y))
    r.y = r.y * req.scale
    return r


def stack_offsets(reqs: list[TraceRequest], default_spacing: float) -> list[float]:
    """Vertical offset of each shown trace: cumulative spacing_after (John's notebook)."""
    out, off = [], 0.0
    for r in reqs:
        out.append(off)
        off += default_spacing if r.spacing_after is None else float(r.spacing_after)
    return out


def local_peaks(E: np.ndarray, y: np.ndarray, min_prominence: float = 0.05, min_distance_points: int = 5) -> list[tuple[float, float]]:
    fin = np.isfinite(y)
    if fin.sum() < 3:
        return []
    idx, _ = find_peaks(y[fin], prominence=min_prominence, distance=max(1, int(min_distance_points)))
    e, v = E[fin], y[fin]
    return [(float(e[i]), float(v[i])) for i in idx]


def export_wide_csv(path: Path, traces: list[tuple[TraceRequest, np.ndarray, np.ndarray]]) -> None:
    """John's wide layout: <label>-energy (eV), <label>-<signal> per trace, rows padded with blanks."""
    n = max((len(E) for _, E, _ in traces), default=0)
    heads, columns = [], []
    for req, E, y in traces:
        heads += [f"{req.label}-energy (eV)", f"{req.label}-{req.signal}"]
        columns += [E, y]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(",".join(heads) + "\n")
        for i in range(n):
            f.write(",".join(("" if i >= len(c) or not np.isfinite(c[i]) else f"{c[i]:.6g}") for c in columns) + "\n")


def save_config(path: Path, reqs: list[TraceRequest], sources: dict[str, str], extra: dict | None = None) -> None:
    Path(path).write_text(json.dumps({"rows": [r.to_json() for r in reqs], "sources": sources, **(extra or {})},
                                     indent=1, ensure_ascii=False), encoding="utf-8")


def load_config(path: Path) -> tuple[list[TraceRequest], dict[str, str], dict]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    reqs = [TraceRequest.from_json(r) for r in d.get("rows", [])]
    extra = {k: v for k, v in d.items() if k not in ("rows", "sources")}
    return reqs, dict(d.get("sources", {})), extra
