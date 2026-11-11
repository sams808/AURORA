"""Small shared widgets and helpers.

- Card(title, help_anchor=...): rounded panel; with an anchor it carries a '?' button that opens the manual there.
- Chip: a coloured status word.
- Worker: fn(progress) off the GUI thread, with cancel(), and failures logged and shown in the error dialog.
- TaskBar: the progress strip of a perspective (bar, message, elapsed time, Cancel; Esc cancels).
- EmptyState: a small card centred over a panel that says what to do next, with one or two buttons.
- FlowLayout: a row of controls that wraps instead of clipping. ElidedLabel: a one-line label that elides.
- TermLabel: a word-wrapped caption whose glossary words are links ('{{i0|beam intensity (I0)}}'): hovering says what
  the word means, a click opens Help > Glossary there.
- plain_error(text_or_exc): a failure in plain words (no exception name, no [Errno n], no doubled backslash).
- page_scroll(widget): a page that scrolls vertically instead of squeezing its cards; fit_height(panel): a panel
  never lower than its word-wrapped rows need at its width; size_to_current_tab(tabs); fit_screen(dialog, w, h).
- set_enabled(widget, on, reason): disabled actions say why in their tooltip.
- default_dir / remember_dir / notify_saved / guarded_write: file dialogs that start in a sensible folder, exports that
  say what they wrote, and write errors shown as one calm dialog.
"""
from __future__ import annotations

import html
import logging
import os
import re
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QAbstractScrollArea, QFrame, QHBoxLayout, QLabel, QLayout, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStyle, QStyleOption, QToolButton,
                               QVBoxLayout, QWidget)

from aurora.core.cancel import CancelToken, Cancelled, cancellable

log = logging.getLogger("aurora.app")


