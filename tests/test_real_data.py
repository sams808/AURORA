"""Checks against the Nov 2026 beamtime folder (skipped where it is not mounted)."""
import numpy as np
import pytest

from aurora.core.io.cache import ReducedCache, load_count_rate
from aurora.core.io.config import read_sample_sheet
from aurora.core.io.h5 import find_h5, xy
from aurora.core.io.raw import read_point_dir
from aurora.core.model import Project

DAY = "2026-11-05"
SBOF = "092758_alpha_beta_f_k_stack"
# Zone "13 alpha_beta" of sgm_report v2.5 (layout cell row 1 col 1, 18 px, centre x = 3.73 y = 5.86 mm). The pixel list is not
# stored in summary.json; it was identified on 2026-11-06 by a non-negative least-squares fit of the CSV's I0 and RAW
# traces against the cache's per-pixel traces (weights came out exactly 0 or 1, residual 1e-11). Two of the 18 are not
# among the 18 pixels nearest the centroid: the zone follows the plate layout grid, not a disc.
SBOF_ZONE_13_PIXELS = [81, 82, 83, 90, 91, 92, 93, 94, 103, 104, 105, 106, 107, 111, 112, 113, 115, 116]
SBOF_ZONE_13_CENTRE = (3.7252661975295487, 5.856076761314025)


@pytest.fixture(scope="module")
def project(real_project):
    return Project.discover(real_project / "raw" / DAY)


