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


def work_folders() -> dict[str, str]:
    """{beamtime root: working folder}: the folders chosen with Choose a working folder… (a beamtime AURORA may not
    write into keeps its session, caches, saved spectra and reports there). Stored as JSON text."""
    import json
    raw = settings().value("work_folders", "")
    try:
        d = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
    except ValueError:
        d = {}
    return {str(k): str(v) for k, v in d.items() if k and v} if isinstance(d, dict) else {}


def remember_work_folder(root: Path, work: Path | None) -> None:
    """Keep (or, with None, forget) the working folder of a beamtime."""
    import json
    try:
        key = str(Path(root).resolve())
    except OSError:
        key = str(root)
    d = {k: v for k, v in work_folders().items() if k != key}
    if work is not None:
        d[key] = str(work)
    s = settings()
    s.setValue("work_folders", json.dumps(d, ensure_ascii=False))
    s.sync()


def remember_project(path: Path) -> None:
    s = settings()
    lst = [str(path)] + [p for p in recent_projects() if p != str(path)]
    s.setValue("recent_projects", lst[:10])
    s.sync()
