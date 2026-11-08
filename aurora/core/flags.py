"""Scan flags (saturated SDDs, I0 invalid, high rate), each with its source.

merged_flags(project, scan) is the one place the perspectives, Easy-mode defaults, the report and the command line read
flags from. It merges two sources:
- the sgm_report summary (processed/<day>/summary.json), source 'sgm_report';
- AURORA's own Check (<project>/.aurora/qc.json, written by aurora.core.qc.save_qc), source 'AURORA check'.
A flag stated by both sources is listed once per source, so the report can quote either.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from aurora.core.constants import DETS

SUMMARY_SOURCE = "sgm_report"
CHECK_SOURCE = "AURORA check"
QC_FILE = (".aurora", "qc.json")               # <project>/.aurora/qc.json


@dataclass(frozen=True)
class Flag:
    text: str                                   # e.g. 'SDD saturated: SDD3'
    kind: str                                   # 'sdd_saturated' | 'i0_invalid' | 'high_rate' | 'note'
    source: str                                 # 'sgm_report' | 'AURORA check'
    dets: tuple[str, ...] = field(default=())   # the detectors concerned ('sdd3'), when any

    def __str__(self) -> str:
        return f"{self.text} ({self.source})"


def _det(name) -> str | None:
    d = str(name).strip().lower()
    return d if d in DETS else None


def summary_flags(scan) -> list[Flag]:
    """Flags stated by the sgm_report summary entry of a scan (empty when there is none)."""
    s = getattr(scan, "summary", None) or {}
    out: list[Flag] = []
    for name in s.get("sdd_saturated") or []:
        d = _det(name)
        if d:
            out.append(Flag(f"SDD saturated: {d.upper()}", "sdd_saturated", SUMMARY_SOURCE, (d,)))
    for name in s.get("sdd_high_rate") or s.get("high_rate") or []:
        d = _det(name)
        if d:
            out.append(Flag(f"high count rate: {d.upper()}", "high_rate", SUMMARY_SOURCE, (d,)))
    if s.get("i0_bad"):
        out.append(Flag("I0 invalid", "i0_invalid", SUMMARY_SOURCE))
    return out


def entry_flags(entry: dict | None, source: str = CHECK_SOURCE) -> list[Flag]:
    """Flags of one qc.json entry ({i0_bad, saturated, high_rate, ...}), the same words as the summary's."""
    if not isinstance(entry, dict):
        return []
    out: list[Flag] = []
    for name in entry.get("saturated") or []:
        d = _det(name)
        if d:
            out.append(Flag(f"SDD saturated: {d.upper()}", "sdd_saturated", source, (d,)))
    for name in entry.get("high_rate") or []:
        d = _det(name)
        if d:
            out.append(Flag(f"high count rate: {d.upper()}", "high_rate", source, (d,)))
    if entry.get("i0_bad"):
        out.append(Flag("I0 invalid", "i0_invalid", source))
    return out


# qc.json read once per change of the file (path -> (mtime_ns, size, entries)); plain data, no Qt objects
_QC_CACHE: dict[str, tuple[int, int, dict]] = {}


def qc_path(project) -> Path | None:
    root = getattr(project, "root", None)
    return Path(root).joinpath(*QC_FILE) if root else None


def qc_entries(project) -> dict[str, dict]:
    """sid -> entry of the project's qc.json (empty when there is none or it is unreadable)."""
    p = qc_path(project)
    if p is None:
        return {}
    try:
        st = os.stat(p)
    except OSError:
        _QC_CACHE.pop(str(p), None)
        return {}
    hit = _QC_CACHE.get(str(p))
    if hit is not None and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    from aurora.core.io.textio import read_json
    d = read_json(p, {})
    entries = {k: v for k, v in (d.items() if isinstance(d, dict) else []) if isinstance(v, dict) and not k.startswith("_")}
    _QC_CACHE[str(p)] = (st.st_mtime_ns, st.st_size, entries)
    return entries


def check_flags(project, scan) -> list[Flag]:
    """Flags of AURORA's own Check for this scan, from <project>/.aurora/qc.json."""
    if project is None or scan is None:
        return []
    sid = f"{getattr(scan, 'day', '')}_{getattr(scan, 'name', '')}"
    return entry_flags(qc_entries(project).get(sid))


def merged_flags(project, scan) -> list[Flag]:
    """Every flag of a scan, each with its source: the sgm_report summary, then AURORA's Check (qc.json)."""
    out = summary_flags(scan)
    for f in check_flags(project, scan):
        if f not in out:
            out.append(f)
    return out


def saturated_dets(flags) -> set[str]:
    return {d for f in flags or [] if f.kind == "sdd_saturated" for d in f.dets}


def i0_invalid(flags) -> bool:
    return any(f.kind == "i0_invalid" for f in flags or [])
