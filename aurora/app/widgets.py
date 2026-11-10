"""Small shared widgets and helpers.

- Card(title, help_anchor=...): rounded panel; with an anchor it carries a '?' button that opens the manual there.
- Chip: a coloured status word.
- Worker: fn(progress) off the GUI thread, with cancel(), and failures logged and shown in the error dialog.
- TaskBar: the progress strip of a perspective (bar, message, elapsed time, Cancel; Esc cancels).
- EmptyState: a small card centred over a panel that says what to do next, with one or two buttons.
- FlowLayout: a row of controls that wraps instead of clipping. ElidedLabel: a one-line label that elides.
- set_enabled(widget, on, reason): disabled actions say why in their tooltip.
- default_dir / remember_dir / notify_saved / guarded_write: file dialogs that start in a sensible folder, exports that
  say what they wrote, and write errors shown as one calm dialog.
"""
from __future__ import annotations

import html
import logging
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QThread, QTimer, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLayout, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QSizePolicy, QStyle, QStyleOption, QToolButton, QVBoxLayout, QWidget)

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
            b.setToolTip(text)
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

    def _place(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        w = min(max(320, self.sizeHint().width()), max(200, p.width() - 24))
        h = self.heightForWidth(w) if self.hasHeightForWidth() else self.sizeHint().height()
        h = max(h, self.sizeHint().height())
        self.setGeometry(max(0, (p.width() - w) // 2), max(0, min((p.height() - h) // 3, 160)), w, h)

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
    "zones": ("zones_html",), "reduced": ("processed/reduced", "processed"), "references": ("references",),
    "config": ("reports",), "csv": ("processed",), "project": (),
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
    hint.setPos(0.5, 0.5)
    for ax in ("left", "bottom"):
        item.hideAxis(ax)
    item._aurora_axes_hidden = True
    item.getViewBox().setRange(xRange=(0, 1), yRange=(0, 1), padding=0, disableAutoRange=True)
