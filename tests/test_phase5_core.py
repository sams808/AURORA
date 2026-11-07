"""Phase 5 core: I0 explorer curves, reference listing, zone proposals, deconvolution, stitching, peak decomposition."""
import json

import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.deconv import deconvolve_grid, gaussian_psf, richardson_lucy, wiener
from aurora.core.fit import peak_decomposition
from aurora.core.io.config import Zone
from aurora.core.model import Project
from aurora.core.propose import otsu, pads_from_survey, zones_from_pads, zones_from_summary
from aurora.core.qc import mcc_curves
from aurora.core.references import guess_edge, list_references
from aurora.core.stack import StackData
from aurora.core.stitch import Trim, auto_trims, kept_mask, seams, stitch
from aurora.core.survey import SurveyData
from aurora.core.zones import members, zone_traces
from aurora.core.io.config import ScanSettings


def test_mcc_curves(fake_project):
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    c = mcc_curves(proj, scan)
    assert len(c.E) == 5 and np.allclose(c.diode, 1.0) and np.allclose(c.mesh, 2.5, atol=0.05)
    assert np.allclose(c.ratio, 1.0, atol=0.05)
    c2 = mcc_curves(proj, scan)
    assert np.array_equal(c2.mesh, c.mesh)                 # cached


def test_references(tmp_path):
    (tmp_path / "NaF_F_K_edge.csv").write_text("energy,y\n680,1\n690,2\n700,3\n")
    (tmp_path / "ref_Se_L3.txt").write_text("1400 1\n1410 2\n1420 3\n")
    (tmp_path / "fluorine-k").mkdir(); (tmp_path / "fluorine-k" / "CaF2.csv").write_text("energy,y\n680,1\n690,2\n")
    (tmp_path / "notes.md").write_text("x")
    refs = list_references(tmp_path)
    assert {(r.label, r.edge) for r in refs} == {("NaF F K", "F K"), ("Se L3", "Se L3"), ("CaF2", "F K")}
    assert {r.label for r in list_references(tmp_path, edge="F K")} == {"NaF F K", "CaF2"}
    assert guess_edge("BaY2F8_f_k") == "F K" and guess_edge("random") == ""
    assert list_references(tmp_path / "missing") == []


def test_otsu_pads_and_proposals(fake_project):
    v = np.r_[np.random.default_rng(0).normal(0, 0.1, 500), np.random.default_rng(1).normal(3, 0.1, 200)]
    t = otsu(v)
    assert 0.5 < t < 2.5
    proj = Project.discover(fake_project)
    sv = SurveyData.load(proj.find(testdata.SURVEY), proj.roi_bins)
    pads = pads_from_survey(sv, min_px=2)
    assert isinstance(pads, list)
    zs = zones_from_pads(pads, start_id=5)
    assert all(z.id >= 5 and len(z.rects) == 1 for z in zs)
    summ = [{"scan": "092758_x", "zones": [{"id": 1, "name": "13 alpha_beta", "x": 1.0, "y": 2.0, "px": 16}, {"id": 2, "name": "13 alpha_beta", "x": 9, "y": 9, "px": 4}]}]
    zz = zones_from_summary(summ, {"092758_x": (0.25, 0.25)})
    assert len(zz) == 1 and zz[0].label == "13 alpha_beta" and zz[0].rects == [[0.5, 1.5, 1.5, 2.5]]     # side = 4 px x 0.25 mm


def test_deconvolution_sharpens():
    rng = np.random.default_rng(0)
    truth = np.zeros((40, 40)); truth[15:25, 10:20] = 1.0; truth[5:9, 28:36] = 0.6
    psf = gaussian_psf(3.0)
    from scipy import ndimage
    blurred = ndimage.convolve(truth, psf, mode="reflect") + rng.normal(0, 0.005, truth.shape)
    rl = richardson_lucy(blurred, psf, iterations=30)
    assert np.mean((rl - truth) ** 2) < np.mean((blurred - truth) ** 2)
    w = wiener(blurred, psf, 0.01)
    assert w.shape == truth.shape and np.isfinite(w).all()
    blurred[0, 0] = np.nan
    d = deconvolve_grid(blurred, fwhm_um=300, pitch_mm=0.1, method="lucy", iterations=5)
    assert np.isnan(d[0, 0]) and np.isfinite(d[1:, 1:]).all()


def test_stitch_two_tiles(fake_project):
    proj = Project.discover(fake_project)
    a = StackData.load(proj, proj.find(testdata.STACK))
    # tile b = a shifted right by 3 pitches (one column of overlap)
    import copy
    b = copy.deepcopy(a); b.x = a.x + 3 * a.pitch[0]; b.name = "tile_b"; b.id = "x_tile_b"
    trims = auto_trims([a, b])
    assert trims[0].right > 0 and trims[1].left > 0 and trims[0].left == 0
    ka, kb = kept_mask(a, trims[0]), kept_mask(b, trims[1])
    assert ka.sum() + kb.sum() <= 24 and ka.sum() >= 9
    st = stitch([a, b], trims)
    assert st.n_pix == ka.sum() + kb.sum() and st.n_energies == 5 and st.labels == ["total XRF"]
    assert st.x.min() == a.x.min() and st.x.max() == pytest.approx(b.x.max())
    s = seams([a, b], trims)
    assert s and s[0].axis == "x" and s[0].verdict in ("PERFECT", "OVERLAP", "GAP")
    # the stitched stack works like any other
    z = Zone(id=1, label="all", rects=[[st.x.min(), st.x.max(), st.y.min(), st.y.max()]])
    pix = members(z, st.x, st.y, st.pitch)
    t = zone_traces(st, pix, ScanSettings(line="total XRF"))
    assert t is not None and t.n_pix == st.n_pix
    with pytest.raises(ValueError):
        stitch([a])


def test_peak_decomposition_recovers():
    E = np.linspace(680, 720, 401)
    truth = 0.1 + 0.002 * (E - 680) + 1.0 * (0.5 + np.arctan((E - 692) / 0.8) / np.pi) \
        + 1.2 * np.exp(-0.5 * ((E - 689.5) / 0.9) ** 2) + 0.5 * np.exp(-0.5 * ((E - 696.0) / 1.4) ** 2)
    y = truth + np.random.default_rng(2).normal(0, 0.01, E.size)
    r = peak_decomposition(E, y, n_peaks=2, shape="gaussian", step=True)
    assert r.converged and r.rmse < 0.02
    centres = sorted([r.params["p1_centre"], r.params["p2_centre"]])
    assert abs(centres[0] - 689.5) < 0.1 and abs(centres[1] - 696.0) < 0.2
    assert abs(r.params["step_centre"] - 692) < 0.3 and r.errors["p1_centre"] < 0.05
    assert set(r.components) == {"background", "step", "peak 1", "peak 2"}
    r2 = peak_decomposition(E, y, n_peaks=1, shape="lorentzian", step=False, background="constant", init_centres=[689.5], fit_range=(685, 693))
    assert r2.E[0] >= 685 and "step" not in r2.components
    with pytest.raises(ValueError):
        peak_decomposition(E[:3], y[:3])
