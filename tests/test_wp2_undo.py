"""I17: undo and redo for every zone, settings and good-I0 edit. One snapshot step per session.edit, repeats within
1.5 s merged (a brush stroke is one step), Propose / import / restore one step each, loads never recorded, Edit >
Undo / Redo / History, and an autosave after an undo that writes the state from before the undone edit."""
import json

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from aurora.core import testdata  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from wp2_helpers import draw_zone, pump, session, sid_of, wait, window  # noqa: E402

SID_A = sid_of(testdata.STACK)


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _zones_json(ses, key="holderA"):
    hz = ses.zones.holders.get(key)
    return [z.to_json() for z in hz.zones] if hz else []


def test_new_zone_and_rectangle_then_ctrl_z_and_ctrl_y(qapp, fake_project):
    win = window(qapp, Project.discover(fake_project))
    ses, rd = win.session, win.reduce
    win.activateWindow(); pump(qapp)
    sd = ses.stack(SID_A); ses.touch(); ses.select_stack(SID_A); pump(qapp)
    x0, x1, y0, y1 = sd.bbox
    rd.map.set_tool("rect+")
    rd.map.rect_drawn.emit(x0, (x0 + x1) / 2, y0, y1); pump(qapp)      # a new zone plus one rectangle: one edit
    z = ses.zone()
    done = _zones_json(ses)
    assert z is not None and len(z.rects) == 1 and z.settings and ses.undo_stack.count() == 1
    ui = ses._ui
    assert ui.undo_action.text() == "Undo add a rectangle to a new zone" and ui.undo_action.isEnabled()
    assert not ui.redo_action.isEnabled() and "nothing to redo" in ui.redo_action.toolTip()
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); pump(qapp)
    assert _zones_json(ses) == [] and ses.current_zone is None
    assert ui.redo_action.text() == "Redo add a rectangle to a new zone"
    QTest.keyClick(win, Qt.Key_Y, Qt.ControlModifier); pump(qapp)
    assert _zones_json(ses) == done and ses.current_zone == z.id
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); pump(qapp)
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier); pump(qapp)     # the other redo key
    assert _zones_json(ses) == done
    assert "redone: add a rectangle to a new zone" in win.statusBar().currentMessage()
    ses._save_timer.stop()
    win.close()


def test_new_sample_then_a_rectangle_undo_in_one_step(qapp, fake_project):
    win = window(qapp, Project.discover(fake_project))
    ses, rd = win.session, win.reduce
    sd = ses.stack(SID_A); ses.touch(); ses.select_stack(SID_A); pump(qapp)
    t = [0.0]
    ses.undo_clock = lambda: t[0]
    rd.new_zone_btn.click(); pump(qapp)                      # 'New sample': an empty zone
    t[0] += 20.0                                             # drawing it takes a while
    x0, x1, y0, y1 = sd.bbox
    rd.map.set_tool("rect+")
    rd.map.rect_drawn.emit(x0, x1, y0, y1); pump(qapp)
    z = ses.zone()
    assert z is not None and len(z.rects) == 1 and ses.undo_stack.count() == 1
    assert ses._ui.undo_action.text() == "Undo add a rectangle to a new zone"
    ses.undo()
    assert ses.holder_zones().zones == []
    ses.redo()
    assert ses.zone(z.id).rects == z.rects
    t[0] += 20.0
    rd.map.rect_drawn.emit(x0, (x0 + x1) / 2, y0, y1); pump(qapp)     # a second rectangle: its own step
    assert ses.undo_stack.count() == 2
    ses._save_timer.stop()
    win.close()


def test_a_brush_drag_is_one_step(qapp, fake_project):
    from aurora.app import mode
    mode.set_advanced(True)
    try:
        win = window(qapp, Project.discover(fake_project))
        ses, rd = win.session, win.reduce
        sd = ses.stack(SID_A); ses.touch(); ses.select_stack(SID_A); pump(qapp)
        rd.map.set_tool("brush+")
        t = [10.0]
        ses.undo_clock = lambda: t[0]
        n0 = ses.undo_stack.count()
        for i in range(5):                                   # the first event starts a new zone
            rd.map.brush_at.emit(float(sd.x[i]), float(sd.y[i]), True)
            pump(qapp)
            t[0] += 0.1
        assert ses.undo_stack.count() == n0 + 1
        z = ses.zone()
        p1 = len(z.paint)
        assert p1 >= 5
        n1 = ses.undo_stack.count()
        t[0] += 3.0                                          # a pause: the next stroke is its own step
        for i in range(5, 10):                               # a second stroke on the same zone
            rd.map.brush_at.emit(float(sd.x[i]), float(sd.y[i]), True)
            pump(qapp)
        assert ses.undo_stack.count() == n1 + 1
        assert len(ses.zone(z.id).paint) > p1
        ses.undo()
        assert len(ses.zone(z.id).paint) == p1
        ses.undo()
        assert ses.holder_zones().zones == []
        ses._save_timer.stop()
        win.close()
    finally:
        mode.set_advanced(False)