def _read_zone_csv(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = [l for l in lines if l.startswith("#")]
    cols = next(l for l in lines if not l.startswith("#")).split(",")
    data = np.genfromtxt(path, delimiter=",", comments="#", skip_header=len(header) + 1)
    return header, cols, data


def test_day_discovery(project):
    day = project.days[0]
    names = {h.name for h in day.holders}
    assert {"N5", "Se", "N6"} <= names
    n5 = next(h for h in day.holders if h.name == "N5")
    assert n5.survey.name == "010013_holderA_survey"          # the three earlier surveys were superseded
    assert len(n5.superseded_surveys) == 3
    assert any(s.name == SBOF for s in n5.stacks)
    sbof = project.find(SBOF)
    assert sbof.status == "complete" and sbof.n_energies == 311 and sbof.n_points == 121
    assert sbof.edge == ("F", "K") and (sbof.e_min, sbof.e_max) == (680.0, 730.0)
    assert project.find("143504_photodiode_f_k_stack").status == "test"
    assert project.find("154516_ZnSe_coarse_se_l3l2_coarse_stack").edge == ("Se", "L3L2")
    assert project.calibration.calibrated
    assert "F Kα" in project.roi_bins.labels


def test_summary_flags(project):
    se = project.find("155838_sampleD_coarse_se_l3l2_coarse_stack")
    assert se is not None and se.summary, "sgm_report summary expected for the Se scans"
    assert se.summary.get("i0_bad") is True and "I0 invalid" in se.flags


def test_reduced_cache_reproduces_sgm_report_zone_csv(real_project):
    """Acceptance check of docs/HANDOFF.md section 3.3: zone '13 alpha_beta' from the per-pixel cache equals the CSV."""
    proc = real_project / "processed" / DAY / SBOF
    rc = ReducedCache.load(proc)
    assert rc is not None and rc.n_energies == 311 and rc.n_pix == 121
    assert rc.roi_labels == ["O Kα", "Sb Mα", "F Kα", "total XRF"]
    assert (~rc.valid).sum() == 1 and rc.skipped[0][0] == 696.8         # one incomplete map (119/121 points)
    x, y = xy(find_h5(real_project / "raw" / DAY / SBOF))
    idx = np.array(SBOF_ZONE_13_PIXELS)
    assert np.allclose((x[idx].mean(), y[idx].mean()), SBOF_ZONE_13_CENTRE, atol=1e-9)
    header, cols, data = _read_zone_csv(proc / "13_alpha_beta_xanes.csv")
    assert header[0].startswith(f"# scan: {SBOF}   zone 1/4: 13 alpha_beta (18 pixels")
    assert data.shape[0] == 311
    ok = rc.valid & np.isfinite(data[:, cols.index("RAW_FKa")])
    assert ok.sum() == 310
    j = rc.roi_labels.index("F Kα")
    i0 = rc.i0[:, idx].sum(axis=1)
    raw = rc.roi_pix_det[:, idx, j, :].sum(axis=(1, 2))
    # the CSV carries 6 significant digits (54.6009 for 54.600889): counts are integers and match exactly
    assert np.allclose(i0[ok], data[ok, cols.index("I0_sum_V")], rtol=2e-6, atol=0)
    assert np.allclose(raw[ok], data[ok, cols.index("RAW_FKa")], rtol=0, atol=1e-6)
    for k in range(4):
        per_det = rc.roi_pix_det[:, idx, j, k].sum(axis=1)
        assert np.allclose(per_det[ok], data[ok, cols.index(f"RAW_FKa_SDD{k + 1}")], rtol=0, atol=1e-6)
    assert np.allclose(raw[ok] / i0[ok], data[ok, cols.index("PFY_FKa_over_I0")], rtol=1e-5, atol=0)
    assert np.isnan(data[~rc.valid, cols.index("PFY_FKa_over_I0")]).all()


def test_count_rate_cache(real_project):
    d = load_count_rate(real_project / "processed" / DAY / SBOF)
    assert d and len(d["rows"]) == 7 and d["time_per_map_s"] > 0


def test_sample_sheet_real(real_project):
    sheets = list(real_project.glob("samples*.csv"))
    if not sheets:
        pytest.skip("sample sheet not found")
    rows = read_sample_sheet(sheets[0])
    assert len(rows) > 100
    assert "Group 7" in {r.group for r in rows}


def test_first_energy_read(project):
    s = project.find(SBOF)
    e, p = s.energy_dirs[0]
    pd = read_point_dir(p)
    assert e == 680.0 and pd.n_pix == 121 and pd.complete and pd.mcc.shape[1] == 8


def test_real_zone_commit_timing(real_project, monkeypatch):
    """One stroke written with every listener connected (Reduce shown, Check and Survey hidden) on the holder with the
    largest survey and its largest stack: the median over 6 strokes is 400 ms or less (it was 2.5-6 s before the
    vectorised de-spike, the shared trace cache and the deferred hidden views), x3 with AURORA_TIMING_LOOSE=1. Never
    writes into the beamtime (Session.save is a no-op here, and the folder is protected)."""
    import os
    import time

    pytest.importorskip("pyqtgraph")
    from aurora.app.main import MainWindow, create_app
    from aurora.app.session import Session
    from aurora.core.zones import Stroke, members
    monkeypatch.setattr(Session, "save", lambda self: None)
    qapp = create_app(["aurora-tests"])
    win = MainWindow()
    win.resize(1200, 760)
    win.show()
    try:
        win.home.set_project(Project.discover(real_project))
        qapp.processEvents()
        win.jobs.wait_idle(120000)
        ses = win.session

        def size(key):
            h = ses.holder_keys[key][1]
            return (h.survey.n_points if h.survey is not None else 0), len(h.stacks)
        ses.set_holder(max(ses.holder_keys, key=size))
        qapp.processEvents()
        sid = ses.stack_id(max(ses.holder().stacks, key=lambda s: s.n_points or 0))
        sd = ses.stack(sid)
        win.reduce._attach(sd)
        ses.touch()
        ses.select_stack(sid)
        win.show_perspective("reduce")
        for _ in range(10):
            qapp.processEvents()
        win.jobs.wait_idle(60000)
        if ses.current_stack != sid:
            ses.select_stack(sid)
        rd = win.reduce
        x0, x1, y0, y1 = sd.bbox
        zid = rd.editor.commit(Stroke(shape="rect", rect=(x0, (x0 + x1) / 2, y0, (y0 + y1) / 2)))
        assert zid is not None
        z = ses.zone(zid)
        pix = members(z, sd.x, sd.y, sd.pitch)
        box = (float(sd.x[pix].min()), float(np.median(sd.x[pix])), float(sd.y[pix].min()), float(np.median(sd.y[pix])))
        times = []
        for i in range(6):                                  # erase, then draw back, the same box: each one writes
            n = ses.undo_stack.count()
            t = time.perf_counter()
            rd.editor.commit(Stroke(shape="rect", rect=box, erase=i % 2 == 0, serial=10 + i))
            times.append(time.perf_counter() - t)
            qapp.processEvents()
            assert ses.undo_stack.count() == n + 1
        limit = 0.4 * (3 if os.environ.get("AURORA_TIMING_LOOSE") == "1" else 1)
        assert float(np.median(times)) <= limit, [round(1000 * v) for v in times]
    finally:
        win.session._save_timer.stop()
        win.session.project = None
        win.close()


def test_real_zone_preview_timings(real_project, monkeypatch):
    """The live preview of a stroke on the holder with the largest survey and its largest stack (the stack loaded),
    medians over 5 strokes of different sizes (the trace cache cleared, so each one computes): the Survey's Zone
    preview of a draft 80 ms or less, Reduce's preview of a draft 60 ms or less without the de-spike and 120 ms or
    less with the coherent de-spike of Easy mode; x3 with AURORA_TIMING_LOOSE=1. Never writes into the beamtime."""
    import os
    import time

    pytest.importorskip("pyqtgraph")
    from aurora.app import trace_cache
    from aurora.app.main import MainWindow, create_app
    from aurora.app.session import Session
    from aurora.core.zones import Stroke
    monkeypatch.setattr(Session, "save", lambda self: None)
    qapp = create_app(["aurora-tests"])
    win = MainWindow()
    win.resize(1200, 760)
    win.show()
    try:
        win.home.set_project(Project.discover(real_project))
        qapp.processEvents()
        win.jobs.wait_idle(120000)
        ses = win.session

        def size(key):
            h = ses.holder_keys[key][1]
            return (h.survey.n_points if h.survey is not None else 0), len(h.stacks)
        ses.set_holder(max(ses.holder_keys, key=size))
        qapp.processEvents()
        sid = ses.stack_id(max(ses.holder().stacks, key=lambda s: s.n_points or 0))
        sd = ses.stack(sid)
        win.reduce._attach(sd)
        ses.touch()
        ses.select_stack(sid)
        win.show_perspective("survey")
        for _ in range(10):
            qapp.processEvents()
        win.jobs.wait_idle(60000)
        if ses.current_stack != sid:
            ses.select_stack(sid)
        x0, x1, y0, y1 = sd.bbox
        loose = 3 if os.environ.get("AURORA_TIMING_LOOSE") == "1" else 1

        def strokes():
            for i, f in enumerate((0.5, 0.6, 0.7, 0.8, 0.9)):
                yield Stroke(shape="rect", rect=(x0, x0 + f * (x1 - x0), y0, y0 + f * (y1 - y0)), serial=100 + i)

        def median_ms(editor, run) -> float:
            times = []
            for st in strokes():
                d = editor.make_draft(st)
                assert not d.empty and d.members_after.size
                trace_cache.clear()
                t = time.perf_counter()
                run(d)
                times.append(1000 * (time.perf_counter() - t))
                qapp.processEvents()
            return float(np.median(times)), [round(v) for v in times]

        sv, rd = win.survey, win.reduce
        n = ses.undo_stack.count()
        ms, all_ms = median_ms(sv.editor, lambda d: sv.preview_draft(d))
        assert sv.zone_preview.emission.data is not None and sv.zone_preview.xas_sum() is not None
        assert ms <= 80 * loose, all_ms
        win.show_perspective("reduce")
        for _ in range(10):
            qapp.processEvents()
        ms, all_ms = median_ms(rd.editor, lambda d: rd.preview_draft(d, fast=True))
        assert rd.preview._traces and ms <= 60 * loose, all_ms
        ms, all_ms = median_ms(rd.editor, lambda d: rd.preview_draft(d, fast=False))
        assert rd.preview._traces[0][1].settings.despike and ms <= 120 * loose, all_ms
        assert ses.undo_stack.count() == n                          # previews never write
    finally:
        win.session._save_timer.stop()
        win.session.project = None
        win.close()
