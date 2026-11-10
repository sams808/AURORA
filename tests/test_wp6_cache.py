"""I37: Maps computes each thing once. The map, the spectrum and the XANES each sit behind a cache keyed by their
inputs; the slider moves the map only while dragged; heavy actions run as jobs; a hidden Maps ignores zone edits until
it is shown again; elemental maps come from the per-bin sums and never read the cube."""
import json
import statistics
import time

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402

from aurora.core import hypercube as hcmod  # noqa: E402
from aurora.core import testdata  # noqa: E402
from aurora.core.cancel import Cancelled  # noqa: E402
from aurora.core.constants import DETS, N_CH  # noqa: E402
from aurora.core.hypercube import Hypercube  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from aurora.core.zones import grid_pitch  # noqa: E402

BINS = {"_meta": {"gains": {}}, "F Kα": {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77], "line_ev": 676.8},
        "O Kα": {"sdd1": [46, 58], "sdd2": [46, 58], "sdd3": [46, 58], "sdd4": [46, 58], "line_ev": 525.0}}


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


class CountingCube:
    """A cube that counts the energy slabs read from it."""

    def __init__(self, a):
        self.a = a
        self.reads = 0

    def __getitem__(self, k):
        self.reads += 1
        return self.a[k]

    @property
    def shape(self):
        return self.a.shape


def synthetic(n_e: int, nx: int, ny: int, broadcast: bool = False, seed: int = 5) -> Hypercube:
    """An in-memory cube: a peak near channel 65 + d on each SDD whose height follows an edge step in energy. With
    broadcast=True every energy shares one slab per detector (no memory per energy, the same work per step)."""
    rng = np.random.default_rng(seed)
    x, y = testdata.snake_grid(nx=nx, ny=ny, pitch=0.05, x0=0.0, y0=0.0)
    E = np.linspace(680.0, 700.0, n_e)
    ch = np.arange(N_CH)
    cubes = []
    for d in range(len(DETS)):
        prof = 200 * np.exp(-0.5 * ((ch - (65 + d)) / 3.0) ** 2) + 2
        amp = rng.uniform(0.2, 1.0, x.size)
        if broadcast:
            base = rng.poisson(amp[:, None] * prof[None, :]).astype(np.uint16)
            cubes.append(np.broadcast_to(base[None], (n_e, x.size, N_CH)))
        else:
            step = 1.0 + 0.8 / (1 + np.exp(-(E - 690.0)))
            cubes.append(rng.poisson(step[:, None, None] * amp[None, :, None] * prof[None, None, :]).astype(np.uint16))
    i0 = 2.5 + 0.01 * rng.standard_normal((n_e, x.size))
    hc = Hypercube(name="synthetic", E=E, x=x, y=y, cubes=cubes, i0=i0, tey=np.ones((n_e, x.size)),
                   valid=np.ones(n_e, bool), pitch=grid_pitch(x, y))
    return hc


def _window(qapp, size=(1300, 820)):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.resize(*size)
    win.show()
    win.show_perspective("maps")
    pump(qapp)
    return win


