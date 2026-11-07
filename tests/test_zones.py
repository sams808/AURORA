"""Zone geometry and trace maths on synthetic data, plus the stack / survey loaders on the fake project."""
import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.io.config import ScanSettings, Zone
from aurora.core.lines import edge_energy, line_of_mode, main_line
from aurora.core.model import Project
from aurora.core.stack import StackData
from aurora.core.survey import SurveyData
from aurora.core.zones import (despike, good_i0_curve, grid_pitch, i0_score, interp, members, normalise, run_median,
                               zone_traces)


def test_grid_pitch_snake():
    x, y = testdata.snake_grid(nx=6, ny=4, pitch=0.25)
    px, py = grid_pitch(x, y)
    assert np.isclose(px, 0.25) and np.isclose(py, 0.25)
    # turnaround points between rows must not corrupt the x pitch
    x2 = np.insert(x, 6, x[5]); y2 = np.insert(y, 6, y[5] + 0.12)
    assert np.isclose(grid_pitch(x2, y2)[0], 0.25)


def test_members_rect_paint_erase():
    x, y = testdata.snake_grid(nx=6, ny=4, pitch=0.25, x0=0.0, y0=0.0)
    pitch = (0.25, 0.25)
    z = Zone(id=1, label="a", rects=[[0.0, 0.5, 0.0, 0.25]])          # touches columns 0..2, rows 0..1
    m = members(z, x, y, pitch)
    assert sorted(x[m].round(2).tolist()) == sorted([0.0, 0.25, 0.5] * 2)
    assert len(m) == 6
    # the 0.45-pitch tolerance: a rectangle ending 0.1 mm short of a pixel centre still takes it
    z2 = Zone(id=2, label="b", rects=[[0.0, 0.15, 0.0, 0.0]])
    assert len(members(z2, x, y, pitch)) == 2                        # x = 0 and x = 0.25
    # erase rectangle and paint / erase squares
    z.erects = [[0.5, 0.5, 0.0, 0.25]]
    assert len(members(z, x, y, pitch)) == 4
    z.paint = [[1.25, 0.75, 0.25, 0.25]]
    m = members(z, x, y, pitch)
    assert len(m) == 5 and any(np.isclose(x[m], 1.25) & np.isclose(y[m], 0.75))
    z.erase = [[1.25, 0.75, 0.25, 0.25]]
    assert len(members(z, x, y, pitch)) == 4
    assert members(Zone(id=3, label="empty"), x, y, pitch).size == 0


