"""One stack as the zone tool sees it: energies, pixel positions, per-pixel I0 / TEY / ROI counts per detector.

Source, in order: sgm_report's `processed/<day>/<scan>/_reduced_cache.npz`, AURORA's own cache under
`<root>/.aurora/cache/<day>/<scan>/`, else a reduction from the raw energy folders with the fixed ROI bins (the rule of
sgm_report `Stack.reduce`: incomplete maps are skipped and NaN, I0 <= 0 energies are invalid).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from aurora.core.constants import DETS, I0_COL, TEY_COL
from aurora.core.io.cache import ReducedCache, save_npz
from aurora.core.io.config import RoiBins
from aurora.core.io.h5 import find_h5, xy
from aurora.core.io.raw import read_point_dir, stack_energy_dirs, stack_last_mtime
from aurora.core.lines import edge_energy, main_line
from aurora.core.model import Project, ScanInfo
from aurora.core.names import pretty_edge, target_edge
from aurora.core.zones import grid_pitch

log = logging.getLogger("aurora.stack")

TOTAL_LABEL = "total XRF"
TOTAL_BOUNDS = (5, 200)


@dataclass
class StackData:
    id: str                             # '<day>_<scan folder>' (the zone tool's stack id)
    name: str
    day: str
    E: np.ndarray                       # (nE,)
    x: np.ndarray                       # (n_pix,)
    y: np.ndarray
    i0: np.ndarray                      # (nE, n_pix), NaN at skipped energies
    tey: np.ndarray
    roi_pix_det: np.ndarray             # (nE, n_pix, n_roi, 4) float32, NaN at skipped energies
    valid: np.ndarray                   # (nE,) bool
    labels: list[str]
    main: str
    edge: tuple[str, str] | None = None
    edge_ev: float | None = None
    source: str = ""                    # where the arrays came from
    notes: list[str] = field(default_factory=list)
    pitch: tuple[float, float] = (0.1, 0.1)

    @property
    def n_energies(self) -> int:
        return int(len(self.E))

    @property
    def n_pix(self) -> int:
        return int(len(self.x))

    @property
    def edge_text(self) -> str:
        return pretty_edge(self.edge)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Footprint on the plate in mm, half a pitch beyond the outermost pixel centres (zone tool `stackBBox`)."""
        px, py = self.pitch
        return (float(self.x.min() - px / 2), float(self.x.max() + px / 2),
                float(self.y.min() - py / 2), float(self.y.max() + py / 2))

    def roi_index(self, label: str) -> int:
        return self.labels.index(label)

    def sum_over(self, pix) -> dict:
        """Zone sums over pixel indices: {'i0': (nE,), 'tey': (nE,), 'det': {label: (nE, 4)}, 'n': n_pix}.
        NaN where every pixel is NaN (skipped energy)."""
        pix = np.asarray(pix, int)
        out = {"n": int(pix.size), "i0": np.full(self.n_energies, np.nan), "tey": np.full(self.n_energies, np.nan), "det": {}}
        if pix.size == 0:
            return out
        out["i0"] = self.i0[:, pix].sum(axis=1)
        out["tey"] = self.tey[:, pix].sum(axis=1)
        for k, lab in enumerate(self.labels):
            out["det"][lab] = self.roi_pix_det[:, pix, k, :].sum(axis=1, dtype=float)
        return out

    def i0_mean(self) -> np.ndarray:
        """Per-energy mean of I0 over the pixels (NaN at skipped energies)."""
        with np.errstate(all="ignore"):
            return np.nanmean(self.i0, axis=1)

    def map_values(self, label: str, det: int | None = None, mode: str = "above", energy_index: int | None = None,
                   per_i0: bool = True) -> np.ndarray:
        """Per-pixel map: ROI counts of `label` (one detector or the 4-SDD sum), integrated 'above' the edge, over
        'all' valid energies, 'at' one energy, or the edge 'jump' (above minus below); divided by I0 per pixel when asked."""
        k = self.roi_index(label)
        a = self.roi_pix_det[:, :, k, :].sum(axis=2) if det is None else self.roi_pix_det[:, :, k, det]
        a = a.astype(float)
        if per_i0:
            with np.errstate(all="ignore"):
                a = np.where(self.i0 > 0, a / self.i0, np.nan)
        v = self.valid
        if mode == "at" and energy_index is not None:
            return a[energy_index]
        if self.edge_ev is not None and self.E.min() < self.edge_ev < self.E.max():
            above = v & (self.E >= self.edge_ev)
            below = v & (self.E < self.edge_ev)
        else:
            above, below = v, np.zeros_like(v)
        with np.errstate(all="ignore"):
            if mode == "jump" and below.any():
                return np.nanmean(a[above], axis=0) - np.nanmean(a[below], axis=0)
            if mode == "above" and above.any():
                return np.nanmean(a[above], axis=0)
            return np.nanmean(a[v], axis=0)

    # ------------------------------------------------------------------ loading
    @classmethod
    def load(cls, project: Project, scan: ScanInfo, progress=None, allow_reduce: bool = True) -> "StackData":
        h5 = scan.h5 or find_h5(scan.path)
        if h5 is None:
            raise FileNotFoundError(f"{scan.path}: no .h5 file")
        x, y = xy(h5)
        edirs = scan.energy_dirs or stack_energy_dirs(scan.path)
        E = np.array([e for e, _ in edirs], float)
        edge = scan.edge or target_edge(scan.name)
        edge_ev = edge_energy(*edge) if edge else None
        mode = scan.summary.get("mode") if scan.summary else None
        sid = f"{scan.day}_{scan.name}"
        for src, cdir in (("sgm_report", project.processed / scan.day / scan.name),
                          ("aurora", project.root / ".aurora" / "cache" / scan.day / scan.name)):
            rc = ReducedCache.load(cdir)
            if rc is None:
                continue
            if rc.n_pix > len(x) or rc.n_energies != len(E):
                log.info("%s: cache %s has %d energies x %d pixels but the scan has %d x %d; ignored",
                         scan.name, cdir, rc.n_energies, rc.n_pix, len(E), len(x))
                continue
            n = rc.n_pix
            sd = cls(id=sid, name=scan.name, day=scan.day, E=E, x=x[:n], y=y[:n], i0=rc.i0, tey=rc.tey,
                     roi_pix_det=rc.roi_pix_det, valid=rc.valid, labels=list(rc.roi_labels),
                     main=main_line(rc.roi_labels, edge, mode), edge=edge, edge_ev=edge_ev,
                     source=f"{src} cache {rc.path}", notes=list(rc.notes))
            sd.pitch = grid_pitch(sd.x, sd.y)
            return sd
        if not allow_reduce:
            raise FileNotFoundError(f"{scan.name}: no reduced cache")
        return cls.reduce(project, scan, x, y, edirs, edge, edge_ev, mode, progress)

    @classmethod
    def reduce(cls, project: Project, scan: ScanInfo, x, y, edirs, edge, edge_ev, mode, progress=None) -> "StackData":
        """Reduce the raw energy folders with the fixed ROI bins of the project (+ the total), cache the result."""
        bins: RoiBins = project.roi_bins or RoiBins()
        labels = [l for l in bins.labels if bins.has(l)] + [TOTAL_LABEL]
        bounds = {l: bins.bounds(l) for l in labels if l != TOTAL_LABEL}
        bounds[TOTAL_LABEL] = {d: TOTAL_BOUNDS for d in DETS}
        nE, npts, nroi = len(edirs), len(x), len(labels)
        E = np.array([e for e, _ in edirs], float)
        i0 = np.full((nE, npts), np.nan); tey = np.full((nE, npts), np.nan)
        rpd = np.full((nE, npts, nroi, len(DETS)), np.nan, dtype=np.float32)
        skipped, notes = [], []
        for i, (e, p) in enumerate(edirs):
            if progress:
                progress(i, nE, f"{scan.name} {e:.2f} eV")
            try:
                pd = read_point_dir(p)
            except (OSError, ValueError) as err:
                skipped.append((e, str(err))); continue
            if not pd.complete or pd.n_pix < npts:
                skipped.append((e, f"incomplete map ({pd.n_mcc}/{npts} points)")); continue
            i0[i] = pd.mcc[:npts, I0_COL]; tey[i] = pd.mcc[:npts, TEY_COL]
            for k, lab in enumerate(labels):
                for dd, d in enumerate(DETS):
                    lo, hi = bounds[lab][d]
                    rpd[i, :, k, dd] = pd.sdd[dd][:npts, lo:hi].sum(axis=1)
        if skipped:
            notes.append(f"{len(skipped)} energy step(s) skipped: " + "; ".join(f"{e} eV {why}" for e, why in skipped[:4]))
        with np.errstate(all="ignore"):
            i0m = np.nanmean(i0, axis=1)
        bad = ~(i0m > 0)
        valid = ~bad & ~np.isin(E, [e for e, _ in skipped])
        if bad.any():
            notes.append(f"I0 <= 0 at {E[bad].tolist()[:6]}")
        log.info("%s: reduced %d energies x %d pixels x %d ROIs from raw (%d skipped)", scan.name, nE, npts, nroi, len(skipped))
        cdir = project.root / ".aurora" / "cache" / scan.day / scan.name
        sig = f"aurora|{nE}|{stack_last_mtime(edirs):.0f}|{sorted(bounds.items())}"
        try:
            save_npz(cdir / "_reduced_cache.npz", signature=sig, i0=i0, tey=tey, roi_pix_det=rpd,
                     roi_det=np.nansum(rpd, axis=1), valid=valid, roi_labels=np.array(labels, dtype=object),
                     skipped=np.array(skipped, dtype=object), notes=np.array(notes, dtype=object))
        except OSError as err:
            log.warning("%s: could not write the cache (%s)", cdir, err)
        sd = cls(id=f"{scan.day}_{scan.name}", name=scan.name, day=scan.day, E=E, x=x, y=y, i0=i0, tey=tey, roi_pix_det=rpd,
                 valid=valid, labels=labels, main=main_line(labels, edge, mode), edge=edge, edge_ev=edge_ev,
                 source="reduced from raw", notes=notes)
        sd.pitch = grid_pitch(sd.x, sd.y)
        return sd
