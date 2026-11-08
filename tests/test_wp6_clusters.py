"""I38: the cluster merger. Tick clusters, see their merged mean and sum, make a zone of them, send the merged mean to
Compare, export the spectra and the map; categorical colours that match the list (the legend); a reproducible seed."""
import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from aurora.core import testdata  # noqa: E402
from aurora.core.hypercube import ClusterResult, Hypercube, merged_spectra, pca_kmeans  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from aurora.core.zones import members  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


@pytest.fixture
def maps(qapp, fake_project):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.resize(1300, 820)
    win.show()
    proj = Project.discover(fake_project)
    win.home.set_project(proj)
    win.show_perspective("maps")
    hc = Hypercube.load(proj, proj.find(testdata.STACK))
    win.maps.set_hypercube(hc)
    win.maps.tabs.setCurrentIndex(3)
    win.jobs.wait_idle(10000)
    pump(qapp)
    yield win, win.maps, hc
    win.session._save_timer.stop()
    win.close()


def _result(hc, labels) -> tuple[ClusterResult, np.ndarray]:
    """A ClusterResult with chosen labels on the cube's pixel matrix (centres = the mean of each label's rows)."""
    M = hc.pixel_matrix(None, 60, 75)
    labels = np.asarray(labels)
    k = int(labels.max()) + 1
    centres = np.array([M[labels == c].mean(axis=0) for c in range(k)])
    return ClusterResult(labels=labels, centres=centres, scores=np.zeros((hc.n_pix, 2)), explained=np.array([0.6, 0.3]),
                         E=hc.E[hc.valid], seed=0), M


def _tick(mp, ticked):
    for i in range(mp.cluster_list.count()):
        it = mp.cluster_list.item(i)
        it.setCheckState(Qt.Checked if int(it.data(Qt.UserRole)) in ticked else Qt.Unchecked)


def test_merged_spectra_are_the_mean_and_sum_of_the_ticked_rows():
    rng = np.random.default_rng(2)
    M = rng.random((50, 7))
    labels = rng.integers(-1, 4, 50)                     # -1: an invalid pixel, never merged
    mean, total, n = merged_spectra(M, labels, {0, 2})
    sel = np.isin(labels, [0, 2])
    assert n == sel.sum() and np.allclose(mean, M[sel].mean(axis=0)) and np.allclose(total, M[sel].sum(axis=0))
    mean, total, n = merged_spectra(M, labels, set())
    assert n == 0 and np.isnan(mean).all()


def test_the_same_seed_gives_the_same_labels(maps, qapp):
    win, mp, hc = maps
    M = hc.pixel_matrix(None, 60, 75)
    a = pca_kmeans(M, hc.E, 3, 3, seed=7)
    b = pca_kmeans(M, hc.E, 3, 3, seed=7)
    assert np.array_equal(a.labels, b.labels) and a.seed == 7
    # through the perspective: the seed is shown and set
    mp.n_k.setValue(3); mp.seed.setValue(11)
    mp.run_clusters(); assert win.jobs.wait_idle(10000)
    first = mp._cluster.labels.copy()
    assert "seed 11" in mp.cluster_info.text()
    mp.run_clusters(); assert win.jobs.wait_idle(10000)
    assert np.array_equal(mp._cluster.labels, first) and mp._cluster.seed == 11


def test_ticking_merges_live_and_dims_the_unticked_clusters(maps, qapp):
    from aurora.app.theme import TOKENS, categorical, composite, with_alpha
    win, mp, hc = maps
    cr, M = _result(hc, np.arange(hc.n_pix) % 3)
    mp.show_clusters(cr, M, {"scan": hc.name})
    assert mp.cluster_list.count() == 3 and mp.ticked_clusters() == [0, 1, 2]
    assert mp.cluster_list.item(1).text() == "cluster 2 · 4 px"
    _tick(mp, {0, 2})
    mean, total, n = mp.merged()
    sel = np.isin(cr.labels, [0, 2])
    assert n == 8 and np.allclose(mean, M[sel].mean(axis=0)) and np.allclose(total, M[sel].sum(axis=0))
    names = [it.name() for it in mp.cluster_plot.plotItem.listDataItems() if it.name()]
    assert names == ["merged 1+3 (8 px)"]
    assert mp.merged_plot.plotItem.listDataItems()[0].name() == "sum of clusters 1+3 (8 px)"
    lut = mp.cluster_map.image.lut
    cols = categorical(3)
    assert QColor(*lut[0]).name() == QColor(cols[0]).name()
    assert QColor(*lut[1]).name() == QColor(composite(with_alpha(cols[1], 0.3), TOKENS["map_bg"])).name()
    # nothing ticked: the actions say why
    _tick(mp, set())
    assert not mp.zone_btn.isEnabled() and "tick at least one cluster" in mp.send_btn.toolTip()


