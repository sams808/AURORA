"""Per-detector XRF emission windows: step 2 of John Bussey's GUI.

A window is a slice of SDD channels [lo, hi), one per detector, hi exclusive (John's convention: '60, 83' is the
slice 60:83, channels 60 to 82). ScanSettings.roi = {'sdd1': [lo, hi], ...} selects windows for a zone x scan; without
it the fixed line bins of config/roi_bins.json (the reduced stack) are used.

Reading the windows needs the raw spectra, which the reduced stacks do not keep. Two passes over the raw energy
folders of a stack exist, each run once (in a worker) and cached under <root>/.aurora/cache/<day>/<scan>/:
- read_emission: the emission spectrum of every pixel summed over the valid energies (n_pix, 4, 256), behind the
  step-2 plot (stack_emission, 'inside the region') and the window maps that follow a drag;
- window_counts: the counts in one set of windows for every energy and pixel (nE, n_pix, 4), behind the zone sums
  (window_sums), so that redrawing a region never reads the raw folders again.
A detector map cut short keeps the pixels it recorded; a pixel it did not record (or a missing file) is NaN for that
detector at that energy, as in John Bussey's GUI. (The reduced stack is stricter: an incomplete map leaves the whole
energy out, aurora.core.stack, so its I0 and TEY are NaN there.)

The raw folders of a StackData are found through register_project (the application registers every project it
opens), attach(stack, scan, root), or the cache path the stack was read from.
"""
from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np

from aurora.core.constants import DETS, N_CH, N_SIGMA
from aurora.core.io.cache import load_npz, save_npz
from aurora.core.io.raw import stack_energy_dirs, stack_last_mtime

log = logging.getLogger("aurora.xrf_windows")

# sgm_report v2.5 RoiBins.ensure: the peak width used when no Gaussian could be fitted ("a typical 3.5 channels")
SIGMA_CH_TYPICAL = 3.5
TOTAL_LABEL = "total XRF"
TOTAL_BOUNDS = (5, 200)                 # aurora.core.stack.TOTAL_BOUNDS (channels 5:200)
JOHN_DEFAULT = (60, 83)                 # John Bussey's GUI, step 2: every detector 60:83 (channels 60 to 82)
WINDOW_LABEL = "XRF window"             # the line name of a window map (StackData view)
EMISSION_FILE = "_emission_cache.npz"
WINDOWS_DIR = "_xrf_windows"
_LINE_FAMILY = {"α": "a", "β": "b", "γ": "g"}


# ----------------------------------------------------------------------------- windows
def normalise_windows(roi) -> dict[str, list[int]]:
    """{sdd: [lo, hi]} with integer channels clipped to 0..256 and lo < hi, for the four detectors. A single [lo, hi]
    applies to all four; a detector missing from the dict takes the first one given."""
    if roi is None:
        raise ValueError("no windows")
    if isinstance(roi, (list, tuple)) and len(roi) == 2 and not isinstance(roi[0], (list, tuple)):
        roi = {d: roi for d in DETS}
    roi = {str(k).lower(): v for k, v in dict(roi).items()}
    first = next((roi[d] for d in DETS if d in roi), None)
    if first is None:
        raise ValueError(f"no detector window in {roi!r}")
    out = {}
    for d in DETS:
        lo, hi = roi.get(d, first)
        lo, hi = int(round(float(lo))), int(round(float(hi)))
        lo, hi = max(0, min(lo, hi)), min(N_CH, max(lo, hi))
        if hi <= lo:
            hi = min(N_CH, lo + 1)
            lo = hi - 1
        out[d] = [lo, hi]
    return out


def same_for_all(windows) -> bool:
    w = normalise_windows(windows)
    return all(w[d] == w[DETS[0]] for d in DETS)


def describe(windows) -> str:
    """'60:74 ch' when the four detectors share a window, else 'SDD1 60:74, SDD2 65:80, SDD3 62:76, SDD4 59:74 ch'
    (slice notation: hi exclusive)."""
    w = normalise_windows(windows)
    if same_for_all(w):
        lo, hi = w[DETS[0]]
        return f"{lo}:{hi} ch"
    return ", ".join(f"SDD{i + 1} {w[d][0]}:{w[d][1]}" for i, d in enumerate(DETS)) + " ch"


