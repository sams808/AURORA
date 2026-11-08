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
