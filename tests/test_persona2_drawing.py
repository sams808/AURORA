"""Second persona round (a geology student and a glass chemist, neither an X-ray absorption specialist), drawing on the
Survey and in Reduce on the demo beamtime in Easy mode at 1200 x 760, with real QTest drags:

- S-01: a grain drawn beside the selected sample starts a new sample (touching is not adding); the Survey has a New
  sample button; Esc and a click on the bare map in Move deselect; a stroke that grew a sample says how to start the
  next one;
- M09: in Move neither Draw nor Erase is lit;
- S-08: a click with Draw makes a one-pixel sample that is drawn and named in the status line;
- M10, S-13: Easy hints say sample and pixels; the table says 'sample 1'; the save does not overwrite the status line;
- M11: a zone is drawn as the pixels it holds: an erased part is not filled;
- S-20: the Survey's erase hint says what the stack keeps;
- M07, S-04: a footprint label stays inside its footprint (shortened to its time when narrow);
- M08, S-14, S-19: the sample sheet shows the Notes; a click on a sheet row names the next sample (and its group);
  Rename offers the sheet's names and sets the group;
- S-12, M13: the outlined-samples card keeps four rows;
- S-07, M17: Reduce's stack map colours from 0 in Easy;
- M01, S-04: Reduce step 1 shows the sample part of the name first, on its own line.
"""
from __future__ import annotations

import contextlib
import io

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=6):
    for _ in range(n):
        qapp.processEvents()


@pytest.fixture
def demo(qapp, tmp_path):
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.cli import cmd_demo
    from aurora.core.model import Project
    with contextlib.redirect_stdout(io.StringIO()):
        assert cmd_demo(tmp_path / "demo") == 0
    mode.set_advanced(False)
    win = MainWindow()
    win.resize(1200, 760)
    win.show()
    win.home.set_project(Project.discover(tmp_path / "demo"))
    pump(qapp)
    win.show_perspective("survey")
    pump(qapp, 10)
    win.jobs.wait_idle(30000)
    pump(qapp)
    yield win
    mode.set_advanced(False)
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


def _feedback(qapp):
    """The Survey's stroke feedback comes at most every FEEDBACK_MS (thousands of points): wait for it."""
    from PySide6.QtTest import QTest
    from aurora.app.zone_tools import FEEDBACK_MS
    QTest.qWait(3 * FEEDBACK_MS)
    pump(qapp)


def _zones(ses):
    hz = ses.holder_zones(create=False)
    return list(hz.zones) if hz else []


def _halves(win):
    from aurora.core import testdata
    sv = win.survey
    x0, x1, y0, y1 = sv._bbox(win.session.project.find(testdata.STACK))
    xm = (x0 + x1) / 2
    left = [(x0 + 0.02, y0 + 0.02), (xm - 0.05, y0 + 0.1), (xm - 0.05, y1 - 0.02)]
    right = [(xm + 0.02, y0 + 0.02), (x1 - 0.1, y0 + 0.1), (x1 - 0.02, y1 - 0.02)]
    return left, right, (x0, x1, y0, y1)


