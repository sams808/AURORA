"""Long jobs: Cancel within half a second, no half-written cache, failures logged and shown once, one job per key,
and a window that closes in seconds while a long job runs (also as a separate process, which must exit 0)."""
import subprocess
import sys
import textwrap
import threading
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def wait_for(cond, timeout=10.0):
    t0 = time.monotonic()
    while not cond() and time.monotonic() - t0 < timeout:
        QCoreApplication.processEvents()
        time.sleep(0.002)
    return cond()


def test_cancel_a_long_job(qapp):
    from aurora.app.jobs import JobManager
    from aurora.app.widgets import TaskBar
    host = QWidget()
    lay = QVBoxLayout(host)
    bar = TaskBar(host)
    start = QPushButton("Start")
    lay.addWidget(bar)
    lay.addWidget(start)
    host.show()
    mgr = JobManager(host)
    at5 = threading.Event()
    ends = []

    def fn(progress):
        for i in range(100):
            progress(i, 100, f"step {i}")
            if i == 5:
                at5.set()
            time.sleep(0.05)
        return "finished"

    start.setEnabled(False)
    job = mgr.submit("fake", fn, on_done=lambda r: ends.append(("done", r)), label="a fake job", taskbar=bar,
                     on_end=lambda state: (ends.append(state), start.setEnabled(True)))
    assert bar.isVisible() and bar.cancel_btn.isVisible() and not mgr.idle()
    assert wait_for(at5.is_set, 10)
    t0 = time.monotonic()
    bar.cancel()                                        # the TaskBar's Cancel (Esc does the same)
    assert wait_for(lambda: not job.is_running(), 2)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, elapsed
    assert job.state == "cancelled" and ends == ["cancelled"] and start.isEnabled()
    assert bar.text() == "cancelled" and not bar.cancel_btn.isVisible() and mgr.idle()
    host.deleteLater()


def test_cancelled_reduction_leaves_no_cache(qapp, fake_project):
    from aurora.core.cancel import CancelToken, Cancelled, cancellable
    from aurora.core import testdata
    from aurora.core.model import Project
    from aurora.core.stack import StackData
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    token = CancelToken()
    seen = []

    def hook(i, n, msg=""):
        seen.append(i)
        if i == 2:
            token.cancel()

    cb = cancellable(hook, token)
    with pytest.raises(Cancelled):
        StackData.load(proj, scan, progress=cb)
    assert seen[-1] == 2 and seen[0] == 0
    assert not list((fake_project / ".aurora" / "cache").rglob("_reduced_cache*.npz"))
    sd = StackData.load(proj, scan)                    # a full run afterwards writes it
    assert sd.n_energies > 2 and list((fake_project / ".aurora" / "cache").rglob("_reduced_cache*.npz"))


def test_cancel_through_the_job_manager(qapp, fake_project):
    """The same through a job: the cancelled reduction raises Cancelled inside the worker, nothing is cached."""
    from aurora.app.jobs import JobManager
    from aurora.core import testdata
    from aurora.core.model import Project
    from aurora.core.stack import StackData
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    host = QWidget()
    mgr = JobManager(host)
    box = {}

    def fn(progress):
        def cb(i, n, msg=""):
            if i == 2:
                box["job"].cancel()
                time.sleep(0.05)
            progress(i, n, msg)
        return StackData.load(proj, scan, progress=cb)

    box["job"] = job = mgr.submit("stack:x", fn, label="reduce")
    assert mgr.wait_idle(20000) and job.state == "cancelled"
    assert not list((fake_project / ".aurora" / "cache").rglob("_reduced_cache*.npz"))
    host.deleteLater()


