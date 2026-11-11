"""Fixtures of the zone-drawing tests. A fake beamtime from aurora.core.testdata (never the demo beamtime, whose
names and survey another line changes) plus one wider stack, so a brush can move several points at once; and the
window helpers of the WP3 tests (open_window, qdrag)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from aurora.core import testdata

WIDE = "093000_wide_f_k_stack"                    # a second complete stack of the holder, 12 x 4 points
WIDE_GRID = {"nx": 12, "ny": 4, "pitch": 0.25, "x0": 2.0, "y0": 5.0}
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def ctime(hhmmss: str, day: str | None = None) -> str:
    """The ctime form the beamline writes into the h5 files ('Fri Mar 14 09:30:00 2025') on testdata.DAY, with English
    names whatever the locale: the stacks of this fixture stay on the fixture's day whatever day testdata uses."""
    import datetime as dt
    d = dt.date.fromisoformat(day or testdata.DAY)
    return (f"{_DAYS[d.weekday()]} {_MONTHS[d.month - 1]} {d.day:2d} {hhmmss[0:2]}:{hhmmss[2:4]}:{hhmmss[4:6]} "
            f"{d.year}")


def holder() -> str:
    """The holder of the fixture's survey (the name testdata gives it)."""
    from aurora.core.names import holder_from_survey
    return holder_from_survey(testdata.SURVEY)


def zone_project(root: Path) -> Path:
    """testdata.make_fake_project(root) plus the WIDE stack (written after the first stack, same holder)."""
    root = testdata.make_fake_project(Path(root))
    x, y = testdata.snake_grid(**WIDE_GRID)
    d = root / "raw" / testdata.DAY / WIDE
    testdata._write_stack(d, WIDE[7:-6], testdata.ENERGIES, len(testdata.ENERGIES), x, y, np.random.default_rng(21),
                          created=ctime(WIDE[:6]))
    return root


def pixel_xy(sd, i: int) -> tuple[float, float]:
    return float(sd.x[i]), float(sd.y[i])


def row_points(sd, row: int) -> np.ndarray:
    """Indices of the stack's pixels on one row (y plateau), left to right."""
    ys = np.unique(np.round(sd.y, 6))
    idx = np.where(np.isclose(sd.y, ys[row]))[0]
    return idx[np.argsort(sd.x[idx])]


def inner_box(sd, frac: float = 0.25):
    """A rectangle (x0, x1, y0, y1) inside the stack's footprint, `frac` of the size in from each side."""
    x0, x1, y0, y1 = sd.bbox
    dx, dy = (x1 - x0) * frac, (y1 - y0) * frac
    return x0 + dx, x1 - dx, y0 + dy, y1 - dy


# ----------------------------------------------------------------------------- a survey that covers the stacks
# Synthetic windows (channels, [first, last + 1)) of the fixture's roi_bins.json: two lines, one per peak below. They
# are test values for a test beamtime, not a calibration: the peaks are written where the windows are.
LINE_A, LINE_B = "F Kα", "O Kα"
BINS = {LINE_A: {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77]},
        LINE_B: {"sdd1": [46, 58], "sdd2": [46, 58], "sdd3": [46, 58], "sdd4": [46, 58]}}
SURVEY_GRID = {"nx": 33, "ny": 10, "pitch": 0.2, "x0": -1.0, "y0": 4.4}     # x -1.0..5.4, y 4.4..6.2 mm
PEAK_A, PEAK_B = 67.0, 52.0                     # channel of each peak (inside its window for every SDD)
SAMPLE = (300.0, 20.0)                          # (line A, line B) peak heights on a stack footprint (the samples)
PLATE = (30.0, 150.0)                           # ... on the plate around them