def test_a_grain_beside_the_selected_one_starts_a_new_sample(qapp, demo):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from wp3_helpers import qdrag, qrelease
    win, ses, sv = demo, demo.session, demo.survey
    tb = sv.toolbar
    assert tb.shape() == "navigate" and not tb.draw_btn.isChecked() and not tb.erase_btn.isChecked()   # M09
    assert sv.new_zone_btn.isVisible() and sv.new_zone_btn.isEnabled()                               # S-01
    QTest.mouseClick(tb.draw_btn, Qt.LeftButton); pump(qapp)
    assert tb.draw_btn.isChecked() and tb.shape() == "rect"
    left, right, _box = _halves(win)
    qdrag(qapp, sv.map, left); pump(qapp)
    assert [z.id for z in _zones(ses)] == [1] and ses.current_zone == 1
    qdrag(qapp, sv.map, right, release=False); _feedback(qapp)
    assert sv.map.hint_text().startswith("new sample 2: ") and "pixel" in sv.map.hint_text()      # S-13: Easy words
    assert "(hold Ctrl to add it to sample 1)" in sv.map.hint_text()
    qrelease(qapp, sv.map, right[-1]); pump(qapp)
    assert [z.id for z in _zones(ses)] == [1, 2] and ses.current_zone == 2                     # not added to 1
    assert [sv.zone_table.item(r, 1).text() for r in range(2)] == ["sample 1", "sample 2"]      # M10
    assert [z.label for z in _zones(ses)] == ["zone 1", "zone 2"]                                # the file keeps zone N
    # a stroke over sample 2 grows it and says how to start the next sample (status line, kept after the autosave)
    ses.flush()
    qdrag(qapp, sv.map, [right[0], (right[-1][0], right[0][1] + 0.05)]); pump(qapp)
    assert len(_zones(ses)) == 2
    ses.flush(); pump(qapp)
    assert win.statusBar().currentMessage() == "Added to sample 2. To start another sample, press New sample (N)."
    # Esc deselects, so the next stroke starts a new sample; New sample does the same with an empty sample
    win.activateWindow()
    sv.map.setFocus()
    QTest.keyClick(sv.map, Qt.Key_Escape); pump(qapp)
    assert ses.current_zone is None
    QTest.mouseClick(sv.new_zone_btn, Qt.LeftButton); pump(qapp)
    assert ses.current_zone == 3 and ses.zone(3).label == "zone 3"
    # in Move a click on the bare plate deselects
    sv.set_tool("navigate"); pump(qapp)
    (vx0, vx1), (vy0, vy1) = sv.map.vb.viewRange()
    from wp3_helpers import view_to_viewport
    s = ses.survey()
    xb, yb = float(s.x.min()) + 0.05, float(s.y.min()) + 0.05          # a corner of the plate, outside the stack
    QTest.mouseClick(sv.map.viewport(), Qt.LeftButton, Qt.NoModifier, view_to_viewport(sv.map, xb, yb)); pump(qapp)
    assert ses.current_zone is None


def test_a_click_makes_a_visible_one_pixel_sample(qapp, demo):
    import pyqtgraph as pg
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from aurora.core.zones import members
    from wp3_helpers import view_to_viewport
    win, ses, sv = demo, demo.session, demo.survey
    sv.set_tool("rect+"); pump(qapp)
    s = ses.survey()
    i = int(np.argmin(s.x + s.y))                              # a plate pixel far from the stack
    QTest.mouseClick(sv.map.viewport(), Qt.LeftButton, Qt.NoModifier,
                     view_to_viewport(sv.map, float(s.x[i]), float(s.y[i])))
    pump(qapp, 10)
    z = ses.zone()
    assert z is not None and members(z, s.x, s.y, s.pitch).tolist() == [i]
    assert sv.hint.text() == "A click adds the pixel under it to a new sample 1 (1 pixel). Ctrl+Z undoes."
    fills = [it for it in sv.map._overlays if isinstance(it, pg.ScatterPlotItem)]
    assert len(fills) == 1 and len(fills[0].data) == 1                   # its pixel square is drawn (S-08)
    ses.flush(); pump(qapp)
    assert "lies on no complete stack" in win.statusBar().currentMessage() or "Ctrl+Z" in win.statusBar().currentMessage()


