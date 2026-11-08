"""WP5 I29: Compare table cells show the state in force: typed editors per column, a cell that cannot be read is put
back with the reason in a message line that replot keeps, the Fit column in view, legacy windows honoured, a colour
button whose colour survives save and load, a default shift for new rows."""
import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QStyleOptionViewItem, QToolButton  # noqa: E402

from aurora.app import mode  # noqa: E402
from aurora.core.compare import NORMS, Spectrum, TraceRequest, normalise_legacy, process  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def demo(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    yield win
    win.session._save_timer.stop()
    win.close()


def _editor(table, row, col):
    d = table.itemDelegateForColumn(col)
    return d.createEditor(table.viewport(), QStyleOptionViewItem(), table.model().index(row, col))


def test_typed_editors(qapp, demo):
    c = demo.compare
    t = c.table
    ed = _editor(t, 0, c.col("Norm"))
    assert isinstance(ed, QComboBox) and [ed.itemText(i) for i in range(ed.count())] == list(NORMS)
    sig = _editor(t, 0, c.col("Signal"))
    r0 = demo.library.requests[0]
    assert [sig.itemText(i) for i in range(sig.count())] == demo.library.signals_of(r0.label)
    for header in ("Shift eV", "Pre lo", "Pre hi", "Post lo", "Post hi", "E0 eV"):
        sp = _editor(t, 0, c.col(header))
        assert isinstance(sp, QDoubleSpinBox) and sp.suffix() == " eV", header
    e0 = _editor(t, 0, c.col("E0"))
    items = [e0.itemText(i) for i in range(e0.count())]
    assert items[:4] == ["deriv", "half", "table", "fixed"] and all(x.startswith("row:") for x in items[4:])
    assert f"row:{r0.key}" not in items                           # a row cannot follow itself
    # the spin editor writes back through the delegate: a value, with the unit stripped
    sp = _editor(t, 0, c.col("Shift eV"))
    sp.setValue(1.25)
    t.itemDelegateForColumn(c.col("Shift eV")).setModelData(sp, t.model(), t.model().index(0, c.col("Shift eV")))
    assert demo.library.requests[0].shift == 1.25


def test_unreadable_cell_is_put_back_and_the_message_stays(qapp, demo):
    c = demo.compare
    r = demo.library.requests[0]
    r.shift = 0.0
    c._fill_table()
    c.table.item(0, c.col("Shift eV")).setText("abc")
    assert c.table.item(0, c.col("Shift eV")).text() == "0" and r.shift == 0
    assert "could not read" in c.message.text() and not c.message_row.isHidden()
    c.replot()
    assert "could not read" in c.message.text()
    # Norm: only the four normalisations; 'tey' is refused, not shown while larch stays in force
    c.table.item(0, c.col("Norm")).setText("tey")
    assert c.table.item(0, c.col("Norm")).text() == r.norm == "larch" and "could not read 'tey'" in c.message.text()
    c.table.item(0, c.col("Norm")).setText("legacy")
    assert r.norm == "legacy" and c.table.item(0, c.col("Pre lo")).text() == "auto 15 %"
    assert c.table.item(0, c.col("Post hi")).text() == "auto 20 %"
    c.table.item(0, c.col("Pre hi")).setText("684")
    assert r.legacy_pre is not None and r.legacy_pre[1] == 684.0
    c.dismiss_btn.click()
    assert c.message_row.isHidden()


def test_fit_column_in_view_in_easy_at_1500(qapp, demo):
    from demo_window import pump
    win = demo
    assert not mode.is_advanced()
    win.resize(1500, 940)
    win.show_perspective("compare")
    pump(qapp, 5)
    t = win.compare.table
    fc = win.compare.col("Fit")
    h = t.horizontalHeader()
    assert not t.isColumnHidden(fc)
    assert h.sectionViewportPosition(fc) + h.sectionSize(fc) <= t.viewport().width()
    # in Advanced the Label column keeps a readable width (the table scrolls sideways)
    mode.set_advanced(True)
    pump(qapp, 3)
    from aurora.app.compare import LABEL_MIN_PX
    assert t.columnWidth(win.compare.col("Label")) >= LABEL_MIN_PX
    assert h.sectionViewportPosition(fc) + h.sectionSize(fc) <= t.viewport().width()
    mode.set_advanced(False)


def test_every_header_has_a_tooltip(qapp, demo):
    t = demo.compare.table
    for c in range(t.columnCount()):
        it = t.horizontalHeaderItem(c)
        assert it is not None and it.toolTip().strip(), c
    tip = t.horizontalHeaderItem(demo.compare.col("Pre lo")).toolTip()
    assert "absolute eV for legacy" in tip and "15 %" in tip


def test_legacy_windows_are_used():
    E = np.linspace(670, 730, 241)
    y = 0.3 + 0.004 * (E - 670) + 1 / (1 + np.exp(-(E - 696) / 1.0))
    s = Spectrum("a", "x", E, y)
    r1 = process(s, TraceRequest("a", "x", norm="legacy", legacy_pre=(680, 684), legacy_post=(715, 725)))
    r2 = process(s, TraceRequest("a", "x", norm="legacy", legacy_pre=(680, 682), legacy_post=(715, 725)))
    assert not np.allclose(r1.y, r2.y)
    pre = (E >= 680) & (E <= 684)
    post = (E >= 715) & (E <= 725)
    assert np.isclose(r1.step, y[post].mean() - y[pre].mean())
    assert r1.pre_window == (680, 684) and r1.post_window == (715, 725)
    auto = process(s, TraceRequest("a", "x", norm="legacy"))
    ref = normalise_legacy(E, y)
    assert np.allclose(auto.y, ref.y) and np.isclose(auto.pre_window[1] - auto.pre_window[0], 0.15 * 60)


def test_colour_button_and_round_trip(qapp, demo, tmp_path, monkeypatch):
    from aurora.app import compare as cmp_mod
    from aurora.app.library import Library
    c = demo.compare
    btn = c.table.cellWidget(0, c.col("Colour"))
    assert isinstance(btn, QToolButton) and btn.accessibleName().startswith("colour of")
    monkeypatch.setattr(cmp_mod, "pick_colour", lambda parent, initial, title: "#123456")
    btn.click()
    assert demo.library.requests[0].colour == "#123456"
    cfg = tmp_path / "cfg.json"
    demo.library.save(cfg)
    fresh = Library()
    missing, _extra = fresh.load(cfg)
    assert not missing and fresh.requests[0].colour == "#123456"


def test_default_shift_for_new_rows(qapp, demo, tmp_path):
    from aurora.core.testdata import write_john_reduced
    from demo_window import pump
    mode.set_advanced(True)
    demo.show_perspective("compare")
    pump(qapp)
    c = demo.compare
    assert c.default_shift.isVisible()
    c.default_shift.setValue(0.7)
    E = np.linspace(680, 720, 41)
    write_john_reduced(tmp_path / "new" / "Z_full_reduced.csv", "Z", E, 1 / (1 + np.exp(-(E - 696))))
    added = demo.library.add_folder(tmp_path / "new")
    assert added and all(r.shift == 0.7 for r in added)
    mode.set_advanced(False)


def test_checkbox_columns_follow_the_rows(qapp, demo):
    c = demo.compare
    r = demo.library.requests[0]
    c.table.item(0, c.col("Fit")).setCheckState(Qt.Unchecked)
    assert r.fit is False
    c.table.item(0, c.col("Fit")).setCheckState(Qt.Checked)
    assert r.fit is True
