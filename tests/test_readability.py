"""Readability at the smallest window and at the largest Text size, in the demo-loaded window and its dialogs.

- every plot keeps a data area its axes can label (single plots >= 80 px, the Reduce preview's three linked panels
  >= px(48), with tick labels that never touch), word-wrapped text gets the height of its lines, no button or
  one-line label is squeezed below one line, and no page is wider than the space it is shown in: the page scrolls
  vertically instead (DESIGN.md: panels scroll, never clip), at 1200 x 760 and 1500 x 940, Normal and Larger text;
- the Compare stacked plot never prints a trace label over another one;
- the Rules dialog fits a 768 px laptop screen at every Text size, its buttons outside the scrolling editor;
- the Help window's code blocks follow Text size and wrap (no sideways scroll), paragraphs are spaced, Esc closes it;
- empty-state cards sit below a table's header and hide the plot hints they cover; their buttons say what they do;
- the Check I0 list keeps its numbers readable; the report page preview fits the card and zooms; spin boxes inset
  their value like line edits; Import zones rows fit their combo boxes; release notes are styled like the manuals;
- the Home scan tree shows every column at 1200 px; Enter closes About; the first Tab of the welcome page lands on
  Open project…

Offscreen text metrics need real fonts: the module skips without them (as tests/test_layout.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QComboBox, QGraphicsView, QLabel, QScrollArea, QWidget  # noqa: E402

from aurora.app import mode, theme  # noqa: E402

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


def pump(qapp, n: int = 5) -> None:
    for _ in range(n):
        qapp.processEvents()


@pytest.fixture
def demo(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    yield win
    win.set_text_size("normal")
    mode.set_advanced(False)
    win.session._save_timer.stop()
    win.close()


def _inner_scroll(w: QWidget, page: QWidget) -> bool:
    """Inside a scroll area of the page other than the page's own vertical scroll (a list, a table, the scan cards)."""
    from aurora.app.widgets import PageScroll
    p = w.parentWidget()
    while p is not None and p is not page:
        if isinstance(p, QScrollArea) and not isinstance(p, PageScroll):
            return True
        p = p.parentWidget()
    return False


def _views(win, key):
    tabs = getattr(win.pages[key], "tabs", None) if key in ("fit", "maps") else None
    return [None] if tabs is None else [i for i in range(tabs.count()) if tabs.isTabVisible(i)]


def _problems(win, key, where: str) -> list[str]:
    from aurora.app.preview import SparseAxis
    from aurora.app.theme import _plot_items
    from aurora.app.widgets import PageScroll
    page = win.pages[key]
    out = []
    for v in page.findChildren(QGraphicsView):
        if not v.isVisible():
            continue
        for pi in _plot_items(v):
            if not pi.isVisible():
                continue
            h = pi.getViewBox().sceneBoundingRect().height()
            need = theme.px(48) if isinstance(pi.getAxis("left"), SparseAxis) else 80
            if h < need:
                out.append(f"{where}: plot {pi.getAxis('left').labelText!r} data area {h:.0f} px < {need}")
    for w in page.findChildren(QWidget):
        if not w.isVisible() or _inner_scroll(w, page):
            continue
        if isinstance(w, QLabel) and w.text() and (w.pixmap() is None or w.pixmap().isNull()):
            if w.wordWrap():
                need = w.heightForWidth(w.width())
                if w.height() < need - 2:
                    out.append(f"{where}: wrapped label {w.text()[:40]!r} {w.height()} px < {need}")
            elif w.height() < w.fontMetrics().height() - 2:
                out.append(f"{where}: label {w.text()[:40]!r} {w.height()} px < one line")
        elif isinstance(w, (QAbstractButton, QComboBox)) and not isinstance(w.parentWidget(), QComboBox):
            if w.height() < w.minimumSizeHint().height() - 2:
                out.append(f"{where}: {type(w).__name__} {getattr(w, 'text', lambda: '')()[:40]!r} {w.height()} px "
                           f"< {w.minimumSizeHint().height()}")
    for sc in page.findChildren(PageScroll):
        if sc.isVisible() and sc.widget().width() > sc.viewport().width() + 1:
            out.append(f"{where}: the page is {sc.widget().width()} px wide in a {sc.viewport().width()} px view")
    return out


