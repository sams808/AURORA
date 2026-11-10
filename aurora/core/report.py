"""The final report: a multi-page PDF organised by sample group, built from the manual zones and their per-scan
settings (docs/HANDOFF.md section 6), plus one CSV per zone and scan and the group figures as PNG + SVG.

Headless (matplotlib Agg); the Report perspective and `aurora report` both call ReportBuilder.

Builds never overwrite or race:
- the builder works on a snapshot of the zones (ZonesFile.from_json of a deep copy) and a frozen copy of the stacks it
  is given, so an edit made while a report is being built cannot change it;
- each build is written into `<out>/.building-<time>-<pid>-<token>/` and renamed to `<out>/<YYYY-mm-dd_HHMM>_<scope>/`
  when it is complete (a suffix _2, _3 ... when that folder exists); `<out>/latest.json` names the latest build and
  lists the last KEEP_BUILDS builds (older build folders are never deleted);
- CSV names carry the holder, the zone id, the label and the scan, each slug cut at SLUG_MAX characters plus a short
  hash of the full text when it was cut; a name already used by another source gets a hash too;
- a target file that is locked by another program is written as <name>_2.<ext>, and the build says so.

Scope and presets: holders and days restrict the build; PRESETS 'final' (everything, 600 dpi) and 'draft' (110 dpi,
no Appendix A or B). Explicit png_dpi / appendix / maps arguments override the preset.

Provenance: every CSV starts with '# AURORA x.y.z (version 2 of John Bussey's SGM XAS GUI)' and carries the zone
geometry, the members count, the full settings, the data source, the ROI channels per SDD, the good-I0 contributors,
skipped / masked energies, spikes in eV, the signal and the flags with their source (aurora.core.provenance), with a
JSON sidecar. report_manifest.json holds every entry (settings, members, flags, CSV sha256), the pre-flight findings
and changes_since (what changed since the previous build); ReportBuilder.verify(manifest) rebuilds the CSVs in a
temporary folder and names every entry whose checksum differs.

Pre-flight: plan() / preflight() say, before building, what the report will contain and what to check (zones without
a group or an included scan, labels not in the sample sheet, stacks covered by no zone, scans covering a zone without
settings, an empty good-I0 pool, saturated detectors still ticked, ...). Coverage is computed from the h5 coordinates.

Author: the builder prints exactly the author it is given ('' = no author line). resolve_author() gives the Settings
value, else the OS account's full name; the Report perspective adds a one-time prompt.

Defaults still to be confirmed are marked DEFAULT in the code.
"""
from __future__ import annotations

import copy
import datetime as dt
import difflib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402

from aurora import APP_NAME, __version__  # noqa: E402
from aurora.credits import CREDITS, CREDITS_SHORT, FOOTER  # noqa: E402
from aurora.core import provenance  # noqa: E402
from aurora.core.constants import DETS  # noqa: E402
from aurora.core.flags import Flag, merged_flags  # noqa: E402
from aurora.core.io.config import ScanSettings, Zone, ZonesFile, read_sample_sheet  # noqa: E402
from aurora.core.io.textio import read_json, write_json_atomic  # noqa: E402
from aurora.core.model import Project, ScanInfo  # noqa: E402
from aurora.core.names import pretty_edge, short_time, split_stack_id  # noqa: E402
from aurora.core.stack import TOTAL_BOUNDS, TOTAL_LABEL, StackData  # noqa: E402
from aurora.core.survey import SurveyData  # noqa: E402
from aurora.core.spectra import ZoneTraces, good_i0_curve  # noqa: E402
from aurora.core.zones import grid_pitch, members, zone_traces  # noqa: E402

log = logging.getLogger("aurora.report")

AUTHOR = ""                     # never a default name: the author is resolved when a report is built (resolve_author)
CREDIT = CREDITS                # the cover credits: one source, aurora.credits
UNGROUPED = "Ungrouped"
# Okabe-Ito, colour never alone: the line style cycles too
PAL = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442", "#000000"]
STYLES = ["-", "--", "-.", ":"]
TRACES_PER_PAGE = 8          # DEFAULT
PAGE = (8.27, 11.69)         # A4 portrait, inches
NORM_TEXT = {"raw": "raw counts", "i0": "divided by I0", "i0pre": "divided by I0, pre-edge line removed",
             "i0jump": "divided by I0, pre-edge line removed, edge jump = 1", "area": "divided by I0, area = 1",
             "max": "divided by I0, maximum = 1"}
# presets: Final = everything at 600 dpi; Draft = quick, 110 dpi, no Appendix A or B (Appendix C, the quality notes,
# is always printed). The Report perspective also limits a Draft to the current holder.
PRESETS = {"final": {"png_dpi": 600, "appendix": True, "maps": True},
           "draft": {"png_dpi": 110, "appendix": False, "maps": False}}
PRESET_TEXT = {"final": "Final: every holder and group, Appendix A and B, figures at 600 dpi",
               "draft": "Draft: figures at 110 dpi, no Appendix A or B"}
MANIFEST = "report_manifest.json"
LATEST = "latest.json"
BUILDING_PREFIX = ".building-"
KEEP_BUILDS = 5              # builds listed in latest.json (build folders themselves are never deleted)
PREVIOUS_LOOKUP = 20         # newest build folders read when looking for the previous build of the same scope
SLUG_MAX = 40                # characters of a label or scan name in a file name; longer ones get a short hash
CSV_FMT = "%.15g"            # 15 significant digits: the CSV numbers reproduce the computation to 1e-14 relative
LOCK_SUFFIXES = 9            # <name>_2 .. <name>_9 are tried when a target file is locked


def slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s.replace("α", "a").replace("β", "b")).strip("_")
    return s or "x"


def short_hash(text: str, n: int = 6) -> str:
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:n]


def slug_name(text: str, max_len: int = SLUG_MAX) -> str:
    """slug(text), cut at max_len characters plus '-<6-character hash of the full text>' when it was longer."""
    s = slug(text)
    if len(s) > max_len:
        s = s[:max_len].rstrip("_.-") + "-" + short_hash(text)
    return s


def _plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


# ----------------------------------------------------------------------------- the report author
def _windows_full_name() -> str:
    import ctypes
    from ctypes import wintypes
    name_display = 3                                            # EXTENDED_NAME_FORMAT NameDisplay
    fn = ctypes.windll.secur32.GetUserNameExW
    fn.argtypes = [ctypes.c_int, wintypes.LPWSTR, ctypes.POINTER(wintypes.ULONG)]
    fn.restype = wintypes.BOOLEAN
    size = wintypes.ULONG(0)
    fn(name_display, None, ctypes.byref(size))
    if size.value:
        buf = ctypes.create_unicode_buffer(size.value + 1)
        if fn(name_display, buf, ctypes.byref(size)) and buf.value.strip():
            return buf.value.strip()
    # a local account outside a domain: the 'Full name' of the account (NetUserGetInfo level 10)

    class UserInfo10(ctypes.Structure):
        _fields_ = [("name", wintypes.LPWSTR), ("comment", wintypes.LPWSTR), ("usr_comment", wintypes.LPWSTR),
                    ("full_name", wintypes.LPWSTR)]
    import getpass
    net = ctypes.windll.netapi32
    net.NetUserGetInfo.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.POINTER(UserInfo10))]
    net.NetUserGetInfo.restype = wintypes.DWORD
    ptr = ctypes.POINTER(UserInfo10)()
    if net.NetUserGetInfo(None, getpass.getuser(), 10, ctypes.byref(ptr)) == 0 and ptr:
        try:
            return (ptr.contents.full_name or "").strip()
        finally:
            net.NetApiBufferFree(ptr)
    return ""


def os_full_name() -> str:
    """The full name of the OS account, '' when the system has none: Windows GetUserNameExW(NameDisplay), then the
    local account's full name; macOS and Linux the first field of the passwd gecos."""
    try:
        if sys.platform.startswith("win"):
            return _windows_full_name()
        import pwd
        gecos = pwd.getpwuid(os.getuid()).pw_gecos or ""
        return gecos.split(",")[0].strip()
    except Exception as err:                    # noqa: BLE001  no name is a normal answer
        log.info("OS account name unavailable (%s)", err)
        return ""


def resolve_author(setting: str | None = None) -> tuple[str, str]:
    """(author, source) in the order of the specification: the Settings value ('Settings'), else the OS account's
    full name ('the system account'), else ('', ''). The Report perspective then asks once."""
    s = (setting or "").strip()
    if s:
        return s, "Settings"
    name = os_full_name().strip()
    if name:
        return name, "the system account"
    return "", ""


# ----------------------------------------------------------------------------- holders, sheet, coordinates
def holder_keys(project: Project | None) -> dict[str, tuple[str, object]]:
    """The holder keys of a project as the session and the zone tool use them: the holder name, suffixed with the
    day when a name repeats. key -> (day, Holder)."""
    out: dict[str, tuple[str, object]] = {}
    if project is None:
        return out
    seen: dict[str, int] = {}
    for day in project.days:
        for h in day.holders:
            seen[h.name] = seen.get(h.name, 0) + 1
    for day in project.days:
        for h in day.holders:
            out[h.name if seen[h.name] == 1 else f"{h.name}_{day.date}"] = (day.date, h)
    return out


def group_rank(sheet_rows) -> dict[str, int]:
    """The sample groups in the order of their first row in the sample sheet ('Sample group' column)."""
    rank: dict[str, int] = {}
    for r in sheet_rows or []:
        g = str(getattr(r, "group", "") or "").strip()
        if g and g not in rank:
            rank[g] = len(rank)
    return rank


