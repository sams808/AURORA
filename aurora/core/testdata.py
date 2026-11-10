"""A small synthetic beamtime folder in the real raw layout, for the tests, the demo and the self-test.

One survey, one complete stack, one aborted stack and one diagnostic (test) stack, 12 pixels on a 4 x 3 snake grid,
F K alpha peak near channel 65 of every SDD, I0 and TEY with an edge step. Variants: more holders later the same day
(n_holders), the scan folders loose in one folder as John Bussey's GUI kept them (loose), three zones
(make_fake_zones), and the demo's reduced spectra and references (write_demo_extras). Nothing here is real data.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np

from aurora.core.constants import DETS, N_CH, N_MCC
from aurora.core.io.h5 import open_h5

DAY = "2026-11-05"
SURVEY = "010013_holderA_survey"
STACK = "092758_alpha_beta_f_k_stack"
ABORTED = "101456_gamma_f_k_stack"
TEST = "143504_photodiode_f_k_stack"
ENERGIES = (680.0, 683.0, 686.0, 689.0, 692.0)      # 3 energies already span 6 eV (> MIN_SPAN_EV): an aborted stack is not a test
# The demo's complete stack (aurora demo, Open the demo beamtime): 660-740 eV in 2 eV steps around the step at 682 eV, so
# a zone saved by the tutorials' Easy path spans Compare's E0-relative windows (pre -20..-5, post +15..+35 eV) and
# plots; on ENERGIES (12 eV) every window is empty. The tests' fake project keeps ENERGIES.
DEMO_STACK_ENERGIES = tuple(660.0 + 2.0 * i for i in range(41))
# holders 2.. (n_holders): a survey on the hour from 15:00, then one complete stack; holder names N6, N7, ...
EXTRA_SAMPLES = ("CaF2", "NaF", "BaF2", "LiF", "MgF2", "SrF2", "AlF3", "KF")
SURVEY2 = "150000_holderB_survey"
STACK2 = "151502_CaF2_f_k_stack"


def snake_grid(nx: int = 4, ny: int = 3, pitch: float = 0.25, x0: float = -0.5, y0: float = 5.0):
    """Points in acquisition order: row 0 left to right, row 1 right to left, ..."""
    xs, ys = [], []
    for j in range(ny):
        cols = range(nx) if j % 2 == 0 else range(nx - 1, -1, -1)
        for i in cols:
            xs.append(x0 + i * pitch)
            ys.append(y0 + j * pitch)
    return np.array(xs), np.array(ys)


def _write_point_dir(d: Path, n_pix: int, e_ev: float, rng: np.random.Generator, i0_level: float = 2.5) -> None:
    d.mkdir(parents=True, exist_ok=True)
    step = 1.0 + 0.8 * (e_ev >= 682.0)
    mcc = np.zeros((n_pix, N_MCC))
    mcc[:, 0] = i0_level + 0.01 * rng.standard_normal(n_pix)
    mcc[:, 1] = 1.0
    mcc[:, 3] = 0.5 * step + 0.01 * rng.standard_normal(n_pix)
    mcc[:, 4:] = -10.2
    with open(d / "mcc_flyer_0.csv", "w", newline="\n", encoding="utf-8") as f:
        f.write("# " + ",".join(f"ch{i + 1}" for i in range(N_MCC)) + "\n")
        for row in mcc:
            f.write(",".join(f"{v:.6f}" for v in row) + "\n")
    ch = np.arange(N_CH)
    for k, det in enumerate(DETS):
        peak = 200 * step * np.exp(-0.5 * ((ch - (65 + k)) / 3.0) ** 2)
        spec = rng.poisson(peak + 2.0, size=(n_pix, N_CH)).astype(np.uint32)
        spec.tofile(d / f"{det}_0.bin")


def _write_stack(scan_dir: Path, base: str, energies, n_dirs: int, x, y, rng, time_per_map: float = 6.0,
                 created: str = "Thu Nov  5 09:28:00 2026") -> None:
    scan_dir.mkdir(parents=True, exist_ok=True)
    with open_h5(scan_dir / f"{DAY}_{scan_dir.name[:6]}_stack_data.h5", "w") as h:
        h.create_dataset("hexapod_waves/x", data=x)
        h.create_dataset("hexapod_waves/y", data=y)
        h.create_dataset("map_data/energy", data=np.array(energies[:n_dirs]))
        g = h.create_group("stack_metadata")
        g.attrs["energies"] = np.array2string(np.array(energies), max_line_width=10 ** 6)
        g.attrs["time_per_map"] = time_per_map
        g.attrs["plan_name"] = "stack"
        g.attrs["scan_name"] = base
        g.attrs["num_maps"] = len(energies)
        for k, v in zip(("xstart", "xstop", "ystart", "ystop"), (x.min(), x.max(), y.min(), y.max())):
            g.attrs[k] = float(v)
        h.attrs["file_creation_timestamp_iso"] = created
    for e in energies[:n_dirs]:
        name = f"{base}_{int(e)}_{int(round((e % 1) * 100)):02d}eV"
        _write_point_dir(scan_dir / name, len(x), e, rng)


def _write_survey(sdir: Path, tag: str, hhmmss: str, rng: np.random.Generator) -> None:
    """A one-energy survey map (8 x 6 points) named like the beamline names it."""
    sx, sy = snake_grid(nx=8, ny=6, pitch=0.2, x0=-1.0, y0=-0.6)
    sdir.mkdir(parents=True, exist_ok=True)
    with open_h5(sdir / f"{tag}_survey_{DAY}_{hhmmss}_695.00eV.h5", "w") as h:
        h.create_dataset("hexapod_waves/x", data=sx)
        h.create_dataset("hexapod_waves/y", data=sy)
        g = h.create_group("scan_metadata")
        g.attrs["energy"] = 695.0
        g.attrs["step_size"] = 0.2
        g.attrs["plan_name"] = "survey"
        g.attrs["scan_name"] = f"{tag}_survey"
        h.attrs["ring_current"] = 219.0
    _write_point_dir(sdir, len(sx), 695.0, rng)


def make_fake_project(root: Path, old_minutes: float = 120.0, n_holders: int = 1, loose: bool = False,
                      stack_energies=ENERGIES) -> Path:
    """Create root/raw/<DAY>/... with empty processed/ and config/; returns the project root.

    stack_energies: the energies of the complete stack (the demo uses DEMO_STACK_ENERGIES).

    n_holders > 1 adds holders N6, N7, ... later the same day (a survey on the hour from 15:00 and one complete stack
    each, see SURVEY2 / STACK2); holder N5 is identical in every variant. loose=True writes the scan folders directly
    into `root` (no raw/<day>, processed/ or config/), the way John Bussey's GUI kept them ('CLS GUI/<scan>')."""
    if not 1 <= n_holders <= 1 + len(EXTRA_SAMPLES):
        raise ValueError(f"n_holders must be 1..{1 + len(EXTRA_SAMPLES)}")
    root = Path(root)
    raw_day = root if loose else root / "raw" / DAY
    rng = np.random.default_rng(7)
    x, y = snake_grid()
    _write_survey(raw_day / SURVEY, "holderA", "010013", rng)
    # complete stack
    _write_stack(raw_day / STACK, "alpha_beta_f_k", stack_energies, len(stack_energies), x, y, rng)
    # aborted stack: 3 of 5 planned energies, untouched for a while
    _write_stack(raw_day / ABORTED, "gamma_f_k", ENERGIES, 3, x, y, rng)
    old = time.time() - 60 * old_minutes
    for p in (raw_day / ABORTED).rglob("*"):
        os.utime(p, (old, old))
    os.utime(raw_day / ABORTED, (old, old))
    # diagnostic stack: named photodiode, 3 energies over 2 eV
    _write_stack(raw_day / TEST, "photodiode_f_k", ENERGIES[:3], 3, x, y, rng)
    for k in range(2, n_holders + 1):
        hh = 13 + k                                       # holder 2 at 15:00, holder 3 at 16:00, ...
        holder, sample = f"N{4 + k}", EXTRA_SAMPLES[k - 2]
        _write_survey(raw_day / f"{hh:02d}0000_plate{holder}_survey", f"plate{holder}", f"{hh:02d}0000", rng)
        _write_stack(raw_day / f"{hh:02d}1502_{sample}_f_k_stack", f"{sample}_f_k", ENERGIES,
                     len(ENERGIES), x, y, rng, created=f"Thu Nov  5 {hh:02d}:15:02 2026")
    if not loose:
        (root / "processed").mkdir(exist_ok=True)
        (root / "config").mkdir(exist_ok=True)
    return root


