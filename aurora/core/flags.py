"""Scan flags (saturated SDDs, I0 invalid, high rate), each with its source.

merged_flags(project, scan) is the one place the perspectives, Easy-mode defaults and the report read flags from.
This is the foundation stub: it reads the sgm_report summary only. The Check package adds AURORA's own verdicts
(qc.json) and merges them here, without changing the signature.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aurora.core.constants import DETS

SUMMARY_SOURCE = "sgm_report"
CHECK_SOURCE = "AURORA check"


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


def merged_flags(project, scan) -> list[Flag]:
    """Every flag of a scan, each with its source. Stub: the sgm_report summary only."""
    return summary_flags(scan)


def saturated_dets(flags) -> set[str]:
    return {d for f in flags or [] if f.kind == "sdd_saturated" for d in f.dets}


def i0_invalid(flags) -> bool:
    return any(f.kind == "i0_invalid" for f in flags or [])
