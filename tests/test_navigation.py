"""Navigate from anywhere: the holder switcher in the top-bar crumb, Ctrl+PgUp / Ctrl+PgDown, Ctrl+1..8, Ctrl+Return,
visible focus, the generated shortcuts list, and no '1 days / 1 holders' anywhere."""
import re

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QAction  # noqa: E402

from aurora.app import mode  # noqa: E402
from aurora.core import testdata  # noqa: E402
from aurora.core.model import Project  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


@pytest.fixture
def two_holders(qapp, tmp_path):
    from aurora.app.main import MainWindow
    root = testdata.make_fake_project(tmp_path / "beamtime", n_holders=2)
    win = MainWindow()
    win.show()
    win.activateWindow()
    win.home.set_project(Project.discover(root))
    pump(qapp)
    win.jobs.wait_idle(30000)
    yield win
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


def test_holder_switcher_in_the_crumb(qapp, two_holders):
    win = two_holders
    ses = win.session
    keys = list(ses.holder_keys)
    assert keys == ["N5", "N6"] and ses.current_holder == "N5"
    assert win.crumb.text() == "beamtime ▸ 2026-11-05 ▸ N5"
    assert win.crumb.holder_btn.text() == "N5 ▾"
    win.show_perspective("reduce")
    entries = win.crumb.holder_actions()
    assert [a.text() for a in entries] == ["N5 · 2026-11-05 · 1 stack · 0 zones", "N6 · 2026-11-05 · 1 stack · 0 zones"]
    entries[1].trigger()
    pump(qapp)
    assert ses.current_holder == "N6" and win.crumb.text() == "beamtime ▸ 2026-11-05 ▸ N6"
    sids = [win.reduce.scan_list.item(i).data(Qt.UserRole) for i in range(win.reduce.scan_list.count())]
    assert sids == [ses.stack_id(s) for s in ses.stacks_of("N6")] and testdata.STACK2 in sids[0]
    assert win.reduce.scans_card.title.text() == "Scans of holder N6"
    assert win.check.run_btn.text() == "Run the checks on holder N6"


def test_ctrl_pgdown_and_pgup_cycle_through_the_holders(qapp, two_holders):
    from PySide6.QtTest import QTest
    win = two_holders
    ses = win.session
    seen = []
    for _ in range(3):
        QTest.keyClick(win, Qt.Key_PageDown, Qt.ControlModifier)
        pump(qapp)
        seen.append(ses.current_holder)
    assert seen == ["N6", "N5", "N6"]
    QTest.keyClick(win, Qt.Key_PageUp, Qt.ControlModifier)
    pump(qapp)
    assert ses.current_holder == "N5"


def test_ctrl_number_opens_a_perspective(qapp, two_holders):
    from PySide6.QtTest import QTest
    from aurora.app.main import PERSPECTIVES
    win = two_holders
    QTest.keyClick(win, Qt.Key_3, Qt.ControlModifier)
    pump(qapp)
    assert win.stack.currentWidget() is win.reduce
    for i, (key, title) in enumerate(PERSPECTIVES):
        assert win.rail_buttons[key].toolTip() == f"{title} (Ctrl+{i + 1})"
        assert win.goto_actions[key].shortcut().toString() == f"Ctrl+{i + 1}"
    QTest.keyClick(win, Qt.Key_7, Qt.ControlModifier)             # Maps in Easy mode: a status hint, Reduce stays
    pump(qapp)
    assert win.stack.currentWidget() is win.reduce
    assert win.statusBar().currentMessage() == "Maps is part of Advanced mode (Ctrl+Shift+A)"
    assert not win.goto_actions["maps"].isVisible()
    mode.set_advanced(True)
    pump(qapp)
    QTest.keyClick(win, Qt.Key_7, Qt.ControlModifier)
    pump(qapp)
    assert win.stack.currentWidget() is win.maps and win.goto_actions["maps"].isVisible()
    mode.set_advanced(False)


