"""Names of new samples (plain PL-20): a zone drawn on a stack whose name holds a sample-sheet ID of the holder takes
that name and group; without a sheet row for the holder the sample box offers the labels of the holder's scans; Add to
Check on a sample still named 'zone N' asks for its name (Enter names it, Esc keeps 'zone N')."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from wp3_helpers import close, open_window, pump  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _demo(path: Path, sheet: str | None) -> Path:
    from aurora.cli import cmd_demo
    from aurora.core import testdata
    with contextlib.redirect_stdout(io.StringIO()):
        assert cmd_demo(path) == 0
    if sheet is None:
        (path / testdata.SHEET).unlink()
    else:
        (path / testdata.SHEET).write_text(sheet, encoding="utf-8")
    return path


def test_the_words_of_a_name():
    from aurora.app.session import _holds, _words
    assert _words("092758_Alpha-beta f_k") == ["092758", "alpha", "beta", "f", "k"]
    assert _holds(_words("092758_alpha_beta_f_k_stack"), _words("Alpha beta"))
    assert not _holds(_words("092758_alphabet_f_k_stack"), _words("alpha"))       # whole words only
    assert not _holds(_words("x"), [])


def test_a_new_zone_takes_the_sheet_name_its_stack_holds(qapp, tmp_path):
    from aurora.core import testdata
    from aurora.core.model import Project
    sheet = ("Sample ID,Holder,Group\nalpha,{h},Glasses\nAlpha beta,{h},Pairs\nbet,{h},Other\ngamma,holderZ,X\n"
             .format(h=testdata.HOLDER))
    demo = _demo(tmp_path / "demo", sheet)
    win, sid = open_window(qapp, Project.discover(demo))
    try:
        rd, ses = win.reduce, win.session
        sd = ses.stack_loaded(sid)
        x0, x1, y0, y1 = sd.bbox
        rd.map.rect_drawn.emit(x0, (x0 + x1) / 2, y0, y1)
        pump(qapp)
        z = ses.zone()
        assert (z.label, z.group) == ("Alpha beta", "Pairs")        # the longest ID the stack name holds
        ses.select_zone(None)
        rd.map.rect_drawn.emit((x0 + x1) / 2, x1, y0, y1)
        pump(qapp)
        z2 = ses.zone()
        assert z2.id != z.id and z2.label == "Alpha beta 2"
        ses.undo()
        assert ses.zone(z2.id) is None                          # the name is part of the drawing's undo step
    finally:
        close(win)


def test_without_a_sheet_the_box_offers_the_scan_labels_and_add_to_check_asks(qapp, tmp_path):
    from aurora.core.model import Project
    demo = _demo(tmp_path / "demo", None)
    win, sid = open_window(qapp, Project.discover(demo))
    try:
        rd, ses = win.reduce, win.session
        sd = ses.stack_loaded(sid)
        x0, x1, y0, y1 = sd.bbox
        rd.map.rect_drawn.emit(x0, x1, y0, y1)
        pump(qapp)
        z = ses.zone()
        assert z.label == "zone 1"
        items = [rd.z_label.itemText(i) for i in range(rd.z_label.count())]
        assert items == ses.scan_labels() and items and all(items)
        assert rd.name_row.isHidden()
        assert rd.add_to_check() == 1
        assert not rd.name_row.isHidden() and rd.name_edit.placeholderText() == "zone 1"
        assert "Esc keeps 'zone 1'" in rd.name_prompt.text()                 # the field says it (M24)
        rd.name_edit.setFocus()
        QTest.keyClick(rd.name_edit, Qt.Key_Escape)
        assert rd.name_row.isHidden() and ses.zone().label == "zone 1"           # Esc keeps the name
        rd.ask_name(ses.zone())
        QTest.keyClicks(rd.name_edit, "my sample")
        QTest.keyClick(rd.name_edit, Qt.Key_Return)
        pump(qapp)
        assert rd.name_row.isHidden() and ses.zone().label == "my sample"
        assert rd.z_label.currentText() == "my sample" and "zone 1 is now named my sample" in rd.status_label.text()
        assert len(ses.check_queue) == 1 and ses.check_queue[0].zone_id == z.id     # the queued sample is the renamed one
        ses.undo()
        assert ses.zone().label == "zone 1"
    finally:
        close(win)