def test_repeats_merge_only_within_the_window(qapp, fake_project):
    ses = session(fake_project)
    t = [100.0]
    ses.undo_clock = lambda: t[0]
    z = draw_zone(ses)
    n = ses.undo_stack.count()
    for i in range(3):
        with ses.edit(f"rename zone {z.id}"):
            z.label = f"v{i}"
        t[0] += 1.0
    assert ses.undo_stack.count() == n + 1                     # 0, 1, 2 s apart: one step
    t[0] += 2.0
    with ses.edit(f"rename zone {z.id}"):
        z.label = "later"
    assert ses.undo_stack.count() == n + 2                     # 2 s after the previous one: a new step
    with ses.edit("an edit that changes nothing"):
        pass
    assert ses.undo_stack.count() == n + 2
    ses.undo()
    assert ses.zone(z.id).label == "v2"
    ses.undo()
    assert ses.zone(z.id).label == "zone 1"
    ses._save_timer.stop()


def test_a_proposal_of_two_zones_is_one_step(qapp, fake_project):
    from aurora.app import mode
    summary = {"day": testdata.DAY, "holders": [{"holder": "holderA", "survey": testdata.SURVEY, "scans": [
        {"scan": testdata.STACK, "zones": [{"name": "1 alpha", "x": -0.375, "y": 5.125, "px": 4},
                                           {"name": "2 beta", "x": 0.125, "y": 5.375, "px": 4}]}]}]}
    d = fake_project / "processed" / testdata.DAY
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    mode.set_advanced(True)
    try:
        win = window(qapp, Project.discover(fake_project))
        ses = win.session
        ses.stack(SID_A); ses.touch(); pump(qapp)
        draw_zone(ses, "mine", [0.2, 0.3, 5.4, 5.6])
        n = ses.undo_stack.count()
        assert win.reduce.propose_zones("summary") == 2
        assert ses.undo_stack.count() == n + 1 and ses.undo_stack.undoText() == "propose 2 zones"
        assert [z.label for z in ses.holder_zones().zones] == ["mine", "1 alpha", "2 beta"]
        ses.undo()
        assert [z.label for z in ses.holder_zones().zones] == ["mine"]
        ses._save_timer.stop()
        win.close()
    finally:
        mode.set_advanced(False)


def test_after_an_undo_the_autosave_writes_the_state_before_the_edit(qapp, fake_project):
    ses = session(fake_project)
    a = draw_zone(ses, "A")
    ses.flush()
    before = json.loads(json.dumps(ses.zones.holders["holderA"].to_json()))
    good_before = dict(ses.zones.good_i0)
    with ses.edit(f"change the settings of 09:27 in zone {a.id}"):
        a.settings[SID_A].fluo_norm = "max"
        a.settings[SID_A].sdd = [True, False, True, True]
        ses.zones.good_i0[SID_A] = True
    ses.flush()
    assert json.loads(ses.session_file.read_text(encoding="utf-8"))["holders"]["holderA"] != before
    ses.undo()
    assert ses._save_timer.isActive()
    wait(qapp, 1200)                                        # the autosave
    on_disk = json.loads(ses.session_file.read_text(encoding="utf-8"))
    assert on_disk["holders"]["holderA"] == before and on_disk["goodI0"] == good_before
    ses._save_timer.stop()


def test_loading_a_stack_adds_no_step(qapp, fake_project):
    ses = session(fake_project)
    draw_zone(ses)
    n = ses.undo_stack.count()
    ses.stack(SID_A)
    ses.touch()
    ses.select_stack(SID_A)
    assert ses.undo_stack.count() == n
    ses._save_timer.stop()


def test_delete_needs_no_confirmation_and_undoes(qapp, fake_project, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: pytest.fail("asked to confirm")))
    win = window(qapp, Project.discover(fake_project))
    ses = win.session
    for _ in range(3):
        draw_zone(ses)
    ses.select_zone(3); pump(qapp)
    win.reduce.del_zone_btn.click(); pump(qapp)
    assert [z.id for z in ses.holder_zones().zones] == [1, 2]
    from aurora.app.shortcuts import native_text
    assert win.statusBar().currentMessage() == native_text("zone 3 deleted · Ctrl+Z to undo")
    ses.undo()
    assert [z.id for z in ses.holder_zones().zones] == [1, 2, 3] and ses.current_zone == 3
    ses._save_timer.stop()
    win.close()