def group_key(rank: dict[str, int] | None = None):
    """Sort key of the report's groups: the sample sheet's order (group_rank), then the groups the sheet does not
    name alphabetically, Ungrouped last."""
    rank = rank or {}
    return lambda g: (0, rank[g], "") if g in rank else (2 if g == UNGROUPED else 1, 0, g.lower())


def load_sample_sheet(project: Project | None) -> list:
    """The first parsable sample sheet at the project root (the session's rule); [] when there is none."""
    if project is None:
        return []
    for s in sorted(Path(project.root).glob("*.csv")):
        try:
            rows = read_sample_sheet(s)
        except Exception:                       # noqa: BLE001  not a sample sheet
            continue
        if rows:
            return rows
    return []


_XY_CACHE: dict[tuple[str, int, int], tuple[np.ndarray, np.ndarray, tuple[float, float]]] = {}
_XY_CACHE_MAX = 1024


def stack_coords(scan: ScanInfo, stacks=None) -> tuple[np.ndarray, np.ndarray, tuple[float, float]] | None:
    """(x, y, pitch) of a stack: from a loaded StackData when one is given (the pixels the report will use), else
    from the h5 hexapod waves (cached per file and modification time). None when the h5 is missing or unreadable."""
    sid = f"{scan.day}_{scan.name}"
    sd = (stacks or {}).get(sid)
    if sd is not None:
        return sd.x, sd.y, sd.pitch
    from aurora.core.io.h5 import find_h5, xy
    h5 = scan.h5 or find_h5(scan.path)
    if h5 is None:
        return None
    try:
        st = Path(h5).stat()
        key = (str(h5), st.st_mtime_ns, st.st_size)
        if key not in _XY_CACHE:
            x, y = xy(h5)
            if len(_XY_CACHE) >= _XY_CACHE_MAX:
                _XY_CACHE.pop(next(iter(_XY_CACHE)))
            _XY_CACHE[key] = (x, y, grid_pitch(x, y))
        return _XY_CACHE[key]
    except Exception as err:                    # noqa: BLE001  a scan still being written
        log.info("%s: coordinates unreadable (%s)", h5, err)
        return None


def _norm_label(s: str) -> str:
    """A label or a sample ID compared loosely: the leading plate number ('13 ') dropped, case, spaces and punctuation
    ignored."""
    s = re.sub(r"^\s*\d+[A-Za-z]?\s+", "", str(s))
    return re.sub(r"[^0-9a-z]+", "", s.lower())


def _label_matches(n: str, sheet_norm: str) -> bool:
    """A zone label matches a sheet sample when the two are equal loosely, or the label starts with the sample ID
    (at least 4 characters), e.g. '4 hematite Fe2O3' and 'hematite'."""
    return bool(n) and (n == sheet_norm or (len(sheet_norm) >= 4 and n.startswith(sheet_norm)))


def _plates(plate: str) -> set[str]:
    return {p.strip().rstrip("?").strip() for p in str(plate).split("/") if p.strip()}


# ----------------------------------------------------------------------------- pre-flight
FINDING_KINDS = {
    "ungrouped": "zones without a group",
    "no_included": "zones without an included scan",
    "no_pixels": "included scans with no pixel in the zone",
    "scan_missing": "scans not found in the project",
    "label_not_in_sheet": "labels not in the sample sheet",
    "duplicate_label": "the same label on several holders of a group",
    "uncovered_stack": "complete stacks covered by no zone",
    "no_settings": "scans covering a zone without settings",
    "good_i0_empty": "good-I0 asked for with an empty pool",
    "saturated_ticked": "saturated detectors still ticked",
    "sheet_row_no_zone": "sample-sheet rows without a zone",
}
NOTE_KINDS = {"ungrouped", "label_not_in_sheet", "sheet_row_no_zone", "duplicate_label"}     # the others drop data


@dataclass(frozen=True)
class Finding:
    """One thing to check before building. `id` is stable across sessions (the ignore list keeps ids)."""
    id: str
    kind: str
    text: str
    holder: str | None = None                  # the holder key (session / zones file)
    zone_id: int | None = None
    sid: str | None = None

    @property
    def tone(self) -> str:
        return "muted" if self.kind in NOTE_KINDS else "warn"

    def to_json(self) -> dict:
        return {"id": self.id, "kind": self.kind, "text": self.text, "holder": self.holder, "zone_id": self.zone_id,
                "sid": self.sid}


@dataclass
class PlannedZone:
    """A zone as the next build will treat it: its spectra (sid, edge, pixels) or the reason it is left out."""
    holder_key: str
    holder: str
    day: str
    zone_id: int
    label: str
    group: str
    spectra: list[tuple[str, str, int]] = field(default_factory=list)
    skip: str = ""

    @property
    def included(self) -> int:
        return len(self.spectra)

    @property
    def edges(self) -> list[str]:
        return sorted({e for _s, e, _n in self.spectra if e})


@dataclass
class Plan:
    """The contents of the next build (group ▸ holder ▸ zone) and the pre-flight findings."""
    zones: list[PlannedZone] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    group_rank: dict[str, int] = field(default_factory=dict)      # the sample sheet's group order

    @property
    def n_zones(self) -> int:
        return sum(1 for z in self.zones if z.spectra)

    @property
    def n_spectra(self) -> int:
        return sum(len(z.spectra) for z in self.zones)

    def open_findings(self, ignored=()) -> list[Finding]:
        ign = set(ignored or ())
        return [f for f in self.findings if f.id not in ign]

    def totals(self, ignored=()) -> str:
        """'14 zones · 31 spectra · 3 warnings' (ignored findings are not counted)."""
        return " · ".join([_plural(self.n_zones, "zone"), _plural(self.n_spectra, "spectrum", "spectra"),
                           _plural(len(self.open_findings(ignored)), "warning")])

    def by_group(self) -> dict[str, list[PlannedZone]]:
        out: dict[str, list[PlannedZone]] = {}
        for z in self.zones:
            out.setdefault(z.group, []).append(z)
        return {g: out[g] for g in sorted(out, key=group_key(self.group_rank))}


def _in_scope(key: str, holder, day: str, holders, days) -> bool:
    """`holder` is a HolderZones (its .holder) or a model Holder (its .name); holders match the key or the name."""
    if holders:
        names = {key, getattr(holder, "holder", None) or getattr(holder, "name", None) or key}
        if not names & set(holders):
            return False
    if days and day not in set(days):
        return False
    return True


