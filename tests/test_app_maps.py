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
