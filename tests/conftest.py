import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REAL_PROJECT = Path(os.environ.get("AURORA_REAL_PROJECT", r"<beamtime folder>"))


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
