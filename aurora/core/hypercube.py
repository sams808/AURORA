"""The full hypercube of a stack: (energy, pixel, channel) counts per detector, for the Maps perspective
(CLS SGM_Beamline_Analysis dashboard parity): maps of any channel window at any energy, ROI spectra, elemental maps,
RGB composites, pixel PCA + k-means, PyMca HDF5 exports.

The cube is read once from the raw energy folders and cached as one .npy per detector under
`<root>/.aurora/cache/<day>/<scan>/cube_sddN.npy` (uint32, memory-mapped on later opens).
"""
from __future__ import annotations

import json
import logging
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


@dataclass
class Hypercube:
    name: str
    E: np.ndarray                           # (nE,)
    x: np.ndarray                           # (n_pix,)
    y: np.ndarray
    cubes: list[np.ndarray]                 # 4 x (nE, n_pix, 256) uint32 (memmap)
    i0: np.ndarray                          # (nE, n_pix)
    tey: np.ndarray
    valid: np.ndarray                       # (nE,) complete maps
    calibration: Calibration | None = None
    pitch: tuple[float, float] = (0.1, 0.1)
    skipped: list[tuple[float, str]] = field(default_factory=list)

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
        """Channel slice (lo, hi) for an emission window in eV (hi exclusive)."""
        if self.calibration:
            return self.calibration.bounds(DETS[det], e_lo, e_hi)
        return int(max(0, np.floor(e_lo / 10))), int(min(N_CH, np.ceil(e_hi / 10)))

    # ------------------------------------------------------------------ spectra and maps
    def spectrum(self, det: int, pix=None, energies=None) -> np.ndarray:
        """256-channel emission spectrum summed over pixels (all when None) and energies (all valid when None)."""
        c = self.cubes[det]
        e_idx = np.where(self.valid)[0] if energies is None else np.atleast_1d(energies)
        p_idx = slice(None) if pix is None else np.asarray(pix, int)
        out = np.zeros(N_CH, np.float64)
        for e in e_idx:
            out += c[e][p_idx].sum(axis=0, dtype=np.float64)
        return out

    def window_map(self, det: int | None, lo: int, hi: int, energy_index: int | None = None, per_i0: bool = False) -> np.ndarray:
        """Per-pixel counts in channels [lo, hi) at one energy (or averaged over the valid energies when None),
        one detector or the sum of the four; divided by I0 per pixel when asked."""
        dets = range(len(DETS)) if det is None else [det]
        if energy_index is not None:
            m = sum(self.cubes[d][energy_index][:, lo:hi].sum(axis=1, dtype=np.float64) for d in dets)
            i0 = self.i0[energy_index]
        else:
            v = np.where(self.valid)[0]
            m = np.zeros(self.n_pix)
            for d in dets:
                for e in v:
                    m += self.cubes[d][e][:, lo:hi].sum(axis=1, dtype=np.float64)
            m /= max(len(v), 1)
            i0 = np.nanmean(self.i0[v], axis=0) if v.size else np.full(self.n_pix, np.nan)
        if per_i0:
            with np.errstate(all="ignore"):
                m = np.where(i0 > 0, m / i0, np.nan)
        return m

    def xanes(self, det: int | None, lo: int, hi: int, pix=None, per_i0: bool = True) -> np.ndarray:
        """XANES trace: window counts summed over the pixels at every energy (NaN at skipped energies)."""
        dets = range(len(DETS)) if det is None else [det]
        p_idx = slice(None) if pix is None else np.asarray(pix, int)
        out = np.full(self.n_energies, np.nan)
        for e in range(self.n_energies):
            if not self.valid[e]:
                continue
            s = sum(float(self.cubes[d][e][p_idx][:, lo:hi].sum()) for d in dets)
            if per_i0:
                i0 = float(self.i0[e][p_idx].sum())
                s = s / i0 if i0 > 0 else np.nan
            out[e] = s
        return out

    def rgb(self, det: int | None, windows: list[tuple[int, int]], energy_index: int | None = None,
            clip: tuple[float, float] = (1, 99)) -> np.ndarray:
        """(n_pix, 3) composite of three channel windows, each stretched between its percentiles."""
        out = np.zeros((self.n_pix, 3))
        for k, (lo, hi) in enumerate(windows[:3]):
            m = self.window_map(det, lo, hi, energy_index)
            fin = np.isfinite(m)
            if fin.any():
                a, b = np.percentile(m[fin], clip)
                out[:, k] = np.clip((m - a) / (b - a if b > a else 1.0), 0, 1)
        return out

    # ------------------------------------------------------------------ statistics on pixels
    def pixel_matrix(self, det: int | None, lo: int, hi: int, per_i0: bool = True) -> np.ndarray:
        """(n_pix, nE_valid) XANES per pixel for the window: the matrix PCA and k-means work on."""
        dets = range(len(DETS)) if det is None else [det]
        v = np.where(self.valid)[0]
        M = np.zeros((self.n_pix, v.size))
        for j, e in enumerate(v):
            col = sum(self.cubes[d][e][:, lo:hi].sum(axis=1, dtype=np.float64) for d in dets)
            if per_i0:
                with np.errstate(all="ignore"):
                    col = np.where(self.i0[e] > 0, col / self.i0[e], np.nan)
            M[:, j] = col
        return M

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, project: Project, scan: ScanInfo, progress=None, calibration: Calibration | None = None) -> "Hypercube":
        h5 = scan.h5 or find_h5(scan.path)
        if h5 is None:
            raise FileNotFoundError(f"{scan.path}: no .h5 file")
        x, y = xy(h5)
        edirs = scan.energy_dirs or stack_energy_dirs(scan.path)
        E = np.array([e for e, _ in edirs], float)
        nE, npts = len(E), len(x)
        cdir = project.root / ".aurora" / "cache" / scan.day / scan.name
        key = f"cube|{nE}|{npts}|{stack_last_mtime(edirs):.0f}"
        meta_p = cdir / "cube_meta.json"
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            if meta.get("key") == key and all((cdir / f"cube_{d}.npy").exists() for d in DETS):
                cubes = [np.load(cdir / f"cube_{d}.npy", mmap_mode="r") for d in DETS]
                aux = np.load(cdir / "cube_aux.npz")
                hc = cls(name=scan.name, E=E, x=x, y=y, cubes=cubes, i0=aux["i0"], tey=aux["tey"], valid=aux["valid"].astype(bool),
                         calibration=calibration or project.calibration, skipped=[tuple(s) for s in meta.get("skipped", [])])
                hc.pitch = grid_pitch(x, y)
                log.info("%s: hypercube from cache %s", scan.name, cdir)
                return hc
        except (OSError, ValueError):
            pass
        cubes = [np.zeros((nE, npts, N_CH), np.uint32) for _ in DETS]
        i0 = np.full((nE, npts), np.nan); tey = np.full((nE, npts), np.nan)
        valid = np.zeros(nE, bool); skipped = []
        for i, (e, p) in enumerate(edirs):
            if progress:
                progress(i, nE, f"{scan.name} {e:.2f} eV")
            try:
                pd = read_point_dir(p)
            except (OSError, ValueError) as err:
                skipped.append((e, str(err))); continue
            if not pd.complete or pd.n_pix < npts:
                skipped.append((e, f"incomplete map ({pd.n_mcc}/{npts} points)")); continue
            for d in range(len(DETS)):
                cubes[d][i] = pd.sdd[d][:npts]
            i0[i] = pd.mcc[:npts, I0_COL]; tey[i] = pd.mcc[:npts, TEY_COL]
            valid[i] = True
        try:
            from aurora.core.io.safety import assert_writable
            assert_writable(cdir)
            cdir.mkdir(parents=True, exist_ok=True)
            for d, c in zip(DETS, cubes):
                np.save(cdir / f"cube_{d}.npy", c)
            np.savez(cdir / "cube_aux.npz", i0=i0, tey=tey, valid=valid)
            meta_p.write_text(json.dumps({"key": key, "skipped": skipped}), encoding="utf-8")
            cubes = [np.load(cdir / f"cube_{d}.npy", mmap_mode="r") for d in DETS]
        except OSError as err:
            log.warning("%s: hypercube cache not written (%s)", cdir, err)
        hc = cls(name=scan.name, E=E, x=x, y=y, cubes=cubes, i0=i0, tey=tey, valid=valid, calibration=calibration or project.calibration,
                 skipped=skipped)
        hc.pitch = grid_pitch(x, y)
        log.info("%s: hypercube %d energies x %d pixels x 256 x 4 read from raw (%d skipped)", scan.name, nE, npts, len(skipped))
        return hc


