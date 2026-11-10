"""UTF-8 text files and atomic writes.

write_text_atomic / write_json_atomic write a temporary file in the target folder, flush and fsync it, then move it
over the target with os.replace, so a crash or a full disk never leaves a half-written session.json, qc.json,
library.json or manifest behind. On Windows a virus scanner or an indexer may hold the new file for a moment:
os.replace is retried (not when the target is a read-only file, which no wait changes). A temporary file whose write
fails (a full disk) is removed. Every write goes through aurora.core.io.safety.assert_writable first.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import time
from pathlib import Path

log = logging.getLogger("aurora.io")

REPLACE_RETRIES = 6                     # os.replace attempts before giving up (PermissionError on Windows)
REPLACE_WAIT_S = 0.1                    # first wait between attempts, growing linearly


def read_json(path, default=None):
    """The parsed JSON of a UTF-8 file, or `default` when it is missing or unreadable (the reason is logged)."""
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))      # a BOM (Notepad, PowerShell 5) is accepted
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as err:
        log.warning("%s unreadable (%s); ignored", path, err)
        return default


def write_text_atomic(path, text: str, encoding: str = "utf-8") -> Path:
    """Write `text` to `path` in one step: the old content stays intact until the new one is complete on disk."""
    from aurora.core.io.safety import assert_writable
    path = Path(path)
    assert_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = text.encode(encoding)
    try:
        with open(tmp, "wb") as f:              # explicit handle: closed before the rename (Windows refuses otherwise)
            f.write(data)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:                     # some network shares refuse fsync; the rename still protects the file
                pass
    except BaseException:                       # a full disk mid-write: no partial temporary file is left behind
        _unlink(tmp)
        raise
    # a read-only target (its file attribute) refuses every attempt: one is enough, the window does not wait seconds
    retries = REPLACE_RETRIES if not path.exists() or os.access(path, os.W_OK) else 1
    last: Exception | None = None
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return path
        except PermissionError as err:          # the target or the new file is held for a moment
            last = err
            if attempt + 1 < retries:
                time.sleep(REPLACE_WAIT_S * (attempt + 1))
        except FileNotFoundError as err:        # the temporary file vanished (seen in synced folders): write directly
            log.warning("%s: temporary file vanished (%s); writing in place", path, err)
            with open(path, "wb") as f:
                f.write(data)
            return path
    _unlink(tmp)
    raise last if last is not None else OSError(f"{path}: could not be replaced")


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def write_json_atomic(path, obj, indent: int | None = 1) -> Path:
    """JSON (UTF-8, non-ASCII kept as is) written with write_text_atomic."""
    return write_text_atomic(path, json.dumps(obj, indent=indent, ensure_ascii=False))


@contextlib.contextmanager
def open_atomic(path, encoding: str = "utf-8"):
    """`with open_atomic(path) as f: f.write(...)`: what the block writes replaces `path` in one step when it ends
    (write_text_atomic, '\\n' line ends); nothing is written when the block raises. A protected path is refused
    before the block runs, so no file and no folder ever appears there."""
    from aurora.core.io.safety import assert_writable
    assert_writable(path)
    buf = io.StringIO()
    yield buf
    write_text_atomic(path, buf.getvalue(), encoding)
