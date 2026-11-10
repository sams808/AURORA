"""I23: Easy Check = John Bussey's 'Check scans' queue: qc.check_zone's sanity messages, the queue on the demo, the
Easy / Advanced split of the page, 'Save ticked → Compare'."""
import numpy as np
import pytest

from aurora.core.constants import BAD_POINT_FRACTION, FEW_POINTS, I0_VARIES_FACTOR
from aurora.core.flags import CHECK_SOURCE, Flag
from aurora.core.io.config import ScanSettings
from aurora.core.qc import check_zone, worst
from aurora.core.spectra import build_traces
from aurora.core.zones import ZoneSums

E = np.linspace(680.0, 730.0, 51)


def sums(n_pix=4, i0=None, det=None, tey=None) -> ZoneSums:
    rng = np.random.default_rng(1)
    step = 1.0 + 1.0 * (E > 696)
    if det is None:
        det = np.column_stack([rng.poisson(4 * 300 * g * step) for g in (1.0, 0.8, 1.2, 0.6)]).astype(float)
    return ZoneSums(E=E, n_pix=n_pix, i0=np.full(len(E), 2.5 * n_pix) if i0 is None else i0,
                    tey=(0.5 * step * n_pix) if tey is None else tey, det={"F Kα": det}, main="F Kα", edge_ev=696.7)


def levels(msgs, level):
    return [m for lv, m in msgs if lv == level]


def test_check_zone_ports_johns_messages():
    st = ScanSettings(line="F Kα", despike_mode="coherent")
    # a 0-pixel zone
    assert build_traces(sums(n_pix=0), st) is None
    msgs = check_zone(None, n_total=12)
    assert worst(msgs) == "bad" and "contains 0 measurement points" in levels(msgs, "bad")[0]
    # a clean zone: ok only
    ok = check_zone(build_traces(sums(), st), n_total=12)
    assert worst(ok) == "ok" and "region contains 4 of 12 measurement points" in levels(ok, "ok")
    # I0 = 0 at one energy
    i0 = np.full(len(E), 10.0); i0[20] = 0.0
    msgs = check_zone(build_traces(sums(i0=i0), st), n_total=12)
    assert "I0 is zero or missing at 1 energy" in levels(msgs, "warn")
    # a window with zero counts in one detector
    det = sums().det["F Kα"].copy(); det[:, 2] = 0.0
    msgs = check_zone(build_traces(sums(det=det), st), n_total=12)
    bad = levels(msgs, "bad")
    assert len(bad) == 1 and bad[0].startswith("SDD3: signal is zero everywhere - check its window")
    # an unticked detector is not checked
    assert worst(check_zone(build_traces(sums(det=det), ScanSettings(line="F Kα", sdd=[True, True, False, True])))) == "ok"
    # few points, no finite values, missing energies, I0 varying, TEY without values
    assert levels(check_zone(build_traces(sums(n_pix=FEW_POINTS - 1), st)), "warn")[0].endswith(": noisy")
    det2 = sums().det["F Kα"].copy(); det2[:, 0] = np.nan
    assert "SDD1: no finite values (missing files?)" in levels(check_zone(build_traces(sums(det=det2), st)), "bad")
    det3 = sums().det["F Kα"].copy(); det3[[5, 6], :] = np.nan
    assert "2 missing energy points" in levels(check_zone(build_traces(sums(det=det3), st)), "warn")
    i0v = np.linspace(2.0, 2.0 * (I0_VARIES_FACTOR + 1), len(E))
    assert any(m.startswith("I0 varies by a factor 4.0") for m in levels(check_zone(build_traces(sums(i0=i0v), st)), "warn"))
    assert "TEY has no finite values" in levels(check_zone(build_traces(sums(tey=np.full(len(E), np.nan)), st)), "warn")


def test_a_dead_detector_names_itself_and_the_way_out():
    """A ticked SDD with zero counts everywhere makes the row bad (the item spec; John Bussey's GUI only warned): the
    message names the detector, the window and how to leave it out, and says John's GUI warned; unticked, the row is
    clean."""
    st = ScanSettings(line="F Kα", despike_mode="coherent")
    det = sums().det["F Kα"].copy(); det[:, 2] = 0.0
    msgs = check_zone(build_traces(sums(det=det), st), n_total=12)
    assert worst(msgs) == "bad"
    assert levels(msgs, "bad") == ["SDD3: signal is zero everywhere - check its window (F Kα), or leave a dead SDD3 "
                                   "out of the sum on the scan card (Advanced mode); John Bussey's GUI only warned"]
    off = ScanSettings(line="F Kα", despike_mode="coherent", sdd=[True, True, False, True])
    assert worst(check_zone(build_traces(sums(det=det), off), n_total=12)) == "ok"