def test_twenty_slider_steps_compute_the_xanes_once(qapp):
    win = _window(qapp)
    mp = win.maps
    hc = synthetic(30, 12, 10)
    hc.cubes = [CountingCube(c) for c in hc.cubes]
    mp.set_hypercube(hc)
    mp.roi_mode.setCurrentIndex(2)                                   # a rectangle ROI: its XANES reads the cube
    mp.map.rect_drawn.emit(0.1, 0.3, 0.1, 0.3)
    assert mp._roi is not None and 0 < len(mp._roi) < hc.n_pix
    assert win.jobs.wait_idle(10000)
    base = mp.stats["xanes"]
    reads0 = sum(c.reads for c in hc.cubes)
    for i in range(20):
        mp.energy.setValue((i * 7 + 3) % 30)
        pump(qapp, 1)
    assert win.jobs.wait_idle(10000)
    assert mp.stats["xanes"] == base                                 # cached: 0 new computations in 20 steps
    reads = sum(c.reads for c in hc.cubes) - reads0
    assert reads <= 20 * 2 * len(DETS), reads                       # the map and the ROI spectrum, never 30 energies
    names = [it.name() for it in mp.xanes.plotItem.listDataItems()]
    assert names[-1] == "sum" and len(names) == 5
    # the window band moves: the XANES is computed once more, in a job with a 'computing' note
    mp.band.setRegion([630.0, 690.0])
    mp.band.sigRegionChangeFinished.emit(mp.band)
    assert mp.stats["xanes"] == base + 1
    assert win.jobs.wait_idle(10000)
    for i in range(5):
        mp.energy.setValue(i)
        pump(qapp, 1)
    assert mp.stats["xanes"] == base + 1
    # ticking an SDD re-sums the cached XANES of the four detectors: no new computation
    mp.sdd_boxes[3].setChecked(False)
    assert mp.stats["xanes"] == base + 1 and mp.xanes.plotItem.listDataItems()[-1].name() == "sum of SDD1, SDD2, SDD3"
    win.close()


def test_dragging_moves_only_the_map(qapp):
    win = _window(qapp)
    mp = win.maps
    mp.set_hypercube(synthetic(20, 8, 6))
    pump(qapp)
    s0, x0, r0 = mp.stats["spectra"], mp.stats["xanes"], mp.stats["redraws"]
    mp.energy.setSliderDown(True)
    for v in (3, 4, 5, 6, 7):
        mp.energy.setValue(v)
    assert mp._map_timer.isActive() and mp.stats["redraws"] == r0      # debounced: nothing yet
    time.sleep(0.08)
    pump(qapp, 3)
    assert mp.energy_label.text().startswith(f"{mp.hc.E[7]:.2f}")       # the map followed once, 60 ms after the last step
    assert mp.stats["spectra"] == s0 and mp.stats["redraws"] == r0
    mp.energy.setSliderDown(False)                                      # released: everything else follows
    assert mp.stats["redraws"] == r0 + 1 and mp.stats["spectra"] == s0 + 1 and mp.stats["xanes"] == x0
    win.close()


def test_a_slider_step_on_a_4000_pixel_cube_is_quick(qapp):
    win = _window(qapp, size=(1500, 940))
    mp = win.maps
    hc = synthetic(100, 80, 50, broadcast=True)
    assert hc.n_pix == 4000 and hc.n_energies == 100
    hc.precompute()                                                     # as Hypercube.load leaves it
    mp.set_hypercube(hc)
    assert win.jobs.wait_idle(10000)
    pump(qapp, 5)
    times = []
    for i in range(1, 21):
        t0 = time.perf_counter()
        mp.energy.setValue((i * 13) % 100)
        qapp.processEvents()
        times.append(time.perf_counter() - t0)
    med = statistics.median(times)
    print(f"slider step on 4000 px x 100 E: median {1000 * med:.1f} ms, max {1000 * max(times):.1f} ms")
    assert med < 0.030
    win.close()


