"""Provenance of exported files: a dict becomes a '# ' header block (one 'key: value' line per item) and a JSON sidecar.

    lines = header_block({"zone": "1 alpha", "members": 6, "settings": {...}})
    # AURORA 0.4.0 (version 2 of John Bussey's SGM XAS GUI)
    # zone: 1 alpha
    # members: 6
    # settings: {"inc": true, ...}
    parse_header(lines) == {"zone": "1 alpha", "members": 6, "settings": {...}}

Values are written so that parse_header gives back exactly what went in: numbers, booleans, None, lists and dicts as
compact JSON; a plain string as it is, unless it would read back as something else (a number, 'true', '[1]', a string
with a line break or surrounding spaces), in which case it is written as a JSON string. Keys are plain text without
': ' or line breaks. The first line names the application, so any reader knows where the file came from.

Headless: no Qt.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aurora import APP_NAME, __version__

LINEAGE = "version 2 of John Bussey's SGM XAS GUI"


def first_line() -> str:
    """'# AURORA x.y.z (version 2 of John Bussey's SGM XAS GUI)'."""
    return f"# {APP_NAME} {__version__} ({LINEAGE})"


def plain(v):
    """numpy scalars and arrays, tuples and Paths as plain JSON types (json.dumps default)."""
    if hasattr(v, "tolist"):
        return v.tolist()
    if isinstance(v, (set, frozenset)):
        return sorted(v)
    if isinstance(v, Path):
        return str(v)
    raise TypeError(f"{type(v).__name__} is not JSON serialisable")


def _json(v) -> str:
    return json.dumps(v, ensure_ascii=False, separators=(", ", ": "), allow_nan=True, default=plain)


def _reads_back_as_string(s: str) -> bool:
    if s != s.strip() or "\n" in s or "\r" in s or not s:
        return False
    try:
        json.loads(s)
    except ValueError:
        return True
    return False                              # '6', 'true', 'null', '[1]', '"x"' would come back as JSON


def format_value(v) -> str:
    if isinstance(v, str) and _reads_back_as_string(v):
        return v
    if isinstance(v, Path):
        return format_value(str(v))
    if isinstance(v, tuple):
        v = list(v)
    return _json(v)


def parse_value(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text


def header_block(meta: dict, prefix: str = "# ") -> list[str]:
    """The '# ' lines for `meta`, after the AURORA line. Keys must not contain ': ' or a line break."""
    lines = [first_line()]
    for key, value in meta.items():
        k = str(key)
        if ": " in k or "\n" in k or not k.strip():
            raise ValueError(f"provenance key {k!r}: no ': ', no line break, not empty")
        lines.append(f"{prefix}{k}: {format_value(value)}")
    return lines


def parse_header(lines) -> dict:
    """The dict of a header block (lines or a whole text): every '# key: value' line, the AURORA line and the first
    non-comment line (the column names) ending it."""
    if isinstance(lines, str):
        lines = lines.splitlines()
    out: dict = {}
    for ln in lines:
        s = ln.rstrip("\r\n")
        if not s.startswith("#"):
            break
        body = s[2:] if s.startswith("# ") else s[1:]
        key, sep, value = body.partition(": ")
        if not sep:                             # the AURORA line, or a free comment
            continue
        out[key] = parse_value(value)
    return out


def read_header(path) -> dict:
    """parse_header of a file's leading '#' lines (UTF-8)."""
    lines = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            if not ln.startswith("#"):
                break
            lines.append(ln)
    return parse_header(lines)


def sidecar_path(path) -> Path:
    """The JSON sidecar of an exported file: '<name>.csv' -> '<name>.json'."""
    p = Path(path)
    return p.with_suffix(".json")


def sidecar(meta: dict) -> dict:
    """The sidecar content: the application line and the same items as the header."""
    return {"aurora": f"{APP_NAME} {__version__}", "lineage": LINEAGE, **meta}


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
