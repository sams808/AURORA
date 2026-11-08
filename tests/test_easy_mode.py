"""Easy / Advanced mode: a fresh start is Easy; on the demo each perspective stays within its Easy limit of visible
interactive widgets; Advanced shows every marked widget; switching never writes the session or changes a value;
Ctrl+Shift+A drives the three entry points; Maps lives in Advanced only. Later packages add their rows here."""
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QLineEdit,  # noqa: E402
                               QSlider, QTabBar, QWidget)

from aurora.app import mode  # noqa: E402

# Easy limits of visible interactive widgets (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox, QSlider; a table
# or list view counts as one). Survey: besides the map and the tables; Check: besides the table. The cards' '?' help
# buttons (property aurora_help_button) open the manual and are not counted.
# Report: title, output, Browse…, Build, the 'Check before building' / Contents view (one tab shown), ◀, ▶, Open PDF,
# Open folder (I40 adds the findings view to the eight of WP0).
LIMITS = {"home": 8, "survey": 2, "reduce": 18, "check": 10, "compare": 12, "fit": 10, "report": 9}
VIEWS_NOT_COUNTED = {"survey", "check"}
INTERACTIVE = (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox, QSlider)


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def demo(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    yield win
    win.close()


def _inside(w: QWidget, root: QWidget, types) -> bool:
    p = w.parentWidget()
    while p is not None and p is not root:
        if isinstance(p, types):
            return True
        p = p.parentWidget()
    return False


def interactive(page: QWidget, count_views: bool = True) -> list[QWidget]:
    """Visible interactive widgets of a page (internal parts of combos, spin boxes, views and tab bars excluded)."""
    out = []
    for w in page.findChildren(QWidget):
        if not w.isVisible() or _inside(w, page, (QAbstractItemView, QComboBox, QAbstractSpinBox, QTabBar)):
            continue
        if w.property("aurora_help_button"):
            continue
        if isinstance(w, QAbstractItemView):
            if count_views:
                out.append(w)
        elif isinstance(w, INTERACTIVE):
            out.append(w)
    return out


def _describe(ws) -> list[str]:
    return [f"{type(w).__name__}:{getattr(w, 'text', lambda: '')() if not isinstance(w, QComboBox) else w.currentText()}" for w in ws]


def values(root: QWidget) -> list:
    """Every widget value under root (checked states, combo indices and texts, line edits, spin boxes, sliders)."""
    out = []
    for w in root.findChildren(QWidget):
        if isinstance(w, QAbstractButton) and w.isCheckable():
            out.append(("check", w.objectName(), w.text(), w.isChecked()))
        elif isinstance(w, QComboBox):
            out.append(("combo", w.currentIndex(), w.currentText(), w.count()))
        elif isinstance(w, QLineEdit) and not isinstance(w.parentWidget(), (QComboBox, QAbstractSpinBox)):
            out.append(("line", w.text()))
        elif isinstance(w, QAbstractSpinBox):
            out.append(("spin", w.text()))
        elif isinstance(w, QSlider):
            out.append(("slider", w.value()))
    return out


def test_fresh_settings_start_in_easy(qapp):
    from aurora.app.main import MainWindow
    assert mode.is_advanced() is False
    win = MainWindow()
    assert win.mode_btn.text() == "Easy mode" and not win.mode_btn.isChecked() and not win.mode_action.isChecked()
    assert win.mode_action.shortcut().toString() == "Ctrl+Shift+A"
    win.close()


def test_easy_limits_on_the_demo(qapp, demo):
    from demo_window import pump
    win = demo
    assert not mode.is_advanced()
    for key, limit in LIMITS.items():
        win.show_perspective(key)
        pump(qapp, 3)
        ws = interactive(win.pages[key], count_views=key not in VIEWS_NOT_COUNTED)
        assert 0 < len(ws) <= limit, (key, len(ws), _describe(ws))
        for w in mode.advanced_marked(win.pages[key]):
            assert w.isHidden(), (key, w)
    # Easy defaults stated read-only where they matter
    win.show_perspective("fit")
    pump(qapp)
    assert win.fit.lcf_fixed.isVisible() and "± from 200 bootstrap draws" in win.fit.lcf_fixed.text()
    assert [win.fit.tabs.isTabVisible(i) for i in range(win.fit.tabs.count())] == [True, False, False, False]
    win.show_perspective("report")
    pump(qapp)
    assert win.report.scope_line.isVisible() and "all groups" in win.report.scope_line.text()
    # hidden columns: Compare shows Show, Fit, Label, Signal, Shift eV, Colour (WP5 I28); Home hides Kind
    t = win.compare.table
    shown = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount()) if not t.isColumnHidden(c)]
    assert shown == ["Show", "Fit", "Label", "Signal", "Shift eV", "Colour"]
    assert win.home.tree.isColumnHidden(2)