def test_clustering_runs_off_the_gui_thread_and_cancel_keeps_the_old_map(qapp, fake_project, monkeypatch):
    win = _window(qapp)
    proj = Project.discover(fake_project)
    win.home.set_project(proj)
    win.show_perspective("maps")
    mp = win.maps
    mp.set_hypercube(Hypercube.load(proj, proj.find(testdata.STACK)))
    mp.tabs.setCurrentIndex(3)
    mp.n_k.setValue(2)
    mp.run_clusters()
    assert win.jobs.wait_idle(20000)
    cr0 = mp._cluster
    img0 = np.array(mp.cluster_map.image.image, copy=True)
    assert cr0 is not None
    orig = hcmod.pca_kmeans

    def slow(*a, **k):
        time.sleep(2.0)
        return orig(*a, **k)
    monkeypatch.setattr(hcmod, "pca_kmeans", slow)
    ticks = []
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start()
    t0 = time.perf_counter()
    mp.cluster_btn.click()
    assert time.perf_counter() - t0 < 0.5                               # Run returns at once
    assert not mp.cluster_btn.isEnabled() and "running" in mp.cluster_btn.toolTip()
    assert mp.taskbar.isVisible() and mp.taskbar.cancel_btn.isVisible()
    while time.perf_counter() - t0 < 1.0:
        qapp.processEvents(QEventLoop.AllEvents, 10)
        time.sleep(0.002)
    mp.taskbar.cancel()
    assert win.jobs.wait_idle(8000)
    timer.stop()
    gaps = np.diff(ticks)
    assert len(ticks) >= 8 and gaps.max() < 0.150, gaps          # 150 ms as the MCR test: 103 ms seen on a CI Mac
    assert mp._cluster is cr0 and np.array_equal(mp.cluster_map.image.image, img0, equal_nan=True)
    assert mp.cluster_btn.isEnabled() and mp.taskbar.text() == "cancelled"
    win.close()


def test_a_hidden_maps_ignores_zone_edits_until_it_is_shown(qapp, fake_project):
    win = _window(qapp)
    proj = Project.discover(fake_project)
    win.home.set_project(proj)
    ses, mp = win.session, win.maps
    hc = Hypercube.load(proj, proj.find(testdata.STACK))
    mp.set_hypercube(hc)
    sid = ses.stack_id(proj.find(testdata.STACK))
    ses.stack(sid); ses.touch()
    z = ses.new_zone("z")
    with ses.edit("add a rectangle to zone 1"):
        z.rects.append([float(hc.x.min()), float(hc.x.max()), float(hc.y.min()), float(hc.y.min())])
    ses.select_zone(z.id)
    mp.roi_mode.setCurrentIndex(1)
    assert len(mp._roi) == 4
    win.show_perspective("reduce")
    pump(qapp)
    mp.stats["redraws"] = 0
    for i in range(20):
        ses.touch()
    with ses.edit("add a rectangle to zone 1"):
        z.rects.append([float(hc.x.min()), float(hc.x.max()), float(hc.y.max()), float(hc.y.max())])
    pump(qapp)
    assert mp.stats["redraws"] == 0
    win.show_perspective("maps")
    pump(qapp)
    assert mp.stats["redraws"] == 1 and len(mp._roi) == 8                # one redraw, with the edited zone
    # a visible Maps on 'current zone' follows each edit; on 'whole map' it ignores them
    ses.touch()
    assert mp.stats["redraws"] == 2
    mp.roi_mode.setCurrentIndex(0)
    n = mp.stats["redraws"]
    win.show_perspective("reduce"); pump(qapp)
    for i in range(5):
        ses.touch()
    win.show_perspective("maps"); pump(qapp)
    assert mp.stats["redraws"] == n
    win.close()


def _v03_elemental(hc, labels_bounds, d, ei, per_i0):
    """AURORA 0.3 draw_elements, from the cube itself."""
    out = []
    for lab, b in labels_bounds:
        if d is None:
            m = sum(hc.window_map(k, *b[DETS[k]], ei, False) for k in range(4))
            if per_i0:
                i0 = hc.i0[ei] if ei is not None else np.nanmean(hc.i0[hc.valid], axis=0)
                with np.errstate(all="ignore"):
                    m = np.where(i0 > 0, m / i0, np.nan)
        else:
            m = hc.window_map(d, *b[DETS[d]], ei, per_i0)
        out.append(m)
    return out