def make_fake_zones(project) -> "ZonesFile":
    """Three zones on the synthetic stack (two included, one not): the report and the self-test use them.
    `project` is a discovered Project or the project root folder."""
    from aurora.core.io.config import HolderZones, ScanSettings, Zone, ZonesFile
    from aurora.core.stack import StackData
    if isinstance(project, (str, os.PathLike)):
        from aurora.core.model import Project
        project = Project.discover(Path(project))
    scan = project.find(STACK)
    sd = StackData.load(project, scan)
    sid = f"{scan.day}_{scan.name}"
    x0, x1, y0, y1 = sd.bbox
    z1 = Zone(id=1, label="1 alpha", group="Minerals", rects=[[x0, (x0 + x1) / 2, y0, y1]])
    z2 = Zone(id=2, label="2 beta", group="", rects=[[(x0 + x1) / 2, x1, y0, y1]])
    z3 = Zone(id=3, label="3 gamma", group="Minerals", rects=[[x0, x1, y0, y1]])
    z1.settings[sid] = ScanSettings(stack=scan.name, line="total XRF", n=6)
    z2.settings[sid] = ScanSettings(stack=scan.name, line="total XRF", n=6, sdd=[True, False, True, False], fluo_norm="max",
                                    despike=False)
    z3.settings[sid] = ScanSettings(stack=scan.name, line="total XRF", n=12, include=False)
    zf = ZonesFile(project="TEST", exported="2026-11-06T00:00:00")
    zf.holders["N5"] = HolderZones(holder="N5", day=DAY, survey=SURVEY, zones=[z1, z2, z3])
    return zf


