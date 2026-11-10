"""John Bussey-compatible files for one zone x scan: _full_reduced.csv, _absorption.csv, _polygon_roi.json (or
_zone.json) and _gui_settings.json, in the layout his SGM XAS GUI writes (CheckTab.save_ticked / write_reduced_csvs),
so his Compare tab, AURORA's Compare and his notebooks read them unchanged.

The numbers follow his `reduce_stack`: per detector the mean over the zone pixels of the counts in channels [lo, hi)
(his 'mean'; 'sum' is the other spatial reduction), I0 = mcc ch1 and TEY = mcc ch4 over the same pixels, sdd_total and
sdd_mean. A detector file that holds fewer pixels than the map (an incomplete energy) gives the mean of the zone pixels
it has, NaN when it has none, as in his GUI: on the stack's fixed bins too, whose reduction leaves such an energy out
(sgm_report's rule), the energy is read from its raw folder. AURORA's own processing on top, stated in the header:
- the de-spike of the zone settings (classic or coherent) interpolates each trace at its own spikes, and the ticked
  detectors at the spikes of their sum (coherent), so that sdd_total stays the sum of the written columns;
- energies whose I0 is below I0_LOW_FRAC of its median keep their counts and get an empty I0 (AURORA's / I0 traces
  leave them out, and Compare divides by this column);
- sdd_total and sdd_mean are over the detectors ticked in the zone settings, empty when one of them is missing;
- bad_point is True at every spike, low-I0 and incomplete energy (one without a value, too).
Files are written atomically; nothing is ever written inside a scan folder or a beamline raw/ folder.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from aurora.core.constants import DETS, I0_COL, I0_LOW_FRAC, N_CH, TEY_COL
from aurora.core.io.config import ScanSettings, Zone
from aurora.core.spectra import ZoneTraces, build_traces, fill_spikes
from aurora.core.zones import ZoneSums, members

log = logging.getLogger("aurora.export")

JOHN_COLUMNS = (["energy"] + [c for d in DETS for c in (d, f"{d}_roi_lo", f"{d}_roi_hi")]
                + ["I0", "TEY", "sdd_total", "sdd_mean", "bad_point"])
ABSORPTION_COLUMNS = ["energy", "sdd_total", "TEY", "I0", "bad_point"]
MCC_NAMES = {I0_COL: "ch1", TEY_COL: "ch4"}
SUFFIXES = ("_full_reduced.csv", "_absorption.csv", "_polygon_roi.json", "_zone.json", "_gui_settings.json")
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_label(label: str) -> str:
    """A sample label usable as a file and folder name on every system (characters Windows refuses become '_')."""
    s = _BAD_CHARS.sub("_", str(label or "").strip()).rstrip(". ")
    return s or "zone"


# ----------------------------------------------------------------------------- membership
def polygon_mask(poly, x, y) -> np.ndarray:
    """Pixels strictly inside a polygon [[x, y], ...] (even-odd rule), as matplotlib's Path.contains_points decides
    for every point that is not on an edge: John Bussey's region test."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    v = np.asarray(poly, float)
    inside = np.zeros(len(x), bool)
    if v.ndim != 2 or len(v) < 3:
        return inside
    xj, yj = v[-1]
    for xi, yi in v:
        cross = (yi > y) != (yj > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = (xj - xi) * (y - yi) / (yj - yi) + xi
        inside ^= cross & (x < xint)
        xj, yj = xi, yi
    return inside


def zone_pixels(zone: Zone, stack) -> np.ndarray:
    """Pixel indices of the zone on a stack: zones.members, which holds the rectangles, painted squares and the
    pixels strictly inside the polygons (John Bussey's rule), minus the erasures."""
    return np.asarray(members(zone, stack.x, stack.y, stack.pitch), int)


def roi_polygon(zone: Zone, pitch: tuple[float, float] | None = None) -> list[list[float]] | None:
    """The zone as John's _polygon_roi.json: the vertices of its one polygon, or the corners of its one rectangle
    widened by AURORA's 0.45-pitch tolerance (so his strict test takes the same pixels). None for any other zone."""
    simple = not (zone.erects or zone.paint or zone.erase)
    if simple and len(zone.polys) == 1 and not zone.rects:
        return [[float(a), float(b)] for a, b in zone.polys[0]]
    if simple and len(zone.rects) == 1 and not zone.polys:
        x0, x1, y0, y1 = (float(v) for v in zone.rects[0])
        hx, hy = ((pitch[0] * 0.45, pitch[1] * 0.45) if pitch else (0.0, 0.0))
        x0, x1, y0, y1 = min(x0, x1) - hx, max(x0, x1) + hx, min(y0, y1) - hy, max(y0, y1) + hy
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return None


# ----------------------------------------------------------------------------- the reduced frame
@dataclass
class JohnFrame:
    """One zone x scan reduced the way John Bussey's GUI reduces it, plus AURORA's cleaning."""
    E: np.ndarray
    det: np.ndarray                         # (nE, 4) per-detector values written (cleaned)
    det_measured: np.ndarray                # (nE, 4) before the de-spike
    i0: np.ndarray                          # written I0 (NaN at low-I0 energies)
    i0_measured: np.ndarray
    tey: np.ndarray
    total: np.ndarray                       # sdd_total over the ticked detectors (NaN when one is missing)
    mean: np.ndarray                        # sdd_mean
    bad: np.ndarray                         # bad_point
    windows: dict[str, tuple[int, int] | None]
    n: int                                  # zone pixels
    N: int                                  # stack pixels
    mode: str = "mean"
    dets_in_total: list[str] = field(default_factory=list)
    traces: ZoneTraces | None = None
    spikes_ev: dict[str, list[float]] = field(default_factory=dict)
    low_ev: list[float] = field(default_factory=list)
    incomplete_ev: list[float] = field(default_factory=list)
    i0_note: str = ""
    source: str = ""
    notes: list[str] = field(default_factory=list)


def windows_for(settings: ScanSettings, stack, project=None) -> dict[str, tuple[int, int] | None]:
    """Channel windows [lo, hi) per detector: ScanSettings.roi, else the fixed bins of the line (config/roi_bins.json),
    else the total-XRF bins; None for a detector whose channels are not known."""
    from aurora.core.stack import TOTAL_BOUNDS, TOTAL_LABEL
    line = settings.line or getattr(stack, "main", "")
    bins = getattr(project, "roi_bins", None) if project is not None else None
    fixed = bins.bounds(line) if (bins is not None and bins.has(line)) else (
        {d: TOTAL_BOUNDS for d in DETS} if line == TOTAL_LABEL else {})
    out: dict[str, tuple[int, int] | None] = {}
    for d in DETS:
        w = (settings.roi or {}).get(d) if settings.roi else None
        if w is None:
            w = fixed.get(d)
        out[d] = (max(0, int(w[0])), min(N_CH, int(w[1]))) if w is not None else None
    return out


def _reduce(values: np.ndarray, mode: str) -> float:
    """John's reduce_values on one pixel selection: nanmean ('mean') or nansum ('sum'); NaN when empty."""
    values = np.asarray(values, float)
    if values.size == 0:
        return float("nan")
    return float(np.nansum(values)) if mode == "sum" else float(np.nanmean(values))


def _read_raw(edirs, mask: np.ndarray, windows, mode: str, progress=None):
    """Per energy folder: each detector file read on its own (its own pixel count), the mcc csv likewise, then the
    selected pixels reduced (John's reduce_stack)."""
    nE = len(edirs)
    det = np.full((nE, len(DETS)), np.nan)
    i0 = np.full(nE, np.nan); tey = np.full(nE, np.nan)
    for i, (e, p) in enumerate(edirs):
        if progress:
            progress(i, nE, f"reading {Path(p).name}")
        for k, d in enumerate(DETS):
            w = windows.get(d)
            f = Path(p) / f"{d}_0.bin"
            if w is None:
                continue
            try:
                a = np.fromfile(f, dtype=np.uint32)
            except OSError:
                continue
            n = len(a) // N_CH
            per_pixel = a[:n * N_CH].reshape(n, N_CH)[:, w[0]:w[1]].sum(axis=1)
            m = mask[:n]
            det[i, k] = _reduce(per_pixel[:len(m)][m], mode)
        try:
            mcc = np.loadtxt(Path(p) / "mcc_flyer_0.csv", delimiter=",", ndmin=2)
        except (OSError, ValueError):
            continue
        if mcc.size == 0:
            continue
        n = min(len(mcc), len(mask))
        m = mask[:n]
        i0[i] = _reduce(mcc[:n, I0_COL][m], mode)
        tey[i] = _reduce(mcc[:n, TEY_COL][m], mode)
    return det, i0, tey


def _energy_dirs(scan) -> list:
    if scan is None:
        return []
    edirs = getattr(scan, "energy_dirs", None) or []
    if not edirs:
        from aurora.core.io.raw import stack_energy_dirs
        edirs = stack_energy_dirs(scan.path)
    return list(edirs)


def john_frame(zone: Zone, sid: str, stack, settings: ScanSettings, *, project=None, scan=None, mode: str = "mean",
               good_curve: np.ndarray | None = None, pix=None, progress=None, read_raw: bool | None = None) -> JohnFrame:
    """Reduce one zone x scan in John Bussey's layout. Custom windows (ScanSettings.roi) or read_raw=True read the raw
    energy folders of `scan`; otherwise the stack's fixed line bins are used (StackData.roi_pix_det), and an energy the
    stack left out (an incomplete map) is read from its raw folder, by John's rule, when `scan` has one. The de-spike
    and the I0 rule of the zone settings decide the bad points (spectra.build_traces on these very sums)."""
    if mode not in ("mean", "sum"):
        raise ValueError("spatial reduction is 'mean' or 'sum'")
    pix = zone_pixels(zone, stack) if pix is None else np.asarray(pix, int)
    N = int(stack.n_pix)
    windows = windows_for(settings, stack, project)
    line = settings.line or stack.main
    use_raw = bool(settings.roi) if read_raw is None else bool(read_raw)
    if not use_raw and line not in getattr(stack, "labels", []):
        use_raw = True
    if use_raw:
        edirs = _energy_dirs(scan)
        if not edirs:
            raise ValueError(f"{sid}: the raw energy folders are needed for these windows but are not available")
        mask = np.zeros(N, bool)
        mask[pix[pix < N]] = True
        det, i0, tey = _read_raw(edirs, mask, windows, mode, progress)
        E = np.array([e for e, _ in edirs], float)
        source = f"raw energy folders of {scan.name}"
    else:
        k = stack.roi_index(line)
        E = np.asarray(stack.E, float)
        det = np.full((len(E), len(DETS)), np.nan)
        with np.errstate(all="ignore"):
            for j in range(len(DETS)):
                vals = stack.roi_pix_det[:, pix, k, j].astype(float)
                det[:, j] = np.nansum(vals, axis=1) if mode == "sum" else np.nanmean(vals, axis=1)
            iv = stack.i0[:, pix].astype(float); tv = stack.tey[:, pix].astype(float)
            i0 = np.nansum(iv, axis=1) if mode == "sum" else np.nanmean(iv, axis=1)
            tey = np.nansum(tv, axis=1) if mode == "sum" else np.nanmean(tv, axis=1)
            gone = ~np.isfinite(stack.i0[:, pix]).any(axis=1)
            if mode == "sum":                       # nansum gives 0 for an energy without any value
                det[gone] = np.nan; i0[gone] = np.nan; tey[gone] = np.nan
        source = f"{stack.source or 'stack'} (fixed bins of {line})"
        # the energies the stack left out (an incomplete map, sgm_report's rule): John's GUI keeps what each file holds
        edirs = _energy_dirs(scan) if (gone.any() and pix.size) else []
        if len(edirs) == len(E) and np.allclose([e for e, _ in edirs], E):
            idx = np.where(gone)[0]
            mask = np.zeros(N, bool)
            mask[pix[pix < N]] = True
            det[idx], i0[idx], tey[idx] = _read_raw([edirs[i] for i in idx], mask, windows, mode, progress)
            source += (f"; {len(idx)} {'energy' if len(idx) == 1 else 'energies'} the stack left out read from the raw "
                       "folders")
    n = int(pix.size)
    i0_note = ""
    if settings.i0_source == "good" and good_curve is not None:
        g = np.asarray(good_curve, float)
        if len(g) == len(E):
            i0 = g * (n if mode == "sum" else 1)
            i0_note = "good-I0 mean of the scans ticked in Check (this scan's own I0 is not used)"
    # zone sums for the traces: mean x n (or the sums themselves); the de-spike and the I0 rule are scale-free
    scale = 1.0 if mode == "sum" else float(max(n, 1))
    st = copy.copy(settings)
    st.line = line
    sums = ZoneSums(E=E, n_pix=n, i0=i0 * scale, tey=tey * scale, det={line: det * scale}, main=line,
                    edge_ev=getattr(stack, "edge_ev", None), valid=None, sid=sid)
    traces = build_traces(sums, st) if n else None
    sp = traces.spikes if traces is not None else {}
    clean = det.copy()
    for j, d in enumerate(DETS):
        # a spike of the sum (the coherent rule's own) is interpolated in the detectors it is the sum of
        idx = sorted(set(sp.get(d) or []) | (set(sp.get("sum") or []) if settings.sdd[j] else set()))
        if idx:
            clean[:, j] = fill_spikes(det[:, j], idx)
    i0w = fill_spikes(i0, sp["i0"]) if sp.get("i0") else i0.copy()
    teyw = fill_spikes(tey, sp["tey"]) if sp.get("tey") else tey.copy()
    ticked = [j for j in range(len(DETS)) if settings.sdd[j]]
    if ticked:
        sub = clean[:, ticked]
        complete = np.isfinite(sub).all(axis=1)
        total = np.where(complete, sub.sum(axis=1), np.nan)
        mean = total / len(ticked)
    else:
        complete = np.zeros(len(E), bool)
        total = np.full(len(E), np.nan); mean = total.copy()
    low = traces.masked.get("i0_low") if traces is not None else None
    low = np.zeros(len(E), bool) if low is None else np.asarray(low, bool)
    i0w = np.where(low, np.nan, i0w)
    bad = (traces.bad_mask() if traces is not None else np.zeros(len(E), bool)) | low
    incomplete = ~(complete & np.isfinite(i0)) if ticked else np.zeros(len(E), bool)
    bad = bad | incomplete
    fr = JohnFrame(E=E, det=clean, det_measured=det, i0=i0w, i0_measured=i0, tey=teyw, total=total, mean=mean, bad=bad,
                   windows=windows, n=n, N=N, mode=mode, dets_in_total=[DETS[j] for j in ticked], traces=traces,
                   spikes_ev=dict(traces.spikes_ev) if traces is not None else {},
                   low_ev=[float(e) for e in E[low]], incomplete_ev=[float(e) for e in E[incomplete]], i0_note=i0_note,
                   source=source, notes=list(traces.notes) if traces is not None else [])
    return fr


# ----------------------------------------------------------------------------- writing
def _num(v) -> str:
    """pandas' to_csv form: the shortest repr of a float, an empty cell for NaN."""
    v = float(v)
    return repr(v) if np.isfinite(v) else ""


def _ev(es) -> str:
    return ", ".join(f"{e:.2f}" for e in es)


def default_folder(project, label: str) -> Path:
    """<project>/processed/reduced/<label>/, where John's Compare and AURORA's 'Add saved spectra…' look."""
    return Path(project.root) / "processed" / "reduced" / safe_label(label)


def target_paths(out_dir: Path, label: str, zone: Zone, pitch=None) -> list[Path]:
    """The files write_john would write for this zone (for an overwrite question before writing)."""
    base = safe_label(label)
    out_dir = Path(out_dir)
    poly = "_polygon_roi.json" if roi_polygon(zone, pitch) is not None else "_zone.json"
    return [out_dir / f"{base}{s}" for s in ("_full_reduced.csv", "_absorption.csv", poly, "_gui_settings.json")]


def _check_folder(out_dir: Path, scan=None) -> None:
    from aurora.core.io.safety import assert_writable
    assert_writable(out_dir)
    if scan is not None:
        try:
            Path(out_dir).resolve().relative_to(Path(scan.path).resolve())
        except ValueError:
            return
        raise PermissionError(f"{out_dir}: inside the scan folder {scan.path}; AURORA never writes there")


def header_lines(fr: JohnFrame, *, label: str, zone: Zone, sid: str, settings: ScanSettings, stack, scan=None,
                 survey_h5=None, holder: str = "") -> list[str]:
    from aurora import __version__
    from aurora.core.names import split_stack_id
    h5 = getattr(scan, "h5", None) if scan is not None else None
    scan_name = (getattr(scan, "md", {}) or {}).get("scan_name") if scan is not None else None
    if not scan_name:
        name = split_stack_id(sid)[1] or getattr(stack, "name", "")
        scan_name = re.sub(r"^\d{6}_", "", name)
        scan_name = re.sub(r"_(stack|survey)$", "", scan_name)
    rois = {d: [w[0], w[1]] for d, w in fr.windows.items() if w is not None}
    mode = getattr(settings, "despike_mode", "classic") or "classic"
    ds = f"{mode}, k = {settings.k:g}" if settings.despike else "off"
    spikes = "; ".join(f"{k.upper() if k.startswith('sdd') else k} at {_ev(v)} eV" for k, v in fr.spikes_ev.items())
    lines = [f"# sample_label: {label}",
             f"# source_h5: {h5 or ''}",
             f"# survey_h5: {survey_h5 or ''}",
             f"# scan_name: {scan_name}",
             f"# sdd_roi_channels: {json.dumps(rois)}",
             "# use_sdd_calibration: False",
             f"# mcc_i0_channel: {MCC_NAMES[I0_COL]}",
             f"# mcc_tey_channel: {MCC_NAMES[TEY_COL]}",
             f"# spatial_reduction: {fr.mode}",
             f"# roi_points: {fr.n} / {fr.N}",
             "# bad_point_reference: AURORA de-spike of each detector, the sum, TEY and I0; I0 below "
             f"{I0_LOW_FRAC:.0%} of its median; incomplete energies",
             f"# spike_z: {settings.k:g}" if settings.despike else "# spike_z: off",
             "# processing: per-trace de-spike interpolation only; no I0 division; no normalization",
             f"# reduced_with: AURORA {__version__}",
             f"# despike: {ds}" + (f" ({spikes} interpolated)" if spikes else (" (no spikes found)" if settings.despike else "")),
             f"# detectors_in_total: {', '.join(fr.dets_in_total) or 'none'}",
             f"# zone: {zone.label} (holder {holder or '?'}, zone id {zone.id}"
             + (f", group {zone.group}" if zone.group else "") + f"); AURORA stack id {sid}",
             f"# data_source: {fr.source}"]
    if fr.i0_note:
        lines.append(f"# i0_source: {fr.i0_note}")
    if fr.low_ev:
        lines.append(f"# i0_masked: {_ev(fr.low_ev)} eV (I0 below {I0_LOW_FRAC:.0%} of its median: I0 left empty)")
    if fr.incomplete_ev:
        lines.append(f"# incomplete: {_ev(fr.incomplete_ev)} eV (a detector or I0 missing: sdd_total and sdd_mean left empty)")
    if getattr(settings, "signal", "pfy") == "ipfy":
        lines.append(f"# signal: the zone uses IPFY (I0 / {settings.line or stack.main}); this file holds the counts, so "
                     "sdd_total / I0 read from it is the PFY of that line")
    return lines


def write_john(zone: Zone, sid: str, stack, settings: ScanSettings, out_dir=None, label: str | None = None, *,
               project=None, scan=None, survey_h5=None, mode: str = "mean", overwrite: bool = False,
               good_curve: np.ndarray | None = None, holder: str = "", frame: JohnFrame | None = None,
               progress=None) -> list[Path]:
    """Write the zone x scan as John Bussey's GUI saves a checked scan, into `out_dir` (default
    <project>/processed/reduced/<label>/). Returns the written paths: _full_reduced.csv, _absorption.csv,
    _polygon_roi.json (one polygon or one rectangle; otherwise _zone.json, which says so) and _gui_settings.json.
    FileExistsError when a target exists and overwrite is False."""
    from aurora import __version__
    from aurora.core.io.textio import write_json_atomic, write_text_atomic
    label = label or zone.label or f"zone {zone.id}"
    if out_dir is None:
        if project is None:
            raise ValueError("write_john needs out_dir or project")
        out_dir = default_folder(project, label)
    out_dir = Path(out_dir)
    _check_folder(out_dir, scan)
    targets = target_paths(out_dir, label, zone, getattr(stack, "pitch", None))
    if not overwrite:
        there = [p for p in targets if p.exists()]
        if there:
            raise FileExistsError(f"{there[0]} already exists")
    fr = frame or john_frame(zone, sid, stack, settings, project=project, scan=scan, mode=mode, good_curve=good_curve,
                             progress=progress)
    head = header_lines(fr, label=label, zone=zone, sid=sid, settings=settings, stack=stack, scan=scan,
                        survey_h5=survey_h5, holder=holder)
    rows, rows_abs = [], []
    for i, e in enumerate(fr.E):
        cells = [_num(e)]
        for j, d in enumerate(DETS):
            w = fr.windows.get(d)
            cells += [_num(fr.det[i, j]), str(w[0]) if w else "", str(w[1]) if w else ""]
        bad = "True" if fr.bad[i] else "False"
        cells += [_num(fr.i0[i]), _num(fr.tey[i]), _num(fr.total[i]), _num(fr.mean[i]), bad]
        rows.append(",".join(cells))
        rows_abs.append(",".join([_num(e), _num(fr.total[i]), _num(fr.tey[i]), _num(fr.i0[i]), bad]))
    full, absorb, roi, gui = targets
    out_dir.mkdir(parents=True, exist_ok=True)
    write_text_atomic(full, "\n".join(head + [",".join(JOHN_COLUMNS)] + rows) + "\n")
    write_text_atomic(absorb, "\n".join(head + [",".join(ABSORPTION_COLUMNS)] + rows_abs) + "\n")
    poly = roi_polygon(zone, getattr(stack, "pitch", None))
    if poly is not None:
        write_json_atomic(roi, poly, indent=2)
    else:
        write_json_atomic(roi, {"_comment": "This zone is not one polygon or one rectangle, so John Bussey's GUI cannot "
                                            "read it as a _polygon_roi.json. AURORA's zone definition follows "
                                            "(rectangles, erased rectangles, painted and erased squares, polygons; mm).",
                                "zone": {k: v for k, v in zone.to_json().items() if k not in ("st", "members")}},
                          indent=2)
    rois = {d: [w[0], w[1]] for d, w in fr.windows.items() if w is not None}
    write_json_atomic(gui, {
        "rois": rois,
        "params": {"i0": MCC_NAMES[I0_COL], "mode": fr.mode, "tey": MCC_NAMES[TEY_COL]},
        "stack_h5": str(getattr(scan, "h5", "") or "") if scan is not None else "",
        "survey_h5": str(survey_h5 or ""),
        "spike_z": float(settings.k) if settings.despike else None,
        "drop_energy_ranges": "",
        "drop_indices": "",
        "aurora": {"version": __version__, "sid": sid, "holder": holder,
                   "zone": {"id": zone.id, "label": zone.label, "group": zone.group, "comment": zone.comment},
                   "settings": settings.to_json(), "roi_points": [fr.n, fr.N],
                   "despike_mode": getattr(settings, "despike_mode", "classic"),
                   "spikes_eV": fr.spikes_ev, "i0_masked_eV": fr.low_ev, "incomplete_eV": fr.incomplete_ev,
                   "detectors_in_total": fr.dets_in_total, "data_source": fr.source,
                   "polygon": ("the zone's polygon" if zone.polys else
                               "the rectangle widened by 0.45 pitch (AURORA's membership rule)") if poly is not None else None},
    }, indent=2)
    log.info("zone %s x %s: wrote %s", zone.label, sid, ", ".join(p.name for p in targets))
    return targets