def test_bad_point_fraction_and_flags():
    st = ScanSettings(line="F Kα", despike_mode="coherent")
    i0 = np.full(len(E), 10.0)
    n_low = int(BAD_POINT_FRACTION * len(E)) + 2
    i0[3:3 + n_low] = 0.3                                   # beam loss: below 20 % of the median
    t = build_traces(sums(i0=i0), st)
    msgs = check_zone(t)
    assert any(m.startswith(f"{round(100 * n_low / len(E))} % of the points flagged bad") for m in levels(msgs, "warn"))
    sat = Flag("SDD saturated: SDD2", "sdd_saturated", CHECK_SOURCE, ("sdd2",))
    inv = Flag("I0 invalid", "i0_invalid", "sgm_report")
    t2 = build_traces(sums(), st)
    msgs = check_zone(t2, flags=[sat, inv])
    assert any("SDD saturated: SDD2 (AURORA check) and in the fluorescence sum" in m for m in levels(msgs, "warn"))
    assert any(m.startswith("I0 invalid (sgm_report)") for m in levels(msgs, "warn"))
    st2 = ScanSettings(line="F Kα", sdd=[True, False, True, True], i0_source="good")
    msgs = check_zone(build_traces(sums(), st2, good_curve=np.full(len(E), 2.5)), flags=[sat, inv])
    assert "SDD saturated: SDD2 (AURORA check): left out of the sum" in levels(msgs, "ok")
    assert "I0 invalid (sgm_report): divided by the good-I0 mean" in levels(msgs, "ok")


# ----------------------------------------------------------------------------- the page on the demo
pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def demo(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    yield win
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


def _queue_demo(win):
    from aurora.app.session import CheckItem
    from aurora.core import testdata
    ses = win.session
    sid = ses.stack_id(ses.project.find(testdata.STACK))
    z = ses.holder_zones().zones[0]
    assert sid in z.settings
    ses.queue_for_check([CheckItem(ses.current_holder, z.id, sid)])
    return z, sid


def _interactive(page):
    from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QLineEdit,
                                   QSlider, QTabBar)
    out = []
    for w in page.findChildren(QAbstractButton) + page.findChildren(QComboBox) + page.findChildren(QLineEdit) + \
            page.findChildren(QAbstractSpinBox) + page.findChildren(QSlider):
        p, inside = w.parentWidget(), False
        while p is not None and p is not page:
            if isinstance(p, (QAbstractItemView, QComboBox, QAbstractSpinBox, QTabBar)):
                inside = True
            p = p.parentWidget()
        if w.isVisible() and not inside and not w.property("aurora_help_button"):
            out.append(w)
    return out


