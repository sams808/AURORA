"""The Maps perspective, offscreen, on the synthetic project."""
import json

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from aurora.core import testdata  # noqa: E402
from aurora.core.hypercube import Hypercube  # noqa: E402
from aurora.core.model import Project  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_maps_perspective(qapp, fake_project):
    (fake_project / "config" / "roi_bins.json").write_text(json.dumps({
        "_meta": {"gains": {}}, "F Kα": {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77], "line_ev": 676.8},
        "O Kα": {"sdd1": [46, 58], "sdd2": [46, 58], "sdd3": [46, 58], "sdd4": [46, 58], "line_ev": 525.0}}))
    from aurora.app.main import MainWindow
    win = MainWindow()
    proj = Project.discover(fake_project)
    win.home.set_project(proj)
    qapp.processEvents()
    mp = win.maps
    assert mp.scan_combo.count() == 1
    hc = Hypercube.load(proj, proj.find(testdata.STACK))
    mp.set_hypercube(hc)
    qapp.processEvents()
    assert mp.map.image.image is not None and mp.map.image.image.shape == (3, 4)
    assert mp.line.count() == 3 and mp.energy.maximum() == 4
    mp.line.setCurrentIndex(1)                                   # F Kα -> window from the bins (10 eV / ch, no calibration)
    assert mp.w_lo.value() == 600 and mp.w_hi.value() == 740          # channels [60, 74) at 10 eV per channel, hi exclusive
    assert mp.window(0) == (60, 74)
    assert len(mp.xanes.plotItem.listDataItems()) == 5          # four detectors + sum
    # rectangle ROI over the first row
    mp.roi_mode.setCurrentIndex(2)
    mp.map.rect_drawn.emit(float(hc.x.min()), float(hc.x.max()), float(hc.y.min()), float(hc.y.min()))
    assert mp._roi is not None and len(mp._roi) == 4
    # current-zone ROI
    ses = win.session
    sid = ses.stack_id(proj.find(testdata.STACK)); ses.stack(sid); ses.touch()
    x0, x1, y0, y1 = (hc.x.min(), hc.x.max(), hc.y.min(), hc.y.max())
    z = ses.new_zone("z"); z.rects.append([x0, x1, y0, y1]); ses.touch(); ses.select_zone(z.id)
    mp.roi_mode.setCurrentIndex(1)
    assert len(mp._roi) == 12
    mp.draw_elements()                                           # a job: drawn when it ends
    assert win.jobs.wait_idle(20000)
    assert len(mp.el_layout.ci.items) == 2
    mp.draw_rgb()
    assert mp.rgb_view.image.image is not None and mp.rgb_view.image.image.shape == (3, 4, 3)
    mp.n_k.setValue(2); mp.run_clusters()
    assert win.jobs.wait_idle(20000)
    assert mp._cluster is not None and set(mp._cluster.labels) <= {0, 1}
    win.show_perspective("maps")
    win.close()


# ----------------------------------------------------------------------------- a cube that lands late
def _window(qapp, root):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.home.set_project(Project.discover(root))
    qapp.processEvents()
    return win


def _close(win):
    win.session._save_timer.stop()
    win.close()


def test_a_cube_that_lands_after_a_holder_switch_is_not_shown(qapp, tmp_path):
    """Load holderA's cube, switch to holderB before it lands (its result is queued to the GUI thread, so the switch comes first,
    as when a user moves on during a minutes-long load): holderB shows no cube, so no holderB zone can be made of holderA's pixels."""
    win = _window(qapp, testdata.make_fake_project(tmp_path / "two", n_holders=2))
    ses, mp = win.session, win.maps
    ses.set_holder("holderA"); qapp.processEvents()
    mp.load()
    ses.set_holder("holderB")
    assert win.jobs.wait_idle(20000)
    assert mp.hc is None
    assert [mp.scan_combo.itemData(i) for i in range(mp.scan_combo.count())] == \
        [ses.stack_id(s) for s in ses.stacks_of("holderB")]
    assert "not shown" in mp.status.text() and testdata.STACK in mp.status.text()
    # back on holderA the cube opens from its cache
    ses.set_holder("holderA"); qapp.processEvents()
    assert "cached" in mp.cube_estimate()
    _close(win)


def test_a_cube_that_lands_after_another_project_opened_is_not_shown(qapp, tmp_path):
    a = testdata.make_fake_project(tmp_path / "a")
    b = testdata.make_fake_project(tmp_path / "b")
    win = _window(qapp, a)
    mp = win.maps
    mp.load()
    win.home.set_project(Project.discover(b))
    assert win.jobs.wait_idle(20000)
    assert mp.hc is None
    _close(win)


