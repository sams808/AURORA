"""Folders AURORA must never write into.

- anything inside a beamline `raw` folder (a folder named raw that holds YYYY-MM-DD day folders): read-only by rule;
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


def _is_beamline_raw(d: Path) -> bool:
    if d.name.lower() != "raw":
        return False
    try:
        return any(c.is_dir() and DAY.match(c.name) for c in d.iterdir())
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
