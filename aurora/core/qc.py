"""Quality checks of sgm_report v2.5, ported: count-rate probe, SDD saturation / high rate, I0 validity, the agreement of
the four detectors on a zone trace, and a spike summary (docs/HANDOFF.md sections 2.4 and 5.4).

Every rule keeps its constants in aurora.core.constants; every result carries the sentence the report prints.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage

from aurora.core.constants import (COUNT_RATE_PROBE_N, DETS, I0_BAD_FRAC, I0_COL, SAT_I0_VAR, SAT_RATE_RATIO, SAT_TOP_CV)
from aurora.core.io.raw import read_point_dir, stack_energy_dirs, stack_last_mtime
from aurora.core.model import Project, ScanInfo
from aurora.core.zones import ZoneTraces, i0_score

log = logging.getLogger("aurora.qc")


# ----------------------------------------------------------------------------- count rate
@dataclass
class CountRate:
    """Total counts per pixel (all 256 channels) at a few energies spread over the scan, as output rate."""
    scan: str
    key: str
    time_per_map_s: float
    rows: list[dict] = field(default_factory=list)      # E_eV, i0, dwell_s, mean[4], top[4]

    @property
    def top_kcps(self) -> np.ndarray:
        """Per detector: rate (kcps) of the brightest pixels (99th percentile), averaged over the probed energies."""
        if not self.rows:
            return np.full(len(DETS), np.nan)
        top = np.array([r["top"] for r in self.rows], float)
        dw = np.array([r["dwell_s"] or np.nan for r in self.rows], float)
        with np.errstate(all="ignore"):
            return np.nanmean(top / dw[:, None] / 1e3, axis=0)

    @property
    def i0_level(self) -> float:
        return float(np.nanmedian([r["i0"] for r in self.rows])) if self.rows else float("nan")

    def to_json(self) -> dict:
        return {"key": self.key, "time_per_map_s": self.time_per_map_s, "rows": self.rows}


def count_rate_probe(project: Project, scan: ScanInfo, n_probe: int = COUNT_RATE_PROBE_N) -> CountRate:
    """Read n_probe energy folders of a stack (cached under .aurora/cache; sgm_report's _count_rate.json is reused)."""
    edirs = scan.energy_dirs or stack_energy_dirs(scan.path)
    key = f"{len(edirs)}:{stack_last_mtime(edirs):.0f}"
    for cdir in (project.processed / scan.day / scan.name, project.root / ".aurora" / "cache" / scan.day / scan.name):
        p = cdir / "_count_rate.json"
        try:
            d = json.loads(p.read_text())
            if d.get("key") == key and d.get("rows"):
                return CountRate(scan=scan.name, key=key, time_per_map_s=float(d.get("time_per_map_s", 0) or 0), rows=d["rows"])
        except (OSError, ValueError):
            continue
    tmap = float(scan.md.get("time_per_map", 0) or 0)
    rows = []
    for i in np.unique(np.linspace(0, max(len(edirs) - 1, 0), n_probe).astype(int)) if edirs else []:
        e, p = edirs[i]
        try:
            pd = read_point_dir(p)
        except (OSError, ValueError):
            continue
        n = pd.n_pix
        if n == 0:
            continue
        tot = [s.sum(axis=1) for s in pd.sdd]
        rows.append(dict(E_eV=float(e), i0=float(np.nanmean(pd.mcc[:, I0_COL])), dwell_s=(tmap / n if tmap else None),
                         mean=[float(t.mean()) for t in tot], top=[float(np.percentile(t, 99)) for t in tot]))
    cr = CountRate(scan=scan.name, key=key, time_per_map_s=tmap, rows=rows)
    cdir = project.root / ".aurora" / "cache" / scan.day / scan.name
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "_count_rate.json").write_text(json.dumps(cr.to_json()))
    except OSError as err:
        log.warning("%s: count-rate cache not written (%s)", cdir, err)
    log.info("%s: count-rate probe at %d energies, top %s kcps", scan.name, len(rows), np.round(cr.top_kcps, 1).tolist())
    return cr


def day_reference(rates: list[CountRate]) -> tuple[np.ndarray, float]:
    """(median top kcps per detector, median I0 level) over a set of scans (the day's other scans)."""
    if not rates:
        return np.full(len(DETS), np.nan), float("nan")
    k = np.array([r.top_kcps for r in rates], float)
    i0 = np.array([r.i0_level for r in rates], float)
    with np.errstate(all="ignore"):
        kcps = np.nanmedian(k, axis=0) if np.isfinite(k).any() else np.full(len(DETS), np.nan)
        lvl = float(np.nanmedian(i0)) if np.isfinite(i0).any() else float("nan")
    return kcps, lvl


