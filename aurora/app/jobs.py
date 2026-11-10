"""Background jobs of a window: one running job per key, progress, Cancel, results on the GUI thread.

    job = window.jobs.submit("stack:" + sid, lambda progress: session.stack(sid, progress=progress),
                             on_done=self._loaded, label="reading the stack", taskbar=self.taskbar)

- One running job per key ('stack:<sid>', 'cube:<sid>', 'survey:<holder>', 'report', 'discover', 'check:<holder>'):
  a second submit with the same key attaches its callbacks to the running job instead of starting another.
- fn(progress) runs on a Worker thread; progress(i, n, message) raises Cancelled once the job is cancelled.
- on_done(result), on_failed(traceback), on_end(state) are called on the GUI thread: the Job is a GUI-thread QObject
  and the worker's signals reach its slots through Qt.QueuedConnection (never a plain function on the worker thread).
- A failure is logged and shown once in the error dialog (crash.show_error); a cancel opens no dialog. An exception in
  a callback reaches sys.excepthook (one error dialog); only a callback whose Qt owner was deleted is skipped quietly.
- A running QThread is never destroyed: a finished job waits for its thread, and the window keeps (or hands to the
  application) any job still running when it closes.
- A finished job drops its result, function and callbacks once they ran; the 16 most recent stay (state, message,
  error), older ones are deleted with their threads.

jobs.of(widget) is the manager of the widget's main window (window.jobs), or a manager of the widget itself while it
is not inside one (perspectives built alone in tests).
"""
from __future__ import annotations

import gc
import logging
import sys
import time
from collections import deque

import shiboken6
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, Qt, Signal, Slot

from aurora.app.widgets import Worker

log = logging.getLogger("aurora.jobs")
_GC_HOLDS = [0]            # jobs that asked for the cyclic garbage collector to pause (plain int: no Qt object)
GONE = ("already deleted", "has been deleted")      # PySide's RuntimeError texts for a deleted Qt object


def _hold_gc() -> None:
    """matplotlib is not thread-safe: a garbage collection started by the GUI thread while a worker draws figures
    can finalise the worker's matplotlib objects under it (an access violation was seen building a report). Jobs
    that draw (the report) pause the cyclic collector while they run; reference counting still frees memory."""
    if _GC_HOLDS[0] == 0 and gc.isenabled():
        gc.disable()
        _GC_HOLDS[0] = 1
    elif _GC_HOLDS[0]:
        _GC_HOLDS[0] += 1


def _release_gc() -> None:
    if _GC_HOLDS[0]:
        _GC_HOLDS[0] -= 1
        if _GC_HOLDS[0] == 0:
            gc.enable()
            gc.collect()


class Job(QObject):
    progressed = Signal(int, int, str)
    ended = Signal(str)                      # 'done' | 'failed' | 'cancelled'

    def __init__(self, key: str, fn, label: str, manager: "JobManager", report: bool = True, pause_gc: bool = False):
        super().__init__(manager)
        self.pause_gc = pause_gc
        self.key = key
        self.label = label
        self.state = "running"
        self.message = ""
        self.result = None
        self.error = ""
        self.started = time.monotonic()
        self.manager = manager
        self._on_done: list = []
        self._on_failed: list = []
        self._on_end: list = []
        self.worker = Worker(fn, label, self, report=report)
        self.worker.done.connect(self._done, Qt.QueuedConnection)
        self.worker.failed.connect(self._failed, Qt.QueuedConnection)
        self.worker.cancelled.connect(self._cancelled, Qt.QueuedConnection)
        self.worker.progress.connect(self._progress, Qt.QueuedConnection)

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        if self.pause_gc:
            _hold_gc()
        self.worker.start()

    def cancel(self) -> None:
        if self.state == "running":
            self.worker.cancel()
            self.message = "cancelling …"

    def is_running(self) -> bool:
        return self.state == "running"

    def add_callbacks(self, on_done=None, on_failed=None, on_end=None) -> None:
        if on_done is not None:
            self._on_done.append(on_done)
        if on_failed is not None:
            self._on_failed.append(on_failed)
        if on_end is not None:
            self._on_end.append(on_end)

    # ------------------------------------------------------------------ worker signals (GUI thread)
    @Slot(int, int, str)
    def _progress(self, i: int, n: int, msg: str) -> None:
        self.message = msg
        self.progressed.emit(i, n, msg)

    @Slot(object)
    def _done(self, result) -> None:
        self.result = result
        self._finish("done")

    @Slot(str)
    def _failed(self, details: str) -> None:
        self.error = details
        self._finish("failed")

    @Slot()
    def _cancelled(self) -> None:
        self._finish("cancelled")

    def _finish(self, state: str) -> None:
        if self.state != "running":
            return
        self.worker.wait(30000)                 # run() has returned: the thread ends within milliseconds
        if self.pause_gc:
            _release_gc()
        self.state = state
        self.message = {"done": "done", "cancelled": "cancelled"}.get(state, "failed")
        self.manager._release(self)             # before the callbacks: a callback may submit the same key again
        if state == "done":
            calls = [(fn, (self.result,)) for fn in self._on_done]
        elif state == "failed":
            calls = [(fn, (self.error,)) for fn in self._on_failed]
        else:
            calls = []
        for fn, args in calls:
            self._call(fn, *args)
        self.ended.emit(state)
        for fn in self._on_end:
            self._call(fn, state)
        self._let_go()
        self.manager.changed.emit()

    def _let_go(self) -> None:
        """Once its callbacks ran, a finished job keeps its state, message and error text only: the result, the
        function (and what it captured: stacks, cubes, the session) and the callbacks would stay alive with it."""
        self.result = None
        self.worker._fn = None
        self._on_done.clear()
        self._on_failed.clear()
        self._on_end.clear()

    @staticmethod
    def _call(fn, *args) -> None:
        try:
            fn(*args)
        except RuntimeError as err:
            if not any(s in str(err) for s in GONE):    # a bug (numpy, a dict changed during iteration ...): reported
                sys.excepthook(*sys.exc_info())
                return
            log.info("job callback skipped: %s", err)   # the callback's Qt owner is gone (a closed window)
        except Exception:                       # noqa: BLE001  reported like an exception in any slot
            sys.excepthook(*sys.exc_info())