def key(windows) -> str:
    w = normalise_windows(windows)
    return "w" + "_".join(f"{w[d][0]}-{w[d][1]}" for d in DETS)


def same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return normalise_windows(a) == normalise_windows(b)


def bins_windows(roi_bins, label: str) -> dict[str, list[int]] | None:
    """The fixed windows of a line: roi_bins.json, or 'total XRF' (channels 5:200); None when unknown."""
    if label == TOTAL_LABEL:
        return {d: list(TOTAL_BOUNDS) for d in DETS}
    if roi_bins is not None and roi_bins.has(label):
        return {d: [int(a), int(b)] for d, (a, b) in roi_bins.bounds(label).items()}
    return None


def label_for_windows(windows, roi_bins, labels=None) -> str | None:
    """The fixed line whose bins are exactly these windows (among `labels` when given), None when none is."""
    if windows is None:
        return None
    w = normalise_windows(windows)
    cands = list(labels) if labels is not None else (list(roi_bins.labels) if roi_bins is not None else []) + [TOTAL_LABEL]
    for lab in cands:
        b = bins_windows(roi_bins, lab)
        if b is not None and b == w:
            return lab
    return None


def emission_line_ev(label: str) -> float | None:
    """Energy (eV) of an emission line label such as 'F Kα' or 'Mn Lα', from xraydb: the intensity-weighted mean of
    the line family (Ka1, Ka2, ...). None when xraydb does not know it."""
    m = re.match(r"^\s*([A-Z][a-z]?)\s+([KLM])([αβγ])\s*$", label or "")
    if not m:
        return None
    el, shell, greek = m.groups()
    fam = shell + _LINE_FAMILY[greek]
    try:
        import xraydb
        lines = {k: v for k, v in xraydb.xray_lines(el).items() if k.startswith(fam)}
    except Exception:
        return None
    if not lines:
        return None
    e = np.array([float(v.energy) for v in lines.values()])
    w = np.array([float(v.intensity) for v in lines.values()])
    return float((e * w).sum() / w.sum()) if w.sum() > 0 else float(e.mean())


@dataclass
class EdgeLine:
    label: str                       # 'F Kα'
    line_ev: float                   # xraydb
    windows: dict[str, list[int]]
    sigma_ch: float
    n_sigma: float
    sigma_source: str                # where the peak width came from
    nominal: bool = False            # the channels come from the nominal 10 eV per channel, not a calibration


def edge_line_windows(edge, calibration, roi_bins=None, nominal: bool = False) -> EdgeLine | None:
    """The preset 'line of the edge': the resonant emission line of the edge (xraydb) at each detector's calibrated
    channel c, floor(c - n_sigma σ) to ceil(c + n_sigma σ) in that detector's channels: the rule of sgm_report's
    fixed bins (RoiBins.ensure), so a line of roi_bins.json gets its own bins back. σ is the line's own fitted width in
    roi_bins.json when it has one, else the median width of the other fitted lines, else sgm_report's typical 3.5
    channels; n_sigma is roi_bins' (1.5). None without a calibration (the channels would be nominal) or an edge;
    nominal=True gives it anyway, on the calibration's nominal gains (UNCALIBRATED_EV_PER_CH per channel for every
    detector without a valid entry), marked `nominal` (Reduce: 'nominal calibration, check the peak')."""
    if edge is None:
        return None
    calibrated = calibration is not None and getattr(calibration, "calibrated", False)
    if not calibrated and not nominal:
        return None
    if calibration is None:
        from aurora.core.io.config import Calibration
        calibration = Calibration()
    from aurora.core.lines import resonant_line
    label = resonant_line(*edge)
    ev = emission_line_ev(label)
    if ev is None:
        return None
    # the fitted widths of the file as it was read (RoiBins.raw): a line kept out for its bins still has its width
    data = getattr(roi_bins, "raw", None) or getattr(roi_bins, "data", {}) or {}

    def width(v):
        w = v.get("sigma_ch") if isinstance(v, dict) else None
        return float(w) if isinstance(w, (int, float)) and not isinstance(w, bool) and np.isfinite(w) and w > 0 \
            else None
    meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
    ns = meta.get("n_sigma")
    n_sigma = float(ns) if isinstance(ns, (int, float)) and not isinstance(ns, bool) and np.isfinite(ns) and ns > 0 \
        else N_SIGMA
    own = width(data.get(label))
    others = [w for k, v in data.items() if not str(k).startswith("_") for w in [width(v)] if w is not None]
    if own is not None:
        sig, src = float(own), f"the fitted width of {label} in roi_bins.json"
    elif others:
        sig, src = float(np.median(others)), "the median fitted width of the lines in roi_bins.json"
    else:
        sig, src = SIGMA_CH_TYPICAL, "sgm_report's typical width (3.5 channels)"
    windows = {}
    for d in DETS:
        c = float(calibration.ch(d, ev))
        windows[d] = [int(max(0, np.floor(c - n_sigma * sig))), int(min(N_CH, np.ceil(c + n_sigma * sig)))]
    return EdgeLine(label=label, line_ev=ev, windows=normalise_windows(windows), sigma_ch=sig, n_sigma=n_sigma,
                    sigma_source=src, nominal=not calibrated)