# ----------------------------------------------------------------------------- saturation / high rate
@dataclass
class SaturationResult:
    saturated: list[str] = field(default_factory=list)
    high_rate: list[str] = field(default_factory=list)
    detail: dict[str, dict] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.saturated and not self.high_rate


def saturation_check(cr: CountRate, day_kcps: np.ndarray, n_pix: int | None = None) -> SaturationResult:
    """Saturated = the brightest pixels give the same total at every probed energy (CV < SAT_TOP_CV) while I0 varied by
    more than SAT_I0_VAR (output no longer follows input) and the rate is >= SAT_RATE_RATIO x the day's median.
    High rate = the rate criterion and a weak I0 following, without the flat signature: dead time / pile-up likely."""
    res = SaturationResult()
    rows = cr.rows
    if len(rows) < 4:
        return res
    i0 = np.array([r["i0"] for r in rows], float)
    top = np.array([r["top"] for r in rows], float)
    if not (i0 > 0).all():
        return res
    i0_var = float(i0.max() / i0.min() - 1)
    kcps = cr.top_kcps
    ri = i0 / i0.mean() - 1
    for j, d in enumerate(DETS):
        col = top[:, j]
        cv = float(col.std() / col.mean()) if col.mean() > 0 else 1.0
        rt = col / col.mean() - 1 if col.mean() > 0 else np.zeros_like(col)
        slope = float(np.dot(rt, ri) / np.dot(ri, ri)) if np.dot(ri, ri) > 0 else float("nan")
        ratio = float(kcps[j] / day_kcps[j]) if np.isfinite(kcps[j]) and np.isfinite(day_kcps[j]) and day_kcps[j] > 0 else float("nan")
        res.detail[d] = dict(cv=round(cv, 4), slope_vs_i0=round(slope, 2) if np.isfinite(slope) else None,
                             kcps=round(float(kcps[j]), 1) if np.isfinite(kcps[j]) else None,
                             ratio_to_day=round(ratio, 2) if np.isfinite(ratio) else None)
        if cv < SAT_TOP_CV and i0_var > SAT_I0_VAR and np.isfinite(slope) and slope < 0.5 and (not np.isfinite(ratio) or ratio >= SAT_RATE_RATIO):
            res.saturated.append(d)
        elif np.isfinite(ratio) and ratio >= SAT_RATE_RATIO and i0_var > SAT_I0_VAR and np.isfinite(slope) and slope < 0.5:
            res.high_rate.append(d)
    if res.saturated:
        rate = ", ".join(f"{d.upper()} {kcps[DETS.index(d)]:.0f} kcps" for d in res.saturated if np.isfinite(kcps[DETS.index(d)]))
        ref = ", ".join(f"{k:.0f}" for k in day_kcps if np.isfinite(k))
        res.notes.append(f"SDD saturation: {', '.join(d.upper() for d in res.saturated)}: the brightest pixels give the same total count "
                         f"at every energy (spread < {SAT_TOP_CV:.0%}) while I0 varied by {i0_var:.0%}; the output no longer follows the input"
                         + (f"; {rate} vs {ref} kcps median of the day's other scans" if rate else "")
                         + (f" (time_per_map {cr.time_per_map_s:g} s / {n_pix} px)" if n_pix else "")
                         + ". Fluorescence traces of this scan are not usable; TEY is.")
    if res.high_rate:
        res.notes.append("high count rate: " + ", ".join(f"{d.upper()} {res.detail[d]['kcps']} kcps ({res.detail[d]['ratio_to_day']}x the day's other scans)"
                                                          for d in res.high_rate)
                         + " with the output barely following I0: dead time / pile-up likely, fluorescence amplitudes not trusted; "
                           "compare with TEY. (SDD throughput limit: TODO: need reference.)")
    return res


# ----------------------------------------------------------------------------- I0 validity
@dataclass
class I0Result:
    level: float
    day_level: float
    bad: bool
    note: str = ""


def i0_check(level: float, day_level: float) -> I0Result:
    """I0 invalid when the scan's median mesh current is <= 0 or below I0_BAD_FRAC of the day's other scans."""
    if not np.isfinite(level):
        return I0Result(level, day_level, False, "no I0 level")
    bad = level <= 0 or (np.isfinite(day_level) and day_level > 0 and level < I0_BAD_FRAC * day_level)
    note = ""
    if bad:
        note = (f"I0 invalid: the Au-mesh monitor (mcc1) read {level:.3g} over this scan against {day_level:.3g} for the day's "
                "other scans; traces divided by it would be noise. Use the good-I0 mean or raw counts; the scan should be repeated.")
    return I0Result(level, day_level, bad, note)


