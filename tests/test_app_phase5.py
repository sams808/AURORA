"""Phase 5 in the app: I0 explorer, references dialog, zone proposals, stitching, deconvolution, peaks tab."""
import json

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from aurora.core import testdata  # noqa: E402
from aurora.core.hypercube import Hypercube  # noqa: E402
from aurora.core.model import Project  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _edge(E, e0=690.0, width=1.0, white=0.6):
    return 0.2 + 1.0 / (1 + np.exp(-(E - e0) / width)) + white * np.exp(-((E - e0 - 2) / 1.5) ** 2)


def test_phase5_ui(qapp, fake_project, tmp_path, monkeypatch):
    from aurora.app.main import MainWindow
    from aurora.app.dialogs import ReferencesDialog, StitchDialog
    (fake_project / "config" / "roi_bins.json").write_text(json.dumps({
        "_meta": {"gains": {}}, "F Kα": {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77], "line_ev": 676.8}}))
    win = MainWindow()
    proj = Project.discover(fake_project)
    win.home.set_project(proj)
    qapp.processEvents()
    ses = win.session
    # I0 explorer
    ck = win.check
    assert ck.x_scan.count() == 3                      # complete + aborted + test stacks
    ck.x_scan.setCurrentIndex([ck.x_scan.itemData(i) for i in range(ck.x_scan.count())].index(testdata.STACK))
    ck.explore_i0()
    assert "mesh level" in ck.x_note.text() and len(ck.x_plot.plotItem.listDataItems()) == 4
    # references dialog
    (tmp_path / "NaF_F_K.csv").write_text("energy,y\n" + "\n".join(f"{e},{v}" for e, v in zip(np.linspace(680, 730, 51), _edge(np.linspace(680, 730, 51)))))
    d = ReferencesDialog(str(tmp_path))
    assert d.list.count() == 1
    d.list.item(0).setCheckState(Qt.Checked)
    assert [(p.name, lab) for p, lab in d.chosen()] == [("NaF_F_K.csv", "NaF F K")]
    n = len(win.library.add_file(*d.chosen()[0]))
    assert n == 1 and win.library.requests[0].label == "NaF F K"
    # zone proposals from the survey pads (no summary on the fake project)
    rd = win.reduce
    sid = ses.stack_id(proj.find(testdata.STACK)); ses.stack(sid); ses.touch(); ses.select_stack(sid)
    n_prop = rd.propose_zones("pads")
    assert n_prop >= 0 and "propose" in rd.status_label.text()
    # stitching two copies of the stack (second shifted) through the dialog's bake path
    import copy
    b = copy.deepcopy(ses.stack_loaded(sid)); b.x = b.x + 3 * b.pitch[0]; b.name = "tile_b"; b.id = "2025-03-14_tile_b"
    ses._stacks[b.id] = b
    dlg = StitchDialog({sid: ses.stack_loaded(sid), b.id: b})
    for i in range(dlg.list.count()):
        dlg.list.item(i).setCheckState(Qt.Checked)
    dlg.check()
    assert "tiles 1-2" in dlg.info.text()
    dlg.bake()
    assert dlg.result is not None and dlg.result.name.startswith("Stitched_")
    rd.add_virtual_stack(dlg.result)
    assert ses.current_stack == dlg.result.id and rd.scan_list.count() == 2
    z = ses.new_zone("whole"); z.rects.append([dlg.result.x.min(), dlg.result.x.max(), dlg.result.y.min(), dlg.result.y.max()]); ses.touch(); ses.select_zone(z.id)
    qapp.processEvents()
    assert dlg.result.id in z.settings and any(c.sid == dlg.result.id for c in rd._cards)
    # deconvolution on the Maps explore map
    mp = win.maps
    mp.set_hypercube(Hypercube.load(proj, proj.find(testdata.STACK)))
    before = mp.map.image.image.copy()
    mp.psf_um.setValue(300); mp.deconv.setChecked(True)
    after = mp.map.image.image
    assert after.shape == before.shape and np.isfinite(after).all() and not np.allclose(after, before)
    mp.deconv_method.setCurrentIndex(1)
    assert np.isfinite(mp.map.image.image).all()
    # peaks tab on a Fit-ticked trace
    win.library.requests[0].fit = True; win.library.changed.emit()
    fit = win.fit
    fit.tabs.setCurrentIndex(3)
    fit.pk_n.setValue(1); fit.pk_centres.setText("692")
    fit.run_peaks()
    r = fit.peak_result
    assert r is not None and r.converged and fit.pk_table.rowCount() == len(r.params) and abs(r.params["p1_centre"] - 692) < 1.5
    fit.export_peaks(tmp_path / "peaks.csv")
    assert (tmp_path / "peaks.csv").read_text().startswith("# peak decomposition of")
    win.close()