def _label_overlaps(win) -> list[str]:
    """Visible stacked-plot labels whose text lines intersect (the text item's document margins left out)."""
    import pyqtgraph as pg
    rects = []
    for it in win.compare.plot.getPlotItem().items:
        if isinstance(it, pg.TextItem) and it.isVisible():
            m = it.textItem.document().documentMargin()
            rects.append((it.textItem.toPlainText(), it.sceneBoundingRect().adjusted(m, m, -m, -m)))
    out = []
    for i, (ta, ra) in enumerate(rects):
        for tb, rb in rects[i + 1:]:
            inter = ra.intersected(rb)
            if inter.width() > 1 and inter.height() > 1:
                out.append(f"{ta!r} over {tb!r}")
    return out


@pytest.mark.parametrize("text", ["normal", "larger"])
def test_plots_and_text_keep_their_height(qapp, demo, text):
    win = demo
    win.set_text_size(text)
    problems = []
    for size in SIZES:
        for advanced in (False, True):
            mode.set_advanced(advanced)
            win.resize(*size)
            pump(qapp, 10)
            for key in win.pages:
                if mode.perspective_hidden(key):
                    continue
                win.show_perspective(key)
                for tab in _views(win, key):
                    if tab is not None:
                        win.pages[key].tabs.setCurrentIndex(tab)
                    pump(qapp, 8)
                    where = f"{text} {size[0]}x{size[1]} {'adv' if advanced else 'easy'} {key}[{tab}]"
                    problems += _problems(win, key, where)
                    if key == "compare":
                        problems += [f"{where}: label {o}" for o in _label_overlaps(win)]
    assert not problems, "\n".join(problems)


def test_the_preview_ticks_never_touch(qapp):
    """The y axis of the preview's short panels: the digits of neighbouring tick labels never touch (2 px apart at
    least; pyqtgraph printed five labels into 56 px), and at least two labels remain (ranges and heights measured in
    the demo window, Normal and Larger text)."""
    import math
    from PySide6.QtGui import QFont, QFontMetricsF
    from aurora.app.preview import SparseAxis
    measured = {12: ((-0.051, 1.07, 56), (-0.051, 1.07, 61), (-0.052, 1.083, 77), (29.964, 30.003, 56),
                     (29.964, 30.003, 71), (0.0, 1.05, 300), (3.2, 7.9, 60)),
                16: ((-0.051, 1.07, 76), (-0.052, 1.083, 134), (29.964, 30.003, 71), (0.0, 1.05, 300))}
    for px, cases in measured.items():
        ax = SparseAxis("left")
        f = QFont()
        f.setPixelSize(px)
        ax.setTickFont(f)
        line = QFontMetricsF(f).height()
        digits = QFontMetricsF(f).tightBoundingRect("0.75").height() + 2
        for lo, hi, size in cases:
            step = ax.tickSpacing(lo, hi, size)[0][0]
            edge = 0.5 * line * (hi - lo) / size          # pyqtgraph drops a label this close to an end
            shown = [k * step for k in range(math.ceil((lo + edge) / step), math.floor((hi - edge) / step) + 1)]
            assert len(shown) >= 2, (px, lo, hi, size, step, shown)
            assert size * step / (hi - lo) >= digits, (px, lo, hi, size, step)
            texts = ax.tickStrings(shown, 1.0, step)        # each label reads its own value (0.25, not '0.2')
            assert all(abs(float(t) - v) < 1e-9 for t, v in zip(texts, shown)), (step, texts, shown)


@pytest.mark.parametrize("text", ["normal", "larger"])
def test_the_rules_dialog_fits_a_laptop_screen(qapp, tmp_path, text):
    from aurora.app.rules_dialog import RulesDialog
    from aurora.app.session import Session
    from aurora.app.widgets import PageScroll
    theme.apply_theme(qapp, text_size=text)
    try:
        d = RulesDialog(Session())
        # a 1366 x 768 laptop: 728 px above the task bar, less the title bar
        assert d.minimumSizeHint().height() <= 690, d.minimumSizeHint()
        assert isinstance(d.findChild(PageScroll), PageScroll) and d.apply_btn.parentWidget() is d
        g = d.screen().availableGeometry()
        assert d.height() <= g.height() and d.width() <= g.width()
        d.deleteLater()
    finally:
        theme.apply_theme(qapp, text_size="normal")