def plan(project: Project, zones: ZonesFile, sheet_rows=None, holders=None, days=None, groups=None, stacks=None,
         progress=None) -> Plan:
    """What the next build will contain and what to check first. Reads only h5 coordinates (or the stacks given),
    never the binaries. `sheet_rows` None = the project's sample sheet; holders / days / groups as for ReportBuilder."""
    stacks = stacks or {}
    hk = holder_keys(project)
    keys = list(hk) + [k for k in zones.holders if k not in hk]
    rows = load_sample_sheet(project) if sheet_rows is None else list(sheet_rows)
    sheet = {_norm_label(r.sample_id): r.sample_id for r in rows if _norm_label(r.sample_id)}
    good_pool = [g for g, on in zones.good_i0.items() if on]
    out = Plan(group_rank=group_rank(rows))
    findings: list[Finding] = []
    seen_labels: dict[tuple[str, str], list[tuple[str, int, str]]] = {}    # (group, norm label) -> [(key, zid, holder)]
    in_scope = []
    for key in keys:
        day, h = hk.get(key, (None, None))
        hz = zones.holders.get(key)
        d = day if day is not None else (hz.day if hz else "")
        if _in_scope(key, hz or h, d, holders, days):
            in_scope.append((key, d, h, hz))
    n_steps = len(in_scope)
    for step, (key, day, h, hz) in enumerate(in_scope):
        hname = hz.holder if hz else (h.name if h else key)
        if progress:
            progress(step, n_steps, f"checking holder {hname}")
        complete = list(h.stacks) if h is not None else []
        coords = {f"{s.day}_{s.name}": (s, stack_coords(s, stacks)) for s in complete}
        covered: set[str] = set()
        zlist = list(hz.zones) if hz else []
        for z in zlist:                                     # coverage counts every zone, whatever the group filter
            for sid, (s, c) in coords.items():
                if c is not None and members(z, *c).size:
                    covered.add(sid)
        for z in zlist:
            group = z.group.strip() or UNGROUPED
            if groups and group not in groups:
                continue
            pz = PlannedZone(key, hname, day, z.id, z.label, group)
            who = f"'{z.label}' ({hname})"
            if not z.group.strip():
                findings.append(Finding(f"ungrouped|{key}|{z.id}", "ungrouped",
                                        f"{who} has no group: it goes under '{UNGROUPED}'.", key, z.id))
            included = [(sid, st) for sid, st in z.settings.items() if st.include]
            good_asked = []
            for sid, st in included:
                scan = project.find(split_stack_id(sid)[1])
                if scan is None:
                    findings.append(Finding(f"scan_missing|{key}|{z.id}|{sid}", "scan_missing",
                                            f"{who}: scan {split_stack_id(sid)[1]} is not in this project, so it is "
                                            "left out.", key, z.id, sid))
                    continue
                c = coords[sid][1] if sid in coords else stack_coords(scan, stacks)
                if c is None:
                    findings.append(Finding(f"scan_missing|{key}|{z.id}|{sid}", "scan_missing",
                                            f"{who}: scan {scan.name} has no readable .h5, so it is left out.",
                                            key, z.id, sid))
                    continue
                n_pix = int(members(z, *c).size)
                if n_pix == 0:
                    findings.append(Finding(f"no_pixels|{key}|{z.id}|{sid}", "no_pixels",
                                            f"{who} has no pixel on {scan.time} {scan.label}, so that scan is left "
                                            "out.", key, z.id, sid))
                    continue
                pz.spectra.append((sid, pretty_edge(scan.edge), n_pix))
                flags = merged_flags(project, scan)
                ticked = {d for d, on in zip(DETS, st.sdd) if on}
                for f in flags:
                    hit = sorted(set(f.dets) & ticked) if f.kind == "sdd_saturated" else []
                    if hit:
                        findings.append(Finding(f"saturated_ticked|{key}|{z.id}|{sid}|{','.join(hit)}",
                                                "saturated_ticked",
                                                f"{who} on {scan.time} still sums {', '.join(d.upper() for d in hit)}, "
                                                f"flagged saturated ({f.source}).", key, z.id, sid))
                if st.i0_source == "good":
                    pool = [g for g in good_pool if _overlaps(project, g, scan)]
                    if not pool:
                        good_asked.append(scan.time)
            if good_asked:
                findings.append(Finding(f"good_i0_empty|{key}|{z.id}", "good_i0_empty",
                                        f"{who} asks for the good-I0 mean on {', '.join(good_asked)}, but no good-I0 "
                                        "scan covers those energies: its own I0 is used.", key, z.id))
            for sid, (s, c) in coords.items():
                if sid not in z.settings and c is not None and members(z, *c).size:
                    findings.append(Finding(f"no_settings|{key}|{z.id}|{sid}", "no_settings",
                                            f"{s.time} {s.label} covers {who} but has no settings for it yet: select "
                                            "the scan in Reduce to attach it.", key, z.id, sid))
            if not pz.spectra:
                if not included:
                    pz.skip = "no scan attached yet" if not z.settings else "no included scan"
                    findings.append(Finding(f"no_included|{key}|{z.id}", "no_included",
                                            f"{who} has {pz.skip}, so it is left out.", key, z.id))
                else:
                    pz.skip = "no included scan with a pixel in the zone"
            if sheet and z.label.strip():
                n = _norm_label(z.label)
                if not any(_label_matches(n, s) for s in sheet):
                    near = difflib.get_close_matches(n, list(sheet), n=1, cutoff=0.6)
                    findings.append(Finding(f"label_not_in_sheet|{key}|{z.id}|{z.label}", "label_not_in_sheet",
                                            f"{who} is not in the sample sheet"
                                            + (f"; closest: '{sheet[near[0]]}'" if near else "") + ".", key, z.id))
            seen_labels.setdefault((group, _norm_label(z.label) or z.label), []).append((key, z.id, hname))
            out.zones.append(pz)
        for sid, (s, c) in coords.items():
            if c is not None and sid not in covered:
                findings.append(Finding(f"uncovered_stack|{key}|{sid}", "uncovered_stack",
                                        f"{s.time} {s.label} ({hname}) is covered by no zone.", key, None, sid))
        if rows:
            labels = [_norm_label(z.label) for z in zlist]
            for r in rows:
                if hname in _plates(r.plate):
                    rn = _norm_label(r.sample_id)
                    if rn and not any(_label_matches(n, rn) for n in labels):
                        findings.append(Finding(f"sheet_row_no_zone|{key}|{r.sample_id}", "sheet_row_no_zone",
                                                f"Sample '{r.sample_id}' of {hname} (sample sheet) has no zone.", key))
    for (group, _n), where in seen_labels.items():
        holders_of = sorted({hn for _k, _z, hn in where})
        if len(holders_of) > 1:
            k0, z0, _h = where[0]
            label = next((z.label for z in zones.holders[k0].zones if z.id == z0), _n) if k0 in zones.holders else _n
            findings.append(Finding(f"duplicate_label|{group}|{_n}", "duplicate_label",
                                    f"'{label}' appears in group {group} on holders {', '.join(holders_of[:-1])} and "
                                    f"{holders_of[-1]}.", k0, z0))
    out.findings = findings
    return out


def preflight(project: Project, zones: ZonesFile, **kw) -> list[Finding]:
    """The findings of plan(project, zones, **kw): what to check before building."""
    return plan(project, zones, **kw).findings


def _overlaps(project: Project, sid: str, scan: ScanInfo) -> bool:
    g = project.find(split_stack_id(sid)[1])
    if g is None:
        return False
    if not (np.isfinite(g.e_min) and np.isfinite(scan.e_min)):
        return True                                       # ranges unknown: assume it applies
    return not (g.e_min > scan.e_max or g.e_max < scan.e_min)


# ----------------------------------------------------------------------------- entries and results
@dataclass
class Entry:
    """One zone on one included scan: everything the report needs."""
    holder_key: str
    holder: str
    day: str
    zone: Zone
    sid: str
    scan: ScanInfo
    stack: StackData
    settings: ScanSettings
    traces: ZoneTraces
    group: str
    flags: list[str] = field(default_factory=list)          # 'text (source)' of every flag
    members: np.ndarray = field(default_factory=lambda: np.zeros(0, int))
    flag_objs: list[Flag] = field(default_factory=list)
    good_sids: list[str] = field(default_factory=list)     # the good-I0 scans averaged for this entry

    @property
    def label(self) -> str:
        return self.zone.label

    @property
    def edge_key(self) -> str:
        return pretty_edge(self.stack.edge) or "unknown edge"

    @property
    def title(self) -> str:
        return f"{self.zone.label} · {self.holder} · {self.scan.time} {self.edge_key}"

    @property
    def key(self) -> tuple[str, int, str]:
        return (self.holder_key, int(self.zone.id), self.sid)

    @property
    def spikes_removed(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.traces.spikes.items() if isinstance(v, list) and v}


@dataclass
class ReportResult:
    pdf: Path
    pages: int
    figures: list[Path]
    csvs: list[Path]
    entries: int
    skipped: list[str]
    manifest: Path
    page_pngs: list[Path] = field(default_factory=list)
    folder: Path | None = None                  # the build folder (<out>/<YYYY-mm-dd_HHMM>_<scope>)
    notes: list[str] = field(default_factory=list)      # what the build worked around (a locked file, ...)
    changes: dict = field(default_factory=dict)         # changes_since
    plan: Plan | None = None


@dataclass
class Verification:
    ok: bool
    checked: int
    differences: list[str]
    manifest: Path
    notes: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok

    def text(self) -> str:
        if self.ok:
            return f"{self.manifest.parent.name}: all {self.checked} CSV files reproduce their checksums."
        return (f"{self.manifest.parent.name}: {_plural(len(self.differences), 'entry', 'entries')} differ from the "
                "current project:\n" + "\n".join(f"  {d}" for d in self.differences))


def _entry_key(d: dict) -> tuple[str, int, str]:
    return (str(d.get("holder_key", d.get("holder", ""))), int(d.get("zone_id", 0)), str(d.get("sid", "")))


def _entry_desc(holder: str, label: str, zid, sid: str) -> str:
    return f"{holder} · '{label}' (zone {zid}) · {short_time(sid) or sid}"