def test_the_queue_on_the_demo(qapp, demo):
    from PySide6.QtCore import Qt
    from demo_window import pump
    from aurora.app import mode
    from aurora.app.widgets import Chip
    win = demo
    ck = win.check
    win.show_perspective("check")
    pump(qapp)
    assert not mode.is_advanced()
    assert ck.queue_state.isVisible() and not ck.save_btn.isEnabled()
    z, sid = _queue_demo(win)
    pump(qapp)
    assert win.jobs.wait_idle(30000)
    pump(qapp)
    rows = ck.queue_rows()
    assert len(rows) == 1 and rows[0]["looks_ok"] and rows[0]["state"] == "ready" and "bad" not in rows[0]["levels"]
    assert rows[0]["points"] == (12, 12) and rows[0]["sample"] == z.label
    cell = ck.queue.cellWidget(0, 4)
    chips = cell.findChildren(Chip)
    assert chips and not [c for c in chips if c.property("chip") == "bad"]
    assert ck.queue.item(0, 5).checkState() == Qt.Checked and not ck.queue_state.isVisible() and ck.save_btn.isEnabled()
    # Easy: at most 10 interactive widgets besides the table; the four Advanced cards hidden
    ws = [w for w in _interactive(ck) if w is not ck.queue]
    assert len(ws) <= 10, [type(w).__name__ for w in ws]
    advanced = (ck.table_card, ck.i0_card, ck.sdd_card, ck.explorer_card)
    assert all(not c.isVisible() for c in advanced) and ck.queue_card.isVisible() and ck.preview_card.isVisible()
    mode.set_advanced(True)
    pump(qapp, 3)
    assert all(c.isVisible() for c in advanced) and ck.every_btn.isVisible() and ck.run_btn.isVisible()
    mode.set_advanced(False)
    pump(qapp, 3)
    assert all(not c.isVisible() for c in advanced)
    # John's five views draw for the selected row
    assert ck.view_combo.count() == 5
    for i in range(5):
        ck.view_combo.setCurrentIndex(i)
        pump(qapp)
        assert ck.qplot.getPlotItem().listDataItems(), ck.view_combo.currentText()
    assert "region contains 12 of 12 measurement points" in ck.msg_label.text()
    # Looks OK = ScanSettings.include, through session.edit; Tick all / Untick all
    descs = []
    orig = win.session.edit

    def spy(desc, holder=None):
        descs.append(desc)
        return orig(desc, holder)
    win.session.edit = spy
    ck.queue.item(0, 5).setCheckState(Qt.Unchecked)
    pump(qapp)
    assert z.settings[sid].include is False and descs[-1].endswith("as not OK") and not ck.save_btn.isEnabled()
    ck.tick_all_btn.click()
    pump(qapp)
    assert z.settings[sid].include is True and descs[-1].startswith("tick all")
    ck.untick_all_btn.click()
    assert z.settings[sid].include is False
    ck.tick_all_btn.click()
    del win.session.edit


def test_save_ticked_to_compare(qapp, demo):
    from demo_window import pump
    win = demo
    ck = win.check
    win.show_perspective("check")
    z, sid = _queue_demo(win)
    pump(qapp)
    assert win.jobs.wait_idle(30000)
    lib = win.library
    before = len(lib.requests)
    asked = []
    ck.ask_overwrite = lambda paths: asked.append(paths) or "yes"
    ck.save_btn.click()
    assert win.jobs.wait_idle(30000)
    pump(qapp)
    folder = win.session.project.root / "processed" / "reduced" / z.label
    names = sorted(p.name for p in folder.iterdir())
    assert names == sorted(f"{z.label}{s}" for s in ("_full_reduced.csv", "_absorption.csv", "_polygon_roi.json",
                                                     "_gui_settings.json"))
    added = lib.requests[before:]
    assert added and {r.label for r in added} == {z.label} and {r.signal for r in added} <= {"sdd-scaled", "sdd_total", "TEY"}
    assert "added to Compare" in ck.queue_note.text() and not asked
    # a second save asks before overwriting; 'no' skips the spectrum
    ck.ask_overwrite = lambda paths: asked.append(paths) or "no"
    ck.save_btn.click()
    assert win.jobs.wait_idle(30000)
    assert len(asked) == 1 and len(asked[0]) == 4 and "nothing to save" in ck.queue_note.text()


def test_a_bad_row_starts_unticked_and_is_not_saved(qapp, demo, monkeypatch):
    from demo_window import pump
    from aurora.app import check
    win = demo
    ck = win.check
    monkeypatch.setattr(check.qc, "check_zone", lambda traces, stack=None, flags=(), n_total=None:
                        [("bad", "SDD1: no finite values (missing files?)")])
    z, sid = _queue_demo(win)
    pump(qapp)
    assert win.jobs.wait_idle(30000)
    rows = ck.queue_rows()
    assert rows[0]["levels"] == ["bad"] and not rows[0]["looks_ok"] and z.settings[sid].include is False
    ck.set_all_ok(True)
    ck.ask_overwrite = lambda paths: "yes"
    ck.save_ticked()
    assert win.jobs.wait_idle(30000)
    assert "has a problem and was not saved" in ck.queue_note.text()
    assert not (win.session.project.root / "processed" / "reduced" / z.label).exists()
    # the zone leaves the queue with the zone
    win.session.delete_zone(z.id)
    pump(qapp)
    assert ck.queue_rows() == [] and win.session.check_queue == []


