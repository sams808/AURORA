"""I19: per-detector XRF windows (John Bussey's step 2): window sums read from the raw bins, used by zone_sums, the
emission spectrum, presets, the step-2 card, and John's own reduction reproduced at every energy."""
import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from aurora.core import testdata
from aurora.core import xrf_windows as xw
from aurora.core.constants import DETS, N_CH
from aurora.core.io.config import ScanSettings, Zone
from aurora.core.io.raw import read_point_dir
from aurora.core.model import Project
from aurora.core.stack import StackData
from aurora.core.zones import members, zone_sums, zone_traces

ROOT = Path(__file__).resolve().parents[1]
JOHN_CANDIDATES = [ROOT / "legacy" / "CLS_GUI_JohnBussey",
                   Path(os.environ.get("AURORA_JOHN_GUI", "") or "/nonexistent"),
                   Path(r"<repository>\legacy\CLS_GUI_JohnBussey")]
JOHN = next((p for p in JOHN_CANDIDATES if (p / "gui_outputs").is_dir()), None)


@pytest.fixture(autouse=True)
def _fresh_memory():
    xw.clear_memory()
    xw.forget_projects()
    yield
    xw.clear_memory()
    xw.forget_projects()


@pytest.fixture
def bare_project(tmp_path):
    """A tmp copy of the fake project with config/ removed: only 'total XRF' (channels 5-200) is reduced."""
    root = testdata.make_fake_project(tmp_path / "bt")
    shutil.rmtree(root / "config")
    proj = Project.discover(root)
    xw.register_project(proj)
    return proj


def _raw_window(scan, pix, lo, hi):
    out = []
    for _e, p in scan.energy_dirs:
        pd = read_point_dir(p)
        out.append([pd.sdd[k][pix, lo:hi].sum() for k in range(4)])
    return np.array(out, float)


def test_windows_60_74_equal_the_raw_bins_at_every_energy(bare_project):
    proj = bare_project
    scan = proj.find(testdata.STACK)
    sd = StackData.load(proj, scan)
    assert sd.labels == ["total XRF"]                      # no roi_bins: F Kα cannot be reduced without windows
    x0, x1, y0, y1 = sd.bbox
    z = Zone(id=1, label="a", rects=[[x0, (x0 + x1) / 2, y0, y1]])
    pix = members(z, sd.x, sd.y, sd.pitch)
    assert 0 < pix.size < sd.n_pix
    st = ScanSettings(stack=scan.name, line="total XRF", roi={d: [60, 74] for d in DETS})
    sums = zone_sums(sd, pix, st)
    want = _raw_window(scan, pix, 60, 74)
    assert sums.windows == {d: [60, 74] for d in DETS}
    np.testing.assert_array_equal(sums.det["total XRF"], want)
    # window_sums on the scan itself (hi exclusive: 60:74 is 14 channels)
    np.testing.assert_array_equal(xw.window_sums(scan, pix, {d: [60, 74] for d in DETS}, root=proj.root), want)
    # the traces name the windows; without roi the fixed 'total XRF' bins are used
    t = zone_traces(sd, pix, st)
    assert "windows 60:74 ch" in t.line and any("raw spectra" in n for n in t.notes)
    plain = zone_sums(sd, pix, ScanSettings(stack=scan.name, line="total XRF"))
    assert plain.windows is None
    np.testing.assert_array_equal(plain.det["total XRF"], sd.sum_over(pix)["det"]["total XRF"])
    # cached on disk under .aurora/cache/<day>/<scan>/_xrf_windows, read back without the raw folders
    cdir = proj.root / ".aurora" / "cache" / scan.day / scan.name / xw.WINDOWS_DIR
    assert len(list(cdir.glob("*.npz"))) == 1
    xw.clear_memory()
    hit = xw.cached_window_counts(scan, {d: [60, 74] for d in DETS}, root=proj.root)
    assert hit is not None and hit.counts.shape == (len(scan.energy_dirs), sd.n_pix, 4)