# ----------------------------------------------------------------------------- the builder
class ReportBuilder:
    def __init__(self, project: Project, zones: ZonesFile, out: Path | None = None, title: str | None = None,
                 groups: list[str] | None = None, holders: list[str] | None = None, preset: str = "final",
                 author: str = AUTHOR, stacks: dict[str, StackData] | None = None, appendix: bool | None = None,
                 maps: bool | None = None, png_dpi: int | None = None, page_png_dpi: int = 110,
                 days: list[str] | None = None, sheet_rows: list | None = None, out_dir: Path | None = None):
        if preset not in PRESETS:
            raise ValueError(f"unknown report preset {preset!r} (one of {', '.join(PRESETS)})")
        if out is None and out_dir is None:
            raise TypeError("ReportBuilder needs the reports folder (out)")
        p = PRESETS[preset]
        self.project = project
        # the snapshot: an edit of the session's zones during the build cannot reach the report
        self.zones = ZonesFile.from_json(copy.deepcopy(zones.to_json()))
        self.root = Path(out if out is not None else out_dir)       # the reports folder; one subfolder per build
        self.out = self.root                    # the folder being written: the build folder during and after build()
        self.folder: Path | None = None
        self.title = title or f"{APP_NAME} report · {self.zones.project or project.root.name}"
        self.groups = list(groups) if groups else None
        self.holders = list(holders) if holders else None
        self.days = list(days) if days else None
        self.preset = preset
        self.author = str(author or "").strip()
        self._given = MappingProxyType(dict(stacks or {}))          # frozen copy of the caller's stacks
        self._loaded: dict[str, StackData] = {}
        self.appendix = p["appendix"] if appendix is None else bool(appendix)
        self.maps = p["maps"] if maps is None else bool(maps)
        self.png_dpi = int(p["png_dpi"] if png_dpi is None else png_dpi)
        self.page_png_dpi = page_png_dpi
        self.sheet_rows = None if sheet_rows is None else list(sheet_rows)
        self.skipped: list[str] = []
        self.not_included: list[tuple[str, str, str]] = []
        self.page_pngs: list[Path] = []
        self._page_no = 0
        self.footers: list[str] = []            # the footer text of every page, in order
        self.cover_credits = ""                 # the credits printed on the cover
        self.cover_lines: list[str] = []        # the text lines of the cover, as printed
        self.appendix_c: list[str] = []         # the text lines of Appendix C
        self.notes: list[str] = []
        self.changes: dict = {}
        self.plan: Plan | None = None
        self._names: dict[str, dict[str, str]] = {}

    # ------------------------------------------------------------------ scope
    def scope_keys(self) -> list[str]:
        """Holder keys this build covers (project holders and zone holders in the holders / days scope)."""
        hk = holder_keys(self.project)
        out = []
        for key in list(hk) + [k for k in self.zones.holders if k not in hk]:
            hz = self.zones.holders.get(key)
            day = hk[key][0] if key in hk else (hz.day if hz else "")
            if _in_scope(key, hz or (hk[key][1] if key in hk else None), day, self.holders, self.days):
                out.append(key)
        return out

    def scope_slug(self) -> str:
        """The scope part of the build folder name: 'all', 'N5', 'N5+N6', '2026-11-05', ... plus '_draft'."""
        parts = []
        for vals, n in ((self.holders, 3), (self.days, 2), (self.groups, 2)):
            if vals:
                vs = [slug(v) for v in vals]
                parts.append("+".join(vs[:n]) + (f"+{len(vs) - n}" if len(vs) > n else ""))
        s = "_".join(parts) or "all"
        if self.preset != "final":
            s += f"_{self.preset}"
        return slug_name(s, 48)

    # ------------------------------------------------------------------ data
    def _stack_source(self) -> dict:
        return {**self._given, **self._loaded}

    def stack(self, sid: str) -> StackData | None:
        if sid in self._given:
            return self._given[sid]
        if sid not in self._loaded:
            scan = self.project.find(split_stack_id(sid)[1])
            if scan is None:
                self._skip(f"{sid}: scan not in the project")
                return None
            try:
                self._loaded[sid] = StackData.load(self.project, scan)
            except Exception as err:
                self._skip(f"{sid}: {err}")
                return None
        return self._loaded[sid]

    def _skip(self, msg: str) -> None:
        if msg not in self.skipped:
            self.skipped.append(msg)

    def good_curve(self, E: np.ndarray) -> np.ndarray | None:
        return self.good_curve_and_sids(E)[0]

    def good_curve_and_sids(self, E: np.ndarray) -> tuple[np.ndarray | None, list[str]]:
        """The good-I0 mean at the energies E and the good scans that contribute (their range overlaps E)."""
        curves, sids = [], []
        for sid, good in self.zones.good_i0.items():
            if good:
                sd = self.stack(sid)
                if sd is not None:
                    curves.append((sd.E, sd.i0_mean()))
                    if not (sd.E[0] > E[-1] or sd.E[-1] < E[0]):
                        sids.append(sid)
        curve = good_i0_curve(E, curves) if curves else None
        return curve, (sids if curve is not None else [])

    def collect(self, progress=None) -> list[Entry]:
        entries: list[Entry] = []
        self.not_included = []
        keys = set(self.scope_keys())
        items = [(hk, hz, z, sid, st) for hk, hz in self.zones.holders.items() if hk in keys
                 for z in hz.zones for sid, st in z.settings.items()]
        for i, (hk, hz, z, sid, st) in enumerate(items):
            if progress:
                progress(i, len(items), f"{hz.holder} {z.label} {short_time(sid)}")
            group = z.group.strip() or UNGROUPED
            if self.groups and group not in self.groups:
                continue
            scan = self.project.find(split_stack_id(sid)[1])
            if not st.include:
                self.not_included.append((hz.holder, z.label, split_stack_id(sid)[1]))
                continue
            sd = self.stack(sid)
            if sd is None or scan is None:
                continue
            pix = members(z, sd.x, sd.y, sd.pitch)
            if pix.size == 0:
                self._skip(f"{hz.holder} {z.label} {split_stack_id(sid)[1]}: no pixel inside the zone")
                continue
            good, good_sids = self.good_curve_and_sids(sd.E) if st.i0_source == "good" else (None, [])
            t = zone_traces(sd, pix, st, good)
            if t is None:
                continue
            fl = list(merged_flags(self.project, scan))
            if st.i0_source == "good" and good is None:
                fl.append(Flag("good-I0 requested but no good scan ticked: own I0 used", "note", "report"))
            entries.append(Entry(holder_key=hk, holder=hz.holder, day=hz.day, zone=z, sid=sid, scan=scan, stack=sd,
                                 settings=st, traces=t, group=group, flags=[str(f) for f in fl], members=pix,
                                 flag_objs=fl, good_sids=good_sids))
        log.info("report: %d zone-scan entries, %d skipped, %d not included", len(entries), len(self.skipped), len(self.not_included))
        return entries

    # ------------------------------------------------------------------ state recorded for the next build
    def state(self) -> dict:
        """The zones, settings and scans of the holders in scope, as the manifest records them (changes_since)."""
        keys = self.scope_keys()
        zones, settings = [], {}
        for key in keys:
            hz = self.zones.holders.get(key)
            for z in (hz.zones if hz else []):
                zones.append({"holder": key, "id": z.id, "label": z.label, "group": z.group})
                for sid, st in z.settings.items():
                    settings[f"{key}|{z.id}|{sid}"] = st.to_json()
        hk = holder_keys(self.project)
        scans = {k: sorted(f"{s.day}_{s.name}" for s in hk[k][1].stacks) for k in keys if k in hk}
        return {"holders": keys, "zones": zones, "settings": settings, "scans": scans}

    def previous_build(self) -> tuple[str, dict] | None:
        """(folder name, manifest) of the build to compare with: the latest build of the same scope listed in
        latest.json or found in the reports folder, else the latest build of any scope."""
        root = self.root
        names: list[str] = []
        lat = read_json(root / LATEST, None)
        if isinstance(lat, dict):
            names += [str(lat.get("latest") or "")] + [str(b.get("folder", "")) for b in lat.get("builds", [])
                                                        if isinstance(b, dict)]
        try:
            names += sorted((d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")
                             and (d / MANIFEST).exists()), reverse=True)[:PREVIOUS_LOOKUP]
        except OSError:
            pass
        if (root / MANIFEST).exists():
            names.append(".")                   # a flat build of AURORA 0.3 in the reports folder itself
        first: tuple[str, dict] | None = None
        want = self.scope_slug()
        for n in dict.fromkeys(x for x in names if x):          # newest first; stop at the first of the same scope
            man = read_json(root / n / MANIFEST, None)
            if not isinstance(man, dict):
                continue
            if (man.get("scope") or {}).get("slug") == want:
                return n, man
            first = first or (n, man)
        return first

    @staticmethod
    def changes_between(prev: dict, prev_name: str, cur: dict) -> dict:
        """changes_since: zones added, removed or renamed, settings changed and scans added, between the previous
        manifest and the current state, over the holders both builds cover."""
        out = {"previous": prev_name, "previous_built": prev.get("built", ""), "zones_added": [], "zones_removed": [],
               "zones_renamed": [], "settings_changed": [], "scans_added": []}
        if "zones" not in prev:
            out["note"] = f"the previous build ({prev.get('aurora', '?')}) did not record its zones"
            return out
        common = set((prev.get("scope") or {}).get("holders") or [z["holder"] for z in prev["zones"]]) & set(cur["holders"])
        pz = {(z["holder"], int(z["id"])): z for z in prev["zones"] if z["holder"] in common}
        cz = {(z["holder"], int(z["id"])): z for z in cur["zones"] if z["holder"] in common}
        for k in cz.keys() - pz.keys():
            out["zones_added"].append(f"{k[0]} · '{cz[k]['label']}' (zone {k[1]})")
        for k in pz.keys() - cz.keys():
            out["zones_removed"].append(f"{k[0]} · '{pz[k]['label']}' (zone {k[1]})")
        for k in sorted(cz.keys() & pz.keys()):
            if cz[k]["label"] != pz[k]["label"]:
                out["zones_renamed"].append(f"{k[0]} zone {k[1]}: '{pz[k]['label']}' → '{cz[k]['label']}'")
        ps, cs = prev.get("settings") or {}, cur["settings"]
        label = {f"{z['holder']}|{z['id']}": z["label"] for z in cur["zones"]}
        for sk in sorted(set(ps) | set(cs)):
            hkey, zid, sid = sk.split("|", 2)
            if hkey not in common or (hkey, int(zid)) not in (cz.keys() & pz.keys()):
                continue
            who = f"{hkey} · '{label.get(f'{hkey}|{zid}', zid)}' · {short_time(sid) or sid}"
            if sk not in ps:
                out["settings_changed"].append(f"{who}: scan attached")
            elif sk not in cs:
                out["settings_changed"].append(f"{who}: scan detached")
            else:
                a, b = ScanSettings.from_json(ps[sk]), ScanSettings.from_json(cs[sk])
                fields = (["include"] if a.include != b.include else []) + a.differs_from(b)
                if fields:
                    out["settings_changed"].append(f"{who}: {', '.join(fields)}")
        prev_scans = prev.get("scans")
        if isinstance(prev_scans, dict):
            for key in sorted(common):
                old = set(prev_scans.get(key) or [])
                for sid in cur["scans"].get(key, []):
                    if sid not in old:
                        out["scans_added"].append(f"{key} · {split_stack_id(sid)[1]}")
        return out

    @staticmethod
    def changes_lines(changes: dict, limit: int = 8) -> list[str]:
        """The changes as short lines (the cover prints them)."""
        if not changes:
            return ["first build in this folder"]
        if changes.get("note"):
            return [changes["note"]]
        names = [("zones_added", "zone added"), ("zones_removed", "zone removed"), ("zones_renamed", "renamed"),
                 ("settings_changed", "settings"), ("scans_added", "scan added")]
        lines = [f"{word}: {item}" for key, word in names for item in changes.get(key, [])]
        if not lines:
            return ["no change in the zones, their settings or the scans"]
        return lines[:limit] + ([f"… and {len(lines) - limit} more (report_manifest.json)"] if len(lines) > limit else [])

    # ------------------------------------------------------------------ files
    def _claim(self, scope: str, base: str, key: str) -> str:
        """A file name unique in `scope`: `base`, or base plus a short hash of `key` when another source has it."""
        owners = self._names.setdefault(scope, {})
        name = base
        if owners.get(name, key) != key:
            name = f"{base}-{short_hash(key)}"
        owners[name] = key
        return name

    @staticmethod
    def _locked(path: Path) -> bool:
        """True when an existing file cannot be opened for writing (open in a viewer that locks it)."""
        if not path.exists():
            return False
        try:
            with open(path, "ab"):
                return False
        except PermissionError as err:
            from aurora.core.io.safety import ProtectedPathError
            if isinstance(err, ProtectedPathError):
                raise
            return True

    def _target(self, path: Path) -> Path:
        """`path`, or <name>_2.<ext>, <name>_3.<ext> ... when it is locked; the build notes the change."""
        cand = Path(path)
        for k in range(2, LOCK_SUFFIXES + 2):
            if not self._locked(cand):
                if cand != path:
                    self.notes.append(f"{path.name} was locked by another program: wrote {cand.name} instead")
                    log.warning("%s locked: writing %s", path, cand)
                return cand
            cand = path.with_name(f"{path.stem}_{k}{path.suffix}")
        raise PermissionError(f"{path}: locked, and so are {path.stem}_2 .. _{LOCK_SUFFIXES}")

    # ------------------------------------------------------------------ build
    def build(self, progress=None) -> ReportResult:
        """Write the report into a new build folder under the reports folder and return what was written."""
        from aurora.core.io.safety import assert_writable
        assert_writable(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        started = dt.datetime.now()
        building = self.root / f"{BUILDING_PREFIX}{started:%Y%m%d-%H%M%S}-{os.getpid()}-{secrets.token_hex(3)}"
        building.mkdir()
        self.out = building
        try:
            res = self._build_into(building, started, progress)
            final = self._finalise(building, started)
        except BaseException:
            shutil.rmtree(building, ignore_errors=True)
            self.out = self.root
            raise
        self.out = self.folder = final

        def moved(p: Path) -> Path:
            try:
                return final / Path(p).relative_to(building)
            except ValueError:
                return Path(p)
        res.pdf, res.manifest, res.folder = moved(res.pdf), moved(res.manifest), final
        res.figures = [moved(p) for p in res.figures]
        res.csvs = [moved(p) for p in res.csvs]
        res.page_pngs = [moved(p) for p in res.page_pngs]
        self.page_pngs = list(res.page_pngs)
        self._update_latest(final, started, res)
        res.notes = list(self.notes)
        log.info("report written: %s (%d pages), %d figures, %d csv", res.pdf, res.pages, len(res.figures), len(res.csvs))
        return res

    def _build_into(self, folder: Path, started: dt.datetime, progress=None) -> ReportResult:
        (folder / "figures").mkdir(exist_ok=True)
        (folder / "csv").mkdir(exist_ok=True)
        rows = self.sheet_rows if self.sheet_rows is not None else load_sample_sheet(self.project)
        self.plan = plan(self.project, self.zones, rows, self.holders, self.days, self.groups,
                         stacks=self._stack_source(), progress=progress)
        entries = self.collect(progress)
        state = self.state()
        prev = self.previous_build()
        self.changes = self.changes_between(prev[1], prev[0], state) if prev else {}
        by_group: dict[str, list[Entry]] = {}
        for e in entries:
            by_group.setdefault(e.group, []).append(e)
        groups = sorted(by_group, key=group_key(group_rank(rows)))         # the sample sheet's order
        date = started.date().isoformat()
        pdf_path = self._target(folder / f"{slug_name(self.title, 80)}_{date}.pdf")
        figures: list[Path] = []
        csvs: list[Path] = []
        n_pages = 0
        meta = {"Title": self.title, "Creator": f"{APP_NAME} {__version__}", "Subject": CREDITS_SHORT}
        if self.author:
            meta["Author"] = self.author             # the resolved report author; never a default name
        with PdfPages(pdf_path, metadata=meta) as pdf:
            n_pages += self._cover(pdf, entries, groups, by_group)
            for gi, g in enumerate(groups):
                if progress:
                    progress(gi, len(groups), f"group {g}")
                n_pages += self._group_pages(pdf, g, by_group[g], figures)
            if self.appendix and entries:
                n_pages += self._sdd_appendix(pdf, entries)
            if self.maps:
                n_pages += self._plate_maps(pdf, entries)
            n_pages += self._qc_pages(pdf, entries)
        man_entries = []
        for i, e in enumerate(entries):
            if progress:
                progress(i, len(entries), f"CSV {e.zone.label} {e.scan.time}")
            p, sha, side = self._write_csv(e, folder)
            csvs.append(p)
            man_entries.append({
                "holder": e.holder, "holder_key": e.holder_key, "day": e.day, "zone_id": e.zone.id,
                "label": e.zone.label, "group": e.group, "sid": e.sid, "scan": e.scan.name, "edge": e.edge_key,
                "settings": e.settings.to_json(), "members": [int(i) for i in e.members], "n_members": int(e.members.size),
                "flags": [{"text": f.text, "kind": f.kind, "source": f.source, "dets": list(f.dets)} for f in e.flag_objs],
                "good_i0": list(e.good_sids), "data_source": self._data_source(e)[0],
                "csv": str(p.relative_to(folder)).replace("\\", "/"),
                "sidecar": str(side.relative_to(folder)).replace("\\", "/"), "sha256": sha})
        ignored = set(self.zones.ui.get("preflight_ignored") or [])
        manifest = folder / MANIFEST
        write_json_atomic(manifest, {
            "title": self.title, "built": started.isoformat(timespec="seconds"), "aurora": __version__,
            "project": str(self.project.root), "zones_exported": self.zones.exported, "author": self.author,
            "pdf": pdf_path.name, "pages": n_pages,
            "options": {"preset": self.preset, "groups": self.groups, "holders": self.holders, "days": self.days,
                        "png_dpi": self.png_dpi, "appendix": self.appendix, "maps": self.maps,
                        "page_png_dpi": self.page_png_dpi},
            "scope": {"slug": self.scope_slug(), "holders": state["holders"], "days": self.days,
                      "groups": self.groups or "all"},
            "groups": {g: [e.title for e in by_group[g]] for g in groups},
            "skipped": self.skipped, "not_included": self.not_included, "figures": [f.name for f in figures],
            "csv": [str(c.relative_to(folder)).replace("\\", "/") for c in csvs],
            "entries": man_entries, "zones": state["zones"], "settings": state["settings"], "scans": state["scans"],
            "changes_since": self.changes,
            "preflight": [{**f.to_json(), "ignored": f.id in ignored} for f in self.plan.findings],
            "scans_without_settings": [f.text for f in self.plan.findings if f.kind == "no_settings"],
            "totals": self.plan.totals(ignored), "notes": self.notes})
        return ReportResult(pdf=pdf_path, pages=n_pages, figures=figures, csvs=csvs, entries=len(entries),
                            skipped=self.skipped, manifest=manifest, page_pngs=list(self.page_pngs), folder=folder,
                            changes=self.changes, plan=self.plan)

    def _finalise(self, building: Path, started: dt.datetime) -> Path:
        """Rename the finished build folder to <YYYY-mm-dd_HHMM>_<scope>, with _2, _3 ... when the name is taken."""
        base = f"{started:%Y-%m-%d_%H%M}_{self.scope_slug()}"
        for k in range(1, 1000):
            target = self.root / (base if k == 1 else f"{base}_{k}")
            if target.exists():
                continue
            try:
                self._rename(building, target)
                return target
            except FileExistsError:
                continue
            except OSError:
                if target.exists():             # another build took the name between the check and the rename
                    continue
                raise
        raise OSError(f"{self.root}: no free build folder name for {base}")

    def _rename(self, src: Path, dst: Path) -> None:
        last = None
        for attempt in range(6):
            try:
                os.rename(src, dst)
                return
            except FileExistsError:
                raise
            except PermissionError as err:      # a scanner or a preview still holds a new file for a moment
                last = err
                time.sleep(0.15 * (attempt + 1))
        shutil.copytree(src, dst)
        shutil.rmtree(src, ignore_errors=True)
        self.notes.append(f"the build folder could not be renamed ({last}); it was copied to {dst.name}")
        log.warning("build folder %s copied to %s (%s)", src, dst, last)

    def _update_latest(self, folder: Path, started: dt.datetime, res: ReportResult) -> None:
        p = self.root / LATEST
        old = read_json(p, {}) or {}
        builds = [b for b in old.get("builds", []) if isinstance(b, dict) and b.get("folder") != folder.name] \
            if isinstance(old, dict) else []
        entry = {"folder": folder.name, "built": started.isoformat(timespec="seconds"), "scope": self.scope_slug(),
                 "preset": self.preset, "pdf": f"{folder.name}/{res.pdf.name}", "entries": res.entries,
                 "aurora": __version__}
        try:
            write_json_atomic(p, {"latest": folder.name, "builds": ([entry] + builds)[:KEEP_BUILDS]})
        except OSError as err:
            self.notes.append(f"{LATEST} could not be updated ({err}); the build itself is complete")
            log.warning("%s not updated: %s", p, err)

    # ------------------------------------------------------------------ verify
    def verify(self, manifest_path) -> Verification:
        """Rebuild the CSVs of a build from the current project and zones in a temporary folder and compare their
        checksums with the manifest; every entry that differs, is gone or is new is named."""
        mp = Path(manifest_path)
        if mp.is_dir():
            mp = mp / MANIFEST
        man = read_json(mp, None)
        if not isinstance(man, dict) or "entries" not in man:
            raise ValueError(f"{mp}: no entries to verify (a manifest of AURORA 0.4 or later is needed)")
        opts = man.get("options") or {}
        notes = []
        if man.get("aurora") != __version__:
            notes.append(f"built with AURORA {man.get('aurora')}, verified with {__version__}: the first CSV line differs")
        with tempfile.TemporaryDirectory(prefix="aurora_verify_") as tmp:
            b = ReportBuilder(self.project, self.zones, Path(tmp), title=man.get("title"), groups=opts.get("groups"),
                              holders=opts.get("holders"), days=opts.get("days"), preset=opts.get("preset", "final"),
                              author=man.get("author", ""), stacks=self._stack_source())
            now = {e.key: e for e in b.collect()}
            old = {_entry_key(d): d for d in man["entries"]}
            diffs = []
            for k, d in old.items():
                desc = _entry_desc(d.get("holder", k[0]), d.get("label", "?"), k[1], k[2])
                e = now.get(k)
                if e is None:
                    diffs.append(f"{desc}: no longer in the report (zone, scan or include changed)")
                    continue
                _p, sha, _s = b._write_csv(e, Path(tmp))
                if sha != d.get("sha256"):
                    fields = []
                    try:
                        prev = ScanSettings.from_json(d.get("settings") or {})
                        fields = (["include"] if prev.include != e.settings.include else []) + prev.differs_from(e.settings)
                    except (TypeError, ValueError):
                        pass
                    if d.get("label") != e.zone.label:
                        fields.insert(0, "label")
                    diffs.append(f"{desc}: the CSV differs" + (f" (changed: {', '.join(fields)})" if fields else ""))
            for k, e in now.items():
                if k not in old:
                    diffs.append(f"{_entry_desc(e.holder, e.zone.label, k[1], k[2])}: not in that build (new zone or scan)")
        return Verification(ok=not diffs, checked=len(old), differences=diffs, manifest=mp, notes=notes)

    # ------------------------------------------------------------------ pages
    def footer_text(self) -> str:
        """'AURORA x.y.z · FOOTER · <project> · <author> · <date>' (no author part when the author is empty)."""
        parts = [f"{APP_NAME} {__version__}", FOOTER, self.zones.project or self.project.root.name, self.author,
                 dt.date.today().isoformat()]
        return " · ".join(p for p in parts if p)

    def _footer(self, fig, page_text: str = "") -> None:
        text = self.footer_text()
        self.footers.append(text)
        head, _, tail = text.partition(f" · {FOOTER} · ")       # two lines: the credit, then project / author / date
        lines = f"{head} · {FOOTER}\n{tail}" if tail else text
        fig.text(0.05, 0.012, lines, fontsize=6.5, color="#4E5D7A", linespacing=1.4)
        if page_text:
            fig.text(0.95, 0.012, page_text, fontsize=7, color="#4E5D7A", ha="right")

    def _save_page(self, pdf: PdfPages, fig) -> int:
        pdf.savefig(fig)
        self._page_no += 1
        if self.page_png_dpi:
            p = self.out / "pages" / f"page_{self._page_no:03d}.png"
            p.parent.mkdir(exist_ok=True)
            fig.savefig(p, dpi=self.page_png_dpi)
            self.page_pngs.append(p)
        plt.close(fig)
        return 1

    @staticmethod
    def _last_finite(y: np.ndarray) -> float:
        fin = y[np.isfinite(y)]
        return float(np.mean(fin[-5:])) if fin.size else 0.0

    def scope_text(self) -> str:
        """'Scope: holders N5; all groups; Draft preset (figures at 110 dpi, no Appendix A or B)'."""
        parts = [f"holders {', '.join(self.holders)}" if self.holders else "every holder"]
        if self.days:
            parts.append(f"days {', '.join(self.days)}")
        parts.append(f"groups {', '.join(self.groups)}" if self.groups else "all groups")
        apps = [n for n, on in (("Appendix A", self.appendix), ("Appendix B", self.maps)) if on]
        parts.append(f"{self.preset.capitalize()} preset (figures at {self.png_dpi} dpi, "
                     + (" and ".join(apps) if apps else "no Appendix A or B") + ")")
        return "Scope: " + "; ".join(parts)

    def _cover(self, pdf, entries, groups, by_group) -> int:
        fig = plt.figure(figsize=PAGE)
        fig.text(0.08, 0.90, self.title, fontsize=20, weight="bold", color="#15213B")
        proj = self.project
        days = sorted({e.day for e in entries}) or sorted(d.date for d in proj.days)
        holders = sorted({e.holder for e in entries})
        changes = self.changes_lines(self.changes, limit=6)
        prev = self.changes.get("previous") if self.changes else None
        lines = [
            f"Beamtime project {self.zones.project or proj.root.name}, SGM beamline (ambxas endstation), Canadian Light Source",
            f"Days: {', '.join(days)}",
            f"Holders: {', '.join(holders)}",
            f"{len(entries)} zone spectra in {len(groups)} sample groups; {len(self.not_included)} scans marked not included; "
            f"{len(self.skipped)} skipped (see QC pages)",
            self.scope_text(),
            "",
            "Provenance",
            f"  zones saved {self.zones.exported or '(no date recorded)'}"
            + (f" (zone tool build {self.zones.built})" if self.zones.built else ""),
            f"  {proj.calibration.describe(proj.root) if proj.calibration else 'no SDD calibration'}",
            "  fixed ROI windows: " + (", ".join(proj.roi_bins.describe(l) for l in proj.roi_bins.labels[:6]) if proj.roi_bins and proj.roi_bins.labels else "none")
            + (" …" if proj.roi_bins and len(proj.roi_bins.labels) > 6 else ""),
            f"  {APP_NAME} {__version__}, report built {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}"
            + (f" by {self.author}" if self.author else ""),
            "",
            "Changes since the previous build" + (f" ({prev})" if prev and prev != "." else ""),
        ] + [f"  {c}" for c in changes] + [
            "",
            "How to read the figures",
            "  Fluorescence = partial fluorescence yield of the line named in each panel, summed over the zone pixels",
            "  and the ticked silicon drift detectors, normalised as stated in the caption (default: divided by I0,",
            "  pre-edge line removed, edge jump = 1). TEY = total electron yield, same normalisation. Traces are offset.",
            "  Spikes (single-point outliers from loose electrons) were interpolated; the counts are in the QC pages.",
            "  Colour never carries the meaning alone: the line style and the label at the right end identify each trace.",
            "",
            "Contents",
        ] + [f"  {g}: {len(by_group[g])} spectra" for g in groups] + [
            "  Appendix A: the four detectors per zone" if self.appendix else "",
            "  Appendix B: plate maps with the zones" if self.maps else "",
            "  Appendix C: quality notes, the check before building, scans not included, skipped",
        ]
        heads = ("Provenance", "How to read the figures", "Contents", "Changes since the previous build")
        import textwrap
        # a line wider than the page (the calibration with its file and four gains) wraps, indented, never cut
        lines = [w for ln in lines for w in (textwrap.wrap(ln, 110, subsequent_indent=" " * (len(ln) - len(ln.lstrip()) + 2))
                                             if len(ln) > 110 else [ln])]
        self.cover_lines = lines
        y, x = 0.85, 0.08
        for ln in lines:
            if not ln:
                y -= 0.012
                continue
            if y < 0.11 and x == 0.08:                    # second column for a long contents list
                x, y = 0.52, 0.85 - 0.022 * (lines.index("Contents") + 1)
            fig.text(x, y, ln, fontsize=9 if not ln.startswith("  ") else 8.5,
                     weight="bold" if ln.startswith(heads) else "normal", family="DejaVu Sans", color="#15213B")
            y -= 0.022
        self.cover_credits = CREDITS
        fig.text(0.08, 0.06, "\n".join(textwrap.wrap(CREDITS, 120)), fontsize=7, color="#4E5D7A", linespacing=1.4)
        self._footer(fig, "page 1")
        return self._save_page(pdf, fig)

    def _group_pages(self, pdf, group: str, entries: list[Entry], figures: list[Path]) -> int:
        n = 0
        # DEFAULT: within a group, one figure per edge type, at most TRACES_PER_PAGE traces per page
        by_edge: dict[str, list[Entry]] = {}
        for e in sorted(entries, key=lambda e: (e.day, e.holder, e.zone.id, e.scan.name)):
            by_edge.setdefault(e.edge_key, []).append(e)
        for edge, items in by_edge.items():
            for p0 in range(0, len(items), TRACES_PER_PAGE):
                page = p0 // TRACES_PER_PAGE + 1
                chunk = items[p0:p0 + TRACES_PER_PAGE]
                fig = self._waterfall_figure(group, edge, chunk, page, (len(items) - 1) // TRACES_PER_PAGE + 1)
                name = self._claim("figures", slug_name(f"{group}_{edge}_p{page}", 64), f"{group}|{edge}|{page}")
                for ext in ("png", "svg"):
                    fp = self._target(self.out / "figures" / f"{name}.{ext}")
                    fig.savefig(fp, dpi=self.png_dpi if ext == "png" else None)
                    if ext == "png":
                        figures.append(fp)
                n += self._save_page(pdf, fig)
        return n

    def _waterfall_figure(self, group: str, edge: str, items: list[Entry], page: int, pages: int):
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=PAGE, gridspec_kw={"height_ratios": [3, 2], "hspace": 0.28},
                                       constrained_layout=False)
        fig.subplots_adjust(left=0.1, right=0.78, top=0.9, bottom=0.07 + 0.0125 * len(items))     # room for the captions
        fig.suptitle(f"{group} · {edge} · {len(items)} spectra" + (f" (page {page} of {pages})" if pages > 1 else ""),
                     fontsize=12, weight="bold", x=0.1, ha="left", color="#15213B")
        off1 = off2 = 0.0
        gap1 = 0.0
        captions = []
        e_min = min(float(e.traces.E[0]) for e in items); e_max = max(float(e.traces.E[-1]) for e in items)
        for i, e in enumerate(items):
            t = e.traces
            col, ls = PAL[i % len(PAL)], STYLES[(i // len(PAL)) % len(STYLES)] if i >= len(PAL) else STYLES[i % len(STYLES)]
            if t.sum is not None:
                y = t.sum + off1
                ax1.plot(t.E, y, color=col, ls=ls, lw=1.1)
                ax1.text(e_max + 0.01 * (e_max - e_min), self._last_finite(y), e.title, fontsize=6.5, color=col, va="center")
                gap1 = max(np.nanmax(t.sum) - np.nanmin(t.sum), 1e-9) * 0.35 if e.settings.fluo_norm != "raw" else np.nanmax(t.sum) * 0.35
                off1 += (np.nanmax(t.sum) - np.nanmin(t.sum)) * 0.6 + gap1
            if e.settings.tey:
                y2 = t.tey + off2
                ax2.plot(t.E, y2, color=col, ls=ls, lw=1.0)
                ax2.text(e_max + 0.01 * (e_max - e_min), self._last_finite(y2), e.zone.label, fontsize=6.5, color=col, va="center")
                rng = np.nanmax(t.tey) - np.nanmin(t.tey)
                off2 += (rng if np.isfinite(rng) else 1.0) * 0.6 + 0.35 * (rng if np.isfinite(rng) and rng > 0 else 1.0)
            sdd_txt = "".join(str(k + 1) for k in range(4) if e.settings.sdd[k]) or "none (TEY only)"
            captions.append(f"{e.zone.label}: {t.n_pix} px, SDD {sdd_txt}, "
                            f"{NORM_TEXT.get(e.settings.fluo_norm, e.settings.fluo_norm)}"
                            + (", IPFY" if e.settings.signal == "ipfy" else "")
                            + (", I0 = good-I0 mean" if e.settings.i0_source == "good" else "")
                            + (f", spikes {sum(e.spikes_removed.values())}" if e.spikes_removed else "")
                            + (f"; {'; '.join(e.flags)}" if e.flags else "")
                            + (f"; {'; '.join(t.notes)}" if t.notes else ""))
            if e.stack.edge_ev and e_min < e.stack.edge_ev < e_max:
                for ax in (ax1, ax2):
                    ax.axvline(e.stack.edge_ev, color="#999999", lw=0.6, ls=":")
        ax1.set_ylabel("fluorescence (offset)"); ax2.set_ylabel("TEY (offset)")
        ax2.set_xlabel("incident energy (eV)")
        ax1.set_xlim(e_min, e_max); ax2.set_xlim(e_min, e_max)
        for ax in (ax1, ax2):
            ax.tick_params(labelsize=8); ax.spines["top"].set_visible(False); ax.spines["right"].set_visible(False)
        fig.text(0.1, 0.03, "\n".join(c[:160] for c in captions), fontsize=5.8, color="#3A4866", va="bottom")
        self._footer(fig)
        return fig

    def _sdd_appendix(self, pdf, entries: list[Entry]) -> int:
        n = 0
        per_page = 6
        for p0 in range(0, len(entries), per_page):
            chunk = entries[p0:p0 + per_page]
            fig, axes = plt.subplots(3, 2, figsize=PAGE)
            fig.subplots_adjust(left=0.1, right=0.97, top=0.92, bottom=0.07, hspace=0.45, wspace=0.3)
            fig.suptitle(f"Appendix A · the four detectors per zone ({p0 + 1}-{p0 + len(chunk)} of {len(entries)})",
                         fontsize=11, weight="bold", x=0.1, ha="left", color="#15213B")
            for ax, e in zip(axes.flat, chunk):
                t = e.traces
                for k in range(4):
                    y = t.det[k]
                    if y is None:
                        continue
                    on = e.settings.sdd[k]
                    ax.plot(t.E, y, color=PAL[k], ls=STYLES[k], lw=0.8, alpha=1.0 if on else 0.35, label=f"SDD{k + 1}" + ("" if on else " (off)"))
                if t.sum is not None:
                    ax.plot(t.E, t.sum, color="black", lw=1.4, label="sum of ticked")
                ax.set_title(e.title, fontsize=7)
                ax.tick_params(labelsize=6)
                ax.set_xlabel("E (eV)", fontsize=7); ax.set_ylabel("normalised", fontsize=7)
                ax.legend(fontsize=5.5, frameon=False)
            for ax in axes.flat[len(chunk):]:
                ax.axis("off")
            self._footer(fig)
            n += self._save_page(pdf, fig)
        return n

    def _plate_maps(self, pdf, entries: list[Entry]) -> int:
        n = 0
        holders = {}
        for e in entries:
            holders.setdefault(e.holder_key, []).append(e)
        keys = sorted(holders, key=lambda k: (holders[k][0].day, k))
        for p0 in range(0, len(keys), 2):
            fig, axes = plt.subplots(2, 1, figsize=PAGE)
            fig.subplots_adjust(left=0.08, right=0.97, top=0.93, bottom=0.06, hspace=0.3)
            fig.suptitle("Appendix B · plate maps with the zones", fontsize=11, weight="bold", x=0.08, ha="left", color="#15213B")
            for ax, key in zip(axes, keys[p0:p0 + 2]):
                es = holders[key]
                hz = self.zones.holders.get(key)
                surv = self.project.find(hz.survey) if hz and hz.survey else None
                ax.set_title(f"holder {es[0].holder} · {es[0].day}" + (f" · survey {surv.name}" if surv else ""), fontsize=8)
                if surv is not None:
                    try:
                        sv = SurveyData.load(surv, self.project.roi_bins)
                        ch = next((l for l in sv.lines if l == es[0].stack.main), "total")
                        g = sv.grid(sv.values(ch, per_i0=True, log10=True))
                        dx, dy = g.dx, g.dy
                        ax.pcolormesh(np.r_[g.xs - dx / 2, g.xs[-1] + dx / 2], np.r_[g.ys - dy / 2, g.ys[-1] + dy / 2], g.img,
                                      cmap="viridis", shading="flat", rasterized=True)
                        ax.set_aspect("equal")
                    except Exception as err:
                        ax.text(0.02, 0.95, f"survey unreadable: {err}", transform=ax.transAxes, fontsize=7, va="top")
                seen = set()
                for i, e in enumerate(es):
                    z = e.zone
                    if z.id in seen:
                        continue
                    seen.add(z.id)
                    col = PAL[i % len(PAL)]
                    for r in z.rects:
                        ax.add_patch(plt.Rectangle((r[0], r[2]), r[1] - r[0], r[3] - r[2], fill=False, ec=col, lw=0.9))
                    for s in z.paint:
                        ax.add_patch(plt.Rectangle((s[0] - s[2] / 2, s[1] - s[3] / 2), s[2], s[3], fill=False, ec=col, lw=0.6))
                    pts = [(r[0], r[3]) for r in z.rects] + [(s[0], s[1]) for s in z.paint]
                    if pts:
                        x, y = pts[0]
                        ax.text(x, y, z.label, fontsize=5, color=col, va="bottom")
                ax.set_xlabel("x (mm)", fontsize=7); ax.set_ylabel("y (mm)", fontsize=7); ax.tick_params(labelsize=6)
            for ax in axes[len(keys[p0:p0 + 2]):]:
                ax.axis("off")
            self._footer(fig)
            n += self._save_page(pdf, fig)
        return n

    def _qc_pages(self, pdf, entries: list[Entry]) -> int:
        lines = self.appendix_c = self.qc_lines(entries)
        n = 0
        per_page = 62
        heads = ("Appendix", "Zone spectra", "Scans marked", "Skipped", "Scans covering", "Check before")
        for p0 in range(0, len(lines), per_page):
            fig = plt.figure(figsize=PAGE)
            y = 0.95
            for ln in lines[p0:p0 + per_page]:
                mono = not ln.startswith(heads)
                fig.text(0.04, y, ln[:150], fontsize=5.6 if mono else 7, family="DejaVu Sans Mono" if mono else "DejaVu Sans",
                         weight="bold" if ln.startswith("Appendix") else "normal", color="#15213B")
                y -= 0.0145
            self._footer(fig)
            n += self._save_page(pdf, fig)
        return n

    def qc_lines(self, entries: list[Entry]) -> list[str]:
        """The text of Appendix C: the included spectra, scans covering a zone without settings, the other findings
        of the check before building (ignored ones marked), scans not included, anything skipped."""
        rows = []
        for e in entries:
            sp = ", ".join(f"{k} {v}" for k, v in e.spikes_removed.items())
            flags = "; ".join(e.flags).replace(" (sgm_report)", " [sgm]").replace(" (AURORA check)", " [check]").replace(
                "good-I0 requested but no good scan ticked: own I0 used (report)", "no good I0 ticked")
            rows.append(f"{e.holder:>5} {e.zone.label[:24]:24} {e.scan.time} {e.scan.label[:20]:20} {e.edge_key:7} "
                        f"{e.traces.n_pix:4d}px SDD{''.join(str(k + 1) for k in range(4) if e.settings.sdd[k]) or '-':4} "
                        f"{e.settings.fluo_norm:6} {('spk ' + sp) if sp else '':24.24} {flags[:40]}")
        lines = ["Appendix C · quality notes", "", "Zone spectra included (holder, zone, scan, edge, pixels, detectors, "
                 "normalisation, spikes interpolated, flags with their source):"] + rows
        findings = self.plan.findings if self.plan else []
        ignored = set(self.zones.ui.get("preflight_ignored") or [])
        no_set = [f for f in findings if f.kind == "no_settings"]
        if no_set:
            lines += ["", "Scans covering a zone without settings (not in the figures and CSVs):"]
            lines += [f"  {f.text}" for f in no_set]
        other = [f for f in findings if f.kind != "no_settings"]
        if other:
            lines += ["", "Check before building (the Report perspective lists the same):"]
            lines += [f"  {f.text}" + ("  [ignored]" if f.id in ignored else "") for f in other]
        if self.not_included:
            lines += ["", "Scans marked NOT included in the zone tool (kept out of the figures and CSVs):"]
            lines += [f"  {h}  {z}  {s}" for h, z, s in self.not_included]
        if self.skipped:
            lines += ["", "Skipped:"] + [f"  {s}" for s in self.skipped]
        return lines

    # ------------------------------------------------------------------ csv
    def _data_source(self, e: Entry) -> tuple[str, str]:
        """('sgm_report cache' | 'raw reduce' | other, the cache file relative to the project). A fresh raw reduce and
        a reload of its AURORA cache give the same text, so verify() compares like with like."""
        src = e.stack.source or ""
        root = self.project.root
        if src.startswith("sgm_report cache"):
            rel = root / "processed" / e.scan.day / e.scan.name / "_reduced_cache.npz"
            kind = "sgm_report cache"
        elif src.startswith("aurora cache") or src == "reduced from raw":
            rel = root / ".aurora" / "cache" / e.scan.day / e.scan.name / "_reduced_cache.npz"
            kind = "raw reduce"
        else:
            return src or "unknown", ""
        try:
            return kind, rel.relative_to(root).as_posix()
        except ValueError:
            return kind, rel.as_posix()

    def roi_channels(self, e: Entry) -> tuple[dict, str]:
        """The emission window per SDD, [first channel, last channel + 1), and where it comes from."""
        if e.settings.roi:
            return {k: [int(v[0]), int(v[1])] for k, v in e.settings.roi.items()}, "the zone settings (per-detector windows)"
        line = e.traces.line
        if line == TOTAL_LABEL:
            return {d: list(TOTAL_BOUNDS) for d in DETS}, "the total XRF window"
        rb = self.project.roi_bins
        if rb is not None and rb.has(line):
            b = rb.bounds(line)
            return {d: [int(b[d][0]), int(b[d][1])] for d in DETS}, "config/roi_bins.json"
        return {}, f"TODO: need reference (line {line!r} is not in config/roi_bins.json)"

    def csv_meta(self, e: Entry) -> dict:
        """Everything the CSV header and its sidecar say about one entry (deterministic: no build time)."""
        t, s, sd = e.traces, e.settings, e.stack
        E = np.asarray(t.E, float)
        roi, roi_from = self.roi_channels(e)
        kind, cache = self._data_source(e)
        valid = getattr(sd, "valid", None)
        skipped = E[~np.asarray(valid, bool)].tolist() if valid is not None and len(valid) == len(E) else []
        spikes = {k: [float(E[i]) for i in v] for k, v in t.spikes.items() if isinstance(v, list)}
        masked = {k: E[np.asarray(m, bool)].tolist() for k, m in (t.masked or {}).items() if np.size(m) == len(E)}
        cal = self.project.calibration
        meta = {
            "zone": f"{e.zone.label} (holder {e.holder}, zone id {e.zone.id}, group {e.group})",
            "zone_label": e.zone.label, "zone_id": int(e.zone.id), "holder": e.holder, "holder_key": e.holder_key,
            "day": e.day, "group": e.group, "comment": e.zone.comment,
            "scan": f"{e.scan.name} ({e.day})", "sid": e.sid, "edge": e.edge_key, "line": t.line,
            "signal": "IPFY" if s.signal == "ipfy" else "PFY",
        }
        if s.signal == "ipfy":
            meta["ipfy_mode"] = s.ipfy_mode
        meta.update({
            "data_source": kind, "data_source_file": cache,
            "sdd_roi_channels": roi, "sdd_roi_channels_from": f"{roi_from}; [first channel, last channel + 1)",
            "detectors in the sum": ", ".join(d for d, on in zip(DETS, s.sdd) if on) or "none (TEY only)",
            "fluorescence normalisation": NORM_TEXT.get(s.fluo_norm, s.fluo_norm),
            "TEY normalisation": NORM_TEXT.get(s.tey_norm, s.tey_norm),
            "pre-edge window (eV)": list(t.pre) if t.pre else None,
            "post-edge window (eV)": list(t.post) if t.post else None,
            "I0 source": "good-I0 mean" if s.i0_source == "good" and t.spikes.get("i0good") else
                         ("own (good-I0 requested, none available)" if s.i0_source == "good" else "own"),
            "good_i0_contributors": list(e.good_sids),
            "de-spike": f"{s.despike_mode} k = {s.k:g}" if s.despike else "off",
            "spikes_eV": spikes, "skipped_energies_eV": skipped, "masked_energies_eV": masked,
            "notes": list(t.notes) + [n for n in sd.notes if n not in t.notes],
            "zone_geometry": {"rects": e.zone.rects, "erects": e.zone.erects, "paint": e.zone.paint,
                              "erase": e.zone.erase, "polys": e.zone.polys},
            "members": int(e.members.size),
            "flags": list(e.flags),
            "calibration": cal.describe(self.project.root) if cal else "none",
            "settings": s.to_json(),
        })
        return meta

    def csv_columns(self, e: Entry) -> tuple[list[str], np.ndarray]:
        t = e.traces
        nan = np.full(len(t.E), np.nan)
        names = ["energy_eV", "I0_zone_sum", "I0_used", "TEY_raw", "TEY_norm", "fluo_sum_raw", "fluo_sum_norm"]
        cols = [t.E, t.i0_raw, t.i0_used, t.tey_raw, t.tey, t.sum_raw if t.sum_raw is not None else nan,
                t.sum if t.sum is not None else nan]
        for k, d in enumerate(DETS):
            names += [f"{d}_raw", f"{d}_norm"]
            cols.append(t.det_raw[k] if t.det_raw[k] is not None else nan)
            cols.append(t.det[k] if t.det[k] is not None else nan)
        for name in sorted(t.err or {}):                # uncertainties, when a package computed them
            v = np.asarray(t.err[name], float)
            if v.shape == (len(t.E),):
                names.append(f"{name}_err")
                cols.append(v)
        return names, np.column_stack([np.asarray(c, float) for c in cols])

    def csv_name(self, e: Entry) -> str:
        return f"{slug(e.holder_key)}_z{e.zone.id}_{slug_name(e.zone.label)}_{slug_name(e.scan.name)}"

    def _write_csv(self, e: Entry, folder: Path | None = None) -> tuple[Path, str, Path]:
        """Write the CSV of one entry with its provenance header and JSON sidecar; (csv, sha256, sidecar)."""
        folder = Path(folder or self.out)
        gdir = self._claim(f"csvdir:{folder}", slug_name(e.group), e.group)
        d = folder / "csv" / gdir
        d.mkdir(parents=True, exist_ok=True)
        name = self._claim(f"csv:{d}", self.csv_name(e), "|".join(map(str, e.key)))
        p = self._target(d / f"{name}.csv")
        meta = self.csv_meta(e)
        names, arr = self.csv_columns(e)
        meta["columns"] = ", ".join(names)
        hdr = provenance.header_block(meta)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(hdr) + "\n")
            f.write(",".join(names) + "\n")
            np.savetxt(f, arr, delimiter=",", fmt=CSV_FMT)
        side = self._target(provenance.sidecar_path(p))
        with open(side, "w", encoding="utf-8", newline="\n") as f:
            json.dump({**provenance.sidecar(meta), "member_indices": [int(i) for i in e.members]}, f, indent=1,
                      ensure_ascii=False, default=provenance.plain)
        return p, provenance.sha256(p), side