def test_a_stack_that_cannot_be_read_gives_an_error_row(qapp, fake_project, error_dialogs):
    from aurora.app.main import MainWindow
    from aurora.app.session import CheckItem
    from aurora.app.widgets import Chip
    from aurora.core import testdata
    from aurora.core.model import Project
    win = MainWindow()
    win.home.set_project(Project.discover(fake_project))
    qapp.processEvents()
    assert win.jobs.wait_idle(30000)                      # Reduce's own read of the first stack must not land after the pop
    ses = win.session
    sid = ses.stack_id(ses.project.find(testdata.STACK))
    sd = ses.stack(sid)
    z = ses.new_zone("z")
    with ses.edit("draw"):
        z.rects.append(list(sd.bbox))
    assert sid in z.settings
    ses._stacks.pop(sid)                                  # forgotten, and now unreadable

    def broken(s, progress=None):
        raise OSError("the share is gone")
    ses.stack = broken
    ses.queue_for_check([CheckItem(ses.current_holder, z.id, sid)])
    assert win.jobs.wait_idle(30000)
    qapp.processEvents()
    rows = win.check.queue_rows()
    assert rows[0]["state"] == "error" and not win.check.save_btn.isEnabled()
    chips = win.check.queue.cellWidget(0, 4).findChildren(Chip)
    assert [c.property("chip") for c in chips] == ["bad"] and "the share is gone" in chips[0].toolTip()
    assert error_dialogs                                   # the failed read was reported once, calmly
    win.session._save_timer.stop()
    win.close()


def test_the_queue_survives_reload_and_a_restart(qapp, fake_project, monkeypatch):
    """The queue is saved with the session: File > Reload keeps it and a restart brings it back, with the first
    verdict applied once (a bad row ticked again by hand stays ticked)."""
    from aurora.app import check
    from aurora.app.main import MainWindow
    from aurora.core.model import Project
    from wp3_helpers import close, open_window, pump
    monkeypatch.setattr(check.qc, "check_zone", lambda traces, stack=None, flags=(), n_total=None:
                        [("bad", "SDD1: no finite values (missing files?)")])
    win, sid = open_window(qapp, Project.discover(fake_project))
    try:
        rd, ses, ck = win.reduce, win.session, win.check
        x0, x1, y0, y1 = ses.stack_loaded(sid).bbox
        rd.map.rect_drawn.emit(x0, x1, y0, y1)
        pump(qapp)
        z = ses.zone()
        assert rd.add_to_check() == 1
        win.show_perspective("check")
        assert win.jobs.wait_idle(30000)
        pump(qapp)
        assert len(ck.queue_rows()) == 1 and z.settings[sid].include is False     # the first verdict: unticked
        ck.set_all_ok(True)                                                       # ticked again by hand
        win.home.set_project(Project.discover(fake_project))                      # File > Reload
        pump(qapp)
        assert win.jobs.wait_idle(30000)
        pump(qapp)
        assert len(ses.check_queue) == 1 and len(ck.queue_rows()) == 1 and not ck.queue_state.isVisible()
        assert ses.zone(z.id).settings[sid].include is True
    finally:
        close(win)
    win2 = MainWindow()                                                           # a restart
    win2.resize(1500, 940)
    win2.show()
    try:
        win2.home.set_project(Project.discover(fake_project))
        pump(qapp)
        win2.show_perspective("check")
        assert win2.jobs.wait_idle(30000)
        pump(qapp)
        ses2 = win2.session
        assert [(it.zone_id, it.sid) for it in ses2.check_queue] == [(z.id, sid)]
        assert len(win2.check.queue_rows()) == 1 and not win2.check.queue_state.isVisible()
        assert ses2.zone(z.id).settings[sid].include is True                     # not unticked a second time
    finally:
        close(win2)


def test_looking_at_an_empty_queue_writes_nothing(qapp, fake_project):
    """The saved queue must not make a look at Check an edit: open, show Check, close: no session.json (I15)."""
    from aurora.app.main import MainWindow
    from aurora.core.model import Project
    from wp3_helpers import pump
    win = MainWindow()
    win.show()
    try:
        win.home.set_project(Project.discover(fake_project))
        pump(qapp)
        win.show_perspective("check")
        assert win.jobs.wait_idle(30000)
        pump(qapp)
        assert win.check.queue_state.isVisible() and not win.session.dirty
    finally:
        win.jobs.wait_idle(30000)
        win.close()
    assert not (fake_project / ".aurora" / "session.json").exists()