# ----------------------------------------------------------------------------- cards
class Card(QFrame):
    """White rounded panel with a title. help_anchor: a MANUAL.md anchor; a small '?' then opens the manual there."""

    def __init__(self, title: str = "", parent: QWidget | None = None, help_anchor: str | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.title = ElidedLabel(title, Qt.ElideRight)
        self.title.setObjectName("cardtitle")
        top.addWidget(self.title)
        top.addStretch(1)
        self.header = QHBoxLayout()            # callers add their header widgets here (right-aligned)
        self.header.setSpacing(6)
        top.addLayout(self.header)
        self._top = top
        self.body.addLayout(top)
        self.help_anchor: str | None = None
        self.help_btn: QToolButton | None = None
        if help_anchor:
            self.set_help_anchor(help_anchor)

    def fill_header(self, w: QWidget) -> None:
        """Put `w` right after the title, taking the free width of the title row (a path or a hint line)."""
        self._top.insertWidget(1, w, 1)
        self._top.setStretch(2, 0)

    def set_title(self, text: str) -> None:
        self.title.setText(text)
        if self.help_btn is not None:
            self.help_btn.setAccessibleName(f"Help on {text}")
            self.help_btn.setToolTip(f"Open the manual at '{text}' (F1 opens the section of the page)")

    def set_help_anchor(self, anchor: str | None) -> None:
        self.help_anchor = anchor or None
        if self.help_anchor and self.help_btn is None:
            b = QToolButton(self)
            b.setObjectName("cardhelp")
            b.setText("?")
            b.setProperty("aurora_help_button", True)
            b.setCursor(Qt.PointingHandCursor)
            b.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            b.clicked.connect(self.open_help)
            self._top.addWidget(b)
            self.help_btn = b
            self.set_title(self.title.text())
        if self.help_btn is not None:
            self.help_btn.setVisible(bool(self.help_anchor))

    def open_help(self) -> None:
        from aurora.app.help import open_help
        if self.help_anchor:
            open_help(self, "MANUAL.md", self.help_anchor)


class Chip(QLabel):
    def __init__(self, text: str, kind: str = "other", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setProperty("chip", kind if kind else "other")   # coloured by the app style sheet: follows the theme
        self.setAlignment(Qt.AlignCenter)


# ----------------------------------------------------------------------------- background work
class _FailureReporter(QObject):
    """Lives in the GUI thread (created there with the worker): shows a failed job in the error dialog."""

    def __init__(self, label: str, parent: QObject | None = None):
        super().__init__(parent)
        self.label = label

    @Slot(str)
    def report(self, details: str) -> None:
        from aurora.app import crash
        last = details.strip().splitlines()[-1] if details.strip() else "unknown error"
        crash.show_error(f"AURORA: {self.label} failed", last, details)


class Worker(QThread):
    """Run fn(progress) off the GUI thread; progress(i, n, message) raises Cancelled once cancel() was called.

    Connect done / failed / cancelled to slots of GUI-thread QObjects with Qt.QueuedConnection (a plain function would
    run on this thread). A failure is logged ('job <label> failed' with the traceback) and, unless report=False, shown
    once in the error dialog (crash.show_error); a cancel is no error and opens no dialog."""
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)
    cancelled = Signal()

    def __init__(self, fn, label: str = "", parent: QObject | None = None, report: bool = True):
        if isinstance(label, QObject) and parent is None:      # older form Worker(fn, parent)
            label, parent = "", label
        super().__init__(parent)
        self._fn = fn
        self.label = str(label or getattr(fn, "__name__", "") or "job")
        self.token = CancelToken()
        self.state = "idle"
        if report:
            self._reporter = _FailureReporter(self.label, self)
            self.failed.connect(self._reporter.report, Qt.QueuedConnection)

    def cancel(self) -> None:
        self.token.cancel()

    @property
    def is_cancelled(self) -> bool:
        return self.token.cancelled

    PROGRESS_EVERY_S = 0.05

    def run(self) -> None:
        self.state = "running"
        last = [-1.0]

        def emit(i, n, msg="") -> None:
            # at most 20 a second, plus the first and the last: every signal costs the GUI thread a queued event, and
            # a job that reports each of thousands of steps (MCR-ALS) would starve the event loop behind them
            now = time.monotonic()
            if now - last[0] >= self.PROGRESS_EVERY_S or int(i) >= int(n) - 1:
                last[0] = now
                self.progress.emit(int(i), int(n), str(msg))
        cb = cancellable(emit, self.token)              # the cancel check still runs at every step
        try:
            result = self._fn(cb)
        except Cancelled:
            self.state = "cancelled"
            self.cancelled.emit()
            return
        except Exception:                       # noqa: BLE001  every failure is logged and reported
            log.exception("job %s failed", self.label)
            self.state = "failed"
            self.failed.emit(traceback.format_exc())
            return
        self.state = "done"
        self.done.emit(result)


class TaskBar(QFrame):
    """The progress strip of a perspective: a bar, the job's message, the elapsed time and Cancel (Esc). Hidden while
    nothing runs; after a cancel it reads 'cancelled', after a failure the short error line."""

    def __init__(self, parent: QWidget | None = None, host: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("taskbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setMaximumWidth(260)
        self.bar.setAccessibleName("progress of the running task")
        self.message = ElidedLabel("", Qt.ElideMiddle)
        self.message.setObjectName("muted")
        self.elapsed = QLabel("")
        self.elapsed.setObjectName("muted")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Stop the running task (Esc). Nothing half-written is kept.")
        self.cancel_btn.setAccessibleName("Cancel the running task")
        self.cancel_btn.clicked.connect(self.cancel)
        lay.addWidget(self.bar)
        lay.addWidget(self.message, 1)
        lay.addWidget(self.elapsed)
        lay.addWidget(self.cancel_btn)
        self._job = None
        self._t0 = 0.0
        self._clock = QTimer(self)
        self._clock.setInterval(500)
        self._clock.timeout.connect(self._tick)
        self._fade = QTimer(self)
        self._fade.setSingleShot(True)
        self._fade.setInterval(6000)
        self._fade.timeout.connect(self._fade_out)
        self.esc_action = None
        owner = host or parent
        if owner is not None:
            from aurora.app import shortcuts
            self.esc_action = shortcuts.add(owner, "Cancel the running task", "Esc", self.cancel, section="General")
            self.esc_action.setEnabled(False)
        self.hide()

    def text(self) -> str:
        return self.message.text()

    @property
    def job(self):
        return self._job

    def attach(self, job) -> None:
        """Follow a jobs.Job: progress, message, elapsed time, Cancel; hidden again when it is done."""
        if job is self._job:
            return
        self._detach()
        self._job = job
        job.progressed.connect(self._progress)
        job.ended.connect(self._ended)
        self._t0 = time.monotonic()
        self._fade.stop()
        self.bar.show()
        self.bar.setRange(0, 0)
        self.message.setText(f"{job.label} …" if job.label else "working …")
        self.elapsed.setText("")
        self.cancel_btn.show()
        self.cancel_btn.setEnabled(True)
        if self.esc_action is not None:
            self.esc_action.setEnabled(True)
        self._clock.start()
        self.show()
        if not job.is_running():
            self._ended(job.state)

    def _detach(self) -> None:
        job = self._job
        self._job = None
        if job is not None:
            try:
                job.progressed.disconnect(self._progress)
                job.ended.disconnect(self._ended)
            except (RuntimeError, TypeError):
                pass

    def cancel(self) -> None:
        if self._job is not None and self._job.is_running():
            self._job.cancel()
            self.message.setText("cancelling …")
            self.cancel_btn.setEnabled(False)

    @Slot(int, int, str)
    def _progress(self, i: int, n: int, msg: str) -> None:
        self.bar.setRange(0, max(int(n), 1))
        self.bar.setValue(int(i))
        if msg:
            self.message.setText(msg)

    def _tick(self) -> None:
        s = int(time.monotonic() - self._t0)
        self.elapsed.setText(f"{s // 60}:{s % 60:02d}")

    @Slot(str)
    def _ended(self, state: str) -> None:
        job = self._job
        self._clock.stop()
        if self.esc_action is not None:
            self.esc_action.setEnabled(False)
        self.cancel_btn.hide()
        self._detach()
        if state == "done":
            self.hide()
            return
        self.bar.hide()
        if state == "cancelled":
            self.message.setText("cancelled")
            self._fade.start()
        else:
            err = getattr(job, "error", "") or ""
            last = err.strip().splitlines()[-1] if err.strip() else "unknown error"
            self.message.setText(f"failed: {last}")

    def _fade_out(self) -> None:
        if self._job is None:
            self.hide()


# ----------------------------------------------------------------------------- empty states
# the HELP entry of an empty-state action: its tooltip says what the button does, as on the Home page (the panel's
# sentence is already on the card)
EMPTY_HELP = {"Open project…": "home.open", "Open the demo beamtime": "home.demo", "Read the tutorials": "home.tutorials",
              "Retry": "home.retry", "Add saved spectra…": "compare.add_folder", "References…": "compare.references",
              "Propose zones": "reduce.propose", "Load hypercube": "maps.load"}


class EmptyState(QFrame):
    """A small card centred over its parent panel: an icon, one sentence and one or two buttons (the next step).

    buttons: [(text, callable)] or [(text, callable, advanced)] where advanced buttons are hidden in Easy mode (an
    action that needs a hidden control is hidden as well). The owner shows or hides it (update_actions)."""

    def __init__(self, text: str = "", buttons=None, icon: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("emptystate")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._icon_name = icon
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignHCenter)
        lay.addWidget(self.icon)
        self.label = QLabel(text)
        self.label.setObjectName("emptytext")
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignHCenter)
        lay.addWidget(self.label)
        self.row = QHBoxLayout()
        self.row.setSpacing(8)
        lay.addLayout(self.row)
        self.buttons: list[QPushButton] = []
        self._advanced: list[QPushButton] = []
        if parent is not None:
            parent.installEventFilter(self)
        from aurora.app.mode import on_mode_changed
        from aurora.app.theme import on_theme_changed
        on_mode_changed(self, self._mode, call_now=False)
        on_theme_changed(self, lambda _s: self._paint_icon())
        self.set_content(text, buttons or [])
        self.hide()

    def text(self) -> str:
        return self.label.text()

    def set_content(self, text: str, buttons=()) -> None:
        """Change the sentence and the buttons (the panel keeps its place)."""
        self.label.setText(text)
        for b in self.buttons:
            self.row.removeWidget(b)
            b.deleteLater()
        self.buttons, self._advanced = [], []
        while self.row.count():
            self.row.takeAt(0)
        self.row.addStretch(1)
        for k, spec in enumerate(buttons):
            label, fn = spec[0], spec[1]
            advanced = bool(spec[2]) if len(spec) > 2 else False
            b = QPushButton(label, self)
            if k == 0:
                b.setObjectName("primary")
            key = EMPTY_HELP.get(label) or ("check.run" if label.startswith("Run the checks on") else "")
            if key:
                from aurora.app.help_text import explain
                explain(b, key)
            elif label.startswith("Go to "):
                b.setToolTip(f"Open the {label[len('Go to '):]} perspective")
            b.setAccessibleName(label.rstrip("…").strip())
            b.clicked.connect(lambda _=False, f=fn: f())
            self.row.addWidget(b)
            self.buttons.append(b)
            if advanced:
                self._advanced.append(b)
        self.row.addStretch(1)
        self._paint_icon()
        from aurora.app.mode import is_advanced
        self._mode(is_advanced())
        self._place()

    def _mode(self, adv: bool) -> None:
        for b in self._advanced:
            b.setVisible(adv)

    def _paint_icon(self) -> None:
        if not self._icon_name:
            self.icon.hide()
            return
        from aurora.app.icons import icon as make_icon
        from aurora.app.theme import TOKENS
        self.icon.setPixmap(make_icon(self._icon_name, TOKENS["muted"], 28).pixmap(28, 28))
        self.icon.show()

    def set_shown(self, on: bool) -> None:
        if on:
            self._place()
            self.show()
            self.raise_()
        else:
            self.hide()
        self._cover_hints()

    def _cover_hints(self) -> None:
        """The plot hints under the card would peek out around it ('...rget and components, then Run LCF'): hidden
        while the card is shown, back when it goes."""
        p = self.parentWidget()
        if p is None:
            return
        import pyqtgraph as pg
        for plot in p.findChildren(pg.PlotWidget):
            hint = getattr(plot.getPlotItem(), "_aurora_hint", None)
            if hint is not None:
                hint.setVisible(not covered(plot))

    def _place(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        # over a table or a list: below its header row (the card hid the column titles), inside the viewport
        area = p.viewport().geometry() if isinstance(p, QAbstractScrollArea) else p.rect()
        w = min(max(320, self.sizeHint().width()), max(200, area.width() - 24))
        h = self.heightForWidth(w) if self.hasHeightForWidth() else self.sizeHint().height()
        h = max(h, self.sizeHint().height())
        self.setGeometry(area.x() + max(0, (area.width() - w) // 2),
                         area.y() + max(0, min((area.height() - h) // 3, 160)), w, h)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.parentWidget() and ev.type() in (QEvent.Resize, QEvent.Show, QEvent.LayoutRequest):
            self._place()
            if self.isVisible():
                self.raise_()
        return False

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._place()
        self.raise_()


# ----------------------------------------------------------------------------- layout helpers
class FlowLayout(QLayout):
    """A row of controls that wraps onto the next line when the panel is narrow (never clips)."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6, margin: int = 0):
        super().__init__(parent)
        self._items = []
        self._space = spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item) -> None:
        self._items.append(item)

    def addSpacing(self, _px: int) -> None:          # API compatibility with QHBoxLayout rows (no fixed gaps)
        pass

    def addStretch(self, _s: int = 0) -> None:       # ditto: a flow row has no stretch
        pass

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self) -> QSize:
        w = sum(it.sizeHint().width() for it in self._items if not it.isEmpty())
        w += self._space * max(0, sum(1 for it in self._items if not it.isEmpty()) - 1)
        h = max((it.sizeHint().height() for it in self._items if not it.isEmpty()), default=0)
        m = self.contentsMargins()
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:
        size = QSize()
        for it in self._items:
            if not it.isEmpty():
                size = size.expandedTo(it.minimumSize()).expandedTo(QSize(it.sizeHint().width(), 0))
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _layout(self, rect: QRect, test_only: bool) -> int:
        """Place the items line by line; each item is centred vertically on its line (labels sit level with their
        fields). Returns the height used."""
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        lines: list[list] = [[]]
        x = eff.x()
        for it in self._items:
            if it.isEmpty():
                continue
            w = it.sizeHint().width()
            if lines[-1] and x + w > eff.right() + 1:
                lines.append([])
                x = eff.x()
            lines[-1].append(it)
            x += w + self._space
        y = eff.y()
        for line in lines:
            if not line:
                continue
            line_h = max(it.sizeHint().height() for it in line)
            x = eff.x()
            for it in line:
                hint = it.sizeHint()
                if not test_only:
                    it.setGeometry(QRect(QPoint(x, y + (line_h - hint.height()) // 2), hint))
                x += hint.width() + self._space
            y += line_h + self._space
        return max(0, y - self._space) - rect.y() + m.bottom()


def pair(*widgets: QWidget, spacing: int = 6) -> QWidget:
    """A label and its field (or fields) kept together on one line of a wrapping row."""
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        lay.addWidget(x)
    return w


def overlay_host(widget: QWidget) -> QWidget:
    """A plain container around `widget` (a QSplitter takes every child widget as a pane, so an EmptyState is put
    over this host instead)."""
    host = QWidget()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget)
    return host


class PageScroll(QScrollArea):
    """A frameless scroll area that scrolls vertically only. It is never narrower than its page's minimum width (a
    plain QScrollArea would let a splitter cut the page on the right) and passes that minimum on when it changes."""

    def __init__(self, widget: QWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidget(widget)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._min_w = -1

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        w = self.widget()
        if w is None:
            return hint
        need = w.minimumSizeHint().width() + self.verticalScrollBar().sizeHint().width() + 2 * self.frameWidth()
        return QSize(max(hint.width(), need), hint.height())

    def event(self, ev) -> bool:
        out = super().event(ev)
        if ev.type() == QEvent.LayoutRequest and self.widget() is not None:
            w = self.widget().minimumSizeHint().width()
            if w != self._min_w:                    # the page's minimum width changed: tell the layout around it
                self._min_w = w
                self.updateGeometry()
        return out


def page_scroll(widget: QWidget) -> QScrollArea:
    """`widget` in a frameless vertical scroll area: at the smallest window or the largest Text size the page scrolls
    instead of squeezing its cards below their minimum (DESIGN.md: panels scroll, never clip; never sideways)."""
    return PageScroll(widget)


def fit_screen(widget: QWidget, width: int, height: int) -> None:
    """widget.resize(width, height), but never larger than the free area of its screen (task bar and title bar
    excluded): a dialog's buttons stay on screen at the smallest display and the largest Text size."""
    from PySide6.QtGui import QGuiApplication
    scr = widget.screen() or QGuiApplication.primaryScreen()
    if scr is not None:
        g = scr.availableGeometry()
        width, height = min(width, g.width() - 16), min(height, g.height() - 48)
    widget.resize(max(200, width), max(200, height))


def size_to_current_tab(tabs) -> None:
    """A QTabWidget as tall as its current tab needs (Qt takes the tallest of all tabs, hidden ones included), so a
    scrolling page around it scrolls only for the tab on screen."""
    own: dict[int, QSizePolicy.Policy] = {}

    def follow(_i: int = 0) -> None:
        for k in range(tabs.count()):
            page = tabs.widget(k)
            sp = page.sizePolicy()
            own.setdefault(id(page), sp.verticalPolicy())
            sp.setVerticalPolicy(own[id(page)] if page is tabs.currentWidget() else QSizePolicy.Ignored)
            page.setSizePolicy(sp)
        tabs.updateGeometry()
    tabs.currentChanged.connect(follow)
    follow()


class _FitHeight(QObject):
    """Keeps a panel's minimum height at what its layout needs at the panel's width (see fit_height)."""

    def __init__(self, panel: QWidget):
        super().__init__(panel)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.refit)
        panel.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.parent() and ev.type() in (QEvent.Resize, QEvent.LayoutRequest, QEvent.Show):
            self._timer.start()                     # deferred: never re-enter the layout pass that sent the event
        return False

    def refit(self) -> None:
        panel = self.parent()
        lay = panel.layout() if panel is not None else None
        if lay is None or panel.width() <= 0:
            return
        w = panel.width()
        need = lay.totalMinimumHeightForWidth(w) if lay.hasHeightForWidth() else lay.totalMinimumSize().height()
        if need > 0 and need != panel.minimumHeight():
            panel.setMinimumHeight(need)


def fit_height(panel: QWidget) -> QWidget:
    """Keep `panel`'s minimum height at the height its rows need at its current width, word-wrapped labels and
    wrapping rows included: Qt counts one line per wrapped label, so a splitter would cut the lines (or print the
    next control over them). Returns the panel."""
    keeper = next((ch for ch in panel.children() if isinstance(ch, _FitHeight)), None) or _FitHeight(panel)
    keeper.refit()                                  # installed once; a later call refits at once
    return panel


class _FitColumns(QObject):
    """Keeps every column of a tree view in view (see fit_columns)."""

    def __init__(self, tree, first_chars: int):
        super().__init__(tree)
        self.first_chars = first_chars
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self.refit)
        tree.installEventFilter(self)
        tree.viewport().installEventFilter(self)
        tree.header().sectionCountChanged.connect(lambda *_a: self._timer.start())

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() in (QEvent.Resize, QEvent.FontChange, QEvent.Show, QEvent.StyleChange):
            self._timer.start()                     # deferred: never re-enter the layout pass that sent the event
        return False

    def schedule(self) -> None:
        """Refit at the next event-loop pass (after a column was hidden or shown)."""
        self._timer.start()

    def refit(self) -> None:
        tree = self.parent()
        head = tree.header()
        cols = [i for i in range(1, head.count()) if not head.isSectionHidden(i)]
        width = tree.viewport().width()
        if not cols or width <= 0:
            return
        digit = tree.fontMetrics().horizontalAdvance("0")
        want = {i: max(tree.sizeHintForColumn(i), head.sectionSizeHint(i)) for i in cols}
        floor = {i: min(want[i], head.sectionSizeHint(i)) for i in cols}       # the header word stays readable
        room = width - self.first_chars * digit - 3 * tree.indentation()       # days > holders > scans
        total = sum(want.values())
        if total > room > 0:                        # shrink the wide columns first, never below their header word
            spare = sum(want[i] - floor[i] for i in cols)
            cut = min(1.0, (total - room) / spare) if spare else 0.0
            want = {i: int(want[i] - cut * (want[i] - floor[i])) for i in cols}
        for i in cols:
            head.resizeSection(i, want[i])


def fit_columns(tree, first_chars: int = 16):
    """Every column of `tree` in view: the first column stretches and keeps room for `first_chars` characters at its
    deepest level; the others take their content width, cut in proportion when the tree is narrow (their text elides
    with the tree's elide mode). Wider fonts (DejaVu Sans on Linux) left the Scan column 36 px with ResizeToContents.
    Call keeper.refit() after filling the tree. Returns the keeper."""
    from PySide6.QtWidgets import QHeaderView
    head = tree.header()
    head.setStretchLastSection(False)
    head.setSectionResizeMode(0, QHeaderView.Stretch)
    for i in range(1, head.count()):
        head.setSectionResizeMode(i, QHeaderView.Interactive)
    keeper = next((ch for ch in tree.children() if isinstance(ch, _FitColumns)), None) or _FitColumns(tree, first_chars)
    keeper.first_chars = first_chars
    return keeper


def breakable(name: str) -> str:
    """A file or scan name that may wrap after its underscores (a zero-width space after each): a word-wrapped label
    never breaks '120000_sample_name_f_k_stack', so its width alone set the page's minimum width."""
    return name.replace("_", "_​")


def flow_row(*widgets: QWidget, spacing: int = 6) -> QWidget:
    """A QWidget holding the widgets in a FlowLayout (a wrapping control row)."""
    w = QWidget()
    lay = FlowLayout(w, spacing=spacing)
    for x in widgets:
        lay.addWidget(x)
    return w


class ElidedLabel(QLabel):
    """A one-line label that elides its text (in the middle by default) when narrow; the full text stays in the
    tooltip and in text()."""

    def __init__(self, text: str = "", mode=Qt.ElideMiddle, parent: QWidget | None = None):
        super().__init__(parent)
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)     # its full width when there is room
        self.setMinimumWidth(40)
        self.setTextFormat(Qt.PlainText)
        self.setText(text)

    def setText(self, text: str) -> None:
        super().setText(text or "")
        self.setToolTip(text or "")

    def full_text(self) -> str:
        return self.text()

    def elided(self) -> bool:
        return self.fontMetrics().horizontalAdvance(self.text()) > self.contentsRect().width()

    def minimumSizeHint(self) -> QSize:
        return QSize(40, super().minimumSizeHint().height())

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        opt = QStyleOption()
        opt.initFrom(self)
        self.style().drawPrimitive(QStyle.PE_Widget, opt, p, self)
        r = self.contentsRect()
        txt = self.fontMetrics().elidedText(self.text(), self._mode, r.width())
        self.style().drawItemText(p, r, int(self.alignment()) | int(Qt.AlignVCenter), self.palette(), self.isEnabled(),
                                  txt, self.foregroundRole())
        p.end()


_TERM = re.compile(r"\{\{([\w-]+)(?:\|(.*?))?\}\}")


def term_markup_plain(markup: str) -> str:
    """'{{i0|beam intensity (I0)}}' -> 'beam intensity (I0)'; '{{tey}}' -> the term's short word."""
    from aurora.glossary import term

    def sub(m) -> str:
        if m.group(2) is not None:
            return m.group(2)
        try:
            return term(m.group(1)).short
        except KeyError:
            return m.group(1)
    return _TERM.sub(sub, markup or "")


def term_markup_keys(markup: str) -> list[str]:
    return [m.group(1) for m in _TERM.finditer(markup or "")]


class TermLabel(QLabel):
    """A word-wrapped caption built from markup '{{key}}' or '{{key|shown words}}' (aurora.glossary keys): each term
    is an underlined link in the link colour. Hovering a link shows 'What is …?' at the cursor; a click (or Tab then
    Enter) opens Help > Glossary at that word. plain_text() is the text without markup (tests, screen readers)."""

    def __init__(self, markup: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._markup = ""
        self.setWordWrap(True)
        self.setTextFormat(Qt.RichText)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.linkHovered.connect(self._hovered)
        self.linkActivated.connect(self._activated)
        from aurora.app.theme import on_theme_changed
        on_theme_changed(self, lambda _s: self._render())
        self.set_markup(markup)

    def set_markup(self, markup: str) -> None:
        self._markup = markup or ""
        self._render()

    def markup(self) -> str:
        return self._markup

    def plain_text(self) -> str:
        return term_markup_plain(self._markup)

    def full_text(self) -> str:
        return self.plain_text()

    def linked_keys(self) -> list[str]:
        return term_markup_keys(self._markup)

    def _render(self) -> None:
        from aurora.app.theme import TOKENS
        out, last = [], 0
        for m in _TERM.finditer(self._markup):
            out.append(html.escape(self._markup[last:m.start()]))
            shown = term_markup_plain(m.group(0))
            out.append(f"<a href=\"glossary:{m.group(1)}\" style=\"color:{TOKENS['link']};text-decoration:underline\">"
                       f"{html.escape(shown)}</a>")
            last = m.end()
        out.append(html.escape(self._markup[last:]))
        super().setText("".join(out).replace("\n", "<br>"))
        self.setAccessibleDescription(self.plain_text())

    def _hovered(self, link: str) -> None:
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QToolTip
        if link.startswith("glossary:"):
            from aurora.glossary import what_is
            try:
                QToolTip.showText(QCursor.pos(), what_is(link.split(":", 1)[1]), self)
            except KeyError:
                pass
        else:
            QToolTip.hideText()

    def _activated(self, link: str) -> None:
        if link.startswith("glossary:"):
            from aurora.app.help import open_glossary
            open_glossary(self, link.split(":", 1)[1])


def fit_to_content(card: QWidget, floor: int = 0) -> None:
    """Give a card in a splitter the height its wrapped lines need at its width (a splitter ignores word wrap, so a
    long caption would otherwise be drawn over the widget below it)."""
    import shiboken6
    if card is None or not shiboken6.isValid(card) or card.width() <= 0:
        return
    lay = card.layout()
    if lay is None:
        return
    need = lay.heightForWidth(card.width()) if lay.hasHeightForWidth() else lay.sizeHint().height()
    need = max(need, floor)
    if need > 0 and need != card.minimumHeight():
        card.setMinimumHeight(need)


def _relative(path: str, root: Path | None) -> str:
    if root is None:
        return path
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return path


def plain_error(err, root: Path | None = None) -> str:
    """A failure in plain words for an Easy-visible text: a protected folder, a refused write, a missing file or a full
    disk by name; anything else its last line without 'module.Class: ' and '[Errno n] '. Paths are shown as written
    (never repr, so no doubled backslashes), relative to `root` when inside it."""
    import errno as _errno
    from aurora.core.io.safety import ProtectedPathError
    if isinstance(err, BaseException):
        exc, text = err, str(err)
    else:
        exc, text = None, str(err or "")
    last = text.strip().splitlines()[-1].strip() if text.strip() else ""
    low = last.lower()
    if isinstance(exc, ProtectedPathError) or "protectedpatherror" in low or "never writes" in low:
        return "this folder is protected: AURORA never writes into raw data or a protected folder"
    if isinstance(exc, PermissionError) or "[errno 13]" in low or "permissionerror" in low or "access is denied" in low \
            or "permission denied" in low:
        return "AURORA may not write here (a read-only folder, or a file open in another program)"
    if isinstance(exc, FileNotFoundError) or "filenotfounderror" in low or "[errno 2]" in low:
        return "the file or folder is missing (moved, or on a share that is not reachable now)"
    if (isinstance(exc, OSError) and getattr(exc, "errno", None) == _errno.ENOSPC) or "[errno 28]" in low \
            or "no space left" in low:
        return "the disk is full"
    msg = re.sub(r"^(?:(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*|\w*(?:Error|Exception|Warning|Interrupt)): ", "", last)
    msg = re.sub(r"\[(?:Errno|WinError) -?\d+\]\s*", "", msg)
    msg = re.sub(r"'([A-Za-z]:\\\\[^']*|/[^']*)'", lambda m: _relative(m.group(1).replace("\\\\", "\\"), root), msg)
    msg = msg.replace("\\\\", "\\").strip().rstrip(".")
    return msg or "unknown error"


def set_enabled(widget: QWidget, on: bool, reason: str = "") -> None:
    """Enable or disable an action; while disabled its tooltip says why ('open a project first')."""
    base = widget.property("aurora_tip")
    if base is None:
        base = widget.toolTip()
        widget.setProperty("aurora_tip", base)
    widget.setEnabled(bool(on))
    if on or not reason:
        widget.setToolTip(base)
    else:
        widget.setToolTip(f"{base}\nNot available: {reason}" if base else f"Not available: {reason}")


# ----------------------------------------------------------------------------- files
# per dialog kind: subfolders of the project tried before the project root (exports go to reports/)
KIND_SUBDIRS = {
    "export": ("reports",), "compare": ("reports",), "fit": ("reports",), "maps": ("reports",), "report": ("reports",),
    "zones": (), "reduced": ("processed/reduced", "processed"), "references": ("references",),
    "config": ("reports",), "csv": ("processed",), "project": (), "sketch": ("sketches",),
}


def _project_root(session=None) -> Path | None:
    """The open project's root from a Session, a Library, a Project or (None) the application's main window."""
    obj = session
    if obj is None:
        from aurora.app import nav
        win = nav.window_of(None)
        obj = getattr(win, "session", None)
    if obj is None:
        return None
    root = getattr(obj, "root", None)
    if root is None:
        proj = getattr(obj, "project", None)
        root = getattr(proj, "root", None)
    return Path(root) if root else None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def default_dir(kind: str, session=None) -> str:
    """The folder a file dialog of this kind starts in: the last one used for this kind (inside the open project, when
    one is open), else the project's reports/ (exports) or matching subfolder, the project root, then ~/Documents."""
    from aurora.app.settings import settings
    root = _project_root(session)
    last = str(settings().value(f"last_dir/{kind}", "") or "")
    if last:
        p = Path(last)
        if p.is_dir() and (root is None or _inside(p, root)):
            return str(p)
    if root is not None:
        for sub in KIND_SUBDIRS.get(kind, ("reports",)):
            d = root / sub
            if d.is_dir():
                return str(d)
        return str(root)
    docs = Path.home() / "Documents"
    return str(docs if docs.is_dir() else Path.home())


def default_path(kind: str, filename: str, session=None) -> str:
    """default_dir(kind) joined with a suggested file name (a dialog never starts at a bare file name)."""
    return str(Path(default_dir(kind, session)) / filename)


def remember_dir(kind: str, path) -> None:
    """Remember the folder of the file (or the folder) just chosen for this dialog kind."""
    if not path:
        return
    from aurora.app.settings import settings
    p = Path(path)
    d = p.parent if p.suffix else p
    s = settings()
    s.setValue(f"last_dir/{kind}", str(d))
    s.sync()


def notify_saved(widget: QWidget | None, paths) -> str:
    """'wrote lcf.csv, lcf.svg, lcf.png → <folder>' in the message line (a QLabel, with an Open folder link) and in
    the status bar of the window. Returns the plain text."""
    ps = [Path(p) for p in paths if p]
    if not ps:
        return ""
    try:                                        # files in several folders (one per sample): the folder holding them all
        folder = Path(os.path.commonpath([str(p.parent) for p in ps]))
    except ValueError:                          # different drives
        folder = ps[0].parent
    plain = f"wrote {', '.join(p.name for p in ps)} → {folder}"
    if isinstance(widget, ElidedLabel):
        widget.setText(plain)
    elif isinstance(widget, QLabel):
        url = QUrl.fromLocalFile(str(folder)).toString()
        widget.setTextFormat(Qt.RichText)
        widget.setOpenExternalLinks(True)
        widget.setText(f"{html.escape(plain)} · <a href=\"{url}\">Open folder</a>")
    win = widget.window() if isinstance(widget, QWidget) else None
    if isinstance(win, QMainWindow):
        win.statusBar().showMessage(plain, 10000)
    log.info("%s", plain)
    return plain


class WriteGuard:
    ok = True
    error: Exception | None = None


def held_file(err: BaseException) -> bool:
    """A refusal of the system on a file (not a folder, not AURORA's protected-folder rule): most often the file is
    open in another program (Excel holds the CSVs it shows)."""
    from aurora.core.io.safety import ProtectedPathError
    if not isinstance(err, PermissionError) or isinstance(err, ProtectedPathError):
        return False
    name = getattr(err, "filename", None)
    return not (name and Path(name).is_dir())


def write_error_text(label: str, err: BaseException, verb: str = "write") -> str:
    """'Could not write lcf.csv: Permission denied. Is it open in another program?': the system's reason without the
    exception's name; a folder that refuses asks whether it is read-only."""
    from aurora.core.io.safety import ProtectedPathError
    reason = (getattr(err, "strerror", None) or str(err) or type(err).__name__).strip().rstrip(".")
    hint = ""
    if held_file(err):
        hint = " Is it open in another program?"
    elif isinstance(err, PermissionError) and not isinstance(err, ProtectedPathError):
        hint = " Is the folder read-only?"
    return f"Could not {verb} {label}: {reason}.{hint}"


@contextmanager
def guarded_write(widget: QWidget | None, label: str):
    """Turn OSError / ValueError raised while writing `label` into one calm dialog:
    'Could not write lcf.csv: Permission denied. Is it open in another program?'. The guard's .ok says whether the
    write went through."""
    g = WriteGuard()
    try:
        yield g
    except (OSError, ValueError) as err:
        g.ok, g.error = False, err
        text = write_error_text(label, err)
        log.warning("%s (%r)", text, err)
        QMessageBox.warning(widget, "Could not write", text)


def covered(widget: QWidget) -> bool:
    """Whether a shown EmptyState lies over `widget` (it is a child of one of the widget's ancestors)."""
    p = widget.parentWidget()
    while p is not None:
        if any(isinstance(e, EmptyState) and not e.isHidden() for e in p.children()):
            return True
        p = p.parentWidget()
    return False


def set_plot_hint(plot, text: str | None) -> None:
    """An empty plot shows a muted, centred sentence instead of bare 0..1 axes; None removes it (after drawing)."""
    import pyqtgraph as pg
    from aurora.app.theme import TOKENS
    item = plot.getPlotItem() if hasattr(plot, "getPlotItem") else plot
    hint = getattr(item, "_aurora_hint", None)
    if not text:
        if hint is not None and hint.scene() is not None:
            item.removeItem(hint)
        for ax in ("left", "bottom"):
            if getattr(item, "_aurora_axes_hidden", False):
                item.showAxis(ax)
        item._aurora_axes_hidden = False
        return
    if hint is None:
        hint = pg.TextItem("", anchor=(0.5, 0.5))
        item._aurora_hint = hint
    hint.setText(text, color=TOKENS["muted"])
    if hint.scene() is None:
        item.addItem(hint, ignoreBounds=True)
    if isinstance(plot, QWidget):
        hint.setVisible(not covered(plot))          # an empty-state card over the panel already says what to do
    hint.setPos(0.5, 0.5)
    for ax in ("left", "bottom"):
        item.hideAxis(ax)
    item._aurora_axes_hidden = True
    item.getViewBox().setRange(xRange=(0, 1), yRange=(0, 1), padding=0, disableAutoRange=True)
