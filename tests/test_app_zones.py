"""Survey and Reduce perspectives, offscreen: zone editing, settings, preview, session persistence, import/export."""
import json

import pytest

pytest.importorskip("pyqtgraph")

from aurora.core import testdata  # noqa: E402
from aurora.core.io.config import ZonesFile  # noqa: E402
from aurora.core.model import Project  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _window(qapp, project):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.home.set_project(project)
    qapp.processEvents()
    return win


def test_survey_and_reduce_on_fake_project(qapp, fake_project):
    proj = Project.discover(fake_project)
    win = _window(qapp, proj)
    ses = win.session
    assert list(ses.holder_keys) == ["N5"] and ses.current_holder == "N5"
    # survey perspective
    sv = win.survey
    assert sv.holder_combo.count() == 1 and sv.channel.count() == 3                # total, TEY, I0 (no fixed bins)
    assert sv.map.image.image is not None and sv.map.image.image.shape == (6, 8)
    assert len(sv.map._overlays) >= 2                                              # footprint rect + label
    # reduce: load the complete stack synchronously, as the worker would
    rd = win.reduce
    assert rd.scan_list.count() == 1
    sid = rd.scan_list.item(0).data(0x0100)
    ses.stack(sid)
    ses.touch()
    ses.select_stack(sid)
    qapp.processEvents()
    assert rd.map.image.image is not None and rd.map_line.currentText() == "total XRF"
    # draw a zone with rect+ over the whole footprint
    sd = ses.stack_loaded(sid)
    x0, x1, y0, y1 = sd.bbox
    rd.map.rect_drawn.emit(x0, x1, y0, y1)
    qapp.processEvents()
    z = ses.zone()
    assert z is not None and z.id == 1 and len(z.rects) == 1
    assert z.members[sid] == list(range(12)) and z.settings[sid].n == 12 and z.settings[sid].line == "total XRF"
    assert rd.zone_list.count() == 1 and len(rd._cards) == 1
    assert len(rd.preview._traces) == 1 and rd.preview._traces[0][1].n_pix == 12
    # erase one pixel with the brush, then an erase rectangle
    rd.map.set_tool("brush-")
    rd.map.brush_at.emit(float(sd.x[0]), float(sd.y[0]), False)
    qapp.processEvents()
    assert len(z.erase) == 1 and len(z.members[sid]) == 11
    rd.map.set_tool("rect-")
    rd.map.rect_drawn.emit(float(sd.x[1]), float(sd.x[1]), float(sd.y[1]), float(sd.y[1]))
    qapp.processEvents()
    assert len(z.erects) == 1 and len(z.members[sid]) == 10
    # settings card edits propagate
    card = rd._cards[0]
    card.sdd[2].setChecked(False)
    card.fn.setCurrentIndex(0)                                                     # raw
    qapp.processEvents()
    assert z.settings[sid].sdd == [True, True, False, True] and z.settings[sid].fluo_norm == "raw"
    assert rd.preview._traces[0][1].settings.fluo_norm == "raw"
    # zone card
    rd.z_label.setEditText("13 alpha_beta"); rd.z_label.lineEdit().editingFinished.emit()
    assert z.label == "13 alpha_beta" and rd.zone_list.item(0).text().startswith("1  13 alpha_beta")
    # survey table follows
    assert win.survey.zone_table.rowCount() == 1 and win.survey.zone_table.item(0, 1).text() == "13 alpha_beta"
    # persistence: save now, reload the project, zones come back
    ses.save()
    assert ses.session_file.exists()
    ses.set_project(Project.discover(fake_project))
    z2 = ses.holder_zones(create=False).zones[0]
    assert z2.label == "13 alpha_beta" and z2.settings[sid].sdd == [True, True, False, True] and len(z2.erects) == 1
    # export / import round trip through the tool's format
    out = fake_project / "zones_all.json"
    ses.export_zones(out)
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["holders"]["N5"]["zones"][0]["label"] == "13 alpha_beta" and d["version"] == 1
    ses.delete_zone(1)
    assert not ses.holder_zones(create=False).zones
    assert ses.import_zones(out) == 1
    assert ses.zone(1).label == "13 alpha_beta"
    win.close()


def test_real_zones_in_the_app(qapp, real_project, monkeypatch):
    zones_file = real_project / "zones_html" / "zones_all.json"
    if not zones_file.exists():
        pytest.skip("zones_all.json not exported yet")
    from aurora.app.session import Session
    monkeypatch.setattr(Session, "save", lambda self: None)        # never write into the real beamtime folder
    proj = Project.discover(real_project / "raw" / "2026-11-05")
    win = _window(qapp, proj)
    ses = win.session
    ses.zones.holders.clear()
    ses.import_zones(zones_file)
    ses.set_holder("N5")
    qapp.processEvents()
    assert win.survey.map.image.image is not None and "F Kα" in [win.survey.channel.itemText(i) for i in range(win.survey.channel.count())]
    assert win.survey.zone_table.rowCount() == 38
    sid = "2026-11-05_092758_alpha_beta_f_k_stack"
    ses.stack(sid)
    ses.touch()
    ses.select_stack(sid)
    z = next(zz for zz in ses.holder_zones().zones if zz.label == "13 alpha_beta")
    ses.select_zone(z.id)
    qapp.processEvents()
    fz = next(zz for zz in ZonesFile.load(zones_file).holders["N5"].zones if zz.label == "13 alpha_beta")
    assert fz.id == z.id and z.members[sid] == fz.members[sid] and len(fz.members[sid]) == fz.settings[sid].n
    assert len(win.reduce._cards) == 1 and win.reduce._cards[0].sid == sid
    t = win.reduce.preview._traces[0][1]
    assert t.n_pix == len(fz.members[sid]) and t.line == "F Kα" and abs(float(t.sum[t.E >= t.post[0]].mean()) - 1.0) < 1e-6
    ses._save_timer.stop()
    ses.project = None                    # a later close (session teardown) has nothing to save
    win.close(); win.deleteLater(); qapp.processEvents()


def test_load_all_scans(qapp, fake_project):
    from PySide6.QtCore import QEventLoop, QTimer
    from aurora.app.main import MainWindow
    proj = Project.discover(fake_project)
    win = _window(qapp, proj)
    rd = win.reduce
    # the list auto-selection started a worker for the first scan; wait for it, then "Load all" finds nothing left
    if rd._worker is not None:
        rd._worker.wait(30000); qapp.processEvents()
    sid = win.session.stack_id(proj.find(testdata.STACK))
    if win.session.stack_loaded(sid) is None:
        win.session.stack(sid)
    rd.load_all()
    assert "every scan" in rd.status_label.text()
    win.session._stacks.clear()
    loop = QEventLoop(); QTimer.singleShot(15000, loop.quit)
    rd.status_label.setText("")
    rd.load_all()
    while rd._worker is not None and rd._worker.isRunning():
        qapp.processEvents()
    qapp.processEvents()
    assert win.session.stack_loaded(sid) is not None and "1 of 1 scans read" in rd.status_label.text()
    win.close()