def john_default_windows(edge) -> tuple[str, dict[str, list[int]]] | None:
    """John Bussey's default step-2 window, JOHN_DEFAULT on every detector, and the resonant line of the edge it holds
    at the nominal 10 eV per channel (F K: F Kα, 676.8 eV, channel 67.7): (line label, windows). None when the window
    does not hold that line (O Kα, 524.9 eV, is channel 52)."""
    if edge is None:
        return None
    from aurora.core.constants import UNCALIBRATED_EV_PER_CH
    from aurora.core.lines import resonant_line
    label = resonant_line(*edge)
    ev = emission_line_ev(label)
    if ev is None or not JOHN_DEFAULT[0] * UNCALIBRATED_EV_PER_CH <= ev < JOHN_DEFAULT[1] * UNCALIBRATED_EV_PER_CH:
        return None
    return label, {d: list(JOHN_DEFAULT) for d in DETS}


def channel_ev(calibration, det: str, ch: float) -> float:
    """eV of a channel edge for one detector (nominal 10 eV per channel without a calibration file)."""
    if calibration is None:
        from aurora.core.constants import UNCALIBRATED_EV_PER_CH
        return float(ch) * UNCALIBRATED_EV_PER_CH
    return float(calibration.ev(det, ch))


# ----------------------------------------------------------------------------- where the raw spectra are
@dataclass
class RawSource:
    """The raw energy folders of one stack and the folder its window caches go to (None: memory only)."""
    scan: object                                  # aurora.core.model.ScanInfo
    cache_dir: Path | None

    @property
    def energy_dirs(self) -> list[tuple[float, Path]]:
        ed = getattr(self.scan, "energy_dirs", None)
        return list(ed) if ed else stack_energy_dirs(self.scan.path)


_LOCK = threading.RLock()
_PROJECTS: list = []                              # projects the application opened, most recent first
_MAX_PROJECTS = 6


def cache_dir_of(scan, root) -> Path | None:
    """<root>/.aurora/cache/<day>/<scan> (the folder of AURORA's reduced cache), None without a root."""
    if root is None:
        return None
    return Path(root) / ".aurora" / "cache" / (getattr(scan, "day", "") or "") / scan.name


def register_project(project) -> None:
    """Remember a project, so the raw folders of its stacks are found from a StackData (zone_sums, reports)."""
    if project is None:
        return
    with _LOCK:
        for p in list(_PROJECTS):
            if p is project or Path(p.root) == Path(project.root):
                _PROJECTS.remove(p)
        _PROJECTS.insert(0, project)
        del _PROJECTS[_MAX_PROJECTS:]


def attach(stack, scan, root=None, cache_dir: Path | None = None) -> RawSource:
    """Tie a StackData to its raw scan folder; caches go to `cache_dir` (or <root>/.aurora/cache/<day>/<scan>)."""
    src = RawSource(scan=scan, cache_dir=Path(cache_dir) if cache_dir is not None else cache_dir_of(scan, root))
    stack._xrf_source = src
    return src