def test_advanced_shows_every_marked_widget(qapp, demo):
    from demo_window import pump
    win = demo
    mode.set_advanced(True)
    pump(qapp)
    marked = 0
    for key, page in win.pages.items():
        win.show_perspective(key)
        pump(qapp, 3)
        for w in mode.advanced_marked(page):
            if w.parentWidget() is not None and w.parentWidget().isVisible():
                assert w.isVisible(), (key, w)
                marked += 1
        for w in mode.easy_marked(page):
            assert w.isHidden(), (key, w)
    assert marked >= 25
    assert all(win.fit.tabs.isTabVisible(i) for i in range(win.fit.tabs.count()))
    t = win.compare.table
    assert not any(t.isColumnHidden(c) for c in range(t.columnCount()))
    assert not win.home.tree.isColumnHidden(2)
    assert win.rail_buttons["maps"].isVisible()
    # the Advanced header menu shows or hides any column, and Easy keeps its own columns hidden
    from aurora.app.mode import _ColumnModes
    helper = t.findChild(_ColumnModes, "aurora_column_modes")
    assert len(helper.menu_actions().actions()) == t.columnCount()
    helper.toggle(2, False)
    assert t.isColumnHidden(2)
    helper.toggle(2, True)
    assert not t.isColumnHidden(2)


def test_switching_never_writes_or_changes_values(qapp, demo, tmp_path):
    from PySide6.QtTest import QTest
    from aurora.app.settings import settings
    win = demo
    ses = win.session
    ses.save()
    ses._save_timer.stop()
    before = ses.session_file.read_bytes()
    ini = settings().fileName()
    settings().sync()

    def other_settings():
        with open(ini, encoding="utf-8") as f:
            return [ln for ln in f.read().splitlines() if not ln.startswith("advanced_mode")]
    s_before = other_settings()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)       # rebuilt cards still waiting for deletion
    qapp.processEvents()
    v_before = values(win)
    for on in (True, False, True, False):
        mode.set_advanced(on)
        QTest.qWait(50)
    QTest.qWait(1200)                                          # longer than the 800 ms autosave delay
    assert ses.session_file.read_bytes() == before
    assert values(win) == v_before
    assert other_settings() == s_before


def test_ctrl_shift_a_drives_the_three_entry_points(qapp):
    from PySide6.QtTest import QTest
    from aurora.app.main import MODE_STATUS, MainWindow
    from aurora.app.settings_dialog import SettingsDialog
    win = MainWindow()
    win.show()
    win.activateWindow()
    qapp.processEvents()
    for expected in (True, False):
        QTest.keyClick(win, Qt.Key_A, Qt.ControlModifier | Qt.ShiftModifier)
        qapp.processEvents()
        assert mode.is_advanced() is expected
        assert win.mode_btn.isChecked() is expected and win.mode_action.isChecked() is expected
        assert win.mode_btn.text() == ("Advanced mode" if expected else "Easy mode")
        d = SettingsDialog(win)
        assert d.advanced.isChecked() is expected
        d.deleteLater()
        assert win.statusBar().currentMessage() == MODE_STATUS[expected]
    # the Settings checkbox and the top-bar switch drive the other two
    d = SettingsDialog(win)
    d.advanced.setChecked(True)
    d.accept()
    assert mode.is_advanced() and win.mode_btn.isChecked() and win.mode_action.isChecked()
    win.mode_btn.click()
    assert not mode.is_advanced() and not win.mode_action.isChecked()
    win.close()


