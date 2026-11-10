"""No clipped control at 1200 x 760 or 1500 x 940: with the demo loaded, in Easy and in Advanced mode, for every
perspective and every Fit and Maps tab, every visible button and combo box and every one-line label gets at least its
size hint (2 px tolerance), or is an ElidedLabel that keeps its full text in the tooltip. Once more at the Larger text
size at 1200 x 760. A page that scrolls (PageScroll) may hide part of a control below the fold, never cut it by its
card: there the control must lie inside every panel around it.

Offscreen Qt measures text only with real fonts: conftest points QT_QPA_FONTDIR at the system fonts folder, and this
module skips when no font is available (the metrics would be meaningless)."""
import sys

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QPoint, QRect, QSize  # noqa: E402
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


def _in_page_scroll(w: QWidget, page: QWidget):
    """The content widget of the PageScroll around `w` (None when there is none, or an inner scroll area first)."""
    from aurora.app.widgets import PageScroll
    p = w.parentWidget()
    while p is not None and p is not page:
        if isinstance(p, PageScroll):
            return p.widget()
        if isinstance(p, QScrollArea):
            return None
        p = p.parentWidget()
    return None


def _cut_by_a_panel(w: QWidget, top: QWidget) -> bool:
    """Whether a panel between `w` and `top` (the scrolling page) cuts `w` (2 px tolerance)."""
    rect = QRect(w.mapTo(top, QPoint(0, 0)), w.size())
    p = w.parentWidget()
    while p is not None and p is not top:
        if not QRect(p.mapTo(top, QPoint(0, 0)), p.size()).adjusted(-2, -2, 2, 2).contains(rect):
            return True
        p = p.parentWidget()
    return False


# event-loop passes before a page is measured: a wrapped label that grows refits its card (fit_height, deferred), and
# the new minimum climbs one widget level per pass to the scrolling page (about seven levels in Fit)
SETTLE = 25


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
        content = _in_page_scroll(w, page)
        if w.width() < need - 2:
            bad.append(f"{type(w).__name__} {text[:40]!r}: {w.width()} < {need}")
        elif content is not None:                                # the page scrolls: no card may cut the control
            if _cut_by_a_panel(w, content):
                bad.append(f"{type(w).__name__} {text[:40]!r}: cut by its panel")
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
    pump(qapp, SETTLE)
    problems = []
    for key, _t in PERSPECTIVES:
        if mode.perspective_hidden(key):
            continue
        for tab in _views(win, key):
            win.show_perspective(key)
            if tab is not None:
                win.pages[key].tabs.setCurrentIndex(tab)
            pump(qapp, SETTLE)
            problems += [f"{key}[{tab}] {p}" for p in clipped(win.pages[key])]
    if advanced and win.reduce._cards:                  # the per-scan card is a two-column grid that fits 420 px
        # 420 px with Segoe UI / SF Pro; DejaVu Sans (Linux) is about 5 % wider (423 px measured on ubuntu-24.04)
        limit = 420 if sys.platform in ("win32", "darwin") else 440
        assert win.reduce._cards[0].minimumSizeHint().width() <= limit
    mode.set_advanced(False)
    assert not problems, "\n".join(problems)


def test_nothing_clipped_at_the_larger_text_size(qapp, demo):
    """View > Text size > Larger at the smallest window: the pages scroll, no control is cut or squeezed."""
    from demo_window import pump
    from aurora.app.main import PERSPECTIVES
    win = demo
    win.set_text_size("larger")
    problems = []
    try:
        for advanced in (False, True):
            mode.set_advanced(advanced)
            win.resize(1200, 760)
            pump(qapp, SETTLE)
            for key, _t in PERSPECTIVES:
                if mode.perspective_hidden(key):
                    continue
                for tab in _views(win, key):
                    win.show_perspective(key)
                    if tab is not None:
                        win.pages[key].tabs.setCurrentIndex(tab)
                    pump(qapp, SETTLE)
                    problems += [f"{'adv' if advanced else 'easy'} {key}[{tab}] {p}" for p in clipped(win.pages[key])]
    finally:
        win.set_text_size("normal")
        mode.set_advanced(False)
    assert not problems, "\n".join(problems)
