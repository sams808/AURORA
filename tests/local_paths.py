"""Machine-specific folders of the tests that need real data (the beamtime, John Bussey's GUI and its outputs).

Each is an environment variable; when it is not set, the key of the same name in tests/local_paths.json (untracked,
see tests/local_paths.example.json). Neither: None, and the tests that need the folder are skipped. No path of one
machine is written in the tracked code.

    AURORA_REAL_PROJECT   a real beamtime folder (raw/, config/, ...), read-only for the whole test session
    AURORA_REAL_EXPECTATIONS  the JSON file of what that beamtime holds (holder and scan names, counts), kept with
                          the beamtime, never in the repository; default <AURORA_REAL_PROJECT>/aurora_expectations.json
                          (tests/real_expectations.example.json lists the keys)
    AURORA_LEGACY         the folder holding CLS_GUI_JohnBussey/ (John Bussey's GUI with its gui_outputs/)
    AURORA_JOHN_GUI       CLS_GUI_JohnBussey/ itself (wins over AURORA_LEGACY)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LOCAL_FILE = HERE / "local_paths.json"


def _local() -> dict:
    try:
        data = json.loads(LOCAL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def local_path(key: str) -> Path | None:
    """The folder named by environment variable `key`, else by the same key in tests/local_paths.json, else None."""
    value = os.environ.get(key) or str(_local().get(key) or "")
    return Path(value) if value.strip() else None


def john_gui() -> Path | None:
    """John Bussey's GUI folder with its gui_outputs/: the checkout's own legacy/ copy (untracked), AURORA_JOHN_GUI,
    or AURORA_LEGACY/CLS_GUI_JohnBussey; None when none of them has gui_outputs/."""
    legacy = local_path("AURORA_LEGACY")
    cands = [ROOT / "legacy" / "CLS_GUI_JohnBussey", local_path("AURORA_JOHN_GUI"),
             legacy / "CLS_GUI_JohnBussey" if legacy else None]
    return next((p for p in cands if p is not None and (p / "gui_outputs").is_dir()), None)


def real_expectations(real_project: Path | None) -> dict | None:
    """What the real-data tests check on that beamtime (its holder and scan names, counts, values): the JSON object of
    AURORA_REAL_EXPECTATIONS, else of <real_project>/aurora_expectations.json; None when there is none or it is not a
    JSON object. The tracked tests only hold the structure checks; every fact of one beamtime comes from this file."""
    cands = [local_path("AURORA_REAL_EXPECTATIONS")]
    if real_project is not None:
        cands.append(Path(real_project) / "aurora_expectations.json")
    for p in cands:
        if p is None or not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None
    return None
