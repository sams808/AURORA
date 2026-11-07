"""Scan-name conventions of the Nov 2026 beamtime: `<HHMMSS>_<holder>_<group>_<edge>_<plan>`.

Ported from sgm_report.py v2.5 (`strip_scan_suffix`, `Survey.holder`, `Stack.target_edge`, `time_label`).
"""
from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def element_names() -> dict[str, str]:
    """lower-case element name -> symbol, H..U (from xraydb, imported lazily: it loads a database)."""
    import xraydb
    return {xraydb.atomic_name(z).lower(): xraydb.atomic_symbol(z) for z in range(1, 93)}


@lru_cache(maxsize=1)
def element_symbols() -> frozenset[str]:
    return frozenset(element_names().values())


@lru_cache(maxsize=1)
def _scan_tokens() -> re.Pattern:
    names = "|".join(sorted(element_names(), key=len, reverse=True))
    return re.compile(r"(?i)_(?:zone|coarse|fine|bigstep|smallstep|wsu|tey|pfy|edge\d*|survey|stack|f_?k|f_coarse|"
                      + names + r"_?[klmn]\d*|"
                      + r"[a-z]{1,2}_(?:k|l\d?|m\d?|n\d{1,2})|u_?n\d+|un\d+)(?=_|$|\d|coarse|fine)")


def time_label(scan_dir_name: str) -> str:
    """'092758_alpha_beta_...' -> '09:27'."""
    return f"{scan_dir_name[0:2]}:{scan_dir_name[2:4]}" if re.match(r"\d{6}_", scan_dir_name) else ""


def strip_scan_suffix(name: str) -> str:
    """'095242_sampleA_f_k_u_n45_stack' -> 'sampleA'; '150255_UF4_u_n45coarse_1_stack' -> 'UF4'."""
    s = re.sub(r"^\d{6}_", "", name)
    s = re.sub(r"_(stack|survey)$", "", s)
    m = _scan_tokens().search(s)
    if m and m.start() > 0:
        s = s[:m.start()]
    return re.sub(r"_\d+$", "", s)


def holder_from_survey(name: str) -> str:
    """'010013_holderA_survey' -> 'N5'; 'N7_plate_survey' -> 'N7'; 'R2_survey_survey' -> 'R2'."""
    base = strip_scan_suffix(name)
    return re.sub(r"(?i)^plate_?|_?plate$|_survey", "", base) or base


def short_label(label: str, holder: str | None) -> str:
    """Drop the holder tag from a label ('holderB_quartz_grains' on holder N2 -> 'quartz_grains')."""
    if holder:
        s = re.sub(rf"(?i)^(plate_?)?{re.escape(holder)}_", "", label)
        if s:
            return s
    return label


def target_edge(name: str) -> tuple[str, str] | None:
    """Edge the scan was set up for, from its name: ('F', 'K'), ('U', 'N45'), ('Se', 'L32') ...; None if not stated.

    The glued form (un45, sel3) needs a digit in the shell, otherwise a sample code such as 'BM' reads as boron M.
    """
    nm = name.replace("-", "_")
    names = element_names()
    m = re.search(r"(?i)_(" + "|".join(names) + r")_?([klmn]\d*)(?=_|$)", nm)
    if m:
        el, shell = names[m.group(1).lower()], m.group(2).upper()
    else:
        # 'se_l3l2' = L3 and L2 in one token
        m = re.search(r"(?i)(?:^|_)(?:([a-z]{1,2})_(k|l\d?(?:l\d)?|m\d?(?:m\d)?|n\d{1,2})|([a-z]{1,2})(l\d|m\d|n\d{1,2}))(?=_|$|coarse|fine)", nm)
        if not m:
            if re.search(r"(?i)_f_coarse", nm):
                return ("F", "K")
            return None
        el = (m.group(1) or m.group(3)).capitalize()
        shell = (m.group(2) or m.group(4)).upper()
    if el not in element_symbols():
        return None
    return el, shell


def pretty_edge(edge: tuple[str, str] | None) -> str:
    """('U', 'N45') -> 'U N4,5'."""
    if edge is None:
        return ""
    el, shell = edge
    shell = re.sub(r"^([KLMN])(\d)[KLMN]?(\d)$", r"\1\2,\3", shell)     # N45 -> N4,5 ; L3L2 -> L3,2
    return f"{el} {shell}"


_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def day_from_path(path: Path | str) -> dt.date | None:
    """First YYYY-MM-DD in the path (the raw/<day> folder), else None."""
    for part in reversed(Path(path).parts):
        m = _DATE.fullmatch(part) or _DATE.search(part)
        if m:
            try:
                return dt.date.fromisoformat(m.group(1))
            except ValueError:
                continue
    return None
