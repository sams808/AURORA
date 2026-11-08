"""Click everything: on a fresh window and on a demo-loaded one, in Easy and in Advanced mode, every visible, enabled
QPushButton, QToolButton and QCheckBox of every perspective (and of every Fit and Maps tab) is clicked once. File,
input and message dialogs answer 'cancel'; background jobs are waited for. Any exception raised inside a slot reaches
the excepthook collector of conftest and fails the test; a failed background job (an error dialog) fails it too."""
import functools
import os

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox,  # noqa: E402
                               QPushButton, QToolButton)

from aurora.app import mode  # noqa: E402

DENY_TEXT = {"Quit", "Full screen"}            # plus the mode switch (objectName modebtn) and buttons with a menu


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def cancel_dialogs(monkeypatch, tmp_path):
    """Every dialog answers 'cancel', the demo beamtime goes to tmp, nothing opens outside the test."""
    import platformdirs
    from PySide6.QtGui import QDesktopServices
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: ([], "")))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: ("", "")))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: ""))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("", False)))
    monkeypatch.setattr(QInputDialog, "getInt", staticmethod(lambda *a, **k: (0, False)))
    monkeypatch.setattr(QInputDialog, "getDouble", staticmethod(lambda *a, **k: (0.0, False)))
    for name in ("information", "warning", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Cancel))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    monkeypatch.setattr(QMenu, "exec", lambda self, *a, **k: None)
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda *a, **k: str(tmp_path / "user data"))
    from aurora.core.report import ReportBuilder          # the report of a click: small figures, quick
    monkeypatch.setattr(ReportBuilder, "__init__", functools.partialmethod(ReportBuilder.__init__, png_dpi=60))
    return opened


def _pump(qapp, win, n=3):
    for _ in range(n):
        qapp.processEvents()
    win.jobs.wait_idle(120000)
    qapp.processEvents()


def _clickable(page):
    import shiboken6
    out = []
    for w in page.findChildren(QAbstractButton):
        if not isinstance(w, (QPushButton, QToolButton, QCheckBox)):
            continue
        if not shiboken6.isValid(w) or not w.isVisible() or not w.isEnabled():
            continue
        if w.objectName() == "modebtn" or w.text().replace("&", "") in DENY_TEXT:
            continue
        if isinstance(w, QToolButton) and w.menu() is not None:
            continue
        out.append(w)
    return out


def _views(win, key):
    """The tab states of a perspective: one per visible tab for Fit and Maps, one otherwise."""
    page = win.pages[key]
    tabs = getattr(page, "tabs", None) if key in ("fit", "maps") else None
    if tabs is None:
        return [None]
    return [i for i in range(tabs.count()) if tabs.isTabVisible(i)]


def click_everything(qapp, win) -> int:
    import shiboken6
    from aurora.app.main import PERSPECTIVES
    clicked = 0
    for key, _title in PERSPECTIVES:
        if mode.perspective_hidden(key):
            assert win.go_to(key) is False
            continue
        for tab in _views(win, key):
            win.show_perspective(key)
            if tab is not None:
                win.pages[key].tabs.setCurrentIndex(tab)
            _pump(qapp, win)
            seen = set()
            for w in _clickable(win.pages[key]):
                if not shiboken6.isValid(w) or not w.isVisible() or not w.isEnabled():
                    continue                                 # an earlier click rebuilt or disabled it
                ident = (type(w).__name__, w.text(), w.objectName())
                w.click()
                clicked += 1
                if os.environ.get("AURORA_MONKEY_VERBOSE"):
                    print(ascii(f"{key}[{tab}] {type(w).__name__} {w.text()!r} in {type(w.parentWidget()).__name__}"), flush=True)
                seen.add(ident)
                _pump(qapp, win)
                if win.stack.currentWidget() is not win.pages[key]:      # a 'Go to' button moved on: come back
                    win.show_perspective(key)
                    if tab is not None:
                        win.pages[key].tabs.setCurrentIndex(tab)
                    _pump(qapp, win)
    return clicked


@pytest.mark.parametrize("advanced", [False, True], ids=["easy", "advanced"])
def test_click_everything_on_a_fresh_window(qapp, cancel_dialogs, error_dialogs, advanced):
    from aurora.app.main import MainWindow
    mode.set_advanced(advanced)
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    _pump(qapp, win)
    n = click_everything(qapp, win)
    assert n >= 10, n
    assert not error_dialogs, error_dialogs
    if win._help is not None:
        win._help.close()
    win.close()


@pytest.mark.parametrize("advanced", [False, True], ids=["easy", "advanced"])
def test_click_everything_on_the_demo(qapp, cancel_dialogs, error_dialogs, tmp_path, advanced):
    from demo_window import build_demo_window
    from aurora.core.testdata import make_fake_zones
    mode.set_advanced(advanced)
    win = build_demo_window(qapp, tmp_path)
    ses = win.session
    ses.zones.holders["N5"].zones.extend(z for z in make_fake_zones(ses.project).holders["N5"].zones
                                         if z.id not in {zz.id for zz in ses.zones.holders["N5"].zones})
    ses.touch()
    _pump(qapp, win)
    n = click_everything(qapp, win)
    assert n >= (80 if advanced else 25), n
    assert not error_dialogs, error_dialogs
    ses._save_timer.stop()
    if win._help is not None:
        win._help.close()
    win.close()


# ----------------------------------------------------------------------------- the two crashes it found, and the collector
@pytest.mark.allow_excepthook
def test_a_raising_slot_reaches_the_collector(qapp, excepthook_collector):
    btn = QPushButton("boom")

    def boom():
        raise ValueError("boom")

    btn.clicked.connect(boom)
    btn.click()
    assert len(excepthook_collector) == 1 and excepthook_collector[0][0] is ValueError
    excepthook_collector.clear()
    btn.deleteLater()


def test_new_zone_without_a_project(qapp, excepthook_collector):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.show()
    win.reduce.new_zone_btn.setEnabled(True)                   # even when forced, the click is harmless
    win.reduce.new_zone_btn.click()
    qapp.processEvents()
    assert not excepthook_collector and win.session.zones.holders == {}
    assert win.session.new_zone() is None and win.session.delete_zone(1) is None and win.session.select_zone(3) is None
    assert "open a project first" in win.reduce.new_zone_btn.toolTip()
    win.close()


def test_difference_adds_exactly_one_row(qapp, tmp_path, excepthook_collector):
    from demo_window import build_demo_window, pump
    win = build_demo_window(qapp, tmp_path)
    mode.set_advanced(True)
    comp = win.compare
    n0 = len(win.library.requests)
    comp.table.clearSelection()
    comp.table.selectRow(0)
    from PySide6.QtCore import QItemSelectionModel
    comp.table.selectionModel().select(comp.table.model().index(1, 0),
                                       QItemSelectionModel.Select | QItemSelectionModel.Rows)
    pump(qapp)
    assert comp.selected_rows() == [0, 1] and comp.diff_btn.isEnabled()
    comp.diff_btn.click()
    pump(qapp)
    assert len(win.library.requests) == n0 + 1 and not excepthook_collector
    win.session._save_timer.stop()
    win.close()
