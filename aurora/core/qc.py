"""Quality checks of sgm_report v2.5, ported: count-rate probe, SDD saturation / high rate, I0 validity, the agreement of
the four detectors on a zone trace, and a spike summary (docs/HANDOFF.md sections 2.4 and 5.4). Plus AURORA's own:
- the results of a holder's checks persist in <project>/.aurora/qc.json (QcResult, save_qc / load_qc) and feed
  aurora.core.flags.merged_flags, so new zones untick saturated SDDs even on a beamtime without sgm_report;
- the reference of a scan (the day's other scans) is energy-local: scans whose range overlaps within I0_REF_WINDOW_EV;
- check_zone: John Bussey's sanity messages for one zone x scan (the Easy 'Spectra to check' queue).

Every rule keeps its constants in aurora.core.constants; every result carries the sentence the report prints.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage

from aurora.core.constants import (BAD_POINT_FRACTION, COUNT_RATE_PROBE_N, DETS, FEW_POINTS, I0_BAD_FRAC, I0_COL,
                                   I0_LOW_FRAC, I0_REF_WINDOW_EV, I0_VARIES_FACTOR, SAT_I0_VAR, SAT_RATE_RATIO,
                                   SAT_TOP_CV)
from aurora.core.flags import CHECK_SOURCE, qc_path
from aurora.core.io.raw import read_point_dir, stack_energy_dirs, stack_last_mtime
from aurora.core.model import Project, ScanInfo
from aurora.core.spectra import ZoneTraces, i0_score

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
            d = json.loads(p.read_text(encoding="utf-8"))
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
        from aurora.core.io.safety import assert_writable
        assert_writable(cdir)
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "_count_rate.json").write_text(json.dumps(cr.to_json()), encoding="utf-8")
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
                           "compare with TEY.")
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


# ----------------------------------------------------------------------------- persisted results (qc.json)
def _f(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


@dataclass
class QcResult:
    """The checks of one scan, as stored in <project>/.aurora/qc.json under its sid ('<day>_<scan folder>')."""
    sid: str
    i0_level: float | None = None
    i0_bad: bool = False
    kcps: list = field(default_factory=lambda: [None] * len(DETS))     # brightest-pixel rate per SDD
    saturated: list[str] = field(default_factory=list)
    high_rate: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    probed_at: str = ""
    aurora_version: str = ""
    scan_mtime: float = 0.0             # stack_last_mtime of the energy folders when probed: re-checked when it changes
    n_energies: int = 0
    holder: str = ""
    e_range: list | None = None          # [first, last] energy (eV) of the scan
    i0_ref: float | None = None          # the energy-local median I0 level it was judged against
    kcps_ref: list = field(default_factory=lambda: [None] * len(DETS))
    n_ref: int = 0                       # scans in the reference

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("sid")
        return d

    @classmethod
    def from_json(cls, sid: str, d: dict) -> "QcResult":
        kc = list(d.get("kcps") or [None] * len(DETS))[:len(DETS)]
        kr = list(d.get("kcps_ref") or [None] * len(DETS))[:len(DETS)]
        return cls(sid=sid, i0_level=_f(d.get("i0_level")), i0_bad=bool(d.get("i0_bad")), kcps=[_f(v) for v in kc],
                   saturated=[str(x) for x in d.get("saturated") or []], high_rate=[str(x) for x in d.get("high_rate") or []],
                   notes=[str(x) for x in d.get("notes") or []], probed_at=str(d.get("probed_at", "")),
                   aurora_version=str(d.get("aurora_version", "")), scan_mtime=float(d.get("scan_mtime") or 0.0),
                   n_energies=int(d.get("n_energies") or 0), holder=str(d.get("holder", "")),
                   e_range=list(d["e_range"]) if isinstance(d.get("e_range"), (list, tuple)) else None,
                   i0_ref=_f(d.get("i0_ref")), kcps_ref=[_f(v) for v in kr], n_ref=int(d.get("n_ref") or 0))

    @property
    def flags(self) -> list:
        from aurora.core.flags import entry_flags
        return entry_flags(self.to_json(), CHECK_SOURCE)


def scan_range(scan: ScanInfo | None, cr: CountRate | None = None) -> tuple[float, float] | None:
    """(first, last) energy of a scan: from its folder list, else from the probed energies."""
    if scan is not None and np.isfinite(scan.e_min) and np.isfinite(scan.e_max):
        return float(scan.e_min), float(scan.e_max)
    if cr is not None and cr.rows:
        es = [float(r["E_eV"]) for r in cr.rows]
        return min(es), max(es)
    return None


def reference_pool(sid: str, ranges: dict[str, tuple[float, float] | None], window_ev: float = I0_REF_WINDOW_EV) -> list[str]:
    """The other scans whose energy range overlaps this scan's range widened by +/- window_ev (docs/HANDOFF.md 2.4:
    'the median of the other scans within +/- 150 eV'). A scan without a known range is compared with every other."""
    me = ranges.get(sid)
    out = []
    for k, r in ranges.items():
        if k == sid:
            continue
        if me is None or r is None or (r[0] <= me[1] + window_ev and r[1] >= me[0] - window_ev):
            out.append(k)
    return out


def scan_mtime(scan: ScanInfo) -> tuple[float, int]:
    """(stack_last_mtime of the energy folders, number of energy folders): what tells a changed scan (a stat each)."""
    edirs = stack_energy_dirs(scan.path) if scan.path.is_dir() else []
    return stack_last_mtime(edirs), len(edirs)


def verdicts(rates: dict[str, CountRate], scans: dict[str, ScanInfo], holder: str = "",
             mtimes: dict[str, tuple[float, int]] | None = None) -> dict[str, QcResult]:
    """Per scan of one holder: saturation and I0 validity against the energy-local reference of the holder's other
    scans (reference_pool). `scans` maps sid -> ScanInfo; `mtimes` sid -> scan_mtime (stored for the re-check)."""
    from aurora import __version__
    ranges = {sid: scan_range(scans.get(sid), cr) for sid, cr in rates.items()}
    now = dt.datetime.now().isoformat(timespec="seconds")
    out: dict[str, QcResult] = {}
    for sid, cr in rates.items():
        pool = reference_pool(sid, ranges)
        day_kcps, day_i0 = day_reference([rates[k] for k in pool])
        scan = scans.get(sid)
        sat = saturation_check(cr, day_kcps, scan.n_points if scan is not None else None)
        i0r = i0_check(cr.i0_level, day_i0)
        k = cr.top_kcps
        mt, ne = (mtimes or {}).get(sid, (0.0, len(scan.energy_dirs) if scan is not None else 0))
        rng = ranges.get(sid)
        out[sid] = QcResult(sid=sid, i0_level=_f(i0r.level), i0_bad=bool(i0r.bad),
                            kcps=[_f(round(float(v), 1)) if np.isfinite(v) else None for v in k],
                            saturated=list(sat.saturated), high_rate=list(sat.high_rate),
                            notes=sat.notes + ([i0r.note] if i0r.note else []), probed_at=now, aurora_version=__version__,
                            scan_mtime=float(mt), n_energies=int(ne), holder=holder,
                            e_range=[rng[0], rng[1]] if rng else None, i0_ref=_f(day_i0),
                            kcps_ref=[_f(round(float(v), 1)) if np.isfinite(v) else None for v in day_kcps], n_ref=len(pool))
    return out


def probe_holders(project: Project, holders: dict[str, list[ScanInfo]], sid_of=None, progress=None,
                  ) -> dict[str, QcResult]:
    """The count-rate probe of every stack of every holder ({holder key: [ScanInfo]}), then the verdicts per holder.
    progress(i, n, message) is called per stack (it may raise aurora.core.cancel.Cancelled). Nothing is saved."""
    sid_of = sid_of or (lambda s: f"{s.day}_{s.name}")
    todo = [(h, s) for h, scans in holders.items() for s in scans]
    rates: dict[str, dict[str, CountRate]] = {h: {} for h in holders}
    infos: dict[str, ScanInfo] = {}
    mtimes: dict[str, tuple[float, int]] = {}
    for i, (h, s) in enumerate(todo):
        if progress:
            progress(i, len(todo), f"count rate {s.name}")
        sid = sid_of(s)
        infos[sid] = s
        try:
            mtimes[sid] = scan_mtime(s)
            rates[h][sid] = count_rate_probe(project, s)
        except (OSError, ValueError) as err:
            log.warning("%s: count-rate probe failed (%s)", s.name, err)
    if progress:
        progress(len(todo), len(todo), "verdicts")
    out: dict[str, QcResult] = {}
    for h, rr in rates.items():
        out.update(verdicts(rr, infos, h, mtimes))
    return out


def load_qc(project: Project) -> dict[str, QcResult]:
    """The stored results of <project>/.aurora/qc.json (empty when there is none)."""
    from aurora.core.flags import qc_entries
    return {sid: QcResult.from_json(sid, d) for sid, d in qc_entries(project).items()}


def save_qc(project: Project, results: dict[str, QcResult]) -> Path:
    """Merge `results` into <project>/.aurora/qc.json (entries of other scans are kept) with an atomic write."""
    from aurora.core.io.textio import read_json, write_json_atomic
    p = qc_path(project)
    old = read_json(p, {})
    data = {k: v for k, v in (old.items() if isinstance(old, dict) else [])}
    for sid, r in results.items():
        data[sid] = r.to_json()
    write_json_atomic(p, dict(sorted(data.items())))
    log.info("%s: %d scan check(s) written (%d in the file)", p, len(results), len(data))
    return p


def stale(results: dict[str, QcResult], scans: dict[str, ScanInfo]) -> list[str]:
    """sids whose scan changed since it was checked (newer energy folders, or more of them). One stat per scan."""
    out = []
    for sid, r in results.items():
        s = scans.get(sid)
        if s is None:
            continue
        try:
            mt, ne = scan_mtime(s)
        except OSError:
            continue
        if abs(mt - r.scan_mtime) > 1e-3 or (r.n_energies and ne != r.n_energies):
            out.append(sid)
    return out


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


# ----------------------------------------------------------------------------- sanity of one zone x scan
LEVELS = ("ok", "warn", "bad")


def _n(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def worst(messages) -> str:
    """'bad', 'warn' or 'ok': the worst level of a message list."""
    levels = {lv for lv, _ in messages or []}
    return "bad" if "bad" in levels else ("warn" if "warn" in levels else "ok")


def check_zone(traces: ZoneTraces | None, stack=None, flags=(), n_total: int | None = None) -> list[tuple[str, str]]:
    """John Bussey's `check_reduced` for one zone x scan, on AURORA's traces: [(level, message)], level ok / warn / bad.

    Ported checks: the region's points (0 = bad, fewer than FEW_POINTS = noisy), per ticked SDD no finite values or a
    signal zero everywhere (bad), missing energies, I0 zero or missing, I0 varying by more than I0_VARIES_FACTOR, TEY
    without finite values, and the fraction of energies flagged bad (spikes plus masked energies, BAD_POINT_FRACTION).
    AURORA adds the scan's flags (saturated SDDs, I0 invalid, high rate, each with its source) and the de-spike notes.
    `stack` (a StackData) or `n_total` gives the stack's pixel count for 'N of M points'."""
    if n_total is None and stack is not None:
        n_total = getattr(stack, "n_pix", None)
    if traces is None or traces.n_pix == 0:
        return [("bad", "region contains 0 measurement points: nothing to reduce; draw it over the scan's footprint")]
    msgs: list[tuple[str, str]] = []
    st = traces.settings
    n = int(traces.n_pix)
    of = f" of {n_total}" if n_total else ""
    if n < FEW_POINTS:
        msgs.append(("warn", f"only {_n(n, 'measurement point')}{of}: noisy"))
    else:
        msgs.append(("ok", f"region contains {n}{of} measurement points"))
    E = np.asarray(traces.E, float)
    nE = len(E)
    ticked = [k for k in range(len(DETS)) if st is None or st.sdd[k]]
    if all(y is None for y in traces.det_raw):
        msgs.append(("bad", f"no per-detector data for the line {traces.line} in this stack"))
    else:
        window = (lambda k: f"channels {st.roi[DETS[k]][0]}-{st.roi[DETS[k]][1] - 1}") if (st is not None and st.roi) else \
            (lambda k: traces.line)
        for k in ticked:
            y = traces.det_raw[k]
            if y is None:
                continue
            y = np.asarray(y, float)
            if not np.isfinite(y).any():
                msgs.append(("bad", f"{DETS[k].upper()}: no finite values (missing files?)"))
            elif np.nanmax(y) <= 0:
                msgs.append(("bad", f"{DETS[k].upper()}: signal is zero everywhere - check its window ({window(k)})"))
        if not ticked:
            msgs.append(("warn", "no detector is ticked: no fluorescence trace"))
    sum_ok = np.ones(nE, bool)
    for k in ticked:
        if traces.det_raw[k] is not None:
            sum_ok &= np.isfinite(np.asarray(traces.det_raw[k], float))
    missing = ~sum_ok if ticked and any(traces.det_raw[k] is not None for k in ticked) else np.zeros(nE, bool)
    if missing.any() and not missing.all():
        msgs.append(("warn", f"{_n(int(missing.sum()), 'missing energy point')}"))
    i0 = np.asarray(traces.i0_raw, float)
    zero = (~np.isfinite(i0) | (i0 <= 0)) & ~missing
    if zero.any():
        msgs.append(("warn", f"I0 is zero or missing at {_n(int(zero.sum()), 'energy', 'energies')}"))
    pos = i0[np.isfinite(i0) & (i0 > 0)]
    if pos.size and pos.max() / max(pos.min(), 1e-30) > I0_VARIES_FACTOR:
        msgs.append(("warn", f"I0 varies by a factor {pos.max() / pos.min():.1f} over the scan"))
    if (st is None or st.tey) and not np.isfinite(np.asarray(traces.tey_raw, float)).any():
        msgs.append(("warn", "TEY has no finite values"))
    bad = traces.bad_mask()
    nb = int(bad.sum())
    low = traces.masked.get("i0_low")
    nlow = int(np.asarray(low, bool).sum()) if low is not None else 0
    if nb == 0:
        msgs.append(("ok", "no bad points"))
    elif nb / max(nE, 1) > BAD_POINT_FRACTION:
        msgs.append(("warn", f"{100 * nb / nE:.0f} % of the points flagged bad ({nb} of {nE}"
                             + (f"; {nlow} with I0 below {I0_LOW_FRAC:.0%} of its median" if nlow else "") + ")"))
    else:
        msgs.append(("ok", f"{_n(nb, 'bad point')} of {nE}"
                           + (f" ({nlow} with I0 below {I0_LOW_FRAC:.0%} of its median, left out)" if nlow else " interpolated")))
    for note in traces.notes:
        if "near the edge" in note:
            msgs.append(("warn", note))
        elif note.startswith("signal: IPFY") or note.startswith("IPFY:"):
            msgs.append(("ok", note))
    in_sum = {DETS[k] for k in ticked}
    for f in flags or []:
        src = getattr(f, "source", "")
        if f.kind == "sdd_saturated":
            if set(f.dets) & in_sum:
                msgs.append(("warn", f"{f.text} ({src}) and in the fluorescence sum: its fluorescence is not usable; "
                                     "TEY is"))
            else:
                msgs.append(("ok", f"{f.text} ({src}): left out of the sum"))
        elif f.kind == "high_rate":
            msgs.append(("warn", f"{f.text} ({src}): dead time or pile-up likely, compare with TEY"))
        elif f.kind == "i0_invalid":
            if st is not None and st.i0_source == "good":
                msgs.append(("ok", f"I0 invalid ({src}): divided by the good-I0 mean"))
            else:
                msgs.append(("warn", f"I0 invalid ({src}): the / I0 traces are not meaningful; use the good-I0 mean"))
    return msgs


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


