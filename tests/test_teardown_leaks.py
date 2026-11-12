"""A destroyed main window leaves nothing alive. Two PySide6 6.12 behaviours kept the Python side of every window a
test destroyed (its perspectives, pyqtgraph's parentless menus: about 1 500 widgets each) alive for the rest of the
run, so each later style-sheet change re-polished all of them and the full suite slowed from minutes to hours:

- QTimer.singleShot(ms, context, fn) keeps fn for ever when the context is destroyed before the timer fires;
  widgets.call_later(context, fn, ms) is the same call without that leak.
- A QUndoCommand pushed on a QUndoStack is owned by the stack, and PySide holds one reference to its Python side that
  the garbage collector cannot see: a strong reference from the command back to the session made the session and its
  window uncollectable. SnapshotCommand holds the session weakly.
"""
import gc
import time
import weakref

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from aurora.core import testdata  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from wp2_helpers import pump, sid_of, window  # noqa: E402

SID_A = sid_of(testdata.STACK)


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _destroy(qapp, w) -> None:
    w.hide()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


def _top_level_count() -> int:
    gc.disable()                                   # the wrappers are made with the collector paused (as nav does)
    try:
        return len(QApplication.topLevelWidgets())
    finally:
        gc.enable()


class _Probe(QWidget):
    def __init__(self, ran):
        super().__init__()
        self.ran = ran

    def note(self):
        self.ran.append(1)


def test_call_later_runs_and_lets_a_destroyed_context_go(qapp):
    from aurora.app.widgets import call_later
    ran = []
    w = _Probe(ran)
    call_later(w, w.note)
    call_later(w, lambda: w.note(), 5)
    deadline = time.monotonic() + 10
    while len(ran) < 2 and time.monotonic() < deadline:
        QTest.qWait(10)
    QTest.qWait(30)
    assert ran == [1, 1]                           # both ran, once each
    gone = _Probe(ran)
    ref = weakref.ref(gone)
    call_later(gone, gone.note)                    # destroyed before the timer fires
    call_later(gone, lambda: gone.note(), 1500)
    _destroy(qapp, gone)
    del gone
    gc.collect()
    assert ref() is None, "call_later kept its destroyed context alive"
    pump(qapp, 3)
    assert ran == [1, 1]                           # and never ran


def test_a_destroyed_window_with_undo_steps_is_freed(qapp, fake_project):
    before = _top_level_count()
    win = window(qapp, Project.discover(fake_project))
    ses = win.session
    sd = ses.stack(SID_A)
    ses.touch()
    ses.select_stack(SID_A)
    pump(qapp)
    x0, x1, y0, y1 = sd.bbox
    win.reduce.map.set_tool("rect+")
    win.reduce.map.rect_drawn.emit(x0, (x0 + x1) / 2, y0, y1)
    pump(qapp)
    assert ses.undo_stack.count() == 1             # one SnapshotCommand owned by the stack
    ses.save()
    ses.set_project(Project.discover(fake_project))  # a project change clears the history: the stack deletes it
    sd = ses.stack(SID_A)
    ses.touch()
    ses.select_stack(SID_A)
    pump(qapp)
    win.reduce.map.rect_drawn.emit(x0, (x0 + x1) / 2, y0, y1)
    pump(qapp)
    assert ses.undo_stack.count() == 1
    for key in ("report", "compare", "check"):     # pages whose updates defer a refit to the event loop
        win.show_perspective(key)
    win.resize(1300, 800)                          # resizeEvent defers refits too; destroyed before they run
    ref, ses_ref = weakref.ref(win), weakref.ref(ses)
    ses._save_timer.stop()
    win.close()
    _destroy(qapp, win)
    del win, ses, sd
    gc.collect()
    assert ref() is None and ses_ref() is None, "the destroyed window is still alive"
    assert _top_level_count() <= before, "the destroyed window left top-level widgets behind"
