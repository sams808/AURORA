"""Scan-name conventions of the SGM beamline: `<HHMMSS>_<name>_<plan>` (`092758_alpha_beta_f_k_stack`), where the name is what the user typed (the
h5 records it as `scan_name`) and often ends in plan words: the edge (`_f_k`, `_fe_l23`, `_ck`), a step (`_coarse`,
`_fine`, `_edge1`), a detector (`_tey`). Labels, holder names and edges are read from that name without any one
lab's words; a beamtime can add its own plan words in config/naming.json (`label_suffix_words`).

Ported from sgm_report.py v2.5 (`strip_scan_suffix`, `Survey.holder`, `Stack.target_edge`, `time_label`), with these
changes: plan words are removed only at the end of a name (a sample called `soil_coarse_fraction` keeps its name), a
holder keeps its trailing number (`Plate_3`), and the edge is checked against the scan's energies (edge_for_range).
"""
from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def element_names() -> dict[str, str]:
    """lower-case element name -> symbol, H..U (from xraydb, imported lazily: it loads a database), plus the British
    spellings aluminium and sulphur."""
    import xraydb
    out = {xraydb.atomic_name(z).lower(): xraydb.atomic_symbol(z) for z in range(1, 93)}
    out.setdefault("aluminium", "Al")
    out.setdefault("aluminum", "Al")
    out.setdefault("sulphur", "S")
    return out


@lru_cache(maxsize=1)
def element_symbols() -> frozenset[str]:
    return frozenset(element_names().values())


# an edge's shell as scan names write it: k, l, l3, l23, l2,3, l3l2, m5, m45, m4,5, n45 ...
SHELL = r"k|l(?:\d(?:,?\d|l\d)?)?|m(?:\d(?:,?\d|m\d)?)?|n(?:\d(?:,?\d)?)?"
SHELL_DIGIT = r"l\d(?:,?\d|l\d)?|m\d(?:,?\d|m\d)?|n\d(?:,?\d)?"           # the glued form needs a digit (BM is no edge)
# glued K edges written without a digit (CK, OK, NaK) are read for the light elements whose K edge SGM reaches
GLUED_K = ("c", "n", "o", "f", "na", "mg", "al", "si")
GENERIC_WORDS = r"coarse\d*|fine\d*|zone\d*|bigstep|smallstep|tey\d*|pfy\d*|edge\d*|survey|stack"


@lru_cache(maxsize=16)
def _end_token(words: tuple[str, ...] = ()) -> re.Pattern:
    """One plan token at the very end of a name: a generic word, an edge (`_f_k`, `_fe_l23`, `_fluorine_k`, `_ck`,
    `_mnL23`, `_u_n45coarse`) or one of the beamtime's own words (config/naming.json)."""
    names = "|".join(sorted(element_names(), key=len, reverse=True))
    own = "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True) if w)
    edge = (rf"(?:{names})_?(?:{SHELL})|[a-z]{{1,2}}_(?:{SHELL})|[a-z]{{1,2}}(?:{SHELL_DIGIT})|"
            rf"(?:{'|'.join(GLUED_K)})k")
    alts = "|".join(a for a in (own, GENERIC_WORDS, rf"(?:{edge})(?:coarse|fine)?") if a)
    return re.compile(rf"(?i)_(?:{alts})$")


def scan_base(name: str) -> str:
    """The name the user typed: a scan folder name without its HHMMSS_ time and its _survey / _stack plan suffix."""
    s = re.sub(r"^\d{6}_", "", str(name))
    return re.sub(r"_(stack|survey)$", "", s)


def label_from_name(name: str, words=()) -> str:
    """The sample label of a scan name (a folder name or the h5 scan_name): the plan tokens at its end removed, one
    after the other, never the whole name; a trailing re-take counter (`_1`) goes only after plan tokens."""
    pat = _end_token(tuple(w.lower() for w in words))
    s = scan_base(name)
    m = re.match(r"^(.+?)_(\d+)$", s)
    if m and pat.search(m.group(1)) and pat.search(m.group(1)).start() > 0:
        s = m.group(1)                                  # 'sampleB_u_n45coarse_1': the re-take counter of a plan
    while True:
        m = pat.search(s)
        if not m or m.start() == 0:
            return s
        s = s[:m.start()]


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


def strip_scan_suffix(name: str, words=()) -> str:
    """The sample label of a scan folder name (label_from_name): '103000_sample1_f_k_stack' -> 'sample1';
    '092758_soil_coarse_fraction_ca_l_stack' -> 'soil_coarse_fraction'; '150255_sampleB_u_n45coarse_1_stack' -> 'sampleB'."""
    return label_from_name(name, words)


