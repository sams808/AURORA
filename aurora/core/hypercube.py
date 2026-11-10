"""The full hypercube of a stack: (energy, pixel, channel) counts per detector, for the Maps perspective
(CLS SGM_Beamline_Analysis dashboard parity): maps of any channel window at any energy, ROI spectra, elemental maps,
RGB composites, pixel PCA + k-means, PyMca HDF5 exports.

Emission windows are per detector. The four SDDs have their own gain and offset (HANDOFF 2.3), so one eV window is a
different channel range on each of them: 640-710 eV is channels 63-71 on SDD1 but 68-76 on SDD2. Every map, spectrum,
matrix and export therefore takes `windows` = {det index: (lo, hi)} (channels, hi exclusive), or a function
det -> (lo, hi) such as functools.partial(hc.window_channels, e_lo=640, e_hi=710); hc.ev_windows(640, 710) gives the
dict. The older form (det, lo, hi), one detector or the four (None) with the same channels, is still accepted.

The cube is built on disk, never in RAM: Hypercube.load streams the raw energy folders one at a time into
memory-mapped .npy files (uint16 while the counts allow, else uint32) inside a '.building-<time>' folder, writes the
auxiliary arrays and cube_meta.json last, then renames the folder in one step to

    <root>/.aurora/cache/<day>/<scan>/hypercube-<key>/
        cube_sdd1.npy .. cube_sdd4.npy   (n_energies, n_pix, 256) counts
        xrf_total.npy                    (4, n_pix, 256) counts summed over the valid energies
        bins_<hash>.npy                  (n_bins, 4, n_energies, n_pix) counts in each fixed ROI bin (roi_bins.json)
        cube_aux.npz                     i0, tey, valid, spec_all (4, n_energies, 256) whole-map spectra
        cube_meta.json                   key, skipped energies, dtype, bins

The derived sums are filled while each energy is in memory, so line windows, elemental maps, whole-map spectra and
energy averages never read the cube again. A project folder that cannot be written (a protected beamtime, a read-only
share) gets its cube in the per-user cache instead ($AURORA_CACHE_DIR, else the platform cache folder). The disk space
is checked before anything is written; a cancel (the progress callback raises Cancelled) or a failure removes the
temporary folder. A 0.3 cache (cube_sddN.npy directly in the scan folder) is converted without reading the raw data.
"""
from __future__ import annotations

import datetime as dt
import gc
import hashlib
import json
import logging
import os
import shutil
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from aurora.core.constants import DETS, N_CH, I0_COL, TEY_COL
from aurora.core.io.config import Calibration
from aurora.core.io.h5 import find_h5, xy
from aurora.core.io.raw import read_point_dir, stack_energy_dirs, stack_last_mtime
from aurora.core.model import Project, ScanInfo
from aurora.core.zones import grid_pitch

log = logging.getLogger("aurora.hypercube")

LAYOUT = 2                                  # cache layout (1 = AURORA 0.3: in RAM, then one .npy per detector)
CUBE_PREFIX = "hypercube-"                  # final cache folder: hypercube-<12 hex of the key>
BUILD_PREFIX = ".building-"                 # temporary folder while a cube is written
STALE_BUILD_S = 3600.0                      # a temporary folder untouched this long is a crashed build: removed
U16_MAX = int(np.iinfo(np.uint16).max)
CACHE_ENV = "AURORA_CACHE_DIR"              # per-user cache root for projects whose own folder is read-only
REPLACE_RETRIES = 6                         # os.replace attempts on Windows (an indexer may hold a new file)
EXPORT_BLOCK = 64                           # HDF5 chunk edge (pixels) of the PyMca exports


class CacheSpaceError(OSError):
    """Not enough free disk space for the cube: 'needs 4.1 GB in <folder>, 2.0 GB free'. Nothing was written."""


# a damaged cache file (as in io/cache.py): a cut or garbled .npy, a cut .npz (BadZipFile), a broken member (Bad CRC-32,
# zlib.error, EOFError), a mapped file shorter than its header says (OSError on Windows, ValueError elsewhere)
_DAMAGE = (OSError, ValueError, KeyError, EOFError, zipfile.BadZipFile, zlib.error)