def _write_survey_points(d: Path, x: np.ndarray, y: np.ndarray, inside: np.ndarray, rng) -> None:
    """mcc_flyer_0.csv and sdd*_0.bin of a survey: two peaks per point, their heights SAMPLE inside, PLATE outside."""
    from aurora.core.constants import DETS, N_CH, N_MCC
    n = len(x)
    mcc = np.zeros((n, N_MCC))
    mcc[:, 0] = 2.5 + 0.01 * rng.standard_normal(n)
    mcc[:, 1] = 1.0
    mcc[:, 3] = 0.9 + 0.01 * rng.standard_normal(n)
    mcc[:, 4:] = -10.2
    with open(d / "mcc_flyer_0.csv", "w", newline="\n", encoding="utf-8") as f:
        f.write("# " + ",".join(f"ch{i + 1}" for i in range(N_MCC)) + "\n")
        for row in mcc:
            f.write(",".join(f"{v:.6f}" for v in row) + "\n")
    ch = np.arange(N_CH)
    a = np.where(inside, SAMPLE[0], PLATE[0])[:, None]
    b = np.where(inside, SAMPLE[1], PLATE[1])[:, None]
    for k, det in enumerate(DETS):
        peak = a * np.exp(-0.5 * ((ch - (PEAK_A + k * 0.5)) / 3.0) ** 2) + b * np.exp(-0.5 * ((ch - PEAK_B) / 3.0) ** 2)
        rng.poisson(peak + 2.0).astype(np.uint32).tofile(d / f"{det}_0.bin")


def survey_project(root: Path) -> Path:
    """zone_project(root) with a survey that covers both complete stacks (a 33 x 10 grid at 0.2 mm), a strong line-A
    peak on the stack footprints and line B on the plate around them, and a roi_bins.json with the two lines."""
    import json
    from aurora.core.io.h5 import open_h5
    root = zone_project(root)
    sdir = root / "raw" / testdata.DAY / testdata.SURVEY
    for p in sdir.iterdir():
        p.unlink()
    x, y = testdata.snake_grid(**SURVEY_GRID)
    boxes = [(-0.5, 0.25, 5.0, 5.5), (WIDE_GRID["x0"], WIDE_GRID["x0"] + 11 * WIDE_GRID["pitch"],
                                      WIDE_GRID["y0"], WIDE_GRID["y0"] + 3 * WIDE_GRID["pitch"])]
    inside = np.zeros(len(x), bool)
    for x0, x1, y0, y1 in boxes:
        inside |= (x >= x0 - 1e-6) & (x <= x1 + 1e-6) & (y >= y0 - 1e-6) & (y <= y1 + 1e-6)
    tag = testdata.SURVEY.split("_", 1)[1].rsplit("_survey", 1)[0]      # the survey's name without time and plan
    with open_h5(sdir / f"{tag}_survey_{testdata.DAY}_{testdata.SURVEY[:6]}_695.00eV.h5", "w") as h:
        h.create_dataset("hexapod_waves/x", data=x)
        h.create_dataset("hexapod_waves/y", data=y)
        g = h.create_group("scan_metadata")
        g.attrs["energy"] = 695.0
        g.attrs["step_size"] = 0.2
        g.attrs["plan_name"] = "survey"
        g.attrs["scan_name"] = f"{tag}_survey"
        h.attrs["ring_current"] = 219.0
    _write_survey_points(sdir, x, y, inside, np.random.default_rng(5))
    (root / "config" / "roi_bins.json").write_text(json.dumps(BINS), encoding="utf-8")
    return root


def sample_points(sv, sd) -> np.ndarray:
    """Indices of the survey points between the stack's pixel centres: those survey_project wrote with the sample's
    peaks (SAMPLE)."""
    x0, x1, y0, y1 = float(sd.x.min()), float(sd.x.max()), float(sd.y.min()), float(sd.y.max())
    return np.where((sv.x >= x0 - 1e-6) & (sv.x <= x1 + 1e-6) & (sv.y >= y0 - 1e-6) & (sv.y <= y1 + 1e-6))[0]


def sample_box(sd, inset: float = 0.05):
    """A rectangle (x0, x1, y0, y1) just inside the stack's pixel centres: with the rectangle rule's 0.45-pitch
    tolerance it holds the sample's survey points only, and every pixel of the stack."""
    return (float(sd.x.min()) + inset, float(sd.x.max()) - inset, float(sd.y.min()) + inset, float(sd.y.max()) - inset)
