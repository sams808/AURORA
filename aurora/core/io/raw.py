"""Raw SGM scan folders: energy sub-folders, the mcc_flyer csv + sdd*_0.bin of one energy, stack status.

Ported from sgm_report.py v2.5 (`energy_from_dirname`, `read_point_dir`, `stack_energy_dirs`, `stack_status`).
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aurora.core.constants import DETS, IN_PROGRESS_MIN, MIN_SPAN_EV, N_CH
from aurora.core.io.h5 import stack_plan

log = logging.getLogger("aurora.io")

_ENERGY_DIR = re.compile(r"_(\d+)_(\d+)eV$")


def energy_from_dirname(name: str) -> float | None:
    """'alpha_beta_f_k_685_25eV' -> 685.25; None when the folder is not an energy folder."""
    m = _ENERGY_DIR.search(name)
    return float(f"{m.group(1)}.{m.group(2)}") if m else None


def scan_kind(name: str) -> str:
    """'survey', 'stack' or 'other' from the scan folder name."""
    if name.endswith("_survey"):
        return "survey"
    if name.endswith("_stack"):
        return "stack"
    return "other"


@dataclass
class PointData:
    """One energy folder: the mcc scaler rows and the four SDD spectra, truncated to the common pixel count."""
    mcc: np.ndarray                     # (n_pix, 8) float
    sdd: list[np.ndarray]               # 4 x (n_pix, 256) uint32
    complete: bool                      # every sdd file had as many pixels as the mcc file
    n_mcc: int                          # rows in the mcc file before truncation

    @property
    def n_pix(self) -> int:
        return self.mcc.shape[0]


def read_point_dir(d: Path) -> PointData:
    """Read `mcc_flyer_0.csv` and `sdd1..4_0.bin` of one energy folder.

    Raises OSError / ValueError on a missing or unreadable file; the caller decides whether to skip the energy.
    """
    d = Path(d)
    mcc = np.loadtxt(d / "mcc_flyer_0.csv", delimiter=",", ndmin=2)
    sdd = []
    for det in DETS:
        a = np.fromfile(d / f"{det}_0.bin", dtype=np.uint32)
        sdd.append(a[:(len(a) // N_CH) * N_CH].reshape(-1, N_CH))
    n = min([mcc.shape[0]] + [s.shape[0] for s in sdd])
    complete = all(s.shape[0] == mcc.shape[0] for s in sdd)
    if not complete:
        log.info("%s: incomplete energy folder (mcc %d rows, sdd %s) -> truncated to %d pixels",
                 d.name, mcc.shape[0], [s.shape[0] for s in sdd], n)
    return PointData(mcc=mcc[:n], sdd=[s[:n] for s in sdd], complete=complete, n_mcc=int(mcc.shape[0]))


def stack_energy_dirs(stack_dir: Path) -> list[tuple[float, Path]]:
    """Energy sub-folders of a stack, sorted by energy (one scandir; the mounted share is slow)."""
    out = []
    with os.scandir(stack_dir) as it:
        for en in it:
            e = energy_from_dirname(en.name)
            if e is not None and en.is_dir():
                out.append((e, Path(en.path)))
    return sorted(out)


def stack_last_mtime(edirs: list[tuple[float, Path]]) -> float:
    """mtime of the newest of the last three energy folders (cheap proxy for 'files changed')."""
    return max(p.stat().st_mtime for _, p in edirs[-3:]) if edirs else 0.0


def stack_status(stack_dir: Path, edirs: list[tuple[float, Path]] | None = None,
                 planned: list[float] | None = None, now: float | None = None, name_test: bool | None = None,
                 span_test: bool = True) -> tuple[str, str]:
    """('complete' | 'aborted' | 'in_progress' | 'test' | 'empty', reason).

    test = beamline diagnostic by name (names.is_test_name, or `name_test` when the caller decided), or a span below
    MIN_SPAN_EV (unless span_test is False); aborted = fewer energy folders than planned and untouched for
    IN_PROGRESS_MIN minutes; in_progress = incomplete but still being written.
    """
    stack_dir = Path(stack_dir)
    ed = stack_energy_dirs(stack_dir) if edirs is None else edirs
    n = len(ed)
    if n == 0:
        return "empty", "no energy folders"
    if name_test is None:
        from aurora.core.names import is_test_name
        name_test = is_test_name(stack_dir.name)
    if name_test:
        return "test", "beamline diagnostic (name), not a sample scan"
    span = ed[-1][0] - ed[0][0]
    if span_test and span < MIN_SPAN_EV:
        return "test", f"{n} energies over {span:.1f} eV"
    if planned is None:
        _, planned = stack_plan(stack_dir)
    if planned and n < len(planned):
        age_min = ((time.time() if now is None else now) - stack_last_mtime(ed)) / 60
        if age_min < IN_PROGRESS_MIN:
            return "in_progress", f"{n}/{len(planned)} energies, last file {age_min:.0f} min ago"
        return "aborted", f"{n}/{len(planned)} planned energies"
    return "complete", f"{n} energies"
