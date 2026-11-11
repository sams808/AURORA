"""HDF5 access for the bluesky scan files (survey and stack).

The beamline may leave a stack .h5 open for write: open tolerant (SWMR fallback) and with file locking off.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

os.environ.setdefault("HDF5_USE_FILE_LOCKING", "FALSE")
import h5py  # noqa: E402
import numpy as np  # noqa: E402

_NUMBER = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")


def open_h5(path: Path | str, mode: str = "r") -> h5py.File:
    """h5py.File that falls back to SWMR when the writer still holds the file."""
    try:
        return h5py.File(path, mode)
    except OSError as err:
        if "already open for write" not in str(err):
            raise
        return h5py.File(path, mode, swmr=True)


def find_h5(scan_dir: Path) -> Path | None:
    """The scan's .h5 file (None when the folder has none yet)."""
    return next(iter(sorted(Path(scan_dir).glob("*.h5"))), None)


def _as_str(v) -> str:
    if isinstance(v, bytes):
        return v.decode(errors="replace")
    if isinstance(v, np.ndarray):
        return np.array2string(v, max_line_width=10 ** 6, threshold=10 ** 6)
    return str(v)


def attrs(path: Path, group: str) -> dict[str, str]:
    """Attributes of one group as strings ({} when the group is missing)."""
    with open_h5(path) as h:
        if group not in h:
            return {}
        return {k: _as_str(v) for k, v in h[group].attrs.items()}


def all_attrs(path: Path) -> dict[str, dict[str, str]]:
    """Every group's attributes, keyed by group path ('' = root)."""
    out: dict[str, dict[str, str]] = {}
    with open_h5(path) as h:
        out[""] = {k: _as_str(v) for k, v in h.attrs.items()}

        def visit(name, obj):
            if isinstance(obj, h5py.Group) and len(obj.attrs):
                out[name] = {k: _as_str(v) for k, v in obj.attrs.items()}

        h.visititems(visit)
    return out


def plan_kind(path: Path) -> str:
    """'survey' or 'stack' from the `plan_name` the scan's .h5 records (scan_metadata for a survey, stack_metadata for
    a stack), else 'other' (no plan recorded, or the file cannot be read)."""
    try:
        with open_h5(path) as h:
            for group in ("stack_metadata", "scan_metadata"):
                if group in h:
                    plan = _as_str(h[group].attrs.get("plan_name", "")).strip().lower()
                    if plan in ("survey", "stack"):
                        return plan
    except (OSError, KeyError, ValueError):
        pass
    return "other"


def root_attrs(path: Path) -> dict[str, str]:
    """Attributes of the file's root group as strings (file_creation_timestamp_iso, ring_current, ...)."""
    with open_h5(path) as h:
        return {k: _as_str(v) for k, v in h.attrs.items()}


def file_day(path: Path | str) -> str:
    """'YYYY-MM-DD' the scan file was written: the date of its `file_creation_timestamp_iso` root attribute (ISO or
    ctime form, 'Fri Mar 14 09:27:59 2025'), else the date in the file name ('2025-03-14_092759_stack_data.h5',
    'holderA_survey_2025-03-14_010013_695.00eV.h5'), else ''. A file that cannot be opened falls back to its name."""
    from aurora.core.names import date_in_text, parse_timestamp
    try:
        ts = parse_timestamp(root_attrs(Path(path)).get("file_creation_timestamp_iso", ""))
    except (OSError, KeyError, ValueError):
        ts = None
    return ts.date().isoformat() if ts is not None else date_in_text(Path(path).name)


def xy(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Stage coordinates (mm) in acquisition order, one set shared by every energy of a stack."""
    with open_h5(path) as h:
        return h["hexapod_waves/x"][()].astype(float), h["hexapod_waves/y"][()].astype(float)


def map_energies(path: Path) -> np.ndarray | None:
    """`map_data/energy` of a stack (the energies actually acquired), None for a survey."""
    with open_h5(path) as h:
        if "map_data/energy" in h:
            return h["map_data/energy"][()].astype(float)
    return None


def planned_energies(md: dict[str, str]) -> list[float]:
    """Energies planned for a stack, parsed from the `stack_metadata/energies` text."""
    return [float(v) for v in _NUMBER.findall(md.get("energies", ""))]


def stack_plan(scan_dir: Path) -> tuple[dict[str, str], list[float]]:
    """(`stack_metadata` attributes, planned energies) of a stack folder; ({}, []) without an .h5 file."""
    h5 = find_h5(scan_dir)
    if h5 is None:
        return {}, []
    md = attrs(h5, "stack_metadata")
    return md, planned_energies(md)