def test_a_failing_job_is_logged_and_shown_once(qapp, tmp_path, monkeypatch, error_dialogs):
    import logging
    monkeypatch.setenv("AURORA_LOG_DIR", str(tmp_path / "logs"))
    from aurora.logsetup import setup_logging
    from aurora.app.widgets import Worker
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        log_path = setup_logging()
        host = QWidget()
        got = []
        w = Worker(lambda progress: (_ for _ in ()).throw(ValueError("boom")), "failing job", host)
        w.failed.connect(lambda t: got.append(t))            # direct: only collects the text
        w.start()
        w.wait(10000)
        assert wait_for(lambda: len(error_dialogs) >= 1, 5)
        for _ in range(20):
            QCoreApplication.processEvents()
        assert len(error_dialogs) == 1
        title, text, details = error_dialogs[0]
        assert "ValueError: boom" in details and text == "ValueError: boom" and "failing job" in title
        assert got and "ValueError: boom" in got[0]
        for h in root.handlers:
            h.flush()
        log = log_path.read_text(encoding="utf-8")
        assert "job failing job failed" in log and "Traceback" in log and "ValueError: boom" in log
        host.deleteLater()
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()


def test_a_cancelled_job_opens_no_dialog(qapp, error_dialogs):
    from aurora.app.jobs import JobManager
    host = QWidget()
    mgr = JobManager(host)

    def fn(progress):
        for i in range(200):
            progress(i, 200)
            time.sleep(0.01)

    job = mgr.submit("c", fn)
    time.sleep(0.05)
    job.cancel()
    assert mgr.wait_idle(5000) and job.state == "cancelled" and not error_dialogs
    host.deleteLater()


def test_one_job_per_key(qapp):
    from aurora.app.jobs import JobManager
    host = QWidget()
    mgr = JobManager(host)
    runs = []
    done = []
    gate = threading.Event()

    def fn(progress):
        runs.append(1)
        gate.wait(5)
        return 42

    j1 = mgr.submit("same", fn, on_done=lambda r: done.append(("a", r)))
    j2 = mgr.submit("same", fn, on_done=lambda r: done.append(("b", r)))
    assert j1 is j2 and mgr.running() == ["same"]
    gate.set()
    assert mgr.wait_idle(5000)
    assert runs == [1] and sorted(done) == [("a", 42), ("b", 42)]
    j3 = mgr.submit("same", lambda p: 7, on_done=lambda r: done.append(("c", r)))     # finished: a new job starts
    assert j3 is not j1 and mgr.wait_idle(5000) and ("c", 7) in done
    host.deleteLater()