def test_an_import_is_one_step_and_a_project_change_clears_the_history(qapp, fake_project, tmp_path):
    ses = session(fake_project)
    draw_zone(ses, "A")
    out = tmp_path / "zones_all.json"
    ses.export_zones(out)
    with ses.edit("delete zone 1"):
        ses.holder_zones().zones.clear()
    n = ses.undo_stack.count()
    assert ses.import_zones(out) == 1 and ses.undo_stack.count() == n + 1
    ses.set_project(Project.discover(fake_project))            # Reload: the history stays
    assert ses.undo_stack.count() == n + 1
    other = testdata.make_fake_project(tmp_path / "other")
    ses.set_project(Project.discover(other))                   # another project: a fresh history
    assert ses.undo_stack.count() == 0
    ses._save_timer.stop()


def test_numpy_values_in_an_edit_are_recorded_and_saved(qapp, fake_project, tmp_path):
    import numpy as np
    ses = session(fake_project)
    z = draw_zone(ses)
    with ses.edit(f"paint pixels in zone {z.id}"):
        z.paint.append([np.float32(-0.5), np.float32(5.0), np.float64(0.25), np.float64(0.25)])
        z.comment = "np"
    assert ses.save()
    saved = json.loads(ses.session_file.read_text(encoding="utf-8"))["holders"]["holderA"]["zones"][0]
    assert saved["paint"] == [[-0.5, 5.0, 0.25, 0.25]]
    ses.export_zones(tmp_path / "zones_all.json")
    ses.undo()
    assert ses.zone(z.id).paint == [] and ses.zone(z.id).comment == ""
    ses._save_timer.stop()


def test_good_i0_edits_undo(qapp, fake_project):
    ses = session(fake_project)
    with ses.edit(f"mark {testdata.STACK} as good I0"):
        ses.zones.good_i0[SID_A] = True
    ses.undo()
    assert ses.zones.good_i0 == {}
    ses.redo()
    assert ses.zones.good_i0 == {SID_A: True}
    ses._save_timer.stop()


def test_history_is_capped_and_shown_in_advanced(qapp, fake_project):
    from aurora.app import mode
    from aurora.app.undo import UNDO_LIMIT
    win = window(qapp, Project.discover(fake_project))
    ses = win.session
    ui = ses._ui
    assert ui.history_action in win.menus["edit"].actions() and not ui.history_action.isVisible()
    edit_texts = [a.text() for a in win.menus["edit"].actions() if a.isVisible()]
    assert edit_texts[:2] == ["Undo", "Redo"]
    z = draw_zone(ses)
    for i in range(UNDO_LIMIT + 20):
        with ses.edit(f"edit {i} of zone {z.id}"):
            z.comment = str(i)
    assert ses.undo_stack.count() == UNDO_LIMIT
    mode.set_advanced(True)
    try:
        assert ui.history_action.isVisible()
        ui.history_action.trigger(); pump(qapp)
        dlg = ui._history
        assert dlg.isVisible() and dlg.view.stack() is ses.undo_stack
        from test_a11y import check_root
        assert check_root("history", dlg) == []
        dlg.view.setCurrentIndex(dlg.view.model().index(UNDO_LIMIT - 5, 0))      # click a line: back to it
        pump(qapp)
        assert ses.undo_stack.index() == UNDO_LIMIT - 5 and ses.zone(z.id).comment == str(UNDO_LIMIT + 14)
        dlg.close()
    finally:
        mode.set_advanced(False)
        ses._save_timer.stop()
        win.close()


def test_shortcuts_are_listed(qapp, fake_project):
    from aurora.app.diagnostics import shortcut_rows
    win = window(qapp, Project.discover(fake_project))
    from PySide6.QtGui import QKeySequence
    native = {k: QKeySequence(k).toString(QKeySequence.NativeText) for k in ("Ctrl+Z", "Ctrl+Y", "Ctrl+Shift+Z")}
    rows = {(sec, text): keys for sec, text, keys, _a in shortcut_rows(win)}      # native text: macOS lists ⌘Z
    assert rows[("Edit", "Undo")] == native["Ctrl+Z"] and native["Ctrl+Y"] in rows[("Edit", "Redo")] and \
        native["Ctrl+Shift+Z"] in rows[("Edit", "Redo")]
    win.close()
