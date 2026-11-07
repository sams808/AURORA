import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REAL_PROJECT = Path(os.environ.get("AURORA_REAL_PROJECT", r"<beamtime folder>"))


@pytest.fixture(scope="session", autouse=True)
def _qt_teardown():
    """Destroy the QApplication deterministically at session end. Left to interpreter teardown, PySide6 >= 6.12
    segfaults after the last test (module globals and the application die in an arbitrary order)."""
    yield
    try:
        from PySide6.QtWidgets import QApplication
        import shiboken6
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in QApplication.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.processEvents()
    import gc
    gc.collect()
    app.processEvents()
    shiboken6.delete(app)


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
