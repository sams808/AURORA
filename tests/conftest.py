import os
import sys
import threading
import traceback
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def system_font_dir() -> str | None:
    """The folder of the system fonts, flat enough for Qt's offscreen font database (None when there is none)."""
    cands = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"] if sys.platform.startswith("win") else \
        [Path("/System/Library/Fonts"), Path("/Library/Fonts")] if sys.platform == "darwin" else \
        [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/dejavu"), Path("/usr/share/fonts/TTF")]
    for c in cands:
        if c.is_dir() and any(c.glob("*.tt[fc]")):
            return str(c)
    return None


# offscreen Qt has no fonts unless told where they are: without them every text metric is meaningless (layout tests)
if system_font_dir():
    os.environ.setdefault("QT_QPA_FONTDIR", system_font_dir())


def pytest_configure(config):
    config.addinivalue_line("markers", "allow_excepthook: the test expects exceptions to reach sys.excepthook")


class ExceptHookCollector(list):
    """Replaces sys.excepthook and threading.excepthook during a test: an exception raised inside a Qt slot (which only
    reaches the excepthook) is collected, and the test fails at teardown."""

    def __call__(self, exc_type, exc, tb) -> None:
        self.append((exc_type, exc, tb))
        traceback.print_exception(exc_type, exc, tb)

    def thread(self, args) -> None:
        self.append((args.exc_type, args.exc_value, args.exc_traceback))
        traceback.print_exception(args.exc_type, args.exc_value, args.exc_traceback)

    def text(self) -> str:
        return "\n".join("".join(traceback.format_exception(t, e, tb)) for t, e, tb in self)


@pytest.fixture(autouse=True)
def excepthook_collector(request, monkeypatch):
    col = ExceptHookCollector()
    monkeypatch.setattr(sys, "excepthook", col)
    monkeypatch.setattr(threading, "excepthook", col.thread)
    yield col
    if col and request.node.get_closest_marker("allow_excepthook") is None:
        pytest.fail(f"{len(col)} exception(s) reached sys.excepthook (a slot raised):\n{col.text()}", pytrace=False)


@pytest.fixture(autouse=True)
def error_dialogs(monkeypatch):
    """crash.show_error records instead of opening a modal dialog (a failed background job in a test)."""
    calls: list = []
    try:
        from aurora.app import crash
    except ImportError:
        yield calls
        return
    monkeypatch.setattr(crash, "show_error", lambda title, text, details="": calls.append((title, text, details)))
    yield calls

from local_paths import local_path, real_expectations  # noqa: E402  (tests/: AURORA_REAL_PROJECT, else local_paths.json)

# no beamtime on this machine: a folder that does not exist, so the real-data tests skip and the guard below still holds
REAL_PROJECT = local_path("AURORA_REAL_PROJECT") or Path("/nonexistent/real_beamtime")
# the real beamtime is read-only for the whole test session: every AURORA write path refuses it (aurora.core.io.safety)
os.environ["AURORA_PROTECT_DIRS"] = os.pathsep.join(filter(None, [os.environ.get("AURORA_PROTECT_DIRS", ""), str(REAL_PROJECT)]))


@pytest.fixture(scope="session", autouse=True)
def _qt_teardown():
    """Destroy the QApplication deterministically at session end. Left to interpreter teardown, PySide6 >= 6.12
    segfaults after the last test (module globals and the application die in an arbitrary order)."""
    yield
    try:
        from PySide6.QtWidgets import QApplication
        from aurora.app.main import shutdown_app
    except ImportError:
        return
    app = QApplication.instance()
    if app is not None:
        shutdown_app(app)


