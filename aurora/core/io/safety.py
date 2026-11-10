"""Folders AURORA must never write into.

- anything inside a beamline `raw` folder (a folder named raw that holds YYYY-MM-DD day folders, day folders under
  another name that hold scan folders, or scan folders): read-only by rule;
- every folder listed in $AURORA_PROTECT_DIRS (os.pathsep-separated), e.g. a real beamtime during the tests.

Every automatic write (session autosave, caches) and every report / export calls `assert_writable(path)` first. A
protected path raises ProtectedPathError, a PermissionError, so the callers' existing `except OSError` paths apply:
the cache is simply not written and the reason is logged.
"""
from __future__ import annotations

import errno
import os
import re
from pathlib import Path

ENV = "AURORA_PROTECT_DIRS"


class ProtectedPathError(PermissionError):
    pass


def _resolve(p) -> Path:
    try:
        return Path(p).expanduser().resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(Path(p).expanduser()))


def protected_dirs() -> list[Path]:
    raw = os.environ.get(ENV, "")
    return [_resolve(d) for d in raw.split(os.pathsep) if d.strip()]


DAY = re.compile(r"^\d{4}-\d{2}-\d{2}")
SCAN = re.compile(r"_(survey|stack)$")


def _scan_folder(d: Path) -> bool:
    """A scan folder: named <...>_survey / <...>_stack, or holding the scan's .h5 file directly."""
    if SCAN.search(d.name):
        return True
    try:
        return any(p.suffix.lower() == ".h5" for p in d.iterdir())
    except OSError:
        return False


def _is_beamline_raw(d: Path) -> bool:
    """A folder named raw that holds day folders (YYYY-MM-DD, or any folder of scan folders: 'Nov05', 'day1') or scan
    folders directly."""
    if d.name.lower() != "raw":
        return False
    try:
        kids = [c for c in d.iterdir() if c.is_dir()]
        if any(DAY.match(c.name) or SCAN.search(c.name) for c in kids):
            return True
        for c in kids:
            if _scan_folder(c):
                return True
            if any(g.is_dir() and _scan_folder(g) for g in c.iterdir()):
                return True
        return False
    except OSError:
        return False


def is_protected(path) -> bool:
    p = _resolve(path)
    for a in [p, *p.parents]:
        if _is_beamline_raw(a):
            return True
    for d in protected_dirs():
        if p == d or d in p.parents:
            return True
    return False


def assert_writable(path) -> None:
    if is_protected(path):
        raise ProtectedPathError(errno.EACCES, "AURORA never writes here (raw data or a protected folder)", str(path))