@pytest.mark.parametrize("text", ["normal", "larger"])
def test_help_code_blocks_follow_text_size_and_wrap(qapp, text):
    from PySide6.QtGui import QFontInfo, QTextFormat
    from PySide6.QtTest import QTest
    from aurora.app.help import DOCS, HelpWindow
    theme.apply_theme(qapp, text_size=text)
    try:
        h = HelpWindow()
        h.show()
        for name, _t, _tip in DOCS:
            h.show_doc(name)
            pump(qapp, 5)
            doc = h.browser.document()
            sizes, gaps = set(), 0
            block = doc.begin()
            while block.isValid():
                if block.blockFormat().hasProperty(QTextFormat.BlockCodeFence) and block.text().strip():
                    it = block.begin()
                    sizes.add(QFontInfo(it.fragment().charFormat().font()).pixelSize())
                elif block.textList() is None and block.text().strip() and block.blockFormat().headingLevel() == 0:
                    gaps += block.blockFormat().bottomMargin() >= theme.px(8)
                block = block.next()
            assert sizes == {theme.px(12)}, (name, sizes)
            assert gaps > 0, name
            assert h.browser.horizontalScrollBar().maximum() == 0, (name, h.browser.horizontalScrollBar().maximum())
        QTest.keyClick(h, Qt.Key_Escape)
        pump(qapp)
        assert not h.isVisible()
        h.deleteLater()
    finally:
        theme.apply_theme(qapp, text_size="normal")


def test_empty_states_sit_below_headers_and_cover_plot_hints(qapp, fake_project):
    from aurora.app.help_text import HELP
    from aurora.app.main import MainWindow
    from aurora.app.shortcuts import native_text
    from aurora.app.widgets import EMPTY_HELP
    from aurora.core.model import Project
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    pump(qapp)
    # Fit with nothing ticked: the empty-state card is up, so the LCF plot's own hint is hidden (it peeked out)
    win.show_perspective("fit")
    pump(qapp)
    hint = win.fit.lcf_plot.getPlotItem()._aurora_hint
    assert win.fit.empty_state.isVisible() and not hint.isVisible()
    # every empty-state button says what it does (the Home tooltips), not the panel's sentence
    for key, page in win.pages.items():
        for es in page.findChildren(type(win.fit.empty_state)):
            for b in es.buttons:
                assert b.toolTip() != es.text(), (key, b.text())
                if b.text() in EMPTY_HELP:
                    assert b.toolTip() == native_text(HELP[EMPTY_HELP[b.text()]][0]), (key, b.text())
    # Check with a project and an empty queue: the card sits below the table's header row
    win.home.set_project(Project.discover(fake_project))
    win.show_perspective("check")
    pump(qapp, 10)
    q, es = win.check.queue, win.check.queue_state
    assert es.isVisible() and es.geometry().top() >= q.viewport().geometry().top()
    win.close()


def test_the_i0_list_keeps_its_numbers(qapp, demo):
    win = demo
    mode.set_advanced(True)
    win.resize(1500, 940)
    win.show_perspective("check")
    pump(qapp, 10)
    lst = win.check.i0_list
    assert lst.count()
    from PySide6.QtWidgets import QStyle
    room = lst.viewport().width() - 2 * lst.style().pixelMetric(QStyle.PM_IndicatorWidth) - 12
    for i in range(lst.count()):
        label, numbers = lst.item(i).text().split("\n")
        assert "level" in numbers and "rough" in numbers and "≤0" in numbers
        assert lst.fontMetrics().horizontalAdvance(numbers) <= room, (numbers, room)


def test_the_page_preview_fits_the_card_and_zooms(qapp, tmp_path):
    from types import SimpleNamespace
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPixmap, QWheelEvent
    from aurora.app.main import MainWindow
    png = tmp_path / "page_001.png"
    pm = QPixmap(910, 1287)
    pm.fill(Qt.white)
    pm.save(str(png))
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    win.show_perspective("report")
    pump(qapp)
    rp = win.report
    assert not rp.zoom.isEnabled()                    # nothing built yet
    rp._result = SimpleNamespace(page_pngs=[png])
    rp._show_page(0)
    pump(qapp)
    vw = rp.scroll.viewport().width()
    assert rp.zoom.isEnabled() and rp.page_view.pixmap().width() == max(200, vw - 16)       # fit width, not 820 px
    assert rp.zoom_pair.isHidden()                    # Easy: the width of the card, no zoom box
    mode.set_advanced(True)
    pump(qapp)
    assert rp.zoom.isVisible()
    rp.zoom.setCurrentIndex(rp.zoom.findData(1.5))
    assert rp.page_view.pixmap().width() == round(910 * 1.5)
    vp = rp.scroll.viewport()
    pos = QPointF(vp.width() / 2, vp.height() / 2)
    ev = QWheelEvent(pos, vp.mapToGlobal(pos), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.ControlModifier,
                     Qt.NoScrollPhase, False)
    qapp.sendEvent(vp, ev)
    assert rp.zoom.currentData() == 2.0               # Ctrl+wheel up: one step larger
    mode.set_advanced(False)
    pump(qapp)
    assert rp.zoom.currentData() == 0.0               # back in Easy: fit width again
    win.close()