# ----------------------------------------------------------------------------- pixel statistics
@dataclass
class ClusterResult:
    labels: np.ndarray                  # (n_pix,) cluster index, -1 for invalid pixels
    centres: np.ndarray                 # (k, nE)
    scores: np.ndarray                  # (n_pix, n_comp) PCA scores
    explained: np.ndarray
    E: np.ndarray


def pca_kmeans(M: np.ndarray, E: np.ndarray, n_components: int = 5, n_clusters: int = 4, seed: int = 0) -> ClusterResult:
    """PCA on the standardised per-pixel XANES, k-means on the scores (scipy kmeans2); cluster centres = mean spectra."""
    from scipy.cluster.vq import kmeans2
    ok = np.all(np.isfinite(M), axis=1) & (M.sum(axis=1) != 0)
    X = M[ok]
    mean = X.mean(axis=0); std = X.std(axis=0); std[std == 0] = 1.0
    Z = (X - mean) / std
    U, s, Vt = np.linalg.svd(Z, full_matrices=False)
    k = min(n_components, len(s))
    scores = (U * s)[:, :k]
    var = s ** 2; explained = var / var.sum() if var.sum() else var
    rng = np.random.default_rng(seed)
    centroid, lab = kmeans2(scores, min(n_clusters, len(scores)), minit="++", seed=rng)
    labels = np.full(M.shape[0], -1); labels[ok] = lab
    centres = np.array([X[lab == c].mean(axis=0) if (lab == c).any() else np.full(M.shape[1], np.nan) for c in range(min(n_clusters, len(scores)))])
    full_scores = np.full((M.shape[0], k), np.nan); full_scores[ok] = scores
    return ClusterResult(labels=labels, centres=centres, scores=full_scores, explained=explained[:k], E=E)


