"""A small synthetic beamtime folder in the real raw layout, for the tests, the demo and the self-test.

Nothing here is real data: the day, the holder, scan and sample names and every number are made up. The names follow
the beamline's own pattern `<HHMMSS>_<name>_<plan>` (`<name>_survey` for a survey, `<name>_<edge>_stack` for a stack).

The test variant (`make_fake_project`): one survey (holder 'holderA'), one complete stack, one aborted stack and one
diagnostic (test) stack, 12 pixels on a 4 x 3 snake grid, an F K alpha peak near channel 65 of every SDD, I0 and TEY
with an edge step, 5 energies 680-692 eV. Variants: more holders later the same day (n_holders), the scan folders loose
in one folder as John Bussey's GUI kept them (loose), a survey map that covers the stacks (survey_covers_stack), a
stack that spans the F K edge with two samples side by side (spans_edge), three zones (make_fake_zones), and the
demo's reduced spectra and references (write_demo_extras).

The demo beamtime (`write_demo`, used by `aurora demo`, Help > Open the demo beamtime, the screenshots and the tests
of the Easy path) is the variant with both options: its survey shows a bright pad under each sample (two under the
complete stack, one under the aborted stack), and its stack runs 670-735 eV in 1 eV steps across the tabulated F K
edge (696.7 eV, xraydb), with sample '1 alpha' on the left pad and '2 beta' on the right one, so the Easy path ends in
two spectra that normalise.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

import numpy as np

from aurora.core.constants import DETS, N_CH, N_MCC
from aurora.core.io.h5 import open_h5

DAY = "2025-03-14"                  # a made-up day
DAY2 = "2025-03-15"                 # the next day: stacks recorded after midnight
HOLDER = "holderA"                  # the holder of the first survey (holder_from_survey(SURVEY))
SURVEY = "010013_holderA_survey"
STACK = "092758_alpha_beta_f_k_stack"
ABORTED = "101456_gamma_f_k_stack"
TEST = "143504_photodiode_f_k_stack"
ENERGIES = (680.0, 683.0, 686.0, 689.0, 692.0)      # 3 energies already span 6 eV (> MIN_SPAN_EV): an aborted stack is not a test
# spans_edge (the demo): 670-735 eV in 1 eV steps, so the larch-style windows -20..-5 and +15..+35 eV around an E0 near
# the tabulated F K edge (696.7 eV) hold data on both sides
EDGE_ENERGIES = tuple(float(e) for e in range(670, 736))
# holders 2.. (n_holders): a survey on the hour from 15:00, then one complete stack; holder names holderB, holderC, ...
EXTRA_SAMPLES = ("CaF2", "NaF", "BaF2", "LiF", "MgF2", "SrF2", "AlF3", "KF")
HOLDER2 = "holderB"
SURVEY2 = "150000_holderB_survey"
STACK2 = "151502_CaF2_f_k_stack"
GROUP1, GROUP2 = "Group 1", "Group 2"           # the groups of the demo's sample sheet and of make_fake_zones
SHEET = "samples.csv"                           # the demo's sample sheet, at the project root
DEMO_VERSION = 2                                # the demo's layout; an older demo folder is left as it is
DEMO_README = "README_demo.txt"
SURVEY_EV, COVER_SURVEY_EV = 695.0, 720.0       # survey energies: the test variant's, the covering (demo) survey's

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def holder_name(k: int) -> str:
    """The k-th holder of the synthetic beamtime (1-based): holderA, holderB, ..."""
    return "holder" + "ABCDEFGHIJ"[k - 1]


def ctime(day: str = DAY, hhmmss: str = "092800") -> str:
    """The ctime form the beamline writes into the h5 files ('Fri Mar 14 09:28:00 2025'), with English names."""
    d = dt.date.fromisoformat(day)
    return f"{_WEEKDAYS[d.weekday()]} {_MONTHS[d.month - 1]} {d.day:2d} {hhmmss[0:2]}:{hhmmss[2:4]}:{hhmmss[4:6]} {d.year}"


def snake_grid(nx: int = 4, ny: int = 3, pitch: float = 0.25, x0: float = -0.5, y0: float = 5.0):
    """Points in acquisition order: row 0 left to right, row 1 right to left, ..."""
    xs, ys = [], []
    for j in range(ny):
        cols = range(nx) if j % 2 == 0 else range(nx - 1, -1, -1)
        for i in cols:
            xs.append(x0 + i * pitch)
            ys.append(y0 + j * pitch)
    return np.array(xs), np.array(ys)


# ----------------------------------------------------------------------------- the samples of the demo
# Pads (x0, x1, y0, y1 mm) of the covering survey: the left and right halves of the complete stack's footprint
# (x -0.5..0.25, y 5.0..5.5), and a third pad under the aborted stack. F and O are relative amounts (made up): '1 alpha'
# a fluoride, '2 beta' a fluoride in an oxide, '3 gamma' a fluoride with a little oxide, the plate oxide only.
PADS = {"1 alpha": (-0.62, -0.125, 4.88, 5.62), "2 beta": (-0.125, 0.37, 4.88, 5.62), "3 gamma": (0.3, 0.9, 4.35, 4.85)}
_PAD_F = {"1 alpha": 1.0, "2 beta": 0.6, "3 gamma": 0.8, "": 0.0}
_PAD_O = {"1 alpha": 0.0, "2 beta": 0.5, "3 gamma": 0.1, "": 0.2}
# where the aborted and the test stacks sit when the survey covers the stacks: the aborted one on pad '3 gamma', the
# photodiode test in a bare corner of the plate (so no footprint hides another); snake_grid keyword arguments
COVER_GRIDS = {"aborted": dict(pitch=0.1, x0=0.45, y0=4.5), "test": dict(pitch=0.05, x0=-0.95, y0=4.45)}


def pad_of(x: float, y: float) -> str:
    """The demo sample under the point (x, y) mm, '' for the bare plate."""
    for name, (x0, x1, y0, y1) in PADS.items():
        if x0 <= x < x1 and y0 <= y <= y1:
            return name
    return ""


def _gauss(centre: float, sigma: float = 3.0) -> np.ndarray:
    return np.exp(-0.5 * ((np.arange(N_CH) - centre) / sigma) ** 2)


def _write_point_dir(d: Path, n_pix: int, e_ev: float, rng: np.random.Generator, i0_level: float = 2.5,
                     fka=None, oka=None, tey=None) -> None:
    """One energy folder: mcc_flyer_0.csv (I0 ch1, ch2 = 1, TEY ch4) and sdd1..4_0.bin (Poisson counts).

    fka / oka: per-pixel heights of the F K alpha (channel 65 + k on SDD k) and O K alpha (51 + k) peaks; tey: the
    per-pixel TEY level. Default: every pixel alike, an F K alpha peak and a TEY step at 682 eV, no O K alpha."""
    d.mkdir(parents=True, exist_ok=True)
    step = 1.0 + 0.8 * (e_ev >= 682.0)
    fka = np.full(n_pix, 200 * step) if fka is None else np.asarray(fka, float)
    tey = np.full(n_pix, 0.5 * step) if tey is None else np.asarray(tey, float)
    mcc = np.zeros((n_pix, N_MCC))
    mcc[:, 0] = i0_level + 0.01 * rng.standard_normal(n_pix)
    mcc[:, 1] = 1.0
    mcc[:, 3] = tey + 0.01 * rng.standard_normal(n_pix)
    mcc[:, 4:] = -10.2
    with open(d / "mcc_flyer_0.csv", "w", newline="\n", encoding="utf-8") as f:
        f.write("# " + ",".join(f"ch{i + 1}" for i in range(N_MCC)) + "\n")
        for row in mcc:
            f.write(",".join(f"{v:.6f}" for v in row) + "\n")
    for k, det in enumerate(DETS):
        lam = fka[:, None] * _gauss(65 + k)[None, :] + 2.0
        if oka is not None:
            lam = lam + np.asarray(oka, float)[:, None] * _gauss(51 + k)[None, :]
        spec = rng.poisson(lam).astype(np.uint32)
        spec.tofile(d / f"{det}_0.bin")


def demo_mu(e: float, name: str) -> float:
    """The synthetic F K absorption of a demo stack sample at e eV: '1 alpha' has the NaF demo edge, '2 beta' a
    0.6 / 0.4 mixture of the CaF2 and LiF demo edges, '3 gamma' the LiF one (see _DEMO_EDGES: made up, not
    measured)."""
    if name == "1 alpha":
        return _edge_at(e, *_DEMO_EDGES["NaF"])
    if name == "2 beta":
        return 0.6 * _edge_at(e, *_DEMO_EDGES["CaF2"]) + 0.4 * _edge_at(e, *_DEMO_EDGES["LiF"])
    if name == "3 gamma":
        return _edge_at(e, *_DEMO_EDGES["LiF"])
    return 0.0


def _edge_at(e: float, e0: float, width: float, white: float) -> float:
    return float(demo_edge(np.array([EDGE_ENERGIES[0], e]), e0, width, white)[1])


def _sample_signals(x, y, e_ev: float):
    """(fka, oka, tey) per pixel of the two-sample demo stack at e_ev."""
    pads = [pad_of(float(a), float(b)) for a, b in zip(x, y)]
    mu = np.array([demo_mu(e_ev, p) for p in pads])
    f = np.array([_PAD_F[p] for p in pads]); o = np.array([_PAD_O[p] for p in pads])
    return 150.0 * f * mu, 100.0 * o, 0.4 + 0.2 * f * mu + 0.1 * o


def _write_stack(scan_dir: Path, base: str, energies, n_dirs: int, x, y, rng, time_per_map: float = 6.0,
                 created: str | None = None, samples: bool = False) -> None:
    """A stack folder: <DAY>_<HHMMSS>_stack_data.h5 (positions, energies, stack_metadata) and one folder per energy
    measured (n_dirs of them). created: the h5 creation time (ctime form; default the scan's own time on DAY).
    samples=True: the two demo samples side by side (_sample_signals) instead of identical pixels."""
    scan_dir.mkdir(parents=True, exist_ok=True)
    if created is None:
        hhmmss = scan_dir.name[:6]
        created = ctime(DAY, hhmmss if hhmmss.isdigit() else "092800")
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
        if samples:
            fka, oka, tey = _sample_signals(x, y, e)
            _write_point_dir(scan_dir / name, len(x), e, rng, fka=fka, oka=oka, tey=tey)
        else:
            _write_point_dir(scan_dir / name, len(x), e, rng)


def _write_survey(sdir: Path, tag: str, hhmmss: str, rng: np.random.Generator, covers_stack: bool = False) -> None:
    """A one-energy survey map named like the beamline names it. Default: 8 x 6 points at 0.2 mm from (-1.0, -0.6),
    695 eV, every point alike (it does not reach the stacks). covers_stack: 10 x 8 points at 0.2 mm from (-1.0, 4.4),
    which hold the stack footprints, at 720 eV (above the F K edge, so F K alpha is excited), with the demo pads
    (PADS) bright in F K alpha, the plate and '2 beta' also in O K alpha."""
    if covers_stack:
        sx, sy = snake_grid(nx=10, ny=8, pitch=0.2, x0=-1.0, y0=4.4)
        e_ev = COVER_SURVEY_EV
    else:
        sx, sy = snake_grid(nx=8, ny=6, pitch=0.2, x0=-1.0, y0=-0.6)
        e_ev = SURVEY_EV
    sdir.mkdir(parents=True, exist_ok=True)
    with open_h5(sdir / f"{tag}_survey_{DAY}_{hhmmss}_{e_ev:.2f}eV.h5", "w") as h:
        h.create_dataset("hexapod_waves/x", data=sx)
        h.create_dataset("hexapod_waves/y", data=sy)
        g = h.create_group("scan_metadata")
        g.attrs["energy"] = e_ev
        g.attrs["step_size"] = 0.2
        g.attrs["plan_name"] = "survey"
        g.attrs["scan_name"] = f"{tag}_survey"
        h.attrs["ring_current"] = 219.0
    if covers_stack:
        pads = [pad_of(float(a), float(b)) for a, b in zip(sx, sy)]
        fka = np.array([220.0 * _PAD_F[p] + 3.0 for p in pads])
        oka = np.array([150.0 * _PAD_O[p] for p in pads])
        tey = np.array([0.5 + 0.4 * _PAD_F[p] + 0.2 * _PAD_O[p] for p in pads])
        _write_point_dir(sdir, len(sx), e_ev, rng, fka=fka, oka=oka, tey=tey)
    else:
        _write_point_dir(sdir, len(sx), e_ev, rng)


def make_fake_project(root: Path, old_minutes: float = 120.0, n_holders: int = 1, loose: bool = False,
                      survey_covers_stack: bool = False, spans_edge: bool = False) -> Path:
    """Create root/raw/<DAY>/... with empty processed/ and config/; returns the project root.

    n_holders > 1 adds holders holderB, holderC, ... later the same day (a survey on the hour from 15:00 and one
    complete stack each, see SURVEY2 / STACK2); holderA is identical in every variant with the same options.
    loose=True writes the scan folders directly into `root` (no raw/<day>, processed/ or config/), the way John
    Bussey's GUI kept them ('CLS GUI/<scan>'). survey_covers_stack: every survey holds the stack footprints (two
    bright pads under the complete stack, a third under the aborted one, the test stack in a bare corner: COVER_GRIDS
    and PADS). spans_edge: the complete stacks run over EDGE_ENERGIES (670-735 eV) with the two demo
    samples side by side, the aborted stack plans the same energies and holds the first 20. ProtectedPathError,
    before anything is written, when the scans would go into an existing beamline raw/ folder or a protected
    folder."""
    from aurora.core.io.safety import assert_writable
    if not 1 <= n_holders <= 1 + len(EXTRA_SAMPLES):
        raise ValueError(f"n_holders must be 1..{1 + len(EXTRA_SAMPLES)}")
    root = Path(root)
    raw_day = root if loose else root / "raw" / DAY
    assert_writable(raw_day)
    rng = np.random.default_rng(7)
    x, y = snake_grid()
    energies = EDGE_ENERGIES if spans_edge else ENERGIES
    _write_survey(raw_day / SURVEY, HOLDER, SURVEY[:6], rng, covers_stack=survey_covers_stack)
    # complete stack
    _write_stack(raw_day / STACK, STACK[7:-6], energies, len(energies), x, y, rng, samples=spans_edge)
    # aborted stack: the first energies of the plan only (3 of 5; 20 of 66 over the edge), untouched for a while
    ax, ay = snake_grid(**COVER_GRIDS["aborted"]) if survey_covers_stack else (x, y)
    _write_stack(raw_day / ABORTED, ABORTED[7:-6], energies, 20 if spans_edge else 3, ax, ay, rng, samples=spans_edge)
    old = time.time() - 60 * old_minutes
    for p in (raw_day / ABORTED).rglob("*"):
        os.utime(p, (old, old))
    os.utime(raw_day / ABORTED, (old, old))
    # diagnostic stack: named photodiode, 3 energies over 6 eV
    tx, ty = snake_grid(**COVER_GRIDS["test"]) if survey_covers_stack else (x, y)
    _write_stack(raw_day / TEST, TEST[7:-6], ENERGIES[:3], 3, tx, ty, rng)
    for k in range(2, n_holders + 1):
        hh = 13 + k                                       # holder 2 at 15:00, holder 3 at 16:00, ...
        holder, sample = holder_name(k), EXTRA_SAMPLES[k - 2]
        _write_survey(raw_day / f"{hh:02d}0000_{holder}_survey", holder, f"{hh:02d}0000", rng,
                      covers_stack=survey_covers_stack)
        _write_stack(raw_day / f"{hh:02d}1502_{sample}_f_k_stack", f"{sample}_f_k", energies, len(energies), x, y, rng,
                     created=ctime(DAY, f"{hh:02d}1502"), samples=spans_edge)
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
    z1 = Zone(id=1, label="1 alpha", group=GROUP1, rects=[[x0, (x0 + x1) / 2, y0, y1]])
    z2 = Zone(id=2, label="2 beta", group="", rects=[[(x0 + x1) / 2, x1, y0, y1]])
    z3 = Zone(id=3, label="3 gamma", group=GROUP1, rects=[[x0, x1, y0, y1]])
    z1.settings[sid] = ScanSettings(stack=scan.name, line="total XRF", n=6)
    z2.settings[sid] = ScanSettings(stack=scan.name, line="total XRF", n=6, sdd=[True, False, True, False], fluo_norm="max",
                                    despike=False)
    z3.settings[sid] = ScanSettings(stack=scan.name, line="total XRF", n=12, include=False)
    zf = ZonesFile(project="DEMO", exported=f"{DAY2}T00:00:00")
    zf.holders[HOLDER] = HolderZones(holder=HOLDER, day=DAY, survey=SURVEY, zones=[z1, z2, z3])
    return zf


# ----------------------------------------------------------------------------- the demo's spectra
DEMO_E = (660.0, 740.0, 321)                # eV grid of the demo spectra: start, stop, points
DEMO_SPECTRA = ("NaF", "CaF2", "LiF", "mixture 1", "mixture 2")
DEMO_REFERENCES = ("NaF", "CaF2", "LiF")
# synthetic edge shapes (centre eV, width eV, white-line height) and mixtures: made up for the demo, not real spectra
_DEMO_EDGES = {"NaF": (690.0, 1.0, 0.6), "CaF2": (700.0, 1.6, 0.2), "LiF": (695.0, 1.2, 0.9)}
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


# ----------------------------------------------------------------------------- the demo beamtime
# fixed emission lines of the demo, at its nominal 10 eV per channel (F K alpha 676.8 eV and O K alpha 524.9 eV,
# xraydb; the synthetic peaks sit at channels 65-68 and 51-54)
DEMO_ROI_BINS = {
    "_meta": {"gains": {"sdd1": 10.0, "sdd2": 10.0, "sdd3": 10.0, "sdd4": 10.0}, "n_sigma": 1.5,
              "help": "line -> {sdd: [first channel, last channel + 1]}; the demo uses 10 eV per channel"},
    "F Kα": {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77], "line_ev": 676.8},
    "O Kα": {"sdd1": [46, 58], "sdd2": [46, 58], "sdd3": [46, 58], "sdd4": [46, 58], "line_ev": 525.0}}
# the demo's sample sheet: the generic columns, UTF-8, one row per sample
SHEET_HEADER = ("Sample ID", "Holder", "Group", "Composition", "Edges", "Notes")
DEMO_SHEET_ROWS = (("1 alpha", HOLDER, GROUP1, "NaF", "F K", "synthetic demo sample on the left pad"),
                   ("2 beta", HOLDER, GROUP2, "CaF2-LiF in silica", "F K", "synthetic demo sample on the right pad"))


def write_demo(root: Path) -> dict:
    """The demo beamtime in `root` (a new or empty folder; the caller checks): the raw data with a survey that
    covers the stack and a stack across the F K edge, config/roi_bins.json (F K alpha, O K alpha), the sample sheet
    samples.csv, the reduced spectra and references (write_demo_extras) and README_demo.txt, which marks the folder as
    this version of the demo. Returns write_demo_extras' dict plus 'root' and 'sheet'."""
    root = make_fake_project(Path(root), survey_covers_stack=True, spans_edge=True)
    (root / "config" / "roi_bins.json").write_text(json.dumps(DEMO_ROI_BINS, indent=1, ensure_ascii=False),
                                                   encoding="utf-8")
    sheet = root / SHEET
    sheet.write_text("\n".join(",".join(r) for r in (SHEET_HEADER, *DEMO_SHEET_ROWS)) + "\n", encoding="utf-8")
    extras = write_demo_extras(root)
    (root / DEMO_README).write_text(
        f"AURORA demo beamtime, version {DEMO_VERSION}\n\n"
        "A small made-up beamtime in the layout the SGM beamline writes (raw/<day>/<scan>/). Every name and number in\n"
        "it is synthetic: holder holderA, a survey map, one complete F K stack over two samples ('1 alpha' and\n"
        "'2 beta'), one aborted stack and one photodiode test, fixed emission lines in config/roi_bins.json, a sample\n"
        "sheet (samples.csv), reduced spectra in processed/reduced/ and references in references/.\n"
        "AURORA's tutorials (Help > Tutorials) use it. It can be deleted at any time.\n", encoding="utf-8")
    return {**extras, "root": root, "sheet": sheet}


def is_current_demo(root: Path) -> bool:
    """True when `root` holds the demo of this version (its README_demo.txt names DEMO_VERSION)."""
    try:
        first = (Path(root) / DEMO_README).read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError, UnicodeDecodeError):
        return False
    return first.strip() == f"AURORA demo beamtime, version {DEMO_VERSION}"


def demo_folder(base: Path) -> Path:
    """Where Help > Open the demo beamtime keeps the demo under `base` (the user data folder): demo_beamtime when it
    is free or holds this version of the demo, else demo_beamtime_<version> (a demo written by an earlier version,
    or anything else found there, is left as it is)."""
    base = Path(base)
    first = base / "demo_beamtime"
    if not first.exists() or is_current_demo(first) or (first.is_dir() and not any(first.iterdir())):
        return first
    return base / f"demo_beamtime_{DEMO_VERSION}"