def test_maps_is_advanced_only(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.show()
    qapp.processEvents()
    assert "maps" in mode.advanced_perspectives() and mode.perspective_hidden("maps")
    assert not win.rail_buttons["maps"].isVisible()
    assert win.go_to("maps") is False and win.stack.currentWidget() is win.home
    from aurora.app.shortcuts import native_text
    assert win.statusBar().currentMessage() == native_text("Maps is part of Advanced mode (Ctrl+Shift+A)")
    mode.set_advanced(True)
    qapp.processEvents()
    assert win.rail_buttons["maps"].isVisible() and win.go_to("maps") and win.stack.currentWidget() is win.maps
    mode.set_advanced(False)                                   # a hidden current perspective falls back to Home
    qapp.processEvents()
    assert win.stack.currentWidget() is win.home
    win.close()


def test_settings_dialog_rows(qapp):
    from aurora.app.settings_dialog import SettingsDialog
    d = SettingsDialog()
    easy = [d.appearance, d.text_size, d.author, d.open_last, d.advanced, d.update_check]
    adv = [d.k, d.fn, d.tn, d.cmap, d.ref_dir]
    assert all(d.form.isRowVisible(w) for w in easy) and not any(d.form.isRowVisible(w) for w in adv)
    assert d.help_btn is not None and d.help_btn.toolTip()          # the Help button opens MANUAL.md#10-settings
    mode.set_advanced(True)
    assert all(d.form.isRowVisible(w) for w in easy + adv)
    d.deleteLater()


def test_mark_helpers(qapp):
    from PySide6.QtWidgets import QLabel, QTableWidget, QVBoxLayout
    host = QWidget()
    lay = QVBoxLayout(host)
    adv, easy, table = QLabel("advanced"), QLabel("easy only"), QTableWidget(2, 3)
    for w in (adv, easy, table):
        lay.addWidget(w)
    mode.mark_advanced(adv)
    mode.mark_easy(easy)
    mode.mark_advanced_column(table, 1)
    loose = QLabel("no parent yet")
    mode.mark_advanced(loose)                                  # a parentless widget never pops up as a window
    host.show()
    qapp.processEvents()
    assert adv.isHidden() and easy.isVisible() and table.isColumnHidden(1) and not table.isColumnHidden(0)
    mode.set_advanced(True)
    qapp.processEvents()
    assert adv.isVisible() and easy.isHidden() and not table.isColumnHidden(1) and not loose.isVisible()
    assert mode.advanced_columns(table) == [1] and adv in mode.advanced_marked(host)
    host.close()
    host.deleteLater()
    loose.deleteLater()


def test_easy_defaults_and_flags(qapp, monkeypatch):
    from types import SimpleNamespace
    from aurora.app.settings import settings
    from aurora.core import flags as fl
    from aurora.core.constants import DESPIKE_K
    scan = SimpleNamespace(name="092758_x_stack", edge=("F", "K"),
                           summary={"sdd_saturated": ["sdd3"], "i0_bad": True, "mode": "PFY F Kα"})
    flags = fl.merged_flags(None, scan)
    assert [str(f) for f in flags] == ["SDD saturated: SDD3 (sgm_report)", "I0 invalid (sgm_report)"]
    assert fl.saturated_dets(flags) == {"sdd3"} and fl.i0_invalid(flags)
    st = mode.easy_scan_settings(scan, flags)
    assert st.sdd == [True, True, False, True] and st.i0_source == "good" and st.line == "F Kα"
    assert st.fluo_norm == "i0jump" and st.tey_norm == "i0jump" and st.k == DESPIKE_K and st.despike and st.tey
    assert st.pre is None and st.post is None and st.stack == scan.name
    clean = SimpleNamespace(name="s", edge=("F", "K"), summary={})
    st2 = mode.easy_scan_settings(clean, fl.merged_flags(None, clean), labels=["total XRF", "O Kα", "F Kα"])
    assert st2.sdd == [True] * 4 and st2.i0_source == "own" and st2.line == "F Kα"
    s = settings(); s.setValue("despike_k", 8.0); s.setValue("fluo_norm", "area"); s.sync()
    st3 = mode.easy_scan_settings(clean, [])
    assert st3.k == 8.0 and st3.fluo_norm == "area"
    assert mode.EASY_DEFAULTS["fit"]["bootstrap"] == 200 and mode.EASY_DEFAULTS["compare"]["spacing"] == 0.6
    assert set(mode.EASY_DEFAULTS) == {"zone_scan", "compare", "fit", "report"}