class JobManager(QObject):
    """The background jobs of one window (window.jobs)."""
    changed = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._jobs: dict[str, Job] = {}
        self._finished: deque = deque(maxlen=16)        # recently finished jobs stay valid for their callers

    def submit(self, key: str, fn, on_done=None, on_failed=None, label: str = "", taskbar=None, on_end=None,
               report: bool = True, pause_gc: bool = False) -> Job:
        job = self._jobs.get(key)
        new = job is None or not job.is_running()
        if new:
            job = Job(key, fn, label or key, self, report=report, pause_gc=pause_gc)
            self._jobs[key] = job
        job.add_callbacks(on_done, on_failed, on_end)
        if taskbar is not None:
            taskbar.attach(job)
        if new:
            log.info("job %s started (%s)", key, job.label)
            job.start()
            self.changed.emit()
        return job

    def job(self, key: str) -> Job | None:
        return self._jobs.get(key)

    def running(self) -> list[str]:
        return [k for k, j in self._jobs.items() if j.is_running()]

    def idle(self) -> bool:
        return not self.running()

    def cancel(self, key: str) -> bool:
        job = self._jobs.get(key)
        if job is not None and job.is_running():
            job.cancel()
            return True
        return False

    def cancel_all(self) -> int:
        n = 0
        for job in list(self._jobs.values()):
            if job.is_running():
                job.cancel()
                n += 1
        return n

    def wait_idle(self, timeout_ms: int = 5000) -> bool:
        """Process events until every job has ended (its callbacks ran) or the timeout passed. True when idle."""
        deadline = time.monotonic() + timeout_ms / 1000.0
        while not self.idle() and time.monotonic() < deadline:
            QCoreApplication.processEvents(QEventLoop.AllEvents, 20)
            if not self.idle():
                time.sleep(0.005)
        QCoreApplication.processEvents()
        return self.idle()

    def keep_running(self) -> int:
        """Hand the jobs still running to the application, so closing the window never destroys a running thread
        (aurora.app.main.shutdown_app waits for them)."""
        app = QCoreApplication.instance()
        n = 0
        for job in list(self._jobs.values()):
            if job.is_running() and app is not None:
                job.setParent(app)
                n += 1
        return n

    def _release(self, job: Job) -> None:
        if self._jobs.get(job.key) is job:
            del self._jobs[job.key]
        if len(self._finished) == self._finished.maxlen:
            old = self._finished.popleft()          # deleted with its thread (which has ended: _finish waited for it)
            if shiboken6.isValid(old) and not old.worker.isRunning():
                old.deleteLater()
        self._finished.append(job)


def of(widget) -> JobManager:
    """The JobManager of the widget's main window, or one owned by the widget while it is outside any window."""
    from PySide6.QtWidgets import QWidget
    w = widget if isinstance(widget, QWidget) else None
    while w is not None:
        top = QWidget.window(w)
        m = getattr(top, "jobs", None)
        if isinstance(m, JobManager):
            return m
        w = top.parentWidget()
    m = widget.findChild(JobManager, "aurora_local_jobs", Qt.FindDirectChildrenOnly)
    if m is None:
        m = JobManager(widget)
        m.setObjectName("aurora_local_jobs")
    return m


def all_managers(root) -> list[JobManager]:
    """Every JobManager under `root` (the window's and the ones perspectives made while outside it)."""
    out = [m for m in [getattr(root, "jobs", None)] if isinstance(m, JobManager)]
    out += [m for m in root.findChildren(JobManager) if m not in out]
    return out