def test_ctrl_return_clicks_the_primary_button(qapp, two_holders):
    from PySide6.QtTest import QTest
    win = two_holders
    win.show_perspective("check")
    pump(qapp)
    assert win.check.run_btn.isEnabled()
    QTest.keyClick(win, Qt.Key_Return, Qt.ControlModifier)
    pump(qapp)
    assert win.jobs.wait_idle(60000)
    pump(qapp)
    assert win.check._results, "the checks ran"


def test_focus_is_visible(qapp):
    from PySide6.QtWidgets import QCheckBox, QPushButton, QToolButton, QVBoxLayout, QWidget
    host = QWidget()
    lay = QVBoxLayout(host)
    widgets = [QPushButton("Run the checks"), QCheckBox("include"), QToolButton()]
    widgets[2].setText("rect+")
    for w in widgets:
        w.setFocusPolicy(Qt.StrongFocus)
        lay.addWidget(w)
    lay.addWidget(QPushButton("elsewhere"))
    host.show()
    host.activateWindow()
    pump(qapp)
    for w in widgets:
        host.findChildren(QPushButton)[-1].setFocus(Qt.TabFocusReason)
        pump(qapp)
        before = w.grab().toImage()
        w.setFocus(Qt.TabFocusReason)
        pump(qapp)
        assert w.hasFocus(), w
        after = w.grab().toImage()
        assert before != after, f"{type(w).__name__}: no visible focus"
    host.deleteLater()


def test_the_shortcuts_dialog_lists_every_shortcut(qapp, two_holders):
    from aurora.app.diagnostics import ShortcutsDialog
    win = two_holders
    d = ShortcutsDialog(win)
    listed = {id(a) for _s, _t, _k, acts in d.rows for a in acts}
    with_keys = [a for a in win.findChildren(QAction) if any(not s.isEmpty() for s in a.shortcuts())]
    assert len(with_keys) >= 20
    missing = [a.text() for a in with_keys if id(a) not in listed]
    assert not missing, missing
    text = d.text()
    for needle in ("Ctrl+3", "Ctrl+Shift+A", "Ctrl+PgDown", "Ctrl+Return", "Ctrl+/", "Shift+F1", "Esc"):
        assert needle in text, needle
    sections = {s for s, _t, _k, _a in d.rows}
    assert {"File", "View", "Go to", "Help", "General"} <= sections
    d.deleteLater()


def _visible_texts(win) -> list[str]:
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QLabel, QListWidget, QTableWidget, QTextBrowser,
                                   QTreeWidget, QTreeWidgetItemIterator, QWidget)
    out = [win.crumb.text(), win.statusBar().currentMessage()]
    for w in win.findChildren(QWidget):
        if not w.isVisible():
            continue
        if isinstance(w, (QLabel, QAbstractButton)):
            out.append(w.text())
        elif isinstance(w, QComboBox):
            out += [w.itemText(i) for i in range(w.count())]
        elif isinstance(w, QTextBrowser):
            out.append(w.toPlainText())
        elif isinstance(w, QListWidget):
            out += [w.item(i).text() for i in range(w.count())]
        elif isinstance(w, QTableWidget):
            out += [w.item(r, c).text() for r in range(w.rowCount()) for c in range(w.columnCount()) if w.item(r, c)]
        elif isinstance(w, QTreeWidget):
            it = QTreeWidgetItemIterator(w)
            while it.value():
                item = it.value()
                out += [item.text(c) for c in range(w.columnCount())]
                it += 1
    return out


def test_no_singular_plural_mistakes_on_the_demo(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    bad = re.compile(r"\b1 (days|holders|stacks|scans|zones)\b")
    from aurora.app.main import PERSPECTIVES
    found = []
    for advanced in (False, True):
        mode.set_advanced(advanced)
        for key, _t in PERSPECTIVES:
            if mode.perspective_hidden(key):
                continue
            win.show_perspective(key)
            pump(qapp)
            found += [t for t in _visible_texts(win) if bad.search(t)]
    mode.set_advanced(False)
    assert not found, found
    from aurora.app.vocab import plural
    assert plural(1, "day") == "1 day" and plural(2, "holder") == "2 holders" and plural(3, "spectrum") == "3 spectra"
    win.session._save_timer.stop()
    win.close()