def holder_from_survey(name: str, scan_name: str | None = None) -> str:
    """The holder a survey starts: the survey's own name (`scan_name` from its h5 when given, else the folder name
    without HHMMSS_), without its '_survey' plan suffix and a 'plate' prefix or suffix. A trailing number stays:
    '101500_holderA_survey' -> 'holderA'; '010013_plateB2_survey' -> 'B2'; '011600_C4_plate_survey' -> 'C4';
    '133400_R2_survey_survey' -> 'R2'; '093000_Plate_3_survey' -> 'Plate_3'; '093000_survey' -> 'holder 09:30'."""
    base = re.sub(r"^\d{6}_", "", str(scan_name or name or ""))
    while re.search(r"(?i)(?:^|_)survey$", base):
        base = re.sub(r"(?i)_?survey$", "", base)
    for pat in (r"(?i)^plate[_\s-]?", r"(?i)[_\s-]?plate$"):
        rest = re.sub(pat, "", base)
        if rest != base and re.search(r"[A-Za-z]", rest):      # 'plateB2' -> 'B2'; 'Plate_3' keeps its word
            base = rest
    base = base.strip("_ -")
    if base:
        return base
    t = time_label(Path(str(name)).name)
    return f"holder {t}" if t else "holder"


def norm_holder(s: str) -> str:
    """A holder name compared loosely: no 'plate' / 'holder' word, case, spaces and punctuation ignored ('Plate B2',
    'plateB2' and 'B2' are one holder; 'holder A' and 'holderA' too)."""
    t = re.sub(r"(?i)plate|holder", "", str(s or ""))
    return re.sub(r"[^0-9a-z]", "", t.lower()) or re.sub(r"[^0-9a-z]", "", str(s or "").lower())


def sheet_holders(value: str) -> set[str]:
    """The holders a sample-sheet cell names, loosely compared (norm_holder): 'B2', 'B2/B3', 'B2?' ."""
    return {norm_holder(p.strip().rstrip("?")) for p in re.split(r"[/;,]", str(value or "")) if p.strip()}


def on_holder(value: str, holder: str) -> bool:
    """Whether a sample-sheet holder cell names `holder`."""
    return bool(holder) and norm_holder(holder) in sheet_holders(value)


# beamline test scans: a strong word anywhere in the name, or a weak word that is the whole label ('dark', but not
# 'dark_glass' or 'Darkite'); each as a whole token, never inside a word
TEST_STRONG = r"(?i)(?:^|_)(?:photodiode|i0_?test|beam_?test)(?:_|$)"
TEST_WEAK = r"(?i)(?:diode|dark|i0)(?:_?\d+)?"


def is_test_name(name: str, words=()) -> bool:
    """Whether a scan folder name says it is a beamline test, not a sample: 'photodiode_f_k', 'i0_test', 'dark' and
    'dark_2' are; 'dark_glass_fe_l3', 'Darkite' and 'my_diode_array' are samples."""
    base = scan_base(name)
    if re.search(TEST_STRONG, base):
        return True
    return bool(re.fullmatch(TEST_WEAK, label_from_name(name, words)))


def short_label(label: str, holder: str | None) -> str:
    """Drop the holder tag from a label ('holderB_quartz_grains' on holder holderB -> 'quartz_grains')."""
    if holder:
        s = re.sub(rf"(?i)^(plate_?)?{re.escape(holder)}_", "", label)
        if s:
            return s
    return label


@lru_cache(maxsize=1)
def _edge_inside() -> re.Pattern:
    """An edge token anywhere in a label, followed by more tokens or the end ('_f_k' in 'glass_f_k_lab')."""
    names = "|".join(sorted(element_names(), key=len, reverse=True))
    edge = (rf"(?:{names})_?(?:{SHELL})|[a-z]{{1,2}}_(?:{SHELL})|[a-z]{{1,2}}(?:{SHELL_DIGIT})|"
            rf"(?:{'|'.join(GLUED_K)})k")
    return re.compile(rf"(?i)_(?:{edge})(?:coarse|fine)?(?=_|$)")


def sample_part(name: str, holder: str | None = None, words=()) -> str:
    """The part of a scan name that names the sample, for lists and map labels where a long name is cut (the full
    name stays in the tooltip): the time, the plan suffix, the holder tag, the edge and everything after it (a lab or
    plan word) removed. '101500_holderB_quartz_grains_f_k_lab_stack' on holderB -> 'quartz_grains'. Never empty:
    the sample label when nothing would be left."""
    label = label_from_name(name, words)
    s = label
    m = _edge_inside().search(s)
    if m and m.start() >= 2:
        s = s[:m.start()]
    s = short_label(s, holder) if holder else s
    return s if len(s) >= 2 else label