def test_per_detector_windows_and_hi_exclusive(bare_project):
    proj = bare_project
    scan = proj.find(testdata.STACK)
    sd = StackData.load(proj, scan)
    pix = np.arange(sd.n_pix)
    w = {"sdd1": [60, 61], "sdd2": [61, 63], "sdd3": [62, 66], "sdd4": [0, 256]}
    s = xw.window_sums(scan, pix, w, root=proj.root)
    for k, d in enumerate(DETS):
        np.testing.assert_array_equal(s[:, k], _raw_window(scan, pix, *w[d])[:, k])
    assert xw.describe(w) == "SDD1 60:61, SDD2 61:63, SDD3 62:66, SDD4 0:256 ch"
    assert xw.describe([60, 83]) == "60:83 ch" and xw.normalise_windows([83, 60])["sdd2"] == [60, 83]
    assert xw.normalise_windows({"sdd1": [-5, 300]})["sdd4"] == [0, N_CH]


def test_stack_emission_matches_the_raw_spectra(bare_project):
    proj = bare_project
    scan = proj.find(testdata.STACK)
    pix = [0, 3, 7]
    spec = xw.stack_emission(scan, DETS, pix=pix, root=proj.root)
    want = np.zeros((4, N_CH))
    for _e, p in scan.energy_dirs:
        pd = read_point_dir(p)
        for k in range(4):
            want[k] += pd.sdd[k][pix].sum(axis=0)
    np.testing.assert_array_equal(spec, want)
    full = xw.stack_emission(scan, ["sdd2"], root=proj.root)
    assert full.shape == (1, N_CH) and full.sum() > spec[1].sum()
    assert (proj.root / ".aurora" / "cache" / scan.day / scan.name / xw.EMISSION_FILE).is_file()


def test_unknown_raw_folders_fall_back_with_a_note(tmp_path):
    root = testdata.make_fake_project(tmp_path / "bt")
    proj = Project.discover(root)
    scan = proj.find(testdata.STACK)
    sd = StackData.load(proj, scan)                  # 'reduced from raw': no cache path, project not registered
    sd.source = "somewhere else"
    pix = np.arange(sd.n_pix)
    sums = zone_sums(sd, pix, ScanSettings(line="total XRF", roi={d: [60, 74] for d in DETS}))
    assert sums.windows is None and any("not applied" in n for n in sums.notes)
    np.testing.assert_array_equal(sums.det["total XRF"], sd.sum_over(pix)["det"]["total XRF"])
    # a stack read from its AURORA cache finds its raw folders from the cache path
    sd2 = StackData.load(proj, scan)
    assert "aurora cache" in sd2.source and xw.source_of(sd2) is not None


def test_the_cache_root_of_every_stack_source():
    """Every source text StackData.load writes names the project root the same way, including the sgm_report cache
    whose windows differ from config/roi_bins.json (I49)."""
    from types import SimpleNamespace
    root = Path("C:/beam time") if os.name == "nt" else Path("/beam time")
    rep = root / "processed" / "2026-11-05" / testdata.STACK / "_reduced_cache.npz"
    own = root / ".aurora" / "cache" / "2026-11-05" / testdata.STACK / "_reduced_cache.npz"
    for text in (f"sgm_report cache {rep}", f"sgm_report cache with other windows {rep}", f"aurora cache {own}"):
        assert xw._cache_root(SimpleNamespace(source=text)) == root, text
    assert xw._cache_root(SimpleNamespace(source="reduced from raw")) is None