def test_spin_boxes_inset_their_value_like_line_edits(qapp):
    from PySide6.QtWidgets import QDoubleSpinBox, QLineEdit, QVBoxLayout
    for text in ("normal", "larger"):
        theme.apply_theme(qapp, text_size=text)
        w = QWidget()
        lay = QVBoxLayout(w)
        s, e = QDoubleSpinBox(), QLineEdit("0.00")
        s.setSuffix(" eV")
        lay.addWidget(s)
        lay.addWidget(e)
        w.show()
        pump(qapp)
        inner = s.findChild(QLineEdit)
        inner.setCursorPosition(0)
        e.setCursorPosition(0)
        x_spin = inner.mapTo(w, inner.cursorRect().topLeft()).x() - s.x()
        x_edit = e.mapTo(w, e.cursorRect().topLeft()).x() - e.x()
        assert abs(x_spin - x_edit) <= 1, (text, x_spin, x_edit)
        w.deleteLater()
    theme.apply_theme(qapp, text_size="normal")
    css = theme.qss()
    for b in ("up-button", "down-button"):            # the arrows sit inside the rounded field border
        rule = css.split(f"QSpinBox::{b}, QDoubleSpinBox::{b} {{", 1)[1].split("}", 1)[0]
        assert "subcontrol-origin: padding" in rule, rule


def test_import_rows_fit_their_combos_and_release_notes_are_styled(qapp, tmp_path):
    from PySide6.QtGui import QColor, QTextFormat
    from aurora.app.import_dialog import ImportDialog
    from aurora.app.updates import UpdateDialog
    from aurora.app.session import ImportRow
    theme.apply_theme(qapp, text_size="larger")
    try:
        d = ImportDialog(tmp_path / "zones_all.json", [ImportRow("N5", "N5", 2, 3, 1), ImportRow("N6", "N6", 1, 1, 0)])
        d.show()
        pump(qapp)
        for r, key in enumerate(("N5", "N6")):
            c = d.combos[key]
            assert c.height() <= d.table.rowHeight(r) and c.visibleRegion().boundingRect().height() == c.height(), key
        d.deleteLater()
    finally:
        theme.apply_theme(qapp, text_size="normal")
    notes = "## Changes\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n```\naurora --selftest\n```\n"
    u = UpdateDialog({"version": "9.9.9", "notes": notes, "page": "https://example.org"})
    block, fenced = u.notes.document().begin(), []
    while block.isValid():
        if block.blockFormat().hasProperty(QTextFormat.BlockCodeFence):
            fenced.append(block.blockFormat().background().color())
        block = block.next()
    assert fenced and all(c == QColor(theme.TOKENS["alt"]) for c in fenced)
    u.deleteLater()


def test_the_home_tree_shows_every_column_at_1200(qapp, demo):
    win = demo
    for advanced in (False, True):
        mode.set_advanced(advanced)
        win.resize(1200, 760)
        win.show_perspective("home")
        pump(qapp, 10)
        tree = win.home.tree
        assert tree.horizontalScrollBar().maximum() == 0, advanced
        assert tree.header().sectionSize(0) >= 120, tree.header().sectionSize(0)


def test_enter_closes_about(qapp, monkeypatch):
    from PySide6.QtTest import QTest
    from aurora.app.about import AboutDialog
    opened = []
    monkeypatch.setattr(AboutDialog, "_licences", lambda self: opened.append(1))
    d = AboutDialog()
    d.show()
    pump(qapp)
    QTest.keyClick(d, Qt.Key_Return)
    pump(qapp)
    assert not opened and not d.isVisible()
    d.deleteLater()


def test_the_first_tab_reaches_the_welcome_card(qapp):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.resize(1300, 820)
    win.show()
    win.activateWindow()
    pump(qapp, 10)
    welcome = win.home.empty_state
    assert welcome.isVisible()
    order = []
    for _ in range(4):
        QTest.keyClick(QApplication.focusWidget() or win, Qt.Key_Tab)
        pump(qapp)
        order.append(QApplication.focusWidget())
    assert order[:3] == welcome.buttons, [getattr(w, "text", lambda: type(w).__name__)() for w in order]
    assert order[3] is win.home.recent_list or not win.home.recent_list.isVisible()
    win.close()