def test_closing_the_window_during_a_long_job(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.show()

    def fn(progress):
        for i in range(40):
            progress(i, 40, f"second {i}")
            time.sleep(1.0)

    job = win.jobs.submit("long", fn, label="forty seconds")
    time.sleep(0.2)
    t0 = time.monotonic()
    win.close()
    assert time.monotonic() - t0 < 6.0
    assert job.state == "cancelled" and win.jobs.idle()


SCRIPT = textwrap.dedent('''
    import os, sys, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from aurora.app.main import MainWindow, create_app, shutdown_app
    app = create_app(["aurora-close-test"])
    win = MainWindow()
    win.show()
    def fn(progress):
        for i in range(40):
            progress(i, 40, "")
            time.sleep(1.0)
    win.jobs.submit("long", fn, label="forty seconds")
    t = {}
    def close():
        t["0"] = time.monotonic()
        win.close()
        t["1"] = time.monotonic()
        app.quit()
    QTimer.singleShot(400, close)
    app.exec()
    shutdown_app(app, win)
    print("closed in %.2f s" % (t["1"] - t["0"]), flush=True)
''')


def test_closing_during_a_job_exits_cleanly_in_a_process(tmp_path):
    env = {**__import__("os").environ, "AURORA_SETTINGS_DIR": str(tmp_path / "settings"),
           "AURORA_LOG_DIR": str(tmp_path / "logs")}
    t0 = time.monotonic()
    r = subprocess.run([sys.executable, "-c", SCRIPT], capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    closed = float(r.stdout.split("closed in ")[1].split()[0])
    assert closed < 6.0 and time.monotonic() - t0 < 40


class _Big:
    """Stands for a StackData or a cube: what a finished job must not keep alive."""


def test_finished_jobs_are_freed(qapp):
    """A finished job lets go of its result, its function (and what it captured) and its callbacks once they ran;
    at most the 16 most recent finished jobs stay, older ones are deleted with their threads."""
    import gc
    import weakref
    from PySide6.QtCore import QEvent, QThread
    from aurora.app.jobs import Job, JobManager
    host = QWidget()
    mgr = JobManager(host)
    results, inputs, got = [], [], []

    def make(cb):
        b = _Big()
        results.append(weakref.ref(b))
        return b
    for i in range(20):
        big = _Big()
        inputs.append(weakref.ref(big))
        mgr.submit(f"k{i}", lambda cb, b=big: (b, make(cb))[1], on_done=lambda r: got.append(type(r).__name__))
        del big
        assert mgr.wait_idle(10000)
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
    assert got == ["_Big"] * 20                                   # every callback had its result
    assert [r() for r in results] == [None] * 20 and [r() for r in inputs] == [None] * 20
    assert len(mgr.findChildren(Job)) <= 16 and len(mgr.findChildren(QThread)) <= 16
    host.deleteLater()


@pytest.mark.allow_excepthook
def test_a_runtime_error_in_a_callback_is_reported(qapp, excepthook_collector):
    """Only a callback whose Qt owner is gone is skipped quietly; any other RuntimeError reaches sys.excepthook."""
    from PySide6.QtCore import QEvent
    from aurora.app.jobs import JobManager
    host = QWidget()
    mgr = JobManager(host)
    gone = QPushButton("gone")
    gone.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)          # the button is deleted now

    def bug(_r):
        raise RuntimeError("dictionary changed size during iteration")
    mgr.submit("a", lambda cb: 1, on_done=lambda r: gone.setText("x"))         # deleted owner: skipped
    assert mgr.wait_idle(5000) and not excepthook_collector
    mgr.submit("b", lambda cb: 1, on_done=bug)
    assert mgr.wait_idle(5000)
    assert [str(e) for _t, e, _tb in excepthook_collector] == ["dictionary changed size during iteration"]
    excepthook_collector.clear()
    host.deleteLater()


STUCK = textwrap.dedent('''
    import os, sys, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from aurora.app import main

    def close(win):
        print("closing at", time.monotonic(), flush=True)
        win.close()

    def start(win, project=None):
        win.jobs.submit("stuck", lambda cb: time.sleep(60), label="one call that does not return")
        QTimer.singleShot(300, lambda: close(win))
    main.open_at_start = start
    sys.exit(main.run())
''')


def test_a_job_stuck_in_one_call_does_not_abort_the_exit(tmp_path):
    """A job stuck in one call (a share that does not answer) when the window closes: run() ends the process with exit
    code 0 once EXIT_WAIT_S passed; deleting the application under the running thread aborted it (exit 127)."""
    from aurora.app.main import CLOSE_WAIT_S, EXIT_WAIT_S
    env = {**__import__("os").environ, "AURORA_SETTINGS_DIR": str(tmp_path / "settings"),
           "AURORA_LOG_DIR": str(tmp_path / "logs")}
    r = subprocess.run([sys.executable, "-c", STUCK], capture_output=True, text=True, timeout=180, env=env)
    ended = time.monotonic()                                           # one clock for both processes
    assert r.returncode == 0, (r.returncode, r.stdout[-2000:], r.stderr[-2000:])
    took = ended - float(r.stdout.split("closing at ")[1].split()[0])
    assert took < CLOSE_WAIT_S + EXIT_WAIT_S + 8, took                 # not the 60 s of the stuck call
    log = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (tmp_path / "logs").rglob("*.log"))
    assert "still running" in log and "one call that does not return" in log