# ----------------------------------------------------------------------------- the four detectors on a zone trace
@dataclass
class SddConsistency:
    flags: list[tuple[str, str]] = field(default_factory=list)
    normalised: np.ndarray | None = None        # (n_good, 4) each detector pre-edge subtracted, scaled to its range
    energies: np.ndarray | None = None
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.flags


def sdd_consistency(traces: ZoneTraces, saturated: list[str] | None = None) -> SddConsistency:
    """Each detector's PFY / I0 normalised (pre-edge subtracted, scaled to its own 1-99 percentile range) against the
    mean of the other three: a systematic difference above 25 % of the edge step, or isolated spikes above 30 % far above
    the noise, flag that detector. Meaningless for saturated detectors, which are flagged as such."""
    res = SddConsistency()
    if saturated:
        res.flags = [(d, "saturated") for d in saturated]
        res.note = "detectors beyond their throughput: comparing them is meaningless"
        return res
    if any(y is None for y in traces.det_raw):
        res.note = "per-detector traces not available for this line"
        return res
    i0 = traces.i0_used
    with np.errstate(all="ignore"):
        P = np.column_stack([np.where(i0 > 0, y / i0, np.nan) for y in traces.det_raw])
        med = np.nanmedian(i0)
        g = np.all(np.isfinite(P), axis=1) & (i0 > 0.2 * med if np.isfinite(med) else np.isfinite(i0))
    if g.sum() < 10:
        res.note = f"only {int(g.sum())} usable energies"
        return res
    Y = P[g]
    n_pre = max(3, int(0.05 * g.sum()))
    N = np.empty_like(Y)
    for d in range(Y.shape[1]):
        y = Y[:, d] - np.mean(Y[:n_pre, d])
        rng = np.percentile(y, 99) - np.percentile(y, 1)
        N[:, d] = y / rng if rng > 0 else y
    for d in range(Y.shape[1]):
        others = np.mean(np.delete(N, d, axis=1), axis=1)
        if np.std(N[:, d]) == 0 or np.std(others) == 0:
            res.flags.append((DETS[d], "flat"))
            continue
        resid = N[:, d] - others
        smooth = ndimage.median_filter(resid, 9)
        dev = np.abs(resid - smooth)
        mad = float(np.median(dev)) + 1e-9
        systematic = float(np.max(np.abs(smooth)))
        spikes = int(((dev > 8 * mad) & (dev > 0.3)).sum())
        if systematic > 0.25:
            res.flags.append((DETS[d], f"differs from the other three by up to {systematic:.0%} of the edge step"))
        elif spikes:
            res.flags.append((DETS[d], f"{spikes} spike(s)"))
    res.normalised, res.energies = N, traces.E[g]
    res.note = ("the four detectors agree (within 25% of the edge step, no spikes)" if not res.flags
                else "; ".join(f"{d.upper()} {why}" for d, why in res.flags))
    return res


# ----------------------------------------------------------------------------- spikes and I0 scores
def spike_summary(traces: ZoneTraces) -> dict[str, int]:
    return {k: len(v) for k, v in traces.spikes.items() if isinstance(v, list) and v}


@dataclass
class I0Candidate:
    sid: str
    label: str
    level: float
    max_step: float
    rough: float
    nonpos: int
    looks_good: bool
    E: np.ndarray
    curve: np.ndarray


def i0_candidates(stacks: dict[str, "object"], labels: dict[str, str]) -> list[I0Candidate]:
    """Score the per-pixel-mean I0 curve of every loaded stack; `looks_good` follows the zone tool's auto-select rule
    against the median level of the pool."""
    scored = []
    for sid, sd in stacks.items():
        m = sd.i0_mean()
        s = i0_score(m)
        if s is None:
            continue
        scored.append((sid, s, m, sd.E))
    pool = float(np.median([s.level for _, s, _, _ in scored])) if scored else 1.0
    out = []
    for sid, s, m, E in scored:
        out.append(I0Candidate(sid=sid, label=labels.get(sid, sid), level=s.level, max_step=s.max_step, rough=s.rough,
                               nonpos=s.nonpos, looks_good=s.looks_good(pool), E=E, curve=m))
    return out