# ----------------------------------------------------------------------------- PyMca exports
def _snap_grid(x: np.ndarray, y: np.ndarray, pitch: tuple[float, float]):
    px, py = pitch
    ix = np.round((x - x.min()) / px).astype(int); iy = np.round((y - y.min()) / py).astype(int)
    return ix, iy, int(ix.max()) + 1, int(iy.max()) + 1


def export_pymca_xanes(hc: Hypercube, path: Path, det: int | None, lo: int, hi: int, per_i0: bool = True, title: str = "") -> Path:
    """NeXus-style 3D stack (y, x, energy) that PyMca opens for PCA / cluster analysis (the CLS `_PCA-CA.h5` layout,
    axis order (Y, X, E) as the code wrote it, not as its guide said)."""
    import h5py
    ix, iy, nx, ny = _snap_grid(hc.x, hc.y, hc.pitch)
    v = np.where(hc.valid)[0]
    with h5py.File(path, "w") as h:
        h.attrs["NX_class"] = "NXroot"; h.attrs["default"] = "entry"
        ent = h.create_group("entry"); ent.attrs["NX_class"] = "NXentry"; ent.attrs["default"] = "measurement"
        ent.create_dataset("title", data=title or hc.name)
        meas = ent.create_group("measurement"); meas.attrs["NX_class"] = "NXdata"
        dets = range(len(DETS)) if det is None else [det]
        total = np.zeros((ny, nx, v.size), np.float32)
        for d in dets:
            arr = np.zeros((ny, nx, v.size), np.float32)
            for j, e in enumerate(v):
                col = hc.cubes[d][e][:, lo:hi].sum(axis=1, dtype=np.float64)
                if per_i0:
                    with np.errstate(all="ignore"):
                        col = np.where(hc.i0[e] > 0, col / hc.i0[e], 0.0)
                arr[iy, ix, j] = col
            meas.create_dataset(DETS[d], data=arr, compression="gzip")
            total += arr
        meas.create_dataset("sum", data=total, compression="gzip")
        meas.create_dataset("average", data=total / max(len(list(dets)), 1), compression="gzip")
        meas.create_dataset("energy", data=hc.E[v]); meas.create_dataset("x", data=hc.x.min() + hc.pitch[0] * np.arange(nx))
        meas.create_dataset("y", data=hc.y.min() + hc.pitch[1] * np.arange(ny))
        meas.attrs["signal"] = "average"; meas.attrs["axes"] = ["y", "x", "energy"]
        meas.attrs["channel_roi"] = [lo, hi]; meas.attrs["normalized"] = bool(per_i0)
        md = h.create_group("stack_metadata")
        md.attrs["scan_name"] = hc.name; md.attrs["nx"] = nx; md.attrs["ny"] = ny; md.attrs["aurora"] = "export_pymca_xanes"
    log.info("%s: PyMca XANES stack written (%d x %d x %d)", path, ny, nx, v.size)
    return Path(path)


