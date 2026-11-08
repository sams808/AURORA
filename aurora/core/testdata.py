"""A small synthetic beamtime folder in the real raw layout, for the tests and `aurora --distcheck`.

One survey, one complete stack, one aborted stack and one diagnostic (test) stack, 12 pixels on a 4 x 3 snake grid,
F K alpha peak near channel 65 of every SDD, I0 and TEY with an edge step. Nothing here is real data.
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
    with open(d / "mcc_flyer_0.csv", "w", newline="\n") as f:
        f.write("# " + ",".join(f"ch{i + 1}" for i in range(N_MCC)) + "\n")
        for row in mcc:
            f.write(",".join(f"{v:.6f}" for v in row) + "\n")
    ch = np.arange(N_CH)
    for k, det in enumerate(DETS):
        peak = 200 * step * np.exp(-0.5 * ((ch - (65 + k)) / 3.0) ** 2)
        spec = rng.poisson(peak + 2.0, size=(n_pix, N_CH)).astype(np.uint32)
        spec.tofile(d / f"{det}_0.bin")


def _write_stack(scan_dir: Path, base: str, energies, n_dirs: int, x, y, rng, time_per_map: float = 6.0) -> None:
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
        h.attrs["file_creation_timestamp_iso"] = "Thu Nov  5 09:28:00 2026"
    for e in energies[:n_dirs]:
        name = f"{base}_{int(e)}_{int(round((e % 1) * 100)):02d}eV"
        _write_point_dir(scan_dir / name, len(x), e, rng)


def make_fake_project(root: Path, old_minutes: float = 120.0) -> Path:
    """Create root/raw/<DAY>/... and empty processed/ + config/. Returns the project root."""
    root = Path(root)
    raw_day = root / "raw" / DAY
    rng = np.random.default_rng(7)
    x, y = snake_grid()
    # survey (one energy, larger grid)
    sx, sy = snake_grid(nx=8, ny=6, pitch=0.2, x0=-1.0, y0=-0.6)
    sdir = raw_day / SURVEY
    sdir.mkdir(parents=True, exist_ok=True)
    with open_h5(sdir / f"holderA_survey_{DAY}_010013_695.00eV.h5", "w") as h:
        h.create_dataset("hexapod_waves/x", data=sx)
        h.create_dataset("hexapod_waves/y", data=sy)
        g = h.create_group("scan_metadata")
        g.attrs["energy"] = 695.0
        g.attrs["step_size"] = 0.2
        g.attrs["plan_name"] = "survey"
        g.attrs["scan_name"] = "holderA_survey"
        h.attrs["ring_current"] = 219.0
    _write_point_dir(sdir, len(sx), 695.0, rng)
    # complete stack
    _write_stack(raw_day / STACK, "alpha_beta_f_k", ENERGIES, len(ENERGIES), x, y, rng)
    # aborted stack: 3 of 5 planned energies, untouched for a while
    _write_stack(raw_day / ABORTED, "gamma_f_k", ENERGIES, 3, x, y, rng)
    old = time.time() - 60 * old_minutes
    for p in (raw_day / ABORTED).rglob("*"):
        os.utime(p, (old, old))
    os.utime(raw_day / ABORTED, (old, old))
    # diagnostic stack: named photodiode, 3 energies over 2 eV
    _write_stack(raw_day / TEST, "photodiode_f_k", ENERGIES[:3], 3, x, y, rng)
    (root / "processed").mkdir(exist_ok=True)
    (root / "config").mkdir(exist_ok=True)
    return root


def make_fake_zones(project) -> "ZonesFile":
    """Three zones on the synthetic stack (two included, one not): the report and the self-test use them."""
    from aurora.core.io.config import HolderZones, ScanSettings, Zone, ZonesFile
    from aurora.core.stack import StackData
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
