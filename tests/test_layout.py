"""No clipped control at 1200 x 760 or 1500 x 940: with the demo loaded, in Easy and in Advanced mode, for every
perspective and every Fit and Maps tab, every visible button and combo box and every one-line label gets at least its
size hint (2 px tolerance), or is an ElidedLabel that keeps its full text in the tooltip.

Offscreen Qt measures text only with real fonts: conftest points QT_QPA_FONTDIR at the system fonts folder, and this
module skips when no font is available (the metrics would be meaningless)."""
import sys

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QSize  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QLabel,  # noqa: E402
                               QScrollArea, QTabBar, QWidget)

from aurora.app import mode  # noqa: E402

SIZES = [(1200, 760), (1500, 940)]


@pytest.fixture(scope="module")
def qapp():
    from conftest import system_font_dir
    if not system_font_dir():
        pytest.skip("no system fonts folder: offscreen text metrics would be meaningless")
    from aurora.app.main import create_app
    app = create_app(["aurora-tests"])
    from PySide6.QtGui import QFontDatabase
    if not QFontDatabase.families():
        pytest.skip("the offscreen platform found no font (QT_QPA_FONTDIR was set after the application started)")
    return app


def _inside(w: QWidget, root: QWidget, types) -> bool:
    p = w.parentWidget()
    while p is not None and p is not root:
        if isinstance(p, types):
            return True
        p = p.parentWidget()
    return False


def clipped(page: QWidget) -> list[str]:
    from aurora.app.widgets import ElidedLabel
    bad = []
    for w in page.findChildren(QWidget):
        if not w.isVisible() or _inside(w, page, (QAbstractItemView, QComboBox, QAbstractSpinBox, QTabBar)):
            continue
        if isinstance(w, ElidedLabel):
            if w.toolTip() != w.full_text():
                bad.append(f"ElidedLabel {w.full_text()[:40]!r}: tooltip differs")
            continue
        if isinstance(w, QLabel):
            if w.wordWrap() or w.pixmap() is not None and not w.pixmap().isNull() or not w.text():
                continue
        elif not isinstance(w, (QAbstractButton, QComboBox)):
            continue
        need = w.sizeHint().width()
        text = w.currentText() if isinstance(w, QComboBox) else w.text()
        if w.width() < need - 2:
            bad.append(f"{type(w).__name__} {text[:40]!r}: {w.width()} < {need}")
        elif not _inside(w, page, (QScrollArea,)):               # a scrolling panel may hide part of it: never clip
            shown = w.visibleRegion().boundingRect()
            if shown.width() < w.width() - 2 or shown.height() < w.height() - 2:
                bad.append(f"{type(w).__name__} {text[:40]!r}: cut by its panel ({shown.width()}x{shown.height()} "
                           f"of {w.width()}x{w.height()})")
    return bad


def _views(win, key):
    tabs = getattr(win.pages[key], "tabs", None) if key in ("fit", "maps") else None
    return [None] if tabs is None else [i for i in range(tabs.count()) if tabs.isTabVisible(i)]


@pytest.fixture
def demo(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    yield win
    win.session._save_timer.stop()
    win.close()


def test_minimum_size(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    assert win.minimumSize() == QSize(1200, 760)
    win.close()


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("advanced", [False, True], ids=["easy", "advanced"])
def test_nothing_clipped(qapp, demo, size, advanced):
    from demo_window import pump
    from aurora.app.main import PERSPECTIVES
    win = demo
    mode.set_advanced(advanced)
    win.resize(*size)
    pump(qapp, 5)
    problems = []
    for key, _t in PERSPECTIVES:
        if mode.perspective_hidden(key):
            continue
        for tab in _views(win, key):
            win.show_perspective(key)
            if tab is not None:
                win.pages[key].tabs.setCurrentIndex(tab)
            pump(qapp, 5)
            problems += [f"{key}[{tab}] {p}" for p in clipped(win.pages[key])]
    if advanced and win.reduce._cards:                  # the per-scan card is a two-column grid that fits 420 px
        # 420 px with Segoe UI / SF Pro; DejaVu Sans (Linux) is about 5 % wider (423 px measured on ubuntu-24.04)
        limit = 420 if sys.platform in ("win32", "darwin") else 440
        assert win.reduce._cards[0].minimumSizeHint().width() <= limit
    mode.set_advanced(False)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
@pytest.mark.parametrize("advanced", [False, True], ids=["easy", "advanced"])
def test_preview_height_at_minimum_size(qapp, tmp_path, size, advanced):
    """The zone previews keep a usable height down to the smallest window, in both modes: Reduce's plots at least
    MIN_PLOT_PX (180 px) with the fluorescence panel at least MIN_TOP_PX (110 px), the controls above them one row; the
    Survey's Zone preview with its XRF emission and its XAS at least 120 px each, the card whole on screen (the
    column scrolls below it, if anything)."""
    from PySide6.QtCore import QPoint
    from aurora.app.preview import MIN_PLOT_PX, MIN_TOP_PX
    from aurora.app.zone_preview import MIN_PANEL_PX
    from aurora.core.model import Project
    from wp3_helpers import close, open_window, pump, qdrag
    from zone_helpers import sample_box, survey_project
    mode.set_advanced(advanced)
    win, sid = open_window(qapp, Project.discover(survey_project(tmp_path / "bt")), size=size)
    try:
        sd = win.session.stack_loaded(sid)
        win.show_perspective("survey")
        pump(qapp, 5)
        sv = win.survey
        sv.set_tool("rect+")
        x0, x1, y0, y1 = sample_box(sd)
        qdrag(qapp, sv.map, [(x0, y0), ((x0 + x1) / 2, (y0 + y1) / 2), (x1, y1)])
        pump(qapp, 10)
        zp = sv.zone_preview
        assert zp.xas.isVisible() and zp.emission.isVisible()
        assert zp.emission.plot.height() >= MIN_PANEL_PX and zp.xas.gl.height() >= MIN_PANEL_PX
        bottom = zp.mapTo(sv.right_scroll.viewport(), QPoint(0, zp.height())).y()
        assert bottom <= sv.right_scroll.viewport().height() + 1, (bottom, sv.right_scroll.viewport().height())
        assert sv.map.height() >= 300 and sv.right_scroll.width() >= 400
        win.show_perspective("reduce")
        pump(qapp, 10)
        rd = win.reduce
        pv = rd.preview
        assert pv.gl.height() >= MIN_PLOT_PX and pv.top.height() >= MIN_TOP_PX - 0.5, (pv.gl.height(), pv.top.height())
        assert pv.readout.y() > pv.gl.y()                               # the readout under the plots
        if advanced:
            assert pv.show_btn.isVisible() and pv.all_scans.isVisible()
            assert abs(pv.show_btn.geometry().center().y() - pv.all_scans.geometry().center().y()) <= 4   # one row
        shown = rd.preview_card.visibleRegion().boundingRect()
        assert shown.height() >= rd.preview_card.height() - 2           # never cut by its panel
    finally:
        mode.set_advanced(False)
        close(win)