def export_pymca_4d(hc: Hypercube, path: Path, per_i0: bool = True, title: str = "") -> Path:
    """The CLS `_Elemental_PyMca.h5` layout: xrf (y, x, 256) summed over energies, xanes sum over channels, and the
    full (y, x, energy, 256) cube per detector and summed."""
    import h5py
    ix, iy, nx, ny = _snap_grid(hc.x, hc.y, hc.pitch)
    v = np.where(hc.valid)[0]
    with h5py.File(path, "w") as h:
        h.create_dataset("energy", data=hc.E[v])
        ent = h.create_group("entry"); ent.create_dataset("title", data=title or hc.name)
        xrf = ent.create_group("xrf_measurement"); xan = ent.create_group("xanes_measurement"); full = ent.create_group("full_4d_measurement")
        xrf_sum = np.zeros((ny, nx, N_CH), np.float32); xan_sum = np.zeros((ny, nx, v.size), np.float32)
        full_sum = np.zeros((ny, nx, v.size, N_CH), np.float32)
        for d in range(len(DETS)):
            cube4 = np.zeros((ny, nx, v.size, N_CH), np.float32)
            for j, e in enumerate(v):
                block = hc.cubes[d][e].astype(np.float32)
                if per_i0:
                    with np.errstate(all="ignore"):
                        block = block / np.where(hc.i0[e] > 0, hc.i0[e], np.nan)[:, None]
                        block = np.nan_to_num(block)
                cube4[iy, ix, j, :] = block
            xrf.create_dataset(DETS[d], data=cube4.sum(axis=2), compression="gzip")
            xan.create_dataset(DETS[d], data=cube4.sum(axis=3), compression="gzip")
            full.create_dataset(DETS[d], data=cube4, compression="gzip")
            xrf_sum += cube4.sum(axis=2); xan_sum += cube4.sum(axis=3); full_sum += cube4
        xrf.create_dataset("sdd_xrf_stack_3d", data=xrf_sum, compression="gzip")
        xan.create_dataset("sdd_xanes_stack_3d", data=xan_sum, compression="gzip")
        full.create_dataset("sdd_sum_4d", data=full_sum, compression="gzip")
        full.attrs["nx"] = nx; full.attrs["ny"] = ny; full.attrs["rows"] = ny; full.attrs["cols"] = nx
        md = h.create_group("stack_metadata"); md.attrs["scan_name"] = hc.name; md.attrs["aurora"] = "export_pymca_4d"
    log.info("%s: PyMca 4D file written", path)
    return Path(path)