def target_edge(name: str) -> tuple[str, str] | None:
    """Edge the scan was set up for, from its name: ('F', 'K'), ('U', 'N45'), ('Se', 'L3L2'), ('Fe', 'L23') ...; None
    if not stated. Read: `fe_l23`, `Fe_L2,3`, `mnL23`, `iron_l3`, `CK`, `OK`, `u_n45coarse`.

    The glued form (un45, sel3) needs a digit in the shell, otherwise a sample code such as 'BM' reads as boron M; a
    glued K without digit is read for the light elements of GLUED_K only (CK, OK, FK, NaK ...).
    """
    nm = str(name).replace("-", "_")
    names = element_names()
    end = r"(?=_|$|coarse|fine)"
    m = re.search(r"(?i)(?:^|_)(" + "|".join(sorted(names, key=len, reverse=True)) + rf")_?({SHELL}){end}", nm)
    if m:
        el, shell = names[m.group(1).lower()], m.group(2)
    else:
        m = re.search(rf"(?i)(?:^|_)(?:([a-z]{{1,2}})_({SHELL})|([a-z]{{1,2}})({SHELL_DIGIT})|"
                      rf"({'|'.join(GLUED_K)})(k)){end}", nm)
        if not m:
            return None
        el = (m.group(1) or m.group(3) or m.group(5)).capitalize()
        shell = m.group(2) or m.group(4) or m.group(6)
    if el not in element_symbols():
        return None
    return el, shell.upper().replace(",", "")


# the edges guessed from a scan's energies: K first, then L3, L2, M5, M4, N5, N4, each for the elements whose edge of
# that kind a soft X-ray beamline measures (H..U from xraydb; no hand-typed energies)
RANGE_FAMILIES = (("K", 3, 20), ("L3", 11, 60), ("L2", 11, 60), ("M5", 55, 83), ("M4", 55, 83), ("N5", 89, 92),
                  ("N4", 89, 92))
EDGE_TOLERANCE_EV = 30.0                # a named edge this far outside the scan's energies is still the name's edge


@lru_cache(maxsize=1)
def _range_edges() -> tuple[tuple[float, int, str, str], ...]:
    import xraydb
    out = []
    for pri, (shell, z0, z1) in enumerate(RANGE_FAMILIES):
        for z in range(z0, z1 + 1):
            el = xraydb.atomic_symbol(z)
            try:
                e = float(xraydb.xray_edge(el, shell).energy)
            except Exception:                       # noqa: BLE001  an edge xraydb does not list
                continue
            if e > 0:
                out.append((e, pri, el, shell))
    return tuple(sorted(out))


def edge_for_range(e_min: float, e_max: float, tolerance: float = EDGE_TOLERANCE_EV) -> tuple[str, str] | None:
    """The edge a scan over e_min..e_max eV most likely measured: among the edges inside the range, the first family of
    RANGE_FAMILIES (K before L3 ...), the lowest edge of it; when none lies inside, the same within `tolerance` eV of
    the range, nearest first; None when no edge is that close."""
    try:
        lo, hi = sorted((float(e_min), float(e_max)))
    except (TypeError, ValueError):
        return None
    if not (lo == lo and hi == hi):
        return None
    inside = [c for c in _range_edges() if lo <= c[0] <= hi]
    if inside:
        e, pri, el, shell = min(inside, key=lambda c: (c[1], c[0]))
        return el, shell
    near = [c for c in _range_edges() if lo - tolerance <= c[0] <= hi + tolerance]
    if not near:
        return None
    e, pri, el, shell = min(near, key=lambda c: (c[1], min(abs(c[0] - lo), abs(c[0] - hi))))
    return el, shell


def resolve_edge(name_edge: tuple[str, str] | None, energies, known=None) -> tuple[tuple[str, str] | None, str]:
    """(edge, note) of a stack: the edge its name states when the edge energy lies within its energies ±
    EDGE_TOLERANCE_EV; else an edge the names of the beamtime's other stacks state (`known`: {edge: number of stacks})
    within that tolerance, the most common first; else the edge the energies cover (edge_for_range); a note says which.
    The name's edge unchanged when the energies are unknown or near no edge."""
    from aurora.core.lines import edge_energy
    import math
    vals = [float(e) for e in (energies or []) if e is not None and math.isfinite(float(e))]
    if not vals:
        return name_edge, ""
    lo, hi = min(vals), max(vals)
    if name_edge is not None:
        ev = edge_energy(*name_edge)
        if ev is not None and lo - EDGE_TOLERANCE_EV <= ev <= hi + EDGE_TOLERANCE_EV:
            return name_edge, ""
    guess, how = None, "the energies"
    near = []
    for edge, n in (known or {}).items():
        ev = edge_energy(*edge)
        if ev is not None and lo - EDGE_TOLERANCE_EV <= ev <= hi + EDGE_TOLERANCE_EV and edge != name_edge:
            near.append((-n, 0.0 if lo <= ev <= hi else min(abs(ev - lo), abs(ev - hi)), edge))
    if near:
        guess, how = min(near)[2], "the energies and the other scans of this beamtime"
    if guess is None:
        guess = edge_for_range(lo, hi)
    if guess is None:
        return name_edge, ""
    if name_edge is None:
        return guess, f"edge {pretty_edge(guess)} from {how} ({lo:.0f}–{hi:.0f} eV): the name states none"
    ev = edge_energy(*name_edge)
    said = f"{pretty_edge(name_edge)} ({ev:.0f} eV)" if ev is not None else pretty_edge(name_edge)
    return guess, f"the name says {said}, the energies cover {lo:.0f}–{hi:.0f} eV: {pretty_edge(guess)} used"


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