def _cache_root(stack) -> Path | None:
    """The project root a StackData was read under, from its cache path ('sgm_report cache <root>/processed/...',
    'sgm_report cache with other windows <root>/processed/...' or 'aurora cache <root>/.aurora/cache/...'); None for a
    stack reduced from raw just now."""
    m = re.search(r"cache (?:with other windows )?(.+_reduced_cache\.npz)\s*$", str(getattr(stack, "source", "")))
    if not m:
        return None
    parts = Path(m.group(1)).parts
    if ".aurora" in parts:
        return Path(*parts[:parts.index(".aurora")])
    if "processed" in parts:
        return Path(*parts[:len(parts) - 1 - parts[::-1].index("processed")])
    return None


def _same_path(a, b) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


def source_of(stack) -> RawSource | None:
    """The raw folders of a StackData: attached; else found in a registered project (the one its cache path names,
    else the most recently registered one that has the scan with the same number of energies); else found next to
    its cache path. None for a stitched stack or when nothing matches."""
    src = getattr(stack, "_xrf_source", None)
    if isinstance(src, RawSource):
        return src
    if str(getattr(stack, "id", "")).startswith("stitched_"):
        return None
    name, day = stack.name, getattr(stack, "day", "") or ""
    root = _cache_root(stack)
    with _LOCK:
        projects = list(_PROJECTS)
    if root is not None:
        projects = [p for p in projects if _same_path(p.root, root)]
    for proj in projects:
        scan = next((s for s in proj.scans if s.name == name and (s.day or "") == day
                     and (not s.energy_dirs or len(s.energy_dirs) == stack.n_energies)), None)
        if scan is not None:
            return attach(stack, scan, proj.root)
    if root is None:
        return None
    for raw in (root / "raw" / day / name, root / day / name, root / name):
        if raw.is_dir():
            from aurora.core.model import ScanInfo
            return attach(stack, ScanInfo.from_dir(raw, day=day), root)
    return None


def forget_projects() -> None:
    """Forget every registered project (tests)."""
    with _LOCK:
        _PROJECTS.clear()


# ----------------------------------------------------------------------------- caches
class _Lru:
    def __init__(self, size: int):
        self.size = size
        self.d: OrderedDict = OrderedDict()

    def get(self, k):
        with _LOCK:
            if k in self.d:
                self.d.move_to_end(k)
                return self.d[k]
        return None

    def put(self, k, v) -> None:
        with _LOCK:
            self.d[k] = v
            self.d.move_to_end(k)
            while len(self.d) > self.size:
                self.d.popitem(last=False)

    def clear(self) -> None:
        with _LOCK:
            self.d.clear()


_EMISSION = _Lru(4)
_WINDOWS = _Lru(12)


def clear_memory() -> None:
    """Forget the in-memory caches (the disk caches stay)."""
    _EMISSION.clear()
    _WINDOWS.clear()


def _signature(kind: str, edirs, n_pix: int, extra: str = "") -> str:
    return f"{kind}|{len(edirs)}|{n_pix}|{stack_last_mtime(edirs):.0f}|{extra}"


def _n_pix(scan, n_pix: int | None) -> int:
    if n_pix:
        return int(n_pix)
    from aurora.core.io.h5 import find_h5, xy
    h5 = getattr(scan, "h5", None) or find_h5(scan.path)
    if h5 is None:
        raise FileNotFoundError(f"{scan.path}: no .h5 file")
    return int(len(xy(h5)[0]))


def _load_disk(path: Path | None, sig: str):
    z = load_npz(path) if path is not None else None
    if z is None:
        return None
    try:
        with z:                                    # closed at once: the rebuilt cache replaces this file (Windows)
            if str(z["signature"]) != sig:
                return None
            return {k: z[k] for k in z.files}
    except Exception:                              # a damaged file or a member that needs pickle: read again from raw
        return None


def _save_disk(path: Path | None, **arrays) -> None:
    if path is None:
        return
    try:
        save_npz(path, **arrays)
    except OSError as err:                          # a protected folder or a full disk: memory only
        log.info("%s not written (%s)", path, err)


