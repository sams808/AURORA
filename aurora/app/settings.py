"""QSettings with an escape hatch for tests and the distcheck: AURORA_SETTINGS_DIR -> an ini file there, never the
user's real registry / plist (QSettings(org, app) ignores setDefaultFormat, so the path form is the only safe one)."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QSettings

from aurora import APP_NAME, ORG_NAME


def settings() -> QSettings:
    d = os.environ.get("AURORA_SETTINGS_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        return QSettings(str(Path(d) / "aurora.ini"), QSettings.IniFormat)
    return QSettings(ORG_NAME, APP_NAME)


def recent_projects() -> list[str]:
    s = settings()
    v = s.value("recent_projects", [])
    if isinstance(v, str):
        v = [v]
    return [str(p) for p in (v or []) if p]


def remember_project(path: Path) -> None:
    s = settings()
    lst = [str(path)] + [p for p in recent_projects() if p != str(path)]
    s.setValue("recent_projects", lst[:10])
    s.sync()