def test_demo_elemental_maps_equal_the_0_3_maps_without_reading_the_cube(qapp, fake_project):
    from aurora.core.grid import to_grid
    (fake_project / "config" / "roi_bins.json").write_text(json.dumps(BINS), encoding="utf-8")
    win = _window(qapp)
    proj = Project.discover(fake_project)
    win.home.set_project(proj)
    mp = win.maps
    hc = Hypercube.load(proj, proj.find(testdata.STACK))
    mp.set_hypercube(hc)
    assert win.jobs.wait_idle(10000)
    bare = Hypercube(name=hc.name, E=hc.E, x=hc.x, y=hc.y, cubes=[np.asarray(c) for c in hc.cubes], i0=hc.i0,
                     tey=hc.tey, valid=hc.valid, calibration=hc.calibration, pitch=hc.pitch)
    lb = [(lab, proj.roi_bins.bounds(lab)) for lab in proj.roi_bins.labels]
    hc.cubes = [CountingCube(c) for c in hc.cubes]
    cases = [(None, 2, True), (None, None, True), (None, 2, False), (1, 2, True)]
    for d, ei, per_i0 in cases:
        for k, b in enumerate(mp.sdd_boxes):
            b.setChecked(d is None or k == d)
        mp.avg.setChecked(ei is None)
        if ei is not None:
            mp.energy.setValue(ei)
        mp.per_i0.setChecked(per_i0)
        assert win.jobs.wait_idle(10000)
        reads0 = sum(c.reads for c in hc.cubes)
        mp.draw_elements()
        assert win.jobs.wait_idle(10000)
        assert sum(c.reads for c in hc.cubes) == reads0                  # the per-bin sums only, never the cube
        ref = _v03_elemental(bare, lb, d, ei, per_i0)
        assert [lab for lab, _g in mp.element_grids] == ["F Kα", "O Kα"]
        for (lab, g), m in zip(mp.element_grids, ref):
            assert np.allclose(g.img, to_grid(hc.x, hc.y, m).img, equal_nan=True), (lab, d, ei, per_i0)
    assert len(mp.el_layout.ci.items) == 2
    win.close()


def test_the_map_cache_keys(qapp):
    win = _window(qapp)
    mp = win.maps
    mp.set_hypercube(synthetic(10, 6, 5))
    n = mp.stats["maps"]
    mp.log.setChecked(True); mp.log.setChecked(False)                   # display only: no new map
    assert mp.stats["maps"] == n
    mp.per_i0.setChecked(False)
    assert mp.stats["maps"] == n + 1
    mp.per_i0.setChecked(True)                                          # back: from the cache
    assert mp.stats["maps"] == n + 1
    mp.energy.setValue(2); mp.energy.setValue(7); mp.energy.setValue(2)
    assert mp.stats["maps"] == n + 3
    win.close()


def test_a_costly_deconvolution_is_a_job_with_the_same_result(qapp, monkeypatch):
    from aurora.app import maps as maps_mod
    from aurora.core.deconv import deconvolve_grid
    win = _window(qapp)
    mp = win.maps
    mp.set_hypercube(synthetic(10, 9, 7))
    raw = np.array(mp.map.image.image, copy=True)
    mp.psf_um.setValue(80)
    monkeypatch.setattr(maps_mod, "DECONV_SYNC_OPS", -1.0)              # every deconvolution is 'costly'
    mp.deconv.setChecked(True)
    assert "deconvolving" in mp.map_hint.full_text()
    assert np.array_equal(mp.map.image.image, raw, equal_nan=True)     # the plain map until the job ends
    assert win.jobs.wait_idle(10000)
    want = deconvolve_grid(raw, 80, float(np.mean(mp.hc.pitch)), "lucy", mp.deconv_iter.value())
    assert np.allclose(mp.map.image.image, want, equal_nan=True) and "deconvolving" not in mp.map_hint.full_text()
    win.close()


def test_richardson_lucy_is_cancellable():
    from aurora.core.deconv import gaussian_psf, richardson_lucy
    img = np.random.default_rng(1).random((20, 20))
    seen = []

    def progress(i, n, msg=""):
        seen.append(i)
        if i == 4:
            raise Cancelled()
    with pytest.raises(Cancelled):
        richardson_lucy(img, gaussian_psf(2.0), 30, progress=progress)
    assert seen == [0, 1, 2, 3, 4]