def test_an_erase_shows_as_a_gap_and_says_what_the_stack_keeps(qapp, demo):
    import pyqtgraph as pg
    from aurora.core.zones import members
    from wp3_helpers import qdrag, qrelease
    win, ses, sv = demo, demo.session, demo.survey
    left, _right, (x0, x1, y0, y1) = _halves(win)
    sv.set_tool("rect+")
    qdrag(qapp, sv.map, left); pump(qapp)
    z = ses.zone()
    s = ses.survey()
    before = members(z, s.x, s.y, s.pitch)
    sv.set_tool("rect-")
    xm = (x0 + x1) / 2
    strip = [(x0 - 0.05, y0 - 0.05), (xm, y0 - 0.05), (xm, y0 + 0.12)]
    qdrag(qapp, sv.map, strip, release=False); _feedback(qapp)
    hint = sv.map.hint_text()
    assert hint.startswith("erases ") and (" pixels from sample 1" in hint or " pixel from sample 1" in hint)
    assert "on 09:27 the sample keeps " in hint                                          # S-20
    qrelease(qapp, sv.map, strip[-1]); pump(qapp)
    after = members(ses.zone(), s.x, s.y, s.pitch)
    assert 0 < after.size < before.size
    fill = [it for it in sv.map._overlays if isinstance(it, pg.ScatterPlotItem)]
    assert len(fill) == 1 and len(fill[0].data) == after.size                              # M11: the gap shows
    sd = ses.stack_loaded(ses.stack_id(ses.project.find("092758_alpha_beta_f_k_stack")))
    if sd is not None:
        n = members(ses.zone(), sd.x, sd.y, sd.pitch).size
        assert f"keeps {n} of" in hint


def test_footprint_labels_stay_inside_their_footprints(qapp, demo):
    sv = demo.survey
    labels = sv.map.fitted_labels()
    assert labels and labels[0].startswith("09:27")
    fm_items = [t for t, *_r in sv.map._fitted]
    for (t, _full, _short, x0, x1) in sv.map._fitted:
        if not t.isVisible():
            continue
        r = t.mapRectToView(t.boundingRect())
        assert r.left() >= x0 - 1e-6 and r.right() <= x1 + 0.02, (t.textItem.toPlainText(), r, x0, x1)
    sv.map.zoom_to(-3, 3, 2, 8, margin=0)                    # zoomed out: the narrow ones shorten to their time
    pump(qapp)
    assert all(lab in ("", "09:27", "10:14") or lab.startswith(("09:27", "10:14")) for lab in sv.map.fitted_labels())
    assert fm_items


def test_sheet_rows_show_their_notes_and_name_the_next_sample(qapp, demo, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from wp3_helpers import qdrag
    win, ses, sv = demo, demo.session, demo.survey
    t = sv.sheet_table
    heads = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    assert heads == ["Sample", "Group", "Composition", "Edges", "Notes"]
    assert t.item(0, 4).text().endswith("left pad") and "left pad" in t.item(0, 0).toolTip()           # S-19
    sv._sheet_row_clicked(1)
    assert sv.armed_name == "2 beta" and sv.toolbar.tool() == "rect+" and ses.current_zone is None
    _left, right, _box = _halves(win)
    qdrag(qapp, sv.map, right); pump(qapp)
    z = ses.zone()
    assert z.label == "2 beta" and z.group == "Group 2" and sv.armed_name == ""                         # M08
    n = ses.undo_stack.index()
    ses.undo(); pump(qapp)
    assert not _zones(ses) and ses.undo_stack.index() == n - 1                    # drawing and naming: one step
    # Rename offers the sheet's names (S-14) and sets the group
    ses.redo(); pump(qapp)
    seen = {}

    def get_item(_parent, title, _label, items, _cur, editable):
        seen.update(title=title, items=list(items), editable=editable)
        return "1 alpha", True
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(get_item))
    with ses.edit("back to zone 1"):
        ses.zone().label, ses.zone().group = "zone 1", ""
    from aurora.app.zone_tools import ask_sample_name
    assert ask_sample_name(sv, ses, ses.current_zone)
    assert seen["title"] == "Name this sample" and seen["editable"] and "1 alpha" in seen["items"]
    assert ses.zone().label == "1 alpha" and ses.zone().group == "Group 1"
    # a click on a row whose sample has an outline selects it
    ses.select_zone(None); pump(qapp)
    sv._sheet_row_clicked(0)
    assert ses.current_zone == ses.zone().id and ses.zone().label == "1 alpha" and sv.armed_name == ""