def test_a_zone_from_clusters_has_exactly_their_pixels(maps, qapp):
    from aurora.core.io.config import Zone
    win, mp, hc = maps
    ses = win.session
    labels = np.array([0, 1, 2, 2, 1, 0, 0, 2, 1, 1, 0, 2])
    cr, M = _result(hc, labels)
    mp.show_clusters(cr, M, {"scan": hc.name})
    _tick(mp, {0, 2})
    n_zones = len(ses.holder_zones().zones)
    z = mp.make_zone_from_clusters()
    assert z is not None and len(ses.holder_zones().zones) == n_zones + 1 and ses.current_zone == z.id
    assert np.array_equal(members(z, hc.x, hc.y, hc.pitch), np.where(np.isin(labels, [0, 2]))[0])
    assert z.label.startswith("clusters 1+3 · N5 09:27") and not z.rects and len(z.paint) == 8
    # zones_all.json keeps its format: the same keys as any zone, painted squares of four numbers
    assert set(z.to_json()) == set(Zone(id=99, label="x").to_json())
    assert all(len(s) == 4 for s in z.paint)
    # the zone attaches to the stack like a drawn one (Reduce sees the same pixels)
    sid = ses.stack_id(ses.project.find(testdata.STACK))
    ses.stack(sid); ses.touch()
    assert sorted(z.members[sid]) == np.where(np.isin(labels, [0, 2]))[0].tolist()


def test_send_to_compare_adds_one_row_with_provenance(maps, qapp):
    win, mp, hc = maps
    mp.n_k.setValue(3)
    mp.run_clusters(); assert win.jobs.wait_idle(10000)
    _tick(mp, {0, 2})
    lib = win.library
    n0 = len(lib.requests)
    assert mp.send_clusters_to_compare() == 1
    assert len(lib.requests) == n0 + 1
    r = lib.requests[-1]
    assert r.label == "clusters 1+3 · N5 09:27" and r.signal == "fluo"
    spec = lib.spectrum_of(r)
    prov = spec.meta["provenance"]
    assert prov["scan"] == hc.name and prov["clusters"] == [1, 3] and prov["k"] == 3 and prov["seed"] == 0
    assert prov["sdds"] == ["sdd1", "sdd2", "sdd3", "sdd4"] and set(prov["windows"]) == {"sdd1", "sdd2", "sdd3", "sdd4"}
    assert prov["pca_components"] == mp.n_pc.value()
    assert np.allclose(spec.y, mp.merged()[0]) and np.allclose(spec.E, hc.E[hc.valid])


def test_twelve_clusters_keep_their_own_colours(maps, qapp):
    win, mp, hc = maps
    assert hc.n_pix == 12
    cr, M = _result(hc, np.arange(12))
    mp.show_clusters(cr, M, {"scan": hc.name})
    pump(qapp)
    img = mp.cluster_map.image
    img.render()
    q = img.qimage
    g = mp._label_grid(cr.labels)
    curves = [it for it in mp.cluster_plot.plotItem.listDataItems() if not it.name()]
    seen = set()
    for i in range(12):
        k = int(cr.labels[i])
        got = q.pixelColor(int(g.ix[i]), int(g.iy[i])).name()
        legend = QColor(mp.cluster_list.item(k).data(Qt.UserRole + 1)).name()
        pen = curves[k].opts["pen"].color().name()
        assert got == legend == pen, (k, got, legend, pen)
        seen.add(got)
    assert len(seen) == 12                                    # no two clusters share a colour (0.3 had 7 for 20)
    # twenty clusters still have twenty colours
    from aurora.app.theme import categorical
    assert len(set(categorical(20))) == 20


def test_export_writes_the_csv_and_the_map(maps, qapp, tmp_path):
    win, mp, hc = maps
    cr, M = _result(hc, np.arange(hc.n_pix) % 2)
    mp.show_clusters(cr, M, {"scan": hc.name, "window": "custom", "windows": {"sdd1": [60, 75]}, "sdds": ["sdd1"],
                             "pca_components": 2, "k": 2, "per_i0": True})
    _tick(mp, {1})
    paths = mp.export_clusters(tmp_path / "c.csv")
    assert [p.name for p in paths] == ["c.csv", "c_map.png"] and paths[1].stat().st_size > 1000
    lines = [ln for ln in paths[0].read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    assert lines[0] == "energy_eV,cluster_1_mean,cluster_2_mean,merged_mean"
    rows = np.array([[float(v) for v in ln.split(",")] for ln in lines[1:]])
    assert rows.shape == (len(cr.E), 4)
    assert np.allclose(rows[:, 0], cr.E) and np.allclose(rows[:, 1:3], cr.centres.T, rtol=1e-6)
    assert np.allclose(rows[:, 3], M[cr.labels == 1].mean(axis=0), rtol=1e-6)
    head = paths[0].read_text(encoding="utf-8").splitlines()[:3]
    assert head[0].startswith("# AURORA Maps clusters of") and "sdd1 60-74" in head[1] and "clusters 2 (6 px)" in head[2]
