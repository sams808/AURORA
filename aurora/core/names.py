"""Scan-name conventions of the SGM beamline: `<HHMMSS>_<name>_<plan>`, for example `092758_alpha_beta_f_k_stack`.

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


_SCAN_START = re.compile(r"_(?=\d{6}_)")
_ISO_DAY_ID = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)$")


def split_stack_id(sid: str) -> tuple[str, str]:
    """'<day>_<scan folder>' -> (day, scan folder), for day keys of any length: '2025-03-14_092758_x_stack' ->
    ('2025-03-14', '092758_x_stack'); '_092758_x_stack' (a loose folder without a day) -> ('', '092758_x_stack');
    'CLS GUI_092758_x_stack' -> ('CLS GUI', '092758_x_stack'). Ids that are not '<day>_<scan>' (stitched stacks,
    'stitched_<id>') come back as ('', sid), so a project lookup of the scan part finds nothing."""
    sid = str(sid)
    if sid.startswith("stitched_"):
        return "", sid
    m = _ISO_DAY_ID.match(sid)
    if m:
        return m.group(1), m.group(2)
    if sid.startswith("_"):
        return "", sid[1:]
    m = _SCAN_START.search(sid)
    if m:
        return sid[:m.start()], sid[m.end():]
    return "", sid


def short_time(sid: str) -> str:
    """'HH:MM' of a stack id or scan folder name ('2025-03-14_092758_alpha_beta_...' -> '09:27'); '' when it has none."""
    return time_label(split_stack_id(sid)[1])


def strip_scan_suffix(name: str) -> str:
    """'095242_sampleA_f_k_u_n45_stack' -> 'sampleA'; '150255_sampleB_u_n45coarse_1_stack' -> 'sampleB'."""
    s = re.sub(r"^\d{6}_", "", name)
    s = re.sub(r"_(stack|survey)$", "", s)
    m = _scan_tokens().search(s)
    if m and m.start() > 0:
        s = s[:m.start()]
    return re.sub(r"_\d+$", "", s)


def holder_from_survey(name: str) -> str:
    """'010013_holderA_survey' -> 'holderA'; 'holderC_plate_survey' -> 'holderC'; 'R2_survey_survey' -> 'R2'."""
    base = strip_scan_suffix(name)
    return re.sub(r"(?i)^plate_?|_?plate$|_survey", "", base) or base


def short_label(label: str, holder: str | None) -> str:
    """Drop the holder tag from a label ('holderB_quartz_grains' on holder holderB -> 'quartz_grains')."""
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
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
# ctime form of the bluesky files' `file_creation_timestamp_iso` root attribute: 'Fri Mar 14 09:27:59 2025'. Parsed
# with a fixed English month table, never strptime: %b depends on the locale Qt sets at start.
_CTIME = re.compile(r"(?i)^\s*(?:[a-z]{3,9}\.?,?\s+)?(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(\d{1,2}),?\s+"
                    r"(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?\s+(\d{4})\s*$")


def parse_timestamp(text) -> dt.datetime | None:
    """A timestamp in ISO 8601 form ('2025-03-14T09:27:59', '2025-03-14 09:27:59.12+00:00', '2025-03-14') or in
    ctime form ('Fri Mar 14 09:27:59 2025', the CLS h5 files); None when it is neither."""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    s = str(text or "").strip()
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        pass
    m = _CTIME.match(s)
    if m:
        try:
            return dt.datetime(int(m.group(6)), _MONTHS.index(m.group(1).lower()[:3]) + 1, int(m.group(2)),
                               int(m.group(3)), int(m.group(4)), int(m.group(5) or 0))
        except ValueError:
            return None
    return None


def date_in_text(text: str) -> str:
    """The first valid YYYY-MM-DD in a text (a file name), '' when there is none."""
    for m in _DATE.finditer(str(text)):
        try:
            return dt.date.fromisoformat(m.group(1)).isoformat()
        except ValueError:
            continue
    return ""


def energy_regions(E, decimals: int = 4) -> list[tuple[float, float, float]]:
    """Regions of constant step of an energy list, as (start eV, stop eV, step eV), in order: 680..685 by 0.5,
    685..700 by 0.1 and 700..730 by 0.2 eV give [(680.0, 685.0, 0.5), (685.0, 700.0, 0.1), (700.0, 730.0, 0.2)].
    Steps and boundaries are rounded to `decimals` (float noise of the planned list); a boundary energy ends one
    region and starts the next. One energy gives one region with step 0; an empty list gives []."""
    import numpy as np
    e = np.asarray([float(v) for v in E], float)
    e = e[np.isfinite(e)]
    if e.size == 0:
        return []
    if e.size == 1:
        return [(round(float(e[0]), decimals), round(float(e[0]), decimals), 0.0)]
    steps = np.round(np.diff(e), decimals)
    out, start = [], 0
    for i in range(1, steps.size + 1):
        if i == steps.size or steps[i] != steps[start]:
            out.append((round(float(e[start]), decimals), round(float(e[i]), decimals),
                        float(round(float(steps[start]), decimals))))
            start = i
    return out


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