# ----------------------------------------------------------------------------- I0 explorer (mesh vs photodiode)
@dataclass
class MccCurves:
    """Per-energy means of the scaler channels of a stack: mesh I0 (ch1), photodiode (ch2), aux (ch3), TEY (ch4)."""
    scan: str
    E: np.ndarray
    mesh: np.ndarray
    diode: np.ndarray
    aux: np.ndarray
    tey: np.ndarray

    @property
    def ratio(self) -> np.ndarray:
        """diode / mesh, each normalised to its median: the mesh's own spectral response shows as a departure from 1."""
        with np.errstate(all="ignore"):
            m = self.mesh / np.nanmedian(self.mesh); d = self.diode / np.nanmedian(self.diode)
            return np.where(m != 0, d / m, np.nan)


def mcc_curves(project: Project, scan: ScanInfo) -> MccCurves:
    """Read only the mcc csv of every energy folder (fast), cached as json under .aurora/cache."""
    edirs = scan.energy_dirs or stack_energy_dirs(scan.path)
    key = f"mcc:{len(edirs)}:{stack_last_mtime(edirs):.0f}"
    cdir = project.root / ".aurora" / "cache" / scan.day / scan.name
    p = cdir / "_mcc_curves.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if d.get("key") == key:
            return MccCurves(scan.name, *(np.array(d[k], float) for k in ("E", "mesh", "diode", "aux", "tey")))
    except (OSError, ValueError):
        pass
    E, cols = [], []
    for e, pdir in edirs:
        try:
            mcc = np.loadtxt(pdir / "mcc_flyer_0.csv", delimiter=",", ndmin=2)
        except (OSError, ValueError):
            continue
        if mcc.size == 0:
            continue
        E.append(float(e)); cols.append(np.nanmean(mcc[:, :4], axis=0))
    arr = np.array(cols) if cols else np.zeros((0, 4))
    cur = MccCurves(scan.name, np.array(E), arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3])
    try:
        from aurora.core.io.safety import assert_writable
        assert_writable(cdir)
        cdir.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"key": key, "E": cur.E.tolist(), "mesh": cur.mesh.tolist(), "diode": cur.diode.tolist(),
                                 "aux": cur.aux.tolist(), "tey": cur.tey.tolist()}), encoding="utf-8")
    except OSError:
        pass
    return cur