def _read_sdds(folder: Path, n_pix: int) -> list:
    """The four SDD spectra files of one energy folder: (rows, 256) uint32 each with rows <= n_pix (a map cut short
    keeps the pixels it recorded), or None for a detector whose file is missing, unreadable or empty."""
    out = []
    for det in DETS:
        try:
            a = np.fromfile(Path(folder) / f"{det}_0.bin", dtype=np.uint32)
        except (OSError, ValueError):
            out.append(None)
            continue
        rows = min(len(a) // N_CH, n_pix)
        out.append(a[:rows * N_CH].reshape(rows, N_CH) if rows > 0 else None)
    return out


def _raw_pass(edirs, n_pix: int, name: str, progress, per_energy) -> np.ndarray:
    """Read the four SDD files of every energy folder once; per_energy(i, k, spectra) is called for each detector k
    with the rows its file holds. Returns rows (nE, 4): the pixels recorded per detector map. A pixel a map did not
    record is NaN for that detector at that energy; the other detectors and pixels keep their counts (John Bussey's
    GUI reads the maps the same way and leaves a missing detector's cell empty)."""
    nE = len(edirs)
    rows = np.zeros((nE, len(DETS)), np.int64)
    short = []
    for i, (e, p) in enumerate(edirs):
        if progress:
            progress(i, nE, f"{name} {e:.2f} eV")
        for k, spec in enumerate(_read_sdds(p, n_pix)):
            if spec is None:
                short.append(f"{e} eV {DETS[k].upper()} missing")
                continue
            if spec.shape[0] < n_pix:
                short.append(f"{e} eV {DETS[k].upper()} {spec.shape[0]}/{n_pix} points")
            per_energy(i, k, spec)
            rows[i, k] = spec.shape[0]
    if short:
        log.info("%s: %d detector map(s) short or missing: %s", name, len(short), "; ".join(short[:6]))
    return rows


# ----------------------------------------------------------------------------- emission spectra
@dataclass
class Emission:
    """Per-pixel emission spectra of a stack summed over its valid energies."""
    E: np.ndarray                       # (nE,)
    rows: np.ndarray                    # (nE, 4) pixels recorded per detector map
    per_pix: np.ndarray                 # (n_pix, 4, 256) float64 counts
    name: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def n_pix(self) -> int:
        return int(self.per_pix.shape[0])

    @property
    def valid(self) -> np.ndarray:
        """(nE, 4) the detector maps recorded in full."""
        return self.rows >= self.n_pix

    def spectrum(self, dets=DETS, pix=None) -> np.ndarray:
        """(len(dets), 256) summed over the pixels (all when None)."""
        idx = [DETS.index(d) if isinstance(d, str) else int(d) for d in dets]
        a = self.per_pix if pix is None else self.per_pix[np.asarray(pix, int)]
        return a[:, idx, :].sum(axis=0)

    def window_map(self, windows, dets=DETS) -> np.ndarray:
        """(n_pix,) counts in the windows summed over the valid energies and the given detectors."""
        w = normalise_windows(windows)
        out = np.zeros(self.n_pix)
        for d in dets:
            k = DETS.index(d) if isinstance(d, str) else int(d)
            lo, hi = w[DETS[k]]
            out += self.per_pix[:, k, lo:hi].sum(axis=1)
        return out


def read_emission(scan, n_pix: int | None = None, progress=None, root=None, cache_dir: Path | None = None) -> Emission:
    """The emission spectra of every pixel of a stack, summed over its valid energies: one pass over the raw energy
    folders, cached (memory, and <root>/.aurora/cache/<day>/<scan>/_emission_cache.npz)."""
    edirs = list(getattr(scan, "energy_dirs", None) or stack_energy_dirs(scan.path))
    n = _n_pix(scan, n_pix)
    cdir = Path(cache_dir) if cache_dir is not None else cache_dir_of(scan, root)
    sig = _signature("emission", edirs, n)
    mkey = (str(scan.path), sig)
    hit = _EMISSION.get(mkey)
    if hit is not None:
        return hit
    E = np.array([e for e, _ in edirs], float)
    path = cdir / EMISSION_FILE if cdir is not None else None
    disk = _load_disk(path, sig)
    if disk is not None and disk["per_pix"].shape == (n, len(DETS), N_CH) and disk["rows"].shape == (len(edirs), len(DETS)):
        em = Emission(E=E, rows=disk["rows"].astype(np.int64), per_pix=disk["per_pix"].astype(float), name=scan.name)
        _EMISSION.put(mkey, em)
        return em
    acc = np.zeros((n, len(DETS), N_CH), np.float64)

    def add(_i, k, spec):
        acc[:spec.shape[0], k, :] += spec
    rows = _raw_pass(edirs, n, scan.name, progress, add)
    em = Emission(E=E, rows=rows, per_pix=acc, name=scan.name)
    if not em.valid.all():
        em.notes.append(f"{int((~em.valid).sum())} short or missing detector map(s): their recorded pixels only")
    _save_disk(path, signature=sig, per_pix=acc, rows=rows)
    _EMISSION.put(mkey, em)
    return em


def stack_emission(scan, dets=DETS, pix=None, progress=None, root=None, n_pix: int | None = None,
                   cache_dir: Path | None = None) -> np.ndarray:
    """The per-detector emission spectrum of a stack, (len(dets), 256) counts summed over its valid energies and over
    the given pixels (all when None). The raw folders are read once (read_emission)."""
    return read_emission(scan, n_pix, progress, root, cache_dir).spectrum(dets, pix)


# ----------------------------------------------------------------------------- window counts and sums
@dataclass
class WindowCounts:
    E: np.ndarray                       # (nE,)
    rows: np.ndarray                    # (nE, 4) pixels recorded per detector map
    counts: np.ndarray                  # (nE, n_pix, 4) float64, NaN for the pixels a map did not record
    windows: dict[str, list[int]]

    @property
    def n_pix(self) -> int:
        return int(self.counts.shape[1])

    @property
    def valid(self) -> np.ndarray:
        """(nE, 4) the detector maps recorded in full."""
        return self.rows >= self.n_pix


def _blank(counts: np.ndarray, rows: np.ndarray) -> None:
    """NaN in the (nE, n_pix, 4) counts for every pixel a detector map did not record."""
    n = counts.shape[1]
    for i, k in zip(*np.where(rows < n)):
        counts[i, int(rows[i, k]):, k] = np.nan


def _window_path(cdir: Path | None, windows) -> Path | None:
    return cdir / WINDOWS_DIR / f"{key(windows)}.npz" if cdir is not None else None


def cached_window_counts(scan, windows, root=None, n_pix: int | None = None,
                         cache_dir: Path | None = None) -> WindowCounts | None:
    """The window counts when they are in memory or on disk; None when the raw folders would have to be read."""
    w = normalise_windows(windows)
    edirs = list(getattr(scan, "energy_dirs", None) or stack_energy_dirs(scan.path))
    n = _n_pix(scan, n_pix)
    sig = _signature("windows", edirs, n, key(w))
    mkey = (str(scan.path), sig)
    hit = _WINDOWS.get(mkey)
    if hit is not None:
        return hit
    cdir = Path(cache_dir) if cache_dir is not None else cache_dir_of(scan, root)
    disk = _load_disk(_window_path(cdir, w), sig)
    if disk is None or disk["counts"].shape != (len(edirs), n, len(DETS)) or disk["rows"].shape != (len(edirs), len(DETS)):
        return None
    rows = disk["rows"].astype(np.int64)
    counts = disk["counts"].astype(np.float64)
    _blank(counts, rows)
    wc = WindowCounts(E=np.array([e for e, _ in edirs], float), rows=rows, counts=counts, windows=w)
    _WINDOWS.put(mkey, wc)
    return wc


def window_counts(scan, windows, progress=None, root=None, n_pix: int | None = None,
                  cache_dir: Path | None = None) -> WindowCounts:
    """Counts in channels [lo, hi) of each SDD, for every energy and pixel of a stack: one pass over the raw folders,
    cached (memory, and <root>/.aurora/cache/<day>/<scan>/_xrf_windows/<windows>.npz)."""
    w = normalise_windows(windows)
    hit = cached_window_counts(scan, w, root, n_pix, cache_dir)
    if hit is not None:
        return hit
    edirs = list(getattr(scan, "energy_dirs", None) or stack_energy_dirs(scan.path))
    n = _n_pix(scan, n_pix)
    sig = _signature("windows", edirs, n, key(w))
    raw = np.zeros((len(edirs), n, len(DETS)), np.uint64)

    def add(i, k, spec):
        lo, hi = w[DETS[k]]
        raw[i, :spec.shape[0], k] = spec[:, lo:hi].sum(axis=1, dtype=np.uint64)
    rows = _raw_pass(edirs, n, scan.name, progress, add)
    cdir = Path(cache_dir) if cache_dir is not None else cache_dir_of(scan, root)
    store = raw.astype(np.uint32) if raw.max(initial=0) < 2 ** 32 else raw
    _save_disk(_window_path(cdir, w), signature=sig, counts=store, rows=rows)
    counts = raw.astype(np.float64)
    _blank(counts, rows)
    wc = WindowCounts(E=np.array([e for e, _ in edirs], float), rows=rows, counts=counts, windows=w)
    _WINDOWS.put((str(scan.path), sig), wc)
    return wc


def window_sums(scan, pix, windows, progress=None, root=None, n_pix: int | None = None,
                cache_dir: Path | None = None) -> np.ndarray:
    """Per-energy sums over the pixels `pix` of channels [lo, hi) for each SDD: (nE, 4). NaN where a detector map did
    not record one of the pixels (and everywhere for an empty pixel list). hi is exclusive, John Bussey's convention
    ('60, 83' == 60:83)."""
    wc = window_counts(scan, windows, progress, root, n_pix, cache_dir)
    pix = np.asarray(pix, int)
    if pix.size == 0:
        return np.full((len(wc.E), len(DETS)), np.nan)
    return wc.counts[:, pix, :].sum(axis=1)


# ----------------------------------------------------------------------------- the same on a StackData
def _stack_source(stack) -> RawSource:
    src = source_of(stack)
    if src is None:
        raise FileNotFoundError(f"the raw spectra of {stack.name} are not known (a stitched stack, or a project not "
                                "registered)")
    n = len(src.energy_dirs)
    if n != stack.n_energies:
        raise ValueError(f"{stack.name}: {n} raw energy folders for {stack.n_energies} reduced energies")
    return src


def stack_window_counts(stack, windows, progress=None) -> WindowCounts:
    src = _stack_source(stack)
    return window_counts(src.scan, windows, progress, n_pix=stack.n_pix, cache_dir=src.cache_dir)


def stack_window_ready(stack, windows) -> bool:
    """True when the window counts of a stack are available without reading the raw folders (or cannot be read)."""
    src = source_of(stack)
    if src is None:
        return True                                 # nothing to read: zone_sums falls back with a note
    try:
        return cached_window_counts(src.scan, windows, n_pix=stack.n_pix, cache_dir=src.cache_dir) is not None
    except (OSError, ValueError):
        return True


def stack_window_sums(stack, pix, windows, progress=None) -> np.ndarray:
    """window_sums for a StackData (its raw folders found through source_of; its own pixel count)."""
    wc = stack_window_counts(stack, windows, progress)
    pix = np.asarray(pix, int)
    if pix.size == 0:
        return np.full((stack.n_energies, len(DETS)), np.nan)
    return wc.counts[:, pix, :].sum(axis=1)


def stack_emission_data(stack, progress=None) -> Emission:
    src = _stack_source(stack)
    return read_emission(src.scan, stack.n_pix, progress, cache_dir=src.cache_dir)


def cached_stack_emission(stack) -> Emission | None:
    """The stack's emission spectra when already read in this session (memory only), else None."""
    src = source_of(stack)
    if src is None:
        return None
    try:
        edirs = src.energy_dirs
        hit = _EMISSION.get((str(src.scan.path), _signature("emission", edirs, stack.n_pix)))
    except OSError:
        return None
    return hit


def window_view(stack, wc: WindowCounts):
    """A copy of the StackData whose only line is the window counts (WINDOW_LABEL), so StackData.map_values draws the
    window map with the stack's own rules (above the edge, at one energy, / I0, ...)."""
    return replace(stack, roi_pix_det=wc.counts[:, :, None, :], labels=[WINDOW_LABEL], main=WINDOW_LABEL)
