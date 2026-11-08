import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REAL_PROJECT = Path(os.environ.get("AURORA_REAL_PROJECT", r"<beamtime folder>"))
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
    doomed = []
    for w in list(QApplication.topLevelWidgets()):
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
