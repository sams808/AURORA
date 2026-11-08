"""Log file for bug reports: one rotating file per user, also used by the frozen app (which has no console).

Location: platformdirs' user log folder for AURORA (Windows %LOCALAPPDATA%\\AURORA\\Logs, macOS ~/Library/Logs/AURORA),
or $AURORA_LOG_DIR when set (tests, the self-test). A test run without $AURORA_LOG_DIR logs into a temporary folder,
never into the user's log. Crashes of the interpreter itself (segfaults) go to faults.log.
"""
from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import platform
import sys
from pathlib import Path

_FAULT_FILE = []          # keep the faulthandler file object alive


def log_dir() -> Path:
    d = os.environ.get("AURORA_LOG_DIR")
    if d:
        p = Path(d)
    elif os.environ.get("PYTEST_CURRENT_TEST"):          # a test run never writes the user's real log
        import tempfile
        p = Path(tempfile.gettempdir()) / "AURORA-test-logs"
    else:
        from platformdirs import user_log_dir
        p = Path(user_log_dir("AURORA", appauthor=False))
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        import tempfile
        p = Path(tempfile.gettempdir()) / "AURORA-logs"
        p.mkdir(parents=True, exist_ok=True)
    return p


def log_file() -> Path:
    return log_dir() / "aurora.log"


def _same_file(a, b) -> bool:
    return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(os.path.abspath(str(b)))


def setup_logging(level: int = logging.INFO) -> Path:
    """Attach a rotating file handler to the root logger and enable faulthandler. Returns the log path.

    Idempotent for one log folder; when $AURORA_LOG_DIR now names another folder (the self-test, a test), the handler
    moves there."""
    path = log_file()
    root = logging.getLogger()
    for h in [h for h in root.handlers if getattr(h, "_aurora", False)]:
        if not _same_file(getattr(h, "baseFilename", ""), path):
            root.removeHandler(h)
            h.close()
    if not any(getattr(h, "_aurora", False) for h in root.handlers):
        try:
            h = logging.handlers.RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        except OSError:
            return path
        h._aurora = True
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        h.setLevel(level)
        root.addHandler(h)
        if root.level > level or root.level == logging.NOTSET:
            root.setLevel(level)
        from aurora import __version__
        logging.getLogger("aurora").info("AURORA %s on Python %s, %s %s (%s)", __version__, sys.version.split()[0],
                                         platform.system(), platform.release(), platform.machine())
    if not _FAULT_FILE:
        try:
            f = open(log_dir() / "faults.log", "a", encoding="utf-8")
            faulthandler.enable(file=f, all_threads=True)
            _FAULT_FILE.append(f)
        except (OSError, RuntimeError, ValueError):
            pass
    return path
