"""The sgm_report.py v2.5 caches under processed/<day>/<scan>/ (70 of them exist for the Nov 2026 beamtime):
`_reduced_cache.npz` (per-pixel, per-detector ROI sums), `_spectra_cache.npz`, `_count_rate.json`.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("aurora.io")


def load_npz(path: Path | None):
    """npz or None (missing, truncated by a killed run, or unreadable)."""
    if path is None or not Path(path).exists():
        return None
    try:
        return np.load(path, allow_pickle=True)
    except Exception:
        return None


def save_npz(path: Path, **arrays) -> None:
    """Atomic write: a run killed mid-way never leaves a half-written cache behind."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    with open(tmp, "wb") as f:                  # explicit handle: closed before the rename (Windows refuses otherwise)
        np.savez(f, **arrays)
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:                 # a virus scanner may still hold the new file for a moment
            time.sleep(0.2 * (attempt + 1))
    os.replace(tmp, path)


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", label.replace("α", "a").replace("β", "b").replace("ℓ", "l"))


def roi_labels_from_csv(scan_proc_dir: Path, nroi: int) -> list[str] | None:
    """ROI labels from the newest `*_xanes.csv` written by sgm_report (older caches lack `roi_labels`).

    The `PFY_<slug>_over_I0` columns are in roi_keys order; the '# ROIs' header line maps slug -> label.
    Newest file first: stale zone CSVs may remain when deletion was refused on the share.
    """
    csvs = sorted(Path(scan_proc_dir).glob("*_xanes.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not csvs:
        return None
    hdr, cols = [], None
    with open(csvs[0], encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                hdr.append(line.rstrip("\n"))
            else:
                cols = [c.strip() for c in line.split(",")]
                break
    known = {"totalXRF": "total XRF"}
    for h in hdr:
        if h.startswith("# ROIs"):
            for part in h.split(": ", 1)[1].split("; "):
                m = re.match(r"(.+?) \d+ eV ch ", part)
                if m:
                    known[_slug(m.group(1))] = m.group(1)
    if not cols:
        return None
    order = [c[4:-8] for c in cols if c.startswith("PFY_") and c.endswith("_over_I0") and not re.search(r"_SDD\d_over_I0$", c)]
    labels = [known.get(sl, sl) for sl in order]
    return labels if len(labels) == nroi else None


@dataclass
class ReducedCache:
    """`_reduced_cache.npz`: the fixed-window ROI reduction of one stack, per pixel and per detector."""
    path: Path
    signature: str
    i0: np.ndarray                  # (nE, n_pix)
    tey: np.ndarray                 # (nE, n_pix)
    roi_pix_det: np.ndarray         # (nE, n_pix, n_roi, 4) float32
    roi_det: np.ndarray             # (nE, n_roi, 4) summed over pixels
    valid: np.ndarray               # (nE,) bool, False for skipped energies
    roi_labels: list[str]
    skipped: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def n_energies(self) -> int:
        return int(self.i0.shape[0])

    @property
    def n_pix(self) -> int:
        return int(self.i0.shape[1])

    @property
    def roi_pix(self) -> np.ndarray:
        """ROI sums over the four detectors, (nE, n_pix, n_roi)."""
        return self.roi_pix_det.sum(axis=3, dtype=float)

    @classmethod
    def load(cls, scan_proc_dir: Path) -> "ReducedCache | None":
        p = Path(scan_proc_dir) / "_reduced_cache.npz"
        z = load_npz(p)
        if z is None or "roi_pix_det" not in z.files:
            return None
        nroi = int(z["roi_pix_det"].shape[2])
        labels = [str(v) for v in z["roi_labels"]] if "roi_labels" in z.files else None
        if not labels or len(labels) != nroi:
            labels = roi_labels_from_csv(scan_proc_dir, nroi) or [f"roi{i + 1}" for i in range(nroi)]
        rc = cls(path=p, signature=str(z["signature"]), i0=z["i0"], tey=z["tey"], roi_pix_det=z["roi_pix_det"],
                 roi_det=z["roi_det"], valid=z["valid"].astype(bool), roi_labels=labels,
                 skipped=[tuple(s) for s in z["skipped"]] if "skipped" in z.files else [],
                 notes=[str(n) for n in z["notes"]] if "notes" in z.files else [])
        log.info("%s: reduced cache %d energies x %d pixels, ROIs %s", p, rc.n_energies, rc.n_pix, labels)
        return rc


def load_count_rate(scan_proc_dir: Path) -> dict | None:
    """`_count_rate.json`: total counts of the brightest pixels at the probed energies (dict with 'rows')."""
    p = Path(scan_proc_dir) / "_count_rate.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return None