@pytest.fixture(autouse=True)
def _destroy_leftover_windows(_isolated_settings):
    """Main windows a test leaves behind are destroyed after it (without closeEvent, so nothing is saved). A theme change
    re-polishes every live widget of the application, so leftover windows would make every later apply_theme slower
    (about 1.5 s per window). A window whose worker thread is still running is kept: a running QThread is never dropped.
    Torn down before _isolated_settings, so nothing reaches the user's real settings. Only QMainWindows: the parentless
    menus pyqtgraph keeps in Python attributes must not be deleted behind its back."""
    yield
    try:
        import shiboken6
        from PySide6.QtCore import QCoreApplication, QEvent, QThread
        from PySide6.QtWidgets import QApplication, QMainWindow
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    from aurora.app.nav import top_level_widgets      # the GC is paused while the wrappers are made (3.11 segfault)
    doomed = []
    for w in top_level_widgets():
        if not shiboken6.isValid(w) or w.parentWidget() is not None or not isinstance(w, QMainWindow):
            continue
        threads = [t for t in w.findChildren(QThread) if t.isRunning()]
        for t in threads:
            t.wait(30000)
        if any(t.isRunning() for t in threads):
            continue
        doomed.append(w)
    for w in doomed:
        w.hide()
        w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    # What a destroyed window leaves on the Python side is cyclic garbage that still owns live widgets (pyqtgraph's
    # parentless ViewBox menus). Collect it here, on the GUI thread: left to the automatic collector, a full
    # collection can start on a job's worker thread in a later test and delete those widgets there (an access
    # violation, or a deadlock between the GIL and Qt). Skipped while a job has paused the collector (jobs._hold_gc).
    if doomed:
        import gc
        if gc.isenabled():
            gc.collect()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """After every test once a QApplication exists, its garbage is collected on the GUI thread, between tests. The
    collection in _destroy_leftover_windows runs while the test's fixture values (item.funcargs, cleared by pytest only
    after the teardown) and its locals still hold the windows, so most of what a window owns (pyqtgraph's parentless
    menus and items, about 15 000 objects) is not garbage yet; and a test without a main window (a dialog, a plot, a
    perspective on its own) leaves its widgets to the collector too. Left to the automatic collector, that garbage was
    freed at an allocation in the middle of a later test: an access violation inside MainWindow() of test_theme after
    test_layout and test_zone_drawing, an abort in a job's progress slot of test_app after test_ui_monkey."""
    try:
        return (yield)
    finally:
        import gc
        if gc.isenabled() and "PySide6.QtWidgets" in sys.modules:
            from PySide6.QtWidgets import QApplication
            if QApplication.instance() is not None:
                gc.collect()


@pytest.fixture(autouse=True)
def _release_modifiers():
    """QTest.keyClick(w, key, Ctrl | Shift) leaves QApplication.keyboardModifiers() at Shift (its synthetic release
    still carries the modifier), and a later test's selectRow() then extends the selection instead of setting it.
    Release every modifier after each test, as a real keyboard would."""
    yield
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QWidget
    except ImportError:
        return
    app = QApplication.instance()
    if app is None or app.keyboardModifiers() == Qt.NoModifier:
        return
    w = QWidget()
    for k in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta):
        QTest.keyRelease(w, k, Qt.NoModifier)
    w.deleteLater()


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Never touch the user's real QSettings (registry / plist) from a test."""
    monkeypatch.setenv("AURORA_SETTINGS_DIR", str(tmp_path / "settings"))


@pytest.fixture
def fake_project(tmp_path) -> Path:
    from aurora.core.testdata import make_fake_project
    return make_fake_project(tmp_path / "beamtime")


@pytest.fixture(scope="session")
def real_project() -> Path:
    if not (REAL_PROJECT / "raw").is_dir():
        pytest.skip(f"real beamtime folder not available: {REAL_PROJECT}")
    return REAL_PROJECT


@pytest.fixture(scope="session")
def real_expect(real_project) -> dict:
    """The facts of the real beamtime the tests check (tests/local_paths.py real_expectations); skip without them."""
    data = real_expectations(real_project)
    if data is None:
        pytest.skip("no aurora_expectations.json for the real beamtime (see tests/real_expectations.example.json)")
    return data