def test_a_cube_that_lands_after_another_stack_was_picked_is_not_shown(qapp, tmp_path):
    """Load stack A, pick stack B of the same holder and load it: B is shown, A (landing last) is dropped."""
    root = testdata.make_fake_project(tmp_path / "p")
    x, y = testdata.snake_grid()
    testdata._write_stack(root / "raw" / testdata.DAY / "094000_LongOne_f_k_stack", "LongOne_f_k",
                          testdata.ENERGIES, len(testdata.ENERGIES), x, y, np.random.default_rng(1),
                          created="Fri Mar 14 09:40:00 2025")
    win = _window(qapp, root)
    mp = win.maps
    sids = [mp.scan_combo.itemData(i) for i in range(mp.scan_combo.count())]
    assert len(sids) == 2
    mp.scan_combo.setCurrentIndex(0); mp.load()
    mp.scan_combo.setCurrentIndex(1)
    assert win.jobs.wait_idle(20000)
    assert mp.hc is None                                          # A landed while B was picked
    mp.load()
    assert win.jobs.wait_idle(20000)
    assert mp.hc is not None and win.session.stack_id(win.session.project.find(mp.hc.name)) == sids[1]
    _close(win)


def test_a_zone_from_clusters_goes_only_to_the_holder_of_the_cube(qapp, tmp_path):
    """A cube of holderA on screen while holderB is the current holder (set directly): Make zone refuses instead of writing holderA's
    pixels into holderB, and the label names the cube's own holder."""
    from aurora.core.hypercube import ClusterResult
    root = testdata.make_fake_project(tmp_path / "two", n_holders=2)
    win = _window(qapp, root)
    ses, mp = win.session, win.maps
    ses.set_holder("holderB"); qapp.processEvents()
    proj = ses.project
    hc = Hypercube.load(proj, proj.find(testdata.STACK))           # holderA's stack
    mp.set_hypercube(hc)
    labels = np.arange(hc.n_pix) % 2
    M = hc.pixel_matrix(None, 60, 75)
    cr = ClusterResult(labels=labels, centres=np.array([M[labels == c].mean(axis=0) for c in (0, 1)]),
                       scores=np.zeros((hc.n_pix, 2)), explained=np.array([0.6, 0.3]), E=hc.E[hc.valid], seed=0)
    mp.show_clusters(cr, M, {"scan": hc.name})
    assert mp._cluster_label().startswith("clusters 1+2 · holderA 09:27")
    assert mp.make_zone_from_clusters() is None
    assert not ses.holder_zones("holderB").zones and not ses.holder_zones("holderA").zones
    assert "holderA" in mp.status.text()
    _close(win)


def test_every_intensity_map_has_a_colour_scale(qapp, fake_project):
    """Survey, Reduce, Maps Explore and each elemental map show their colour scale: the image's own levels, the colour
    map of the Settings and the quantity ('log10(counts / I0 + 1)'); the cluster and RGB maps (no colour map) have none."""
    import pyqtgraph as pg
    (fake_project / "config" / "roi_bins.json").write_text(json.dumps({
        "F Kα": {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77]},
        "O Kα": {"sdd1": [46, 58], "sdd2": [46, 58], "sdd3": [46, 58], "sdd4": [46, 58]}}), encoding="utf-8")
    win = _window(qapp, fake_project)
    ses, sv, rd, mp = win.session, win.survey, win.reduce, win.maps
    sid = rd.scan_list.item(0).data(0x0100)
    ses.stack(sid); ses.touch(); ses.select_stack(sid)
    qapp.processEvents()
    proj = ses.project
    mp.set_hypercube(Hypercube.load(proj, proj.find(testdata.STACK)))

    def label(view):
        return view.bar.getAxis("left").labelText

    for view, text in ((sv.map, "log10(counts / I0 + 1)"), (rd.map, "counts / I0"), (mp.map, "counts / I0")):
        assert view.scale_levels() == pytest.approx(tuple(view.image.levels)), text
        assert label(view) == text and view.bar.colorMap().name == view.colormap_name()
    mp.log.setChecked(True)
    assert label(mp.map) == "log10(counts / I0 + 1)" and mp.map.scale_levels() == pytest.approx(tuple(mp.map.image.levels))
    assert mp.cluster_map.scale_levels() is None and mp.rgb_view.scale_levels() is None
    mp.draw_elements()
    assert win.jobs.wait_idle(20000)
    cells = list(mp.el_layout.ci.items)
    assert len(cells) == 2
    for cell in cells:
        img = next(it for it in cell.items if isinstance(it, pg.PlotItem) and not isinstance(it, pg.ColorBarItem)).items[0]
        bar = next(it for it in cell.items if isinstance(it, pg.ColorBarItem))
        assert bar.levels() == pytest.approx(tuple(img.levels)) and bar.getAxis("left").labelText == "counts / I0"
    _close(win)