# ----------------------------------------------------------------------------- the demo's spectra
DEMO_E = (660.0, 740.0, 321)                # eV grid of the demo spectra: start, stop, points
DEMO_SPECTRA = ("NaF", "CaF2", "BaY2F8", "mixture 1", "mixture 2")
DEMO_REFERENCES = ("NaF", "CaF2", "BaY2F8")
# synthetic edge shapes (centre eV, width eV, white-line height) and mixtures: made up for the demo, not real spectra
_DEMO_EDGES = {"NaF": (690.0, 1.0, 0.6), "CaF2": (700.0, 1.6, 0.2), "BaY2F8": (695.0, 1.2, 0.9)}
_DEMO_MIXES = {"mixture 1": (0.5, 0.3, 0.2), "mixture 2": (0.2, 0.5, 0.3)}


def demo_edge(E: np.ndarray, e0: float, width: float, white: float) -> np.ndarray:
    """A synthetic absorption edge: sloped pre-edge, arctangent-like step at e0, Gaussian white line 2 eV above it."""
    return 0.2 + 0.002 * (E - E[0]) + 1.0 / (1 + np.exp(-(E - e0) / width)) + white * np.exp(-((E - e0 - 2) / 1.5) ** 2)


def demo_spectrum(name: str, E: np.ndarray) -> np.ndarray:
    if name in _DEMO_EDGES:
        return demo_edge(E, *_DEMO_EDGES[name])
    w = _DEMO_MIXES[name]
    return sum(f * demo_edge(E, *_DEMO_EDGES[n]) for f, n in zip(w, DEMO_REFERENCES))


def write_john_reduced(path: Path, label: str, E: np.ndarray, y: np.ndarray) -> Path:
    """One spectrum in John Bussey's _full_reduced.csv layout (per-SDD counts with their channel windows, I0, TEY,
    sdd_total, sdd_mean, bad_point), so Compare reads it as sdd-scaled, sdd_total and TEY (each / I0)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(f"{e:.4f},{10 * v:.6f},60,83,{20 * v:.6f},60,83,{5 * v:.6f},60,83,{5 * v:.6f},60,83,2.0,{0.5 * v:.6f},"
                     f"{40 * v:.6f},{10 * v:.6f},False" for e, v in zip(E, y))
    path.write_text(f"# sample_label: {label}\n# source: synthetic demo spectrum (aurora.core.testdata), not measured\n"
                    "energy,sdd1,sdd1_roi_lo,sdd1_roi_hi,sdd2,sdd2_roi_lo,sdd2_roi_hi,sdd3,sdd3_roi_lo,sdd3_roi_hi,"
                    "sdd4,sdd4_roi_lo,sdd4_roi_hi,I0,TEY,sdd_total,sdd_mean,bad_point\n" + rows + "\n", encoding="utf-8")
    return path


def write_demo_extras(root: Path) -> dict:
    """The demo's spectra next to its raw data: <root>/processed/reduced/<name>/<name>_full_reduced.csv for five
    samples (three end members and two mixtures, small fixed-seed noise) and <root>/references/<name>_F_K_ref.csv
    (noise-free normalised end members, energy + mu_norm). Returns {'reduced', 'references': folders,
    'spectra', 'reference_files': file lists}."""
    root = Path(root)
    E = np.linspace(*DEMO_E)
    reduced, refs = root / "processed" / "reduced", root / "references"
    spectra, ref_files = [], []
    for i, name in enumerate(DEMO_SPECTRA):
        rng = np.random.default_rng(100 + i)
        y = demo_spectrum(name, E) + rng.normal(0.0, 0.01, E.size)
        spectra.append(write_john_reduced(reduced / name / f"{name}_full_reduced.csv", name, E, y))
    refs.mkdir(parents=True, exist_ok=True)
    for name in DEMO_REFERENCES:
        y = demo_spectrum(name, E)
        lo, hi = float(y[:20].mean()), float(y[-40:].mean())
        mu = (y - lo) / (hi - lo)
        p = refs / f"{name}_F_K_ref.csv"
        p.write_text(f"# reference: {name}, F K edge, synthetic (aurora.core.testdata), not measured\nenergy,mu_norm\n"
                     + "\n".join(f"{e:.4f},{v:.6f}" for e, v in zip(E, mu)) + "\n", encoding="utf-8")
        ref_files.append(p)
    return {"reduced": reduced, "references": refs, "spectra": spectra, "reference_files": ref_files}