# ----------------------------------------------------------------------------- small helpers
def _fmt_bytes(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB"


def _det_index(k) -> int:
    if isinstance(k, (int, np.integer)):
        if not 0 <= int(k) < len(DETS):
            raise ValueError(f"no detector {k}")
        return int(k)
    s = str(k).strip().lower()
    if s in DETS:
        return DETS.index(s)
    raise ValueError(f"no detector {k!r}")


def windows_key(windows: dict[int, tuple[int, int]]) -> tuple:
    """A hashable form of a windows dict (cache keys)."""
    return tuple(sorted((int(d), int(lo), int(hi)) for d, (lo, hi) in windows.items()))


def describe_windows(windows: dict[int, tuple[int, int]]) -> str:
    """'SDD1 ch 63-70 · SDD2 ch 68-75' (inclusive channel numbers) for a windows dict."""
    parts = [f"SDD{d + 1} ch {lo}-{hi - 1}" for d, (lo, hi) in sorted(windows.items())]
    return " · ".join(parts) if parts else "no SDD"


def bins_spec(bins) -> list[tuple[str, dict[int, tuple[int, int]]]]:
    """[(label, {det: (lo, hi)})] for every complete fixed ROI bin of a RoiBins (empty for None)."""
    if bins is None:
        return []
    out = []
    for lab in bins.labels:
        if bins.has(lab):
            b = bins.bounds(lab)
            out.append((lab, {d: (int(b[DETS[d]][0]), int(b[DETS[d]][1])) for d in range(len(DETS))}))
    return out


def bins_hash(spec) -> str:
    text = json.dumps([[lab, [list(w[d]) for d in range(len(DETS))]] for lab, w in spec], ensure_ascii=False)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def cube_bytes(n_energies: int, n_pix: int, itemsize: int = 4) -> int:
    """Bytes of the four detector cubes (n_energies x n_pix x 256 channels x 4 detectors)."""
    return int(n_energies) * int(n_pix) * N_CH * len(DETS) * int(itemsize)


def needed_bytes(n_energies: int, n_pix: int, n_bins: int) -> int:
    """Disk space a build may need at its peak: the cubes as uint32 (the worst case after a promotion from uint16),
    the derived sums, the auxiliary arrays and 1 MB of headers."""
    derived = len(DETS) * n_pix * N_CH * 8 + n_bins * len(DETS) * n_energies * n_pix * 8
    aux = 2 * n_energies * n_pix * 8 + n_energies + len(DETS) * n_energies * N_CH * 8
    return cube_bytes(n_energies, n_pix, 4) + derived + aux + 1_000_000


def _free_bytes(path: Path) -> int | None:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return int(shutil.disk_usage(p)[2])
    except OSError:
        return None


def _replace(src: Path, dst: Path) -> None:
    last: Exception | None = None
    for attempt in range(REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError as err:              # a scanner or an indexer holds a file for a moment
            last = err
            time.sleep(0.1 * (attempt + 1))
    raise last if last is not None else OSError(f"{src}: could not be renamed")


def _key_hash(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def fallback_cache_root() -> Path:
    """The per-user cache root for hypercubes of projects whose own folder cannot be written."""
    env = os.environ.get(CACHE_ENV, "").strip()
    if env:
        return Path(env)
    import platformdirs
    return Path(platformdirs.user_cache_dir("AURORA", appauthor=False)) / "hypercubes"


def cache_dirs(project: Project, scan: ScanInfo) -> list[Path]:
    """The scan's cache folders in the order they are tried: the project's own, then the per-user one."""
    own = Path(project.root) / ".aurora" / "cache" / scan.day / scan.name
    tag = hashlib.sha1(str(Path(project.root).resolve()).encode("utf-8")).hexdigest()[:10]
    return [own, fallback_cache_root() / tag / scan.day / scan.name]


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ----------------------------------------------------------------------------- the cube
@dataclass
class Hypercube:
    name: str
    E: np.ndarray                           # (nE,)
    x: np.ndarray                           # (n_pix,)
    y: np.ndarray
    cubes: list                             # 4 x (nE, n_pix, 256) counts (memmap)
    i0: np.ndarray                          # (nE, n_pix)
    tey: np.ndarray
    valid: np.ndarray                       # (nE,) complete maps
    calibration: Calibration | None = None
    pitch: tuple[float, float] = (0.1, 0.1)
    skipped: list[tuple[float, str]] = field(default_factory=list)
    dtype: str = ""                         # dtype of the cached counts ('uint16' or 'uint32'), '' in memory
    cache_dir: Path | None = None           # the hypercube-<key> folder, None for a cube built in memory
    spec_all: np.ndarray | None = None      # (4, nE, 256) emission spectrum of the whole map at each energy
    xrf_total: np.ndarray | None = None     # (4, n_pix, 256) counts summed over the valid energies
    bin_labels: list[str] = field(default_factory=list)
    bin_windows: list[dict[int, tuple[int, int]]] = field(default_factory=list)
    bin_sums: np.ndarray | None = None      # (n_bins, 4, nE, n_pix) counts in each fixed ROI bin

    def __post_init__(self) -> None:
        self._set_bins(self.bin_labels, self.bin_windows, self.bin_sums)

    @property
    def n_energies(self) -> int:
        return int(len(self.E))

    @property
    def n_pix(self) -> int:
        return int(len(self.x))

    def channel_axis(self, det: int) -> np.ndarray:
        """Channel energies (eV) of one detector: calibrated when a calibration exists, else 10 eV per channel."""
        ch = np.arange(N_CH)
        return self.calibration.ev(DETS[det], ch) if self.calibration else 10.0 * ch

    def window_channels(self, det: int, e_lo: float, e_hi: float) -> tuple[int, int]:
        """Channel slice (lo, hi) of detector `det` for an emission window in eV (hi exclusive)."""
        if self.calibration:
            return self.calibration.bounds(DETS[det], e_lo, e_hi)
        return int(max(0, np.floor(e_lo / 10))), int(min(N_CH, np.ceil(e_hi / 10)))

    def ev_windows(self, e_lo: float, e_hi: float, dets=None) -> dict[int, tuple[int, int]]:
        """Each detector's own channels for one eV window: {det: (lo, hi)} through its own calibration."""
        ds = range(len(DETS)) if dets is None else dets
        return {int(d): self.window_channels(int(d), e_lo, e_hi) for d in ds}

    def resolve_windows(self, windows, lo: int | None = None, hi: int | None = None) -> dict[int, tuple[int, int]]:
        """{det: (lo, hi)} from a dict (keys 0..3 or 'sdd1'..), a function det -> (lo, hi) (all four detectors), or
        the older form: one detector index (or None for the four) with the same channels lo, hi."""
        if isinstance(windows, dict):
            if lo is not None or hi is not None:
                raise TypeError("pass either a windows dict or (det, lo, hi), not both")
            return {_det_index(k): (int(v[0]), int(v[1])) for k, v in windows.items()}
        if callable(windows):
            out = {}
            for d in range(len(DETS)):
                a, b = windows(d)
                out[d] = (int(a), int(b))
            return out
        if lo is None or hi is None:
            raise TypeError("channels lo and hi are needed with a detector index")
        dets = range(len(DETS)) if windows is None else [_det_index(windows)]
        return {d: (int(lo), int(hi)) for d in dets}

    # ------------------------------------------------------------------ derived sums
    def _set_bins(self, labels, windows, sums) -> None:
        self.bin_labels = list(labels or [])
        self.bin_windows = [{int(d): (int(a), int(b)) for d, (a, b) in w.items()} for w in (windows or [])]
        self.bin_sums = sums
        self._lookup: dict[tuple[int, int, int], int] = {}
        if sums is not None:
            for b, w in enumerate(self.bin_windows):
                for d, (a, c) in w.items():
                    self._lookup.setdefault((d, a, c), b)

    def _bin_of(self, d: int, lo: int, hi: int) -> int | None:
        return self._lookup.get((int(d), int(lo), int(hi)))

    def bins_hash(self) -> str:
        return bins_hash(list(zip(self.bin_labels, self.bin_windows))) if self.bin_sums is not None else ""

    def served_without_cube(self, windows: dict[int, tuple[int, int]], pix=None) -> bool:
        """True when a XANES of these windows over `pix` comes from the precomputed sums (no read of the cube)."""
        if pix is None and self.spec_all is not None:
            return True
        return all(self._bin_of(d, a, b) is not None for d, (a, b) in windows.items())

    def precompute(self, bins=None, progress=None) -> None:
        """Fill the derived sums in memory (a cube built directly, without Hypercube.load): whole-map spectra, the
        energy total and the fixed ROI bin sums (`bins` a RoiBins, else the bins already set)."""
        spec = bins_spec(bins) if bins is not None else list(zip(self.bin_labels, self.bin_windows))
        nE, npx = self.n_energies, self.n_pix
        spec_all = np.zeros((len(DETS), nE, N_CH))
        xrf = np.zeros((len(DETS), npx, N_CH), np.uint64)
        bsum = np.zeros((len(spec), len(DETS), nE, npx), np.uint64) if spec else None
        wins = [w for _lab, w in spec]
        v = np.where(self.valid)[0]
        for j, e in enumerate(v):
            if progress:
                progress(j, v.size, f"{self.name}: sums at {self.E[e]:.2f} eV")
            for d in range(len(DETS)):
                _accumulate(np.asarray(self.cubes[d][e]), d, int(e), spec_all, xrf, bsum, wins)
        self.spec_all, self.xrf_total = spec_all, xrf
        self._set_bins([lab for lab, _w in spec], wins, bsum)

    # ------------------------------------------------------------------ per-detector building blocks
    def _counts(self, d: int, e: int, lo: int, hi: int) -> np.ndarray:
        """(n_pix,) counts of detector d in channels [lo, hi) at energy index e."""
        b = self._bin_of(d, lo, hi)
        if b is not None:
            return np.asarray(self.bin_sums[b, d, e], np.float64)
        return self.cubes[d][e][:, lo:hi].sum(axis=1, dtype=np.float64)

    def _total(self, d: int, lo: int, hi: int) -> np.ndarray:
        """(n_pix,) counts of detector d in [lo, hi) summed over the valid energies."""
        v = np.where(self.valid)[0]
        b = self._bin_of(d, lo, hi)
        if b is not None:
            return np.asarray(self.bin_sums[b, d][v], np.float64).sum(axis=0) if v.size else np.zeros(self.n_pix)
        if self.xrf_total is not None:
            return self.xrf_total[d][:, lo:hi].sum(axis=1, dtype=np.float64)
        m = np.zeros(self.n_pix)
        for e in v:
            m += self.cubes[d][e][:, lo:hi].sum(axis=1, dtype=np.float64)
        return m

    def _window_sum(self, d: int, e: int, lo: int, hi: int, pix) -> float:
        """Counts of detector d in [lo, hi) at energy e, summed over the pixels (all when pix is None)."""
        if pix is None:
            if self.spec_all is not None:
                return float(self.spec_all[d, e, lo:hi].sum())
            b = self._bin_of(d, lo, hi)
            if b is not None:
                return float(np.asarray(self.bin_sums[b, d, e], np.float64).sum())
            return float(self.cubes[d][e][:, lo:hi].sum())
        b = self._bin_of(d, lo, hi)
        if b is not None:
            return float(np.asarray(self.bin_sums[b, d, e], np.float64)[pix].sum())
        return float(self.cubes[d][e][pix][:, lo:hi].sum())

    # ------------------------------------------------------------------ spectra and maps
    def spectrum(self, det: int, pix=None, energies=None) -> np.ndarray:
        """256-channel emission spectrum summed over pixels (all when None) and energies (all valid when None)."""
        e_idx = np.where(self.valid)[0] if energies is None else np.atleast_1d(energies)
        if pix is None and self.spec_all is not None:
            return self.spec_all[det][e_idx].sum(axis=0) if len(e_idx) else np.zeros(N_CH)
        if pix is not None and energies is None and self.xrf_total is not None:
            return self.xrf_total[det][np.asarray(pix, int)].sum(axis=0, dtype=np.float64)
        c = self.cubes[det]
        p_idx = slice(None) if pix is None else np.asarray(pix, int)
        out = np.zeros(N_CH, np.float64)
        for e in e_idx:
            out += c[e][p_idx].sum(axis=0, dtype=np.float64)
        return out

    def window_map(self, windows, lo: int | None = None, hi: int | None = None, energy_index: int | None = None,
                   per_i0: bool = False) -> np.ndarray:
        """Per-pixel counts in each detector's own window, summed over the detectors of `windows`, at one energy (or
        averaged over the valid energies when energy_index is None); divided by I0 per pixel when asked."""
        w = self.resolve_windows(windows, lo, hi)
        if energy_index is not None:
            m = np.zeros(self.n_pix)
            for d, (a, b) in w.items():
                m = m + self._counts(d, energy_index, a, b)
            i0 = self.i0[energy_index]
        else:
            v = np.where(self.valid)[0]
            m = np.zeros(self.n_pix)
            for d, (a, b) in w.items():
                m += self._total(d, a, b)
            m /= max(len(v), 1)
            i0 = np.nanmean(self.i0[v], axis=0) if v.size else np.full(self.n_pix, np.nan)
        if per_i0:
            with np.errstate(all="ignore"):
                m = np.where(i0 > 0, m / i0, np.nan)
        return m

    def xanes(self, windows, lo: int | None = None, hi: int | None = None, pix=None, per_i0: bool = True,
              progress=None) -> np.ndarray:
        """XANES trace: window counts summed over the pixels and the detectors at every energy (NaN at skipped
        energies). `progress(i, n, msg)` is called once per energy (it may raise Cancelled)."""
        w = self.resolve_windows(windows, lo, hi)
        p_idx = None if pix is None else np.asarray(pix, int)
        out = np.full(self.n_energies, np.nan)
        for e in range(self.n_energies):
            if progress:
                progress(e, self.n_energies, "")
            if not self.valid[e]:
                continue
            s = sum(self._window_sum(d, e, a, b, p_idx) for d, (a, b) in w.items())
            if per_i0:
                i0 = float(self.i0[e].sum() if p_idx is None else self.i0[e][p_idx].sum())
                s = s / i0 if i0 > 0 else np.nan
            out[e] = s
        return out

    def rgb(self, windows, chans=None, energy_index: int | None = None, clip: tuple[float, float] = (1, 99)) -> np.ndarray:
        """(n_pix, 3) composite of three windows, each stretched between its percentiles. `windows` is a list of three
        windows dicts; the older form rgb(det, [(lo, hi), ...]) is still accepted."""
        if chans is None:
            wins = [self.resolve_windows(w) for w in list(windows)[:3]]
        else:
            wins = [self.resolve_windows(windows, a, b) for a, b in list(chans)[:3]]
        out = np.zeros((self.n_pix, 3))
        for k, w in enumerate(wins):
            m = self.window_map(w, energy_index=energy_index)
            fin = np.isfinite(m)
            if fin.any():
                a, b = np.percentile(m[fin], clip)
                out[:, k] = np.clip((m - a) / (b - a if b > a else 1.0), 0, 1)
        return out

    # ------------------------------------------------------------------ statistics on pixels
    def pixel_matrix(self, windows, lo: int | None = None, hi: int | None = None, per_i0: bool = True,
                     progress=None) -> np.ndarray:
        """(n_pix, nE_valid) XANES per pixel for the windows: the matrix PCA and k-means work on."""
        w = self.resolve_windows(windows, lo, hi)
        v = np.where(self.valid)[0]
        M = np.zeros((self.n_pix, v.size))
        for j, e in enumerate(v):
            if progress:
                progress(j, v.size, f"pixel spectra {j + 1}/{v.size}")
            col = np.zeros(self.n_pix)
            for d, (a, b) in w.items():
                col = col + self._counts(d, int(e), a, b)
            if per_i0:
                with np.errstate(all="ignore"):
                    col = np.where(self.i0[e] > 0, col / self.i0[e], np.nan)
            M[:, j] = col
        return M

    # ------------------------------------------------------------------ cache
    @staticmethod
    def cache_exists(project: Project, scan: ScanInfo) -> bool:
        """A cube of this scan sits in a cache folder (it may still be stale: load() checks the key)."""
        for cdir in cache_dirs(project, scan):
            try:
                if any(p.is_dir() for p in cdir.glob(f"{CUBE_PREFIX}*")) or (cdir / "cube_sdd1.npy").exists():
                    return True
            except OSError:
                continue
        return False

    @classmethod
    def _open_cached(cls, folder: Path, key: str, name: str, E, x, y, calibration) -> "Hypercube | None":
        meta = _read_json(folder / "cube_meta.json")
        if not meta or meta.get("key") != key or meta.get("layout") != LAYOUT:
            return None
        files = [folder / f"cube_{d}.npy" for d in DETS] + [folder / "cube_aux.npz", folder / "xrf_total.npy"]
        if not all(p.exists() for p in files):
            return None
        cubes: list = []
        xrf = None
        try:
            for p in files[:len(DETS)]:
                cubes.append(np.load(p, mmap_mode="r"))
            with np.load(folder / "cube_aux.npz") as aux:
                i0, tey, valid, spec_all = aux["i0"], aux["tey"], aux["valid"].astype(bool), aux["spec_all"]
            xrf = np.load(folder / "xrf_total.npy", mmap_mode="r")
        except _DAMAGE as err:
            cubes.clear()                            # unmapped now: Windows refuses to move a folder with a mapped file
            xrf = None
            gc.collect()
            log.warning("%s: hypercube cache unreadable (%s); rebuilt", folder, str(err))    # text: no traceback kept
            return None
        if cubes[0].shape != (len(E), len(x), N_CH):
            return None
        hc = cls(name=name, E=E, x=x, y=y, cubes=cubes, i0=i0, tey=tey, valid=valid, calibration=calibration,
                 skipped=[tuple(s) for s in meta.get("skipped", [])], dtype=str(meta.get("dtype", "")),
                 cache_dir=folder, spec_all=spec_all, xrf_total=xrf)
        hc.pitch = grid_pitch(x, y)
        b = meta.get("bins") or {}
        bp = folder / f"bins_{b.get('hash', '')}.npy"
        if b.get("hash") and bp.exists():
            try:
                hc._set_bins(b.get("labels", []), [{int(d): tuple(w) for d, w in enumerate(ws)} for ws in b.get("windows", [])],
                             np.load(bp, mmap_mode="r"))
            except (OSError, ValueError) as err:
                log.warning("%s: bin sums unreadable (%s); recomputed", bp, err)
        return hc

    def _ensure_bins(self, spec, progress=None) -> None:
        """Make the fixed-bin sums match `spec` (the project's roi_bins): computed from the cube once when the bins
        changed since the cube was cached, and stored next to it when the folder is writable."""
        if not spec:
            self._set_bins([], [], None)
            return
        h = bins_hash(spec)
        if self.bin_sums is not None and self.bins_hash() == h:
            return
        labels, wins = [lab for lab, _w in spec], [w for _lab, w in spec]
        nE, npx = self.n_energies, self.n_pix
        shape = (len(spec), len(DETS), nE, npx)
        folder = self.cache_dir
        tmp = None
        try:
            if folder is None:
                raise OSError("no cache folder")
            from aurora.core.io.safety import assert_writable
            assert_writable(folder)
            tmp = folder / f".bins_{h}.{os.getpid()}.tmp.npy"
            out = np.lib.format.open_memmap(tmp, mode="w+", dtype=np.uint64, shape=shape)
        except OSError as err:
            log.info("%s: bin sums kept in memory (%s)", self.name, err)
            tmp = None
            out = np.zeros(shape, np.uint64)
        v = np.where(self.valid)[0]
        try:
            for j, e in enumerate(v):
                if progress:
                    progress(j, v.size, f"{self.name}: line sums at {self.E[e]:.2f} eV")
                for d in range(len(DETS)):
                    a = self.cubes[d][e]
                    for b, w in enumerate(wins):
                        lo, hi = w[d]
                        out[b, d, e] = a[:, lo:hi].sum(axis=1, dtype=np.uint64)
        except BaseException:
            out = None
            if tmp is not None:
                gc.collect()
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise
        if tmp is not None:
            out.flush()
            out = None
            final = folder / f"bins_{h}.npy"
            try:
                _replace(tmp, final)
                meta = _read_json(folder / "cube_meta.json") or {}
                meta["bins"] = {"hash": h, "labels": labels, "windows": [[list(w[d]) for d in range(len(DETS))] for w in wins]}
                from aurora.core.io.textio import write_json_atomic
                write_json_atomic(folder / "cube_meta.json", meta)
                out = np.load(final, mmap_mode="r")
            except OSError as err:
                log.warning("%s: bin sums not stored (%s)", folder, err)
                out = np.load(final if final.exists() else tmp, mmap_mode="r")
        self._set_bins(labels, wins, out)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, project: Project, scan: ScanInfo, progress=None, calibration: Calibration | None = None,
             cache_root: Path | None = None) -> "Hypercube":
        """The cube of a stack: from its cache when the key (energies, pixels, newest raw folder) matches, else
        streamed from the raw energy folders into a new cache (see the module docstring). `progress(i, n, msg)` is
        called once per energy; when it raises (Cancelled), nothing half-written is kept."""
        h5 = scan.h5 or find_h5(scan.path)
        if h5 is None:
            raise FileNotFoundError(f"{scan.path}: no .h5 file")
        x, y = xy(h5)
        edirs = scan.energy_dirs or stack_energy_dirs(scan.path)
        E = np.array([e for e, _ in edirs], float)
        nE, npts = len(E), len(x)
        key = f"cube|{nE}|{npts}|{stack_last_mtime(edirs):.0f}"
        cal = calibration or project.calibration
        spec = bins_spec(getattr(project, "roi_bins", None))
        dirs = [Path(cache_root) / scan.day / scan.name] if cache_root else cache_dirs(project, scan)
        for cdir in dirs:
            hc = cls._open_cached(cdir / f"{CUBE_PREFIX}{_key_hash(key)}", key, scan.name, E, x, y, cal)
            if hc is not None:
                hc._ensure_bins(spec, progress)
                log.info("%s: hypercube from cache %s", scan.name, hc.cache_dir)
                return hc
        legacy = next((d for d in dirs if _legacy_matches(d, key)), None)
        source = _LegacySource(legacy) if legacy is not None else _RawSource(edirs, npts)
        try:
            hc = cls._build(dirs, key, source, scan, E, x, y, cal, spec, progress)
        finally:
            source.close()
        for d in dirs:                               # 0.3 files: converted above, or stale (the raw data changed)
            if (d / "cube_sdd1.npy").exists():
                _remove_legacy(d)
        return hc

    @classmethod
    def _build(cls, dirs: list[Path], key: str, source, scan: ScanInfo, E, x, y, cal, spec, progress) -> "Hypercube":
        from aurora.core.io.safety import assert_writable
        nE, npts = len(E), len(x)
        need = needed_bytes(nE, npts, len(spec))
        tried = []
        cdir = None
        for cand in dirs:
            try:
                assert_writable(cand)
            except OSError as err:
                tried.append(f"{cand} ({err})")
                continue
            free = _free_bytes(cand)
            if free is not None and free < need:
                raise CacheSpaceError(f"needs {_fmt_bytes(need)} in {cand}, {_fmt_bytes(free)} free")
            try:
                cand.mkdir(parents=True, exist_ok=True)
            except OSError as err:
                tried.append(f"{cand} ({err})")
                continue
            cdir = cand
            break
        if cdir is None:
            raise OSError("the hypercube needs a writable cache folder; tried " + "; ".join(tried))
        if cdir != dirs[0]:
            log.info("%s: project cache not writable, hypercube cached in %s", scan.name, cdir)
        _clean_stale_builds(cdir)
        build = cdir / f"{BUILD_PREFIX}{dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
        build.mkdir()
        wins = [w for _lab, w in spec]
        st = {"dtype": np.uint16, "cubes": [], "xrf": None, "bins": None}
        try:
            st["cubes"] = [np.lib.format.open_memmap(build / f"cube_{d}.u16.npy", mode="w+", dtype=np.uint16,
                                                     shape=(nE, npts, N_CH)) for d in DETS]
            st["xrf"] = np.lib.format.open_memmap(build / "xrf_total.npy", mode="w+", dtype=np.uint64,
                                                  shape=(len(DETS), npts, N_CH))
            if wins:
                st["bins"] = np.lib.format.open_memmap(build / f"bins_{bins_hash(spec)}.npy", mode="w+",
                                                       dtype=np.uint64, shape=(len(wins), len(DETS), nE, npts))
            spec_all = np.zeros((len(DETS), nE, N_CH))
            i0 = np.full((nE, npts), np.nan)
            tey = np.full((nE, npts), np.nan)
            valid = np.zeros(nE, bool)
            skipped: list[tuple[float, str]] = []
            for i in range(nE):
                if progress:
                    progress(i, nE, f"{scan.name} {E[i]:.2f} eV")
                sdd, i0_row, tey_row, reason = source.read(i)
                if sdd is None:
                    skipped.append((float(E[i]), reason))
                    continue
                if st["dtype"] is np.uint16 and max(int(s.max(initial=0)) for s in sdd) > U16_MAX:
                    _promote(st, build, i)
                for d in range(len(DETS)):
                    st["cubes"][d][i] = sdd[d]
                    _accumulate(sdd[d], d, i, spec_all, st["xrf"], st["bins"], wins)
                i0[i] = i0_row
                tey[i] = tey_row
                valid[i] = True
                sdd = None
            if progress:
                progress(nE, nE, f"{scan.name}: writing the cache")
            dtype = "uint16" if st["dtype"] is np.uint16 else "uint32"
            _release(st)                             # flushed and unmapped: the files can be renamed
            for d in DETS:
                _replace(build / f"cube_{d}.{'u16' if dtype == 'uint16' else 'u32'}.npy", build / f"cube_{d}.npy")
            np.savez(build / "cube_aux.npz", i0=i0, tey=tey, valid=valid, spec_all=spec_all)
            meta = {"key": key, "layout": LAYOUT, "skipped": skipped, "dtype": dtype, "n_energies": nE, "n_pix": npts,
                    "scan": scan.name, "built": dt.datetime.now().isoformat(timespec="seconds"),
                    "bins": {"hash": bins_hash(spec), "labels": [lab for lab, _w in spec],
                             "windows": [[list(w[d]) for d in range(len(DETS))] for w in wins]} if wins else {}}
            (build / "cube_meta.json").write_text(json.dumps(meta), encoding="utf-8")
            final = cdir / f"{CUBE_PREFIX}{_key_hash(key)}"
            winner = cls._open_cached(final, key, scan.name, E, x, y, cal) if final.exists() else None
            if winner is not None:                   # a concurrent build of the same key finished first: use it
                shutil.rmtree(build, ignore_errors=True)
                return winner
            if final.exists():                       # a broken leftover of the same key
                aside = cdir / f".stale-{build.name[len(BUILD_PREFIX):]}"
                _replace(final, aside)
                shutil.rmtree(aside, ignore_errors=True)
            _replace(build, final)
        except BaseException:
            _release(st)
            gc.collect()
            shutil.rmtree(build, ignore_errors=True)
            if build.exists():
                log.warning("%s: temporary folder not removed (in use); removed at the next load", build)
            raise
        for old in cdir.glob(f"{CUBE_PREFIX}*"):            # caches of earlier keys (the raw data changed)
            if old != final and old.is_dir():
                shutil.rmtree(old, ignore_errors=True)
        hc = cls._open_cached(final, key, scan.name, E, x, y, cal)
        if hc is None:
            raise OSError(f"{final}: the hypercube just written cannot be read back")
        log.info("%s: hypercube %d energies x %d pixels x 256 x 4 (%s) written to %s (%d skipped)", scan.name, nE,
                 npts, dtype, final, len(skipped))
        return hc


# ----------------------------------------------------------------------------- build helpers
def _accumulate(a: np.ndarray, d: int, i: int, spec_all: np.ndarray, xrf, bsum, wins) -> None:
    """Add one detector's (n_pix, 256) counts at energy index i to the derived sums."""
    spec_all[d, i] = a.sum(axis=0, dtype=np.float64)
    xrf[d] += a
    if bsum is not None:
        for b, w in enumerate(wins):
            lo, hi = w[d]
            bsum[b, d, i] = a[:, lo:hi].sum(axis=1, dtype=np.uint64)


def _promote(st: dict, build: Path, n_done: int) -> None:
    """Counts above 65535 arrived: rewrite the cubes as uint32, one detector and one energy at a time."""
    for k, d in enumerate(DETS):
        old = st["cubes"][k]
        new = np.lib.format.open_memmap(build / f"cube_{d}.u32.npy", mode="w+", dtype=np.uint32, shape=old.shape)
        for e in range(n_done):
            new[e] = old[e]
        new.flush()
        st["cubes"][k] = new
        old = None                                   # unmapped: the uint16 file can go
        new = None
        try:
            (build / f"cube_{d}.u16.npy").unlink()
        except OSError:
            gc.collect()
            (build / f"cube_{d}.u16.npy").unlink(missing_ok=True)
    st["dtype"] = np.uint32
    log.info("%s: counts above 65535, cube written as uint32", build.parent.name)


def _release(st: dict) -> None:
    """Flush and drop every memory map of a build, so its files can be renamed or removed (Windows refuses while a
    file is mapped). The maps live only in `st`: no other name may hold one, or its file stays mapped."""
    maps = list(st.get("cubes") or []) + [st.get("xrf"), st.get("bins")]
    st["cubes"], st["xrf"], st["bins"] = [], None, None
    while maps:
        m = maps.pop()
        if m is not None:
            try:
                m.flush()
            except (OSError, ValueError, AttributeError):
                pass
        m = None


def _clean_stale_builds(cdir: Path) -> None:
    now = time.time()
    try:
        cands = [p for p in cdir.iterdir() if p.is_dir() and (p.name.startswith(BUILD_PREFIX) or p.name.startswith(".stale-"))]
    except OSError:
        return
    for p in cands:
        try:
            if now - p.stat().st_mtime > STALE_BUILD_S or p.name.startswith(".stale-"):
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            continue


class _RawSource:
    """The energy folders of the stack, read one at a time."""

    def __init__(self, edirs, npts: int):
        self.edirs, self.npts = edirs, npts

    def read(self, i: int):
        e, p = self.edirs[i]
        try:
            pd = read_point_dir(p)
        except (OSError, ValueError) as err:
            return None, None, None, str(err)
        if not pd.complete or pd.n_pix < self.npts:
            return None, None, None, f"incomplete map ({pd.n_mcc}/{self.npts} points)"
        n = self.npts
        return [s[:n] for s in pd.sdd], pd.mcc[:n, I0_COL], pd.mcc[:n, TEY_COL], ""

    def close(self) -> None:
        pass


LEGACY_FILES = [f"cube_{d}.npy" for d in DETS] + ["cube_aux.npz", "cube_meta.json"]


def _legacy_matches(cdir: Path, key: str) -> bool:
    meta = _read_json(cdir / "cube_meta.json")
    return bool(meta) and meta.get("key") == key and all((cdir / f).exists() for f in LEGACY_FILES)


class _LegacySource:
    """An AURORA 0.3 cache (uint32 cubes directly in the scan folder): converted without reading the raw data."""

    def __init__(self, cdir: Path):
        self.cubes = [np.load(cdir / f"cube_{d}.npy", mmap_mode="r") for d in DETS]
        with np.load(cdir / "cube_aux.npz") as aux:
            self.i0, self.tey, self.valid = aux["i0"], aux["tey"], aux["valid"].astype(bool)
        meta = _read_json(cdir / "cube_meta.json") or {}
        self.reasons = [str(s[1]) for s in meta.get("skipped", [])]

    def read(self, i: int):
        if not self.valid[i]:
            return None, None, None, self.reasons.pop(0) if self.reasons else "skipped"
        return [np.asarray(c[i]) for c in self.cubes], self.i0[i], self.tey[i], ""

    def close(self) -> None:
        self.cubes = []


def _remove_legacy(cdir: Path) -> None:
    """Remove the AURORA 0.3 cube files of a scan folder (converted, or stale once a new cube exists)."""
    from aurora.core.io.safety import assert_writable
    try:
        assert_writable(cdir)
    except OSError:
        return
    gc.collect()
    for f in LEGACY_FILES:
        try:
            (cdir / f).unlink()
        except OSError as err:
            log.info("%s: old hypercube file kept (%s)", cdir / f, err)


# ----------------------------------------------------------------------------- pixel statistics
@dataclass
class ClusterResult:
    labels: np.ndarray                  # (n_pix,) cluster index, -1 for invalid pixels
    centres: np.ndarray                 # (k, nE)
    scores: np.ndarray                  # (n_pix, n_comp) PCA scores
    explained: np.ndarray
    E: np.ndarray
    seed: int = 0                       # the k-means seed: the same seed gives the same labels


def pca_kmeans(M: np.ndarray, E: np.ndarray, n_components: int = 5, n_clusters: int = 4, seed: int = 0) -> ClusterResult:
    """PCA on the standardised per-pixel XANES, k-means on the scores (scipy kmeans2); cluster centres = mean spectra."""
    from scipy.cluster.vq import kmeans2
    ok = np.all(np.isfinite(M), axis=1) & (M.sum(axis=1) != 0)
    X = M[ok]
    if X.shape[0] < 2:
        raise ValueError("fewer than two pixels with a complete spectrum: nothing to cluster")
    mean = X.mean(axis=0); std = X.std(axis=0); std[std == 0] = 1.0
    Z = (X - mean) / std
    U, s, Vt = np.linalg.svd(Z, full_matrices=False)
    k = min(n_components, len(s))
    scores = (U * s)[:, :k]
    var = s ** 2; explained = var / var.sum() if var.sum() else var
    rng = np.random.default_rng(int(seed))
    centroid, lab = kmeans2(scores, min(n_clusters, len(scores)), minit="++", seed=rng)
    labels = np.full(M.shape[0], -1); labels[ok] = lab
    centres = np.array([X[lab == c].mean(axis=0) if (lab == c).any() else np.full(M.shape[1], np.nan) for c in range(min(n_clusters, len(scores)))])
    full_scores = np.full((M.shape[0], k), np.nan); full_scores[ok] = scores
    return ClusterResult(labels=labels, centres=centres, scores=full_scores, explained=explained[:k], E=E, seed=int(seed))


def merged_spectra(M: np.ndarray, labels: np.ndarray, ticked) -> tuple[np.ndarray, np.ndarray, int]:
    """(mean, sum, pixel count) of the rows of M whose label is one of `ticked` (the merged clusters)."""
    sel = np.isin(np.asarray(labels), np.asarray(sorted(int(t) for t in ticked), int)) & (np.asarray(labels) >= 0)
    rows = M[sel]
    n = int(rows.shape[0])
    if n == 0:
        return np.full(M.shape[1], np.nan), np.full(M.shape[1], np.nan), 0
    return rows.mean(axis=0), rows.sum(axis=0), n


def paint_squares(x: np.ndarray, y: np.ndarray, pitch: tuple[float, float], pix) -> list[list[float]]:
    """Zone.paint squares [cx, cy, pitch_x, pitch_y] (mm), one on each pixel of `pix`: zones.members() of a zone made of
    them returns exactly `pix` on this stack (a square takes the points within 0.51 pitch of its centre)."""
    px, py = (round(float(v), 6) for v in pitch)
    return [[round(float(x[i]), 6), round(float(y[i]), 6), px, py] for i in np.asarray(pix, int)]


# ----------------------------------------------------------------------------- PyMca exports
def _snap_grid(x: np.ndarray, y: np.ndarray, pitch: tuple[float, float]):
    px, py = pitch
    ix = np.round((x - x.min()) / px).astype(int); iy = np.round((y - y.min()) / py).astype(int)
    return ix, iy, int(ix.max()) + 1, int(iy.max()) + 1


def _chunks(ny: int, nx: int, *rest: int) -> tuple[int, ...]:
    return (min(ny, EXPORT_BLOCK), min(nx, EXPORT_BLOCK), *rest)


class _AtomicH5:
    """Write an HDF5 file under a temporary name and move it over the target at the end; a cancel or a failure
    removes the temporary file and leaves any earlier file intact."""

    def __init__(self, path: Path):
        from aurora.core.io.safety import assert_writable
        self.path = Path(path)
        assert_writable(self.path)
        self.tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")

    def __enter__(self):
        import h5py
        self.h = h5py.File(self.tmp, "w")
        return self.h

    def __exit__(self, exc_type, exc, tb):
        try:
            self.h.close()
        finally:
            if exc_type is None:
                _replace(self.tmp, self.path)
            else:
                try:
                    self.tmp.unlink()
                except OSError:
                    pass
        return False


def export_pymca_xanes(hc: Hypercube, path: Path, windows, lo: int | None = None, hi: int | None = None,
                       per_i0: bool = True, title: str = "", progress=None) -> Path:
    """NeXus-style 3D stack (y, x, energy) that PyMca opens for PCA / cluster analysis (the CLS `_PCA-CA.h5` layout,
    axis order (Y, X, E) as the code wrote it, not as its guide said). One dataset per detector of `windows`, each
    with its own channel_roi attribute, plus their sum and average; written one energy slab at a time."""
    w = hc.resolve_windows(windows, lo, hi)
    if not w:
        raise ValueError("no detector selected")
    ix, iy, nx, ny = _snap_grid(hc.x, hc.y, hc.pitch)
    v = np.where(hc.valid)[0]
    if v.size == 0:
        raise ValueError(f"{hc.name}: no valid energy to export")
    dets = sorted(w)
    with _AtomicH5(path) as h:
        h.attrs["NX_class"] = "NXroot"; h.attrs["default"] = "entry"
        ent = h.create_group("entry"); ent.attrs["NX_class"] = "NXentry"; ent.attrs["default"] = "measurement"
        ent.create_dataset("title", data=title or hc.name)
        meas = ent.create_group("measurement"); meas.attrs["NX_class"] = "NXdata"
        kw = dict(dtype=np.float32, chunks=_chunks(ny, nx, 1), compression="gzip")
        ds = {}
        for d in dets:
            ds[d] = meas.create_dataset(DETS[d], shape=(ny, nx, v.size), **kw)
            ds[d].attrs["channel_roi"] = list(w[d])
            meas.attrs[f"channel_roi_{DETS[d]}"] = list(w[d])
        tot = meas.create_dataset("sum", shape=(ny, nx, v.size), **kw)
        avg = meas.create_dataset("average", shape=(ny, nx, v.size), **kw)
        for j, e in enumerate(v):
            if progress:
                progress(j, v.size, f"{Path(path).name}: {hc.E[e]:.2f} eV")
            total = np.zeros((ny, nx), np.float32)
            for d in dets:
                col = hc._counts(d, int(e), *w[d])
                if per_i0:
                    with np.errstate(all="ignore"):
                        col = np.where(hc.i0[e] > 0, col / hc.i0[e], 0.0)
                arr = np.zeros((ny, nx), np.float32)
                arr[iy, ix] = col
                ds[d][:, :, j] = arr
                total += arr
            tot[:, :, j] = total
            avg[:, :, j] = total / max(len(dets), 1)
        meas.create_dataset("energy", data=hc.E[v]); meas.create_dataset("x", data=hc.x.min() + hc.pitch[0] * np.arange(nx))
        meas.create_dataset("y", data=hc.y.min() + hc.pitch[1] * np.arange(ny))
        meas.attrs["signal"] = "average"; meas.attrs["axes"] = ["y", "x", "energy"]
        meas.attrs["sdds"] = [DETS[d] for d in dets]
        meas.attrs["channel_roi"] = np.array([w[d] for d in dets], dtype=np.int64)      # one [lo, hi] row per SDD
        meas.attrs["normalized"] = bool(per_i0)
        md = h.create_group("stack_metadata")
        md.attrs["scan_name"] = hc.name; md.attrs["nx"] = nx; md.attrs["ny"] = ny; md.attrs["aurora"] = "export_pymca_xanes"
    log.info("%s: PyMca XANES stack written (%d x %d x %d, %s)", path, ny, nx, v.size, describe_windows(w))
    return Path(path)


def export_pymca_4d(hc: Hypercube, path: Path, per_i0: bool = True, title: str = "", progress=None,
                    dets=None) -> Path:
    """The CLS `_Elemental_PyMca.h5` layout: xrf (y, x, 256) summed over energies, xanes summed over channels, and the
    full (y, x, energy, 256) cube per detector and summed. Every detector is written; the sums cover `dets` (all four
    when None). Written one detector and energy slab at a time into chunked datasets, never as one array in memory."""
    ix, iy, nx, ny = _snap_grid(hc.x, hc.y, hc.pitch)
    v = np.where(hc.valid)[0]
    if v.size == 0:
        raise ValueError(f"{hc.name}: no valid energy to export")
    summed = list(range(len(DETS))) if dets is None else sorted(_det_index(d) for d in dets)
    kw = dict(dtype=np.float32, compression="gzip")
    with _AtomicH5(path) as h:
        h.create_dataset("energy", data=hc.E[v])
        ent = h.create_group("entry"); ent.create_dataset("title", data=title or hc.name)
        xrf = ent.create_group("xrf_measurement"); xan = ent.create_group("xanes_measurement"); full = ent.create_group("full_4d_measurement")
        f4 = {d: full.create_dataset(DETS[d], shape=(ny, nx, v.size, N_CH), chunks=_chunks(ny, nx, 1, N_CH), **kw)
              for d in range(len(DETS))}
        x3 = {d: xan.create_dataset(DETS[d], shape=(ny, nx, v.size), chunks=_chunks(ny, nx, 1), **kw) for d in range(len(DETS))}
        f4_sum = full.create_dataset("sdd_sum_4d", shape=(ny, nx, v.size, N_CH), chunks=_chunks(ny, nx, 1, N_CH), **kw)
        x3_sum = xan.create_dataset("sdd_xanes_stack_3d", shape=(ny, nx, v.size), chunks=_chunks(ny, nx, 1), **kw)
        xrf_acc = {d: np.zeros((ny, nx, N_CH), np.float32) for d in range(len(DETS))}     # float32, in energy order
        for j, e in enumerate(v):
            if progress:
                progress(j, v.size, f"{Path(path).name}: {hc.E[e]:.2f} eV")
            slab_sum = np.zeros((ny, nx, N_CH), np.float32)
            xs_sum = np.zeros((ny, nx), np.float32)
            for d in range(len(DETS)):
                block = np.asarray(hc.cubes[d][e]).astype(np.float32)
                if per_i0:
                    with np.errstate(all="ignore"):
                        block = block / np.where(hc.i0[e] > 0, hc.i0[e], np.nan)[:, None]
                        block = np.nan_to_num(block)
                slab = np.zeros((ny, nx, N_CH), np.float32)
                slab[iy, ix, :] = block
                f4[d][:, :, j, :] = slab
                xs = slab.sum(axis=2)
                x3[d][:, :, j] = xs
                xrf_acc[d] += slab
                if d in summed:
                    slab_sum += slab
                    xs_sum += xs
            f4_sum[:, :, j, :] = slab_sum
            x3_sum[:, :, j] = xs_sum
        xrf_sum = np.zeros((ny, nx, N_CH), np.float32)
        for d in range(len(DETS)):
            xrf.create_dataset(DETS[d], data=xrf_acc[d], chunks=_chunks(ny, nx, N_CH), **kw)
            if d in summed:
                xrf_sum += xrf_acc[d]
            xrf_acc[d] = None
        xrf.create_dataset("sdd_xrf_stack_3d", data=xrf_sum, chunks=_chunks(ny, nx, N_CH), **kw)
        full.attrs["nx"] = nx; full.attrs["ny"] = ny; full.attrs["rows"] = ny; full.attrs["cols"] = nx
        full.attrs["summed_sdds"] = [DETS[d] for d in summed]
        md = h.create_group("stack_metadata"); md.attrs["scan_name"] = hc.name; md.attrs["aurora"] = "export_pymca_4d"
    log.info("%s: PyMca 4D file written", path)
    return Path(path)