def test_the_outlined_samples_card_says_its_empty_sentence(qapp, demo):
    """S-12, M13: at 1200 x 760 the card keeps two rows (the Zone preview stays whole), and its empty sentence, without
    an icon, fits them; the sample sheet keeps four rows."""
    sv = demo.survey
    t = sv.zone_table
    rows = t.verticalHeader().defaultSectionSize()
    assert t.minimumHeight() >= t.horizontalHeader().sizeHint().height() + 2 * rows
    lab = sv.zones_note
    assert lab.isVisible() and t.isHidden() and lab.text().startswith("No samples outlined on this holder yet")
    assert lab.height() >= lab.heightForWidth(lab.width()) - 1                      # the whole sentence shows
    s = sv.sheet_table
    assert s.minimumHeight() >= s.horizontalHeader().sizeHint().height() + 4 * s.verticalHeader().defaultSectionSize()


def test_reduce_colour_from_zero_and_names_first(qapp, demo):
    from aurora.app import mode
    from aurora.core import testdata
    win = demo
    win.show_perspective("reduce"); pump(qapp, 10)
    win.jobs.wait_idle(30000); pump(qapp, 10)
    rd = win.reduce
    sd = rd.current_stack()
    assert sd is not None
    lo, hi = rd.map.scale_levels()
    assert lo == 0.0 and hi > 0                                                        # S-07
    assert "from 0 (no signal) to the brightest pixel" in rd.map_caption.plain_text()
    first, second = rd.scan_list.item(0).text().split("\n")
    assert first == "09:27 · alpha_beta" and second.startswith("F K-edge · 670–735 eV")        # M01, S-04
    mode.set_advanced(True); pump(qapp)
    rd.redraw_map(); pump(qapp)
    assert rd.map.scale_levels()[0] > 0                                                 # Advanced: min to max
    mode.set_advanced(False); pump(qapp)
    assert testdata.STACK in rd.scan_list.item(0).toolTip()


def test_sample_part_of_scan_names():
    from aurora.core.names import sample_part
    assert sample_part("101500_holderB_quartz_grains_f_k_lab_stack", "holderB") == "quartz_grains"
    assert sample_part("092758_alpha_beta_f_k_stack", "holderA") == "alpha_beta"
    assert sample_part("101842_UO2_u_n45coarse_stack") == "UO2"
    assert sample_part("100000_fk_stack") == "fk"                     # never empty


def test_a_tiny_erase_drag_says_what_a_click_does(qapp, demo):
    """S-15: an erase drag shorter than half a pixel is dropped as a stray drag; it says so, and how to erase one."""
    from PySide6.QtCore import Qt
    from aurora.app.zone_tools import TINY_ERASE_TEXT
    from wp3_helpers import qdrag
    win = demo
    win.show_perspective("reduce"); pump(qapp, 10)
    win.jobs.wait_idle(30000); pump(qapp, 10)
    rd = win.reduce
    sd = rd.current_stack()
    x0, x1, y0, y1 = sd.bbox
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, [(x0, y0), ((x0 + x1) / 2, (y0 + y1) / 2), (x1, y1)]); pump(qapp)
    x, y = float(sd.x[0]), float(sd.y[0])
    rd.map.zoom_to(x - sd.pitch[0], x + sd.pitch[0], y - sd.pitch[1], y + sd.pitch[1], margin=0); pump(qapp)
    qdrag(qapp, rd.map, [(x, y), (x + 0.2 * sd.pitch[0], y), (x + 0.4 * sd.pitch[0], y)], button=Qt.RightButton)
    pump(qapp)
    assert rd.map.hint_text() == TINY_ERASE_TEXT
    assert "a click erases one pixel" in __import__("aurora.app.zone_tools", fromlist=["x"]).EASY_IDLE_ERASE