def test_run_median_and_despike():
    y = np.array([1.0, 1.0, 1.0, 1.0, 8.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    assert run_median(y, 5)[4] == 1.0
    out, spikes = despike(y + np.linspace(0, 0.01, 10), 6)
    assert spikes == [4]
    assert np.isclose(out[4], (out[3] + out[5]) / 2, atol=1e-9)
    flat, sp = despike(np.ones(10), 6)
    assert sp == [] and np.array_equal(flat, np.ones(10))           # MAD = 0 -> nothing removed
    yn = y + np.linspace(0, 0.01, 10); yn[6] = np.nan
    out2, sp2 = despike(yn, 6)
    assert sp2 == [4] and np.isnan(out2[6]) and np.isfinite(out2[4])


def test_normalise_modes():
    E = np.linspace(680, 730, 101)
    i0 = np.full(101, 2.0)
    y = 2.0 * (0.5 + 0.3 * (E - 680) / 50 + 2.0 * (E >= 700))       # sloped pre-edge, step of 2 at 700 eV
    raw = normalise(E, y, "raw", i0)
    assert np.array_equal(raw.y, y) and raw.scale == 1.0
    assert np.allclose(normalise(E, y, "i0", i0).y, y / 2)
    pre = normalise(E, y, "i0pre", i0)
    assert pre.pre == (680.0, 687.5) and pre.post == (720.0, 730.0)
    assert np.allclose(pre.y[E < 700], 0, atol=1e-9)                # the pre-edge line is removed exactly
    jump = normalise(E, y, "i0jump", i0)
    assert np.isclose(jump.y[(E >= 720)].mean(), 1.0)
    assert np.isclose(jump.scale, 2.0)
    area = normalise(E, y, "area", i0)
    assert np.isclose(np.trapezoid(area.y, E), 1.0)
    mx = normalise(E, y, "max", i0)
    assert np.isclose(np.nanmax(mx.y), 1.0)
    explicit = normalise(E, y, "i0jump", i0, pre=[680, 690], post=[710, 730])
    assert explicit.pre == (680.0, 690.0) and explicit.post == (710.0, 730.0)
    assert np.isnan(normalise(E, y, "i0", None).y).all()


def test_i0_score_and_good_curve():
    E = np.linspace(680, 730, 51)
    good = 2.5 + 0.01 * np.sin(E)
    s = i0_score(good)
    assert s.nonpos == 0 and s.max_step < 0.01 and s.rough < 0.01 and s.looks_good(2.4)
    bad = good.copy(); bad[20] = 0.0
    s2 = i0_score(bad)
    assert s2.nonpos == 1 and s2.max_step > 0.9 and not s2.looks_good(2.4)
    curve = good_i0_curve(E, [(E, good), (E + 1000, good)])          # the second curve does not overlap
    assert curve is not None and np.allclose(curve, good)
    assert good_i0_curve(E, [(E + 1000, good)]) is None
    assert np.isnan(interp(E, good, 600))


def test_stack_survey_and_zone_traces(fake_project):
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    sd = StackData.load(proj, scan)                                  # no cache: reduced from raw, cached by AURORA
    assert sd.source == "reduced from raw" and sd.labels == ["total XRF"] and sd.main == "total XRF"
    assert sd.n_energies == 5 and sd.n_pix == 12 and sd.valid.all()
    assert sd.edge == ("F", "K") and 680 < sd.edge_ev < 700
    assert np.isclose(sd.pitch[0], 0.25) and np.isclose(sd.pitch[1], 0.25)
    sd2 = StackData.load(proj, scan, allow_reduce=False)
    assert sd2.source.startswith("aurora cache") and np.array_equal(sd2.roi_pix_det, sd.roi_pix_det)
    x0, x1, y0, y1 = sd.bbox
    z = Zone(id=1, label="z", rects=[[x0, x1, y0, y1]])
    pix = members(z, sd.x, sd.y, sd.pitch)
    assert len(pix) == 12
    st = ScanSettings(fluo_norm="i0jump", tey_norm="i0", despike=True, k=6)
    t = zone_traces(sd, pix, st)
    assert t is not None and t.n_pix == 12 and t.line == "total XRF"
    assert t.sum is not None and np.isfinite(t.sum).all()
    assert np.isclose(np.nanmean(t.sum[t.E >= t.post[0]]), 1.0)
    # the ticked detectors AVERAGE to the sum trace (per-SDD scale = sum scale / number ticked)
    assert all(d is not None for d in t.det) and np.allclose(np.mean([d for d in t.det], axis=0), t.sum, rtol=1e-6, atol=1e-9)
    assert np.isfinite(t.tey).all() and t.i0_used is t.i0
    st2 = ScanSettings(sdd=[True, False, False, False], fluo_norm="raw", despike=False)
    t2 = zone_traces(sd, pix, st2)
    assert np.array_equal(t2.sum, t2.det_raw[0]) and t2.spikes == {}
    assert zone_traces(sd, [], st) is None
    sv = SurveyData.load(proj.find(testdata.SURVEY), proj.roi_bins)
    assert sv.n == 48 and sv.lines == [] and sv.channels == ["total", "TEY", "I0"] and sv.i0_usable
    v = sv.values("total", per_i0=True, log10=True)
    assert v.shape == (48,) and np.isfinite(v).all()
    g = sv.grid(v)
    assert g.img.shape == (6, 8)


def test_lines_helpers():
    assert line_of_mode("PFY F Kα") == "F Kα" and line_of_mode("IPFY O Kα") == "O Kα" and line_of_mode("TEY") is None
    assert main_line(["O Kα", "F Kα", "total XRF"], ("F", "K")) == "F Kα"
    assert main_line(["O Kα", "F Kα", "total XRF"], ("U", "N45"), "IPFY O Kα") == "O Kα"
    assert main_line(["O Kα", "total XRF"], ("U", "N45")) == "O Kα"
    assert 680 < edge_energy("F", "K") < 700 and 1400 < edge_energy("Se", "L3L2") < 1450
    assert edge_energy("Xx", "K") is None