def test_roi_round_trips_through_the_session_file(tmp_path):
    from aurora.core.io.config import HolderZones, ZonesFile
    zf = ZonesFile(project="T")
    z = Zone(id=1, label="a", rects=[[0, 1, 0, 1]], polys=[[[0, 0], [1, 0], [0, 1]]])
    z.settings["d_s"] = ScanSettings(roi={"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77]})
    zf.holders["N5"] = HolderZones(holder="N5", day="d", survey="s", zones=[z])
    p = tmp_path / "session.json"
    zf.save(p)
    back = ZonesFile.load(p).holders["N5"].zones[0]
    assert back.settings["d_s"].roi == z.settings["d_s"].roi and back.polys == z.polys
    assert json.loads(p.read_text(encoding="utf-8"))["holders"]["N5"]["zones"][0]["st"]["d_s"]["roi"]["sdd3"] == [62, 76]


def test_demo_preset_f_ka_reproduces_the_cached_line(tmp_path):
    from aurora.cli import cmd_demo
    root = tmp_path / "demo"
    cmd_demo(root)
    proj = Project.discover(root)
    xw.register_project(proj)
    scan = proj.find(testdata.STACK)
    sd = StackData.load(proj, scan)
    assert "F Kα" in sd.labels
    w = xw.bins_windows(proj.roi_bins, "F Kα")
    assert w == {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77]}
    assert xw.label_for_windows(w, proj.roi_bins) == "F Kα"
    for pix in (np.arange(sd.n_pix), np.array([1, 2, 5])):
        np.testing.assert_array_equal(xw.stack_window_sums(sd, pix, w), sd.sum_over(pix)["det"]["F Kα"])
    # the same through zone_sums with the windows set explicitly
    st = ScanSettings(line="F Kα", roi=w)
    np.testing.assert_array_equal(zone_sums(sd, np.arange(6), st).det["F Kα"], sd.sum_over(np.arange(6))["det"]["F Kα"])


def test_line_of_the_edge_needs_a_calibration():
    from aurora.core.io.config import Calibration, RoiBins
    assert abs(xw.emission_line_ev("F Kα") - 676.8) < 0.05 and xw.emission_line_ev("nonsense") is None
    nominal = Calibration(data={})
    assert not nominal.calibrated and xw.edge_line_windows(("F", "K"), nominal) is None
    cal = Calibration(data={d: {"gain": 10.0, "offset": 0.0} for d in DETS})
    bins = RoiBins(data={"_meta": {"n_sigma": 1.5}, "F Kα": {**{d: [60, 74] for d in DETS}, "sigma_ch": 4.5}})
    el = xw.edge_line_windows(("F", "K"), cal, bins)
    assert el.label == "F Kα" and el.sigma_ch == 4.5 and el.n_sigma == 1.5
    lo, hi = cal.bounds("sdd1", el.line_ev - 1.5 * 4.5 * 10, el.line_ev + 1.5 * 4.5 * 10)
    assert el.windows["sdd1"] == [lo, hi] == [60, 75]
    typical = xw.edge_line_windows(("F", "K"), cal, RoiBins(data={}))
    assert typical.sigma_ch == xw.SIGMA_CH_TYPICAL and "typical" in typical.sigma_source


@pytest.mark.skipif(JOHN is None, reason="John Bussey's GUI folder (legacy/CLS_GUI_JohnBussey) is not available")
def test_john_windows_and_polygon_reproduce_his_full_reduced_csv():
    from aurora.core.io.h5 import xy
    from aurora.core.model import ScanInfo
    stack_dir = JOHN / "070810_BO3_LiSO3F_LR_fluorine_k_stack"
    out = JOHN / "gui_outputs" / "reduced" / "BO3_LiSO3F_LR_fluorine_k"
    settings = json.loads((out / "BO3_LiSO3F_LR_fluorine_k_gui_settings.json").read_text(encoding="utf-8"))
    poly = json.loads((out / "BO3_LiSO3F_LR_fluorine_k_polygon_roi.json").read_text(encoding="utf-8"))
    scan = ScanInfo.from_dir(stack_dir, day="")
    x, y = xy(scan.h5)
    from aurora.core.zones import grid_pitch
    z = Zone(id=1, label="BO3", polys=[poly])
    pix = members(z, x, y, grid_pitch(x, y))
    assert pix.tolist() == [37, 38, 41, 42, 57, 58, 61, 62]
    windows = settings["rois"]
    assert windows == {d: [60, 83] for d in DETS}
    sums = xw.window_sums(scan, pix, windows)          # no root: memory only, nothing is written next to his data
    rows = [ln for ln in (out / "BO3_LiSO3F_LR_fluorine_k_full_reduced.csv").read_text(encoding="utf-8").splitlines()
            if ln and not ln.startswith("#")]
    head = rows[0].split(",")
    def num(v):
        return np.nan if v == "" else (float(v == "True") if v in ("True", "False") else float(v))
    data = np.array([[num(v) for v in r.split(",")] for r in rows[1:]])
    assert data.shape[0] == 311 == len(scan.energy_dirs)
    np.testing.assert_allclose(np.array([e for e, _ in scan.energy_dirs]), data[:, head.index("energy")], rtol=1e-12)
    for k, d in enumerate(DETS):
        np.testing.assert_allclose(sums[:, k] / pix.size, data[:, head.index(d)], rtol=1e-12, atol=0, equal_nan=True)
    # his empty cell: the sdd2 file of 704.60 eV is empty; that detector alone is left out there
    i = int(np.argmin(np.abs(data[:, 0] - 704.6)))
    assert np.isnan(data[i, head.index("sdd2")]) and np.isnan(sums[i, 1]) and np.isfinite(sums[i, [0, 2, 3]]).all()
    assert int(np.isnan(sums).sum()) == 1


# ----------------------------------------------------------------------------- the step-2 card in the application
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_same_range_moves_all_four_and_presets(qapp, tmp_path):
    from aurora.cli import cmd_demo
    from wp3_helpers import close, open_window, pump
    root = tmp_path / "demo"
    cmd_demo(root)
    win, sid = open_window(qapp, Project.discover(root))
    try:
        ses, rd = win.session, win.reduce
        wc = rd.window_card
        sd = ses.stack_loaded(sid)
        assert [wc.preset.itemText(i) for i in range(wc.preset.count())] == [
            "F Kα (fixed bins)", "O Kα (fixed bins)", "total XRF (fixed bins)", "custom"]   # no calibration: no edge line
        assert wc.preset.currentText() == "F Kα (fixed bins)" and wc.choice() == {"line": "F Kα", "roi": None}
        assert [tuple(r.getRegion()) for r in wc.regions] == [(60, 74), (61, 75), (62, 76), (63, 77)]
        assert "10 eV/ch nominal" in wc.ev_label.text()
        x0, x1, y0, y1 = sd.bbox
        rd.map.rect_drawn.emit(x0, x1, y0, y1)
        pump(qapp)
        z = ses.zone()
        assert z.settings[sid].line == "F Kα" and z.settings[sid].roi is None
        # 'same range' ticked: setting one region moves all four, and the spins follow
        assert wc.same_range.isChecked()
        wc.regions[1].setRegion((50, 70))
        pump(qapp)
        assert [tuple(round(v) for v in r.getRegion()) for r in wc.regions] == [(50, 70)] * 4
        assert (wc.lo.value(), wc.hi.value()) == (50, 70) and wc.preset.currentText() == "custom"
        wc.commit_now()
        pump(qapp)
        st = z.settings[sid]
        assert st.roi == {d: [50, 70] for d in DETS} and st.line == "F Kα" and {"line", "roi"} <= set(st.pinned)
        win.jobs.wait_idle(30000)
        pump(qapp)
        rd.update_preview()
        t = rd.preview._traces[0][1]
        assert "windows 50:70 ch" in t.line
        pix = members(z, sd.x, sd.y, sd.pitch)
        np.testing.assert_array_equal(zone_sums(sd, pix, st).det["F Kα"], _raw_window(proj_scan(ses, sid), pix, 50, 70))
        # the map shows the counts in the windows
        assert rd.map_line.currentText() == xw.WINDOW_LABEL
        # back to the preset: the fixed line again, no windows
        wc.preset.setCurrentIndex(0)
        pump(qapp)
        assert st.line == "F Kα" and st.roi is None and rd.map_line.currentText() == "F Kα"
        # windows equal to a line's bins are that line
        wc.set_window(None, 46, 58, commit=False)
        assert wc.choice() == {"line": "O Kα", "roi": None}
        # unlinked (Advanced): one detector apart
        wc.same_range.setChecked(False)
        wc.det.setCurrentIndex(2)
        wc.set_window(2, 10, 20, commit=False)
        assert wc.windows()["sdd3"] == [10, 20] and wc.windows()["sdd1"] == [46, 58]
    finally:
        close(win)


def proj_scan(ses, sid):
    return ses.scan_of(sid)


def test_line_of_the_edge_offered_with_a_calibration(qapp, fake_project):
    from wp3_helpers import close, open_window
    (fake_project / "config" / "sdd_calibration.json").write_text(json.dumps(
        {d: {"gain": 10.0, "offset": 0.0} for d in DETS}), encoding="utf-8")
    win, sid = open_window(qapp, Project.discover(fake_project))
    try:
        wc = win.reduce.window_card
        items = [wc.preset.itemText(i) for i in range(wc.preset.count())]
        assert items[0] == "total XRF (fixed bins)" and items[1].startswith("line of the edge: F Kα 676.8 eV")
        assert "(calibrated)" in wc.ev_label.text()
        wc.preset.setCurrentIndex(1)
        st = None
        z = win.session.zone()
        assert z is None                              # no zone yet: the choice waits for the next sample
        sd = win.session.stack_loaded(sid)
        win.reduce.map.rect_drawn.emit(*sd.bbox)
        st = win.session.zone().settings[sid]
        assert st.line == "F Kα" and st.roi == xw.edge_line_windows(("F", "K"), win.session.project.calibration,
                                                                    win.session.project.roi_bins).windows
    finally:
        close(win)


def test_a_roi_bins_line_the_stack_lacks_is_read_from_the_raw_spectra(qapp, fake_project):
    """Every line of roi_bins.json is a preset. A line the stack was reduced without becomes windows read from the raw
    spectra, so the trace still exists. The stack here is sgm_report's cache holding only some of the project's lines
    (sgm_report picks the lines per sample); AURORA's own cache is re-reduced when roi_bins.json gains a line (I49)."""
    from aurora.core.io.raw import stack_last_mtime
    from wp3_helpers import close, open_window, pump
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    first = StackData.load(proj, scan)                            # reduced with 'total XRF' only
    assert first.labels == ["total XRF"]
    rep = proj.processed / scan.day / scan.name                   # ... and kept as sgm_report v2.5 would write it
    rep.mkdir(parents=True, exist_ok=True)
    windows = {"total XRF": {d: [5, 200] for d in DETS}}
    np.savez(rep / "_reduced_cache.npz", i0=first.i0, tey=first.tey, roi_pix_det=first.roi_pix_det, valid=first.valid,
             roi_labels=np.array(first.labels, dtype=object),
             signature=f"v2.5|{len(scan.energy_dirs)}|{stack_last_mtime(scan.energy_dirs):.0f}|"
                       f"{json.dumps(windows, sort_keys=True)}")
    (proj.root / ".aurora" / "cache" / scan.day / scan.name / "_reduced_cache.npz").unlink()
    (fake_project / "config" / "roi_bins.json").write_text(json.dumps(
        {"F Kα": {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77]}}), encoding="utf-8")
    win, sid = open_window(qapp, Project.discover(fake_project))
    try:
        ses, rd = win.session, win.reduce
        wc = rd.window_card
        sd = ses.stack_loaded(sid)
        assert sd.labels == ["total XRF"] and sd.source.startswith("sgm_report cache ") and not sd.window_note
        items = [wc.preset.itemText(i) for i in range(wc.preset.count())]
        assert items == ["total XRF (fixed bins)", "F Kα (fixed bins)", "custom"]
        rd.map.rect_drawn.emit(*sd.bbox)
        pump(qapp)
        wc.preset.setCurrentIndex(1)
        pump(qapp)
        st = ses.zone().settings[sid]
        assert st.line == "F Kα" and st.roi == {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77]}
        assert wc.preset.currentText() == "F Kα (fixed bins)"
        win.jobs.wait_idle(30000)
        pump(qapp)
        rd.update_preview()
        t = rd.preview._traces[0][1]
        assert t.sum is not None and "windows SDD1 60:74" in t.line
    finally:
        close(win)


def test_real_windows_reproduce_the_sgm_report_bins(real_project):
    """Read-only, on the real beamtime: the roi_bins F Kα windows read from the raw spectra give exactly the F Kα line of
    sgm_report's reduced cache (an independent implementation) at every energy the cache kept. Memory only: no root,
    nothing written."""
    from aurora.core.io.safety import is_protected
    proj = Project.discover(real_project / "raw" / "2026-11-05")
    assert is_protected(proj.root)
    w = xw.bins_windows(proj.roi_bins, "F Kα")
    scan = proj.find("111037_sampleE_f_k_stack")
    sd = StackData.load(proj, scan, allow_reduce=False)
    pix = np.arange(sd.n_pix)
    s = xw.window_sums(scan, pix, w, n_pix=sd.n_pix)
    ref = sd.sum_over(pix)["det"]["F Kα"]
    ok = np.isfinite(ref).all(axis=1)
    assert ok.sum() >= sd.n_energies - 1
    np.testing.assert_array_equal(s[ok], ref[ok])
