"""Project model: a beamtime folder (raw/<day>/<scan>, processed/, config/) discovered into days, holders and scans.

Discovery reads only folder names and .h5 attributes, never the binaries, so a 130-scan beamtime opens in seconds
on a local disk. The grouping into holders is the one of sgm_report.py `process_day`: a survey starts a holder, a
survey re-taken before any stack replaces the earlier one (on the same day). It runs over the chronological sequence
of every day, ordered by (day, HHMMSS), so a stack recorded after midnight, before the next survey, continues the
holder of the previous day.

A folder that holds scan folders directly, the way John Bussey's GUI kept them ('CLS GUI/<scan folders>'), is a
project of its own (a loose folder): its .aurora/ lives inside it, and each scan's day key is the date its .h5 file
was written (h5io.file_day), else the folder name. A folder that holds the day folders directly (the raw/ level left
out when they were copied) is a beamtime whose raw folder is itself.

A day folder, a scan folder or an .h5 path opens that one day of its beamtime: Project.days_left_out names the other
day folders of raw/, so the window can say so.

Other labs' layouts: a raw/ child that is not named YYYY-MM-DD but holds scan folders ('raw/Nov05', 'raw/day1') is a
day folder whose scans are dated from their .h5 files; a scan folder whose name does not end in _survey / _stack is
recognised from the `plan_name` its .h5 records. What discovery did not recognise, or read in another way, is said in
plain words in Project.layout_notes (Home's project summary and `aurora project` show them), and every problem of an
optional configuration file in Project.config_problems.

Kinds set by hand: the session's ui value 'kind_override' ({scan folder: 'sample' | 'test'}, Home's Treat as a sample
scan / Treat as a test) is read from <root>/.aurora/session.json at discovery, so the command line and the window
group the scans the same way.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from aurora.core.io import h5 as h5io
from aurora.core.io.config import (CALIBRATION_FILE, NAMING_FILE, PLATE_LAYOUT_FILE, ROI_BINS_FILE, Calibration,
                                   RoiBins, load_naming, load_plate_layout_checked, load_summary, session_ui_value,
                                   summary_by_scan)
from aurora.core.io.raw import scan_kind, stack_energy_dirs, stack_status
from aurora.core.io.h5 import planned_energies
from aurora.core.names import (holder_from_survey, is_test_name, label_from_name, pretty_edge, resolve_edge,
                               target_edge, time_label)

log = logging.getLogger("aurora.model")

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
ARCHIVES = (".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz")
UNKNOWN_HOLDER = "unknown"                      # the holder of stacks recorded before any survey (its session key)
BEFORE_SURVEY = "stacks before the first survey"  # ... and how the window names it (Holder.display)
SIBLING_LIBRARY = "SGM_Beamline_Analysis-main"  # the CLS toolkit folder sgm_report kept next to a beamtime
KIND_OVERRIDE = "kind_override"                 # session ui key: {scan folder name: 'sample' | 'test'}


class NotAProjectError(FileNotFoundError):
    """A path that exists but is nothing AURORA opens (a file that is not a scan's .h5, an archive). Its text is one
    sentence for the person who chose it, without the path: the window and the command line show the path."""


def _not_a_project_file(p: Path) -> NotAProjectError:
    """The error for an existing file that is not a scan's .h5: an archive, one file of a scan, any other file."""
    if p.suffix.lower() in ARCHIVES:
        return NotAProjectError(f"{p.name} is an archive, which AURORA does not read: extract it first, then open the "
                                "extracted folder")
    scan = next((d for d in (p.parent, p.parent.parent) if scan_kind(d.name) != "other"), None)
    if scan is not None:                             # a detector .bin or mcc .csv, also inside an energy folder
        return NotAProjectError(f"{p.name} is one file of the scan {scan.name}: open the scan folder or the beamtime "
                                "folder instead")
    return NotAProjectError(f"{p.name} is a file, not a folder: open the beamtime folder (the one that holds raw/), a "
                            "scan folder or one scan's .h5 file")


def _looks_like_scan(d: Path) -> bool:
    """A scan folder: its name ends in _survey / _stack, or it holds the scan's .h5 file directly."""
    if scan_kind(d.name) != "other":
        return True
    try:
        return any(p.suffix.lower() == ".h5" and p.is_file() for p in d.iterdir())
    except OSError:
        return False


def _holds_scans(d: Path) -> bool:
    try:
        children = [c for c in d.iterdir() if c.is_dir() and not c.name.startswith(".")]
    except OSError:
        return False
    return any(scan_kind(c.name) != "other" for c in children) or any(_looks_like_scan(c) for c in children)


def _day_dirs(raw: Path) -> list[Path]:
    """The day folders of a raw folder: every YYYY-MM-DD child, then every other child that holds scan folders
    (a day folder renamed 'Nov05' or 'day1'), each group in name order."""
    try:
        kids = sorted(c for c in raw.iterdir() if c.is_dir() and not c.name.startswith("."))
    except OSError:
        return []
    iso = [c for c in kids if _DAY.fullmatch(c.name)]
    return iso + [c for c in kids if not _DAY.fullmatch(c.name) and _holds_scans(c)]


@dataclass
class ScanInfo:
    """What is known about one scan folder without reading its binaries."""
    path: Path
    day: str
    kind: str                                   # survey | stack | other
    name: str = ""
    time: str = ""
    label: str = ""
    holder: str = ""
    edge: tuple[str, str] | None = None
    status: str = ""                            # complete | aborted | in_progress | test | empty (stacks); 'survey'
    status_reason: str = ""
    h5: Path | None = None
    n_energies: int = 0
    e_min: float = float("nan")
    e_max: float = float("nan")
    n_planned: int = 0
    n_points: int = 0
    md: dict[str, str] = field(default_factory=dict)
    energy_dirs: list[tuple[float, Path]] = field(default_factory=list)
    summary: dict = field(default_factory=dict)  # sgm_report summary entry when one exists
    error: str = ""
    planned: list[float] = field(default_factory=list)            # planned energies (stack_metadata), eV
    grid: tuple[int, int] = (0, 0)                                # nx x ny points of the map
    pitch: tuple[float, float] | None = None                      # x, y step (mm)
    x_range: tuple[float, float] | None = None                    # first and last point centre (mm)
    y_range: tuple[float, float] | None = None
    continues: str = ""                         # day key of the holder this stack continues (recorded after midnight)
    edge_note: str = ""                         # how the edge was read when not from the name ('the name says O K …')
    kind_from_h5: bool = False                  # the kind comes from the .h5 plan_name (the folder name has no suffix)
    override: str = ""                          # 'sample' | 'test': set by hand in the session (Home's scan menu)
    named_edge: tuple[str, str] | None = None   # the edge the name states (target_edge), before the energies' check

    @property
    def edge_text(self) -> str:
        return pretty_edge(self.edge)

    @property
    def flags(self) -> list[str]:
        """Short quality flags from the sgm_report summary (empty until a reduction exists)."""
        s = self.summary
        out = []
        if s.get("sdd_saturated"):
            sat = s["sdd_saturated"]
            out.append("saturated " + ", ".join(sat).upper() if isinstance(sat, list) else "saturated")
        if s.get("i0_bad"):
            out.append("I0 invalid")
        if s.get("dropped"):
            out.append("dropped")
        return out

    @property
    def energy_text(self) -> str:
        if self.kind == "survey":
            return f"{self.e_min:.1f} eV" if self.e_min == self.e_min else ""
        if self.n_energies:
            return f"{self.e_min:.1f}-{self.e_max:.1f} eV"
        return ""

    @classmethod
    def from_dir(cls, path: Path, day: str = "", now: float | None = None, words=(), override: str = "") -> "ScanInfo":
        """What the folder name and the .h5 attributes say about one scan folder. `words`: the beamtime's own plan
        words (config/naming.json); `override`: 'sample' or 'test' set by hand in the session."""
        path = Path(path)
        kind = scan_kind(path.name)
        info = cls(path=path, day=day, kind=kind, name=path.name, time=time_label(path.name),
                   label=label_from_name(path.name, words), override=override if override in ("sample", "test") else "")
        info.h5 = h5io.find_h5(path)
        try:
            if kind == "other" and info.h5 is not None:
                kind = h5io.plan_kind(info.h5)
                if kind != "other":
                    info.kind, info.kind_from_h5 = kind, True
            if kind == "survey":
                info.status = "survey"
                if info.h5 is not None:
                    info.md = h5io.attrs(info.h5, "scan_metadata")
                    e = info.md.get("energy")
                    if e is None:
                        m = re.search(r"_(\d+\.\d+)eV\.h5$", info.h5.name)
                        e = m.group(1) if m else None
                    info.e_min = info.e_max = float(e) if e is not None else float("nan")
                info.holder = holder_from_survey(path.name, info.md.get("scan_name") or None)
                if info.md.get("scan_name"):
                    info.label = label_from_name(info.md["scan_name"], words) or info.label
                if info.h5 is not None:
                    info._geometry(*h5io.xy(info.h5))
            elif kind == "stack":
                info.energy_dirs = stack_energy_dirs(path)
                info.n_energies = len(info.energy_dirs)
                if info.energy_dirs:
                    info.e_min, info.e_max = info.energy_dirs[0][0], info.energy_dirs[-1][0]
                planned: list[float] = []
                if info.h5 is not None:
                    info.md, planned = h5io.stack_plan(path)
                    if info.md.get("scan_name"):
                        info.label = label_from_name(info.md["scan_name"], words) or info.label
                    info._geometry(*h5io.xy(info.h5))
                info.planned = list(planned)
                info.n_planned = len(planned)
                info.named_edge = target_edge(info.md.get("scan_name") or "") or target_edge(path.name)
                info.edge, info.edge_note = resolve_edge(info.named_edge, info.planned or
                                                         [e for e, _ in info.energy_dirs])
                info.restatus(now, words)
            else:
                info.status = "other"
        except Exception as err:  # a scan still being written, a truncated h5: list it, say why
            info.error = f"{type(err).__name__}: {err}"
            info.status = info.status or "unreadable"
            log.warning("%s: %s", path, info.error)
        return info

    def restatus(self, now: float | None = None, words=()) -> None:
        """The status of a stack from its energy folders, its plan and its name (stack_status), or from the kind set
        by hand (`override`): 'test' makes it a test; 'sample' lifts the test rules (name, short span)."""
        if self.kind != "stack":
            return
        if self.override == "test" and self.energy_dirs:
            self.status, self.status_reason = "test", "marked as a test in this session (Home, right-click)"
            return
        name_test = False if self.override == "sample" else (is_test_name(self.name, words) or
                                                             is_test_name(self.md.get("scan_name") or "", words))
        self.status, self.status_reason = stack_status(self.path, self.energy_dirs, self.planned, now=now,
                                                       name_test=name_test, span_test=self.override != "sample")
        if self.override == "sample" and self.status_reason:
            self.status_reason += " (marked as a sample scan in this session)"

    def _geometry(self, x, y) -> None:
        """Points, grid nx x ny, pitch and x / y ranges from the stage coordinates (the turnaround points dropped)."""
        import numpy as np
        from aurora.core.grid import to_grid
        self.n_points = int(len(x))
        if not len(x):
            return
        g = to_grid(x, y, np.zeros(len(x)))
        self.grid = (int(len(g.xs)), int(len(g.ys)))
        self.pitch = (g.dx, g.dy)
        self.x_range = (float(np.min(x)), float(np.max(x)))
        self.y_range = (float(np.min(y)), float(np.max(y)))

    @property
    def planned_energies(self) -> list[float]:
        """The planned energies (stack_metadata), else the energies of the folders found."""
        return list(self.planned) or planned_energies(self.md) or [e for e, _ in self.energy_dirs]


@dataclass
class Holder:
    name: str
    survey: ScanInfo | None = None
    stacks: list[ScanInfo] = field(default_factory=list)       # complete stacks
    dropped: list[ScanInfo] = field(default_factory=list)      # aborted / in progress / test / empty
    superseded_surveys: list[ScanInfo] = field(default_factory=list)
    day: str = ""                                              # day key of the survey that started it

    @property
    def display(self) -> str:
        """The holder as the window names it: its name, or 'stacks before the first survey' for the stacks recorded
        before any survey (the holder 'unknown', which stays its session key)."""
        return BEFORE_SURVEY if self.survey is None and self.name == UNKNOWN_HOLDER else self.name

    @property
    def scans(self) -> list[ScanInfo]:
        out = list(self.superseded_surveys)
        if self.survey is not None:
            out.append(self.survey)
        out += sorted(self.stacks + self.dropped, key=lambda s: (s.day, s.name))       # chronological, over midnight
        return out


@dataclass
class Day:
    date: str
    path: Path
    holders: list[Holder] = field(default_factory=list)
    others: list[ScanInfo] = field(default_factory=list)
    summary: dict | None = None
    # stacks recorded on this day that continue a holder started on an earlier day (listed under that holder, so
    # they are not in `scans` here: Project.scans lists every scan once)
    continued: list[ScanInfo] = field(default_factory=list)

    @property
    def scans(self) -> list[ScanInfo]:
        return [s for h in self.holders for s in h.scans] + self.others


def group_days(days: list[tuple["Day", list[ScanInfo]]]) -> None:
    """sgm_report grouping over the chronological sequence of every day (days in order, scans by name, which starts
    with HHMMSS): a survey starts a holder; a survey re-acquired on the same day before any stack replaces the
    previous one; a stack joins the last holder started, also one of an earlier day (a holder continues across
    midnight: the stack goes to that holder and to `Day.continued` of its own day). 'unknown' is used only for
    stacks before any survey of the whole project. Sets holders, others and continued of every day."""
    cur: Holder | None = None
    cur_day: Day | None = None
    for day, scans in days:
        day.holders, day.others, day.continued = [], [], []
        for s in sorted(scans, key=lambda s: s.name):
            if s.kind == "survey":
                if (cur is not None and cur_day is day and cur.name == s.holder and not cur.stacks
                        and not cur.dropped):
                    if cur.survey is not None:
                        cur.superseded_surveys.append(cur.survey)
                    cur.survey = s
                    continue
                cur, cur_day = Holder(name=s.holder, survey=s, day=day.date), day
                day.holders.append(cur)
            elif s.kind == "stack":
                if cur is None:
                    cur, cur_day = Holder(name=UNKNOWN_HOLDER, day=day.date), day
                    day.holders.append(cur)
                s.holder = cur.name
                s.continues = "" if cur_day is day else cur_day.date
                (cur.stacks if s.status == "complete" else cur.dropped).append(s)
                if cur_day is not day:
                    day.continued.append(s)
            else:
                day.others.append(s)


def group_holders(scans: list[ScanInfo]) -> tuple[list[Holder], list[ScanInfo]]:
    """sgm_report grouping of one day's scans: a survey starts a holder; a survey re-acquired before any stack
    replaces the previous."""
    day = Day(date=scans[0].day if scans else "", path=Path("."))
    group_days([(day, scans)])
    return day.holders, day.others


def own_dirs(project) -> list[Path]:
    """AURORA's folders of a project, the one written first (Project.aurora_dirs; <root>/.aurora for a stand-in)."""
    dirs = getattr(project, "aurora_dirs", None)
    return list(dirs()) if callable(dirs) else [Path(project.root) / ".aurora"]


def work_folder_for(root: Path, work_folders: dict | None) -> Path | None:
    """The working folder remembered for a beamtime root ({root: folder}, compared as resolved paths), or None."""
    if not work_folders:
        return None
    try:
        key = str(Path(root).resolve())
    except OSError:
        key = str(root)
    for k, v in work_folders.items():
        try:
            same = str(Path(k).resolve()) == key
        except OSError:
            same = str(k) == key
        if same and v:
            return Path(v)
    return None


@dataclass
class Project:
    """A beamtime folder: raw/<day>/<scan>, processed/<day>/..., config/*.json, reports/, sketches/."""
    root: Path
    raw: Path
    days: list[Day] = field(default_factory=list)
    calibration: Calibration | None = None
    roi_bins: RoiBins | None = None
    plate_layout: dict = field(default_factory=dict)
    loose: bool = False                         # a loose folder of scan folders (John Bussey's GUI), its own root
    days_left_out: list[str] = field(default_factory=list)   # day folders of raw/ not opened (a day, scan or .h5 path)
    # what discovery did not recognise or read in another way, in plain words (Home, `aurora project`)
    layout_notes: list[str] = field(default_factory=list)
    # problems of the optional configuration files: file (relative to the root) -> what was left out and why
    config_problems: dict[str, list[str]] = field(default_factory=dict)
    naming_words: list[str] = field(default_factory=list)   # config/naming.json label_suffix_words
    kind_overrides: dict[str, str] = field(default_factory=dict)    # scan folder -> 'sample' | 'test' (the session)
    # a working folder for a beamtime AURORA may not write into (Choose a working folder…): the session, the caches,
    # the saved spectra and the reports go there; None writes next to the beamtime (its .aurora/, processed/, reports/)
    work: Path | None = None

    @property
    def work_root(self) -> Path:
        """Where AURORA writes for this beamtime: the working folder when one is chosen, else the beamtime folder."""
        return Path(self.work) if self.work else self.root

    @property
    def aurora_dir(self) -> Path:
        """AURORA's own folder (session, checks, library, caches, sketches): <work root>/.aurora."""
        return self.work_root / ".aurora"

    @property
    def output_root(self) -> Path:
        """The folder under which saved spectra (processed/reduced) and reports (reports/aurora) are written."""
        return self.work_root

    def aurora_dirs(self) -> list[Path]:
        """AURORA's folders in the order they are read: the working folder's, then the beamtime's own .aurora/ (what
        was written there before a working folder was chosen, read and never written)."""
        own = self.root / ".aurora"
        return [self.aurora_dir] if self.aurora_dir == own else [self.aurora_dir, own]

    @property
    def processed(self) -> Path:
        return self.root / "processed"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def scans(self) -> list[ScanInfo]:
        return [s for d in self.days for s in d.scans]

    def find(self, name: str) -> ScanInfo | None:
        return next((s for s in self.scans if s.name == name), None)

    @property
    def n_holders(self) -> int:
        return sum(len(d.holders) for d in self.days)

    @staticmethod
    def resolve(path: Path) -> tuple[Path, Path, list[Path]]:
        """(project root, raw root, day folders) for a project folder, a raw folder, a folder of day folders without
        raw/ (its own raw root), a day folder, a scan folder, a loose folder of scan folders (its own root and its only
        'day' folder) or a scan's .h5 file. Any other file raises NotAProjectError. A day folder is a YYYY-MM-DD
        folder, or any folder of raw/ that holds scan folders (_day_dirs)."""
        p = Path(path).resolve()
        if p.suffix.lower() == ".h5" and not p.is_dir():
            return Project.resolve(p.parent)
        if (p / "raw").is_dir():
            raw = p / "raw"
            return p, raw, _day_dirs(raw)
        if p.name.lower() == "raw" and p.is_dir():
            return p.parent, p, _day_dirs(p)
        if _DAY.fullmatch(p.name) or (p.parent.name.lower() == "raw" and p.is_dir() and not _looks_like_scan(p)
                                      and _holds_scans(p)):
            root = p.parent.parent if p.parent.name.lower() == "raw" else p.parent
            return root, p.parent, [p]
        if scan_kind(p.name) != "other" or (p.is_dir() and p.parent != p and _looks_like_scan(p)):
            return Project.resolve(p.parent)
        if p.is_file():
            raise _not_a_project_file(p)
        children = [c for c in p.iterdir() if c.is_dir() and not c.name.startswith(".")]
        if any(scan_kind(c.name) != "other" for c in children) or any(_looks_like_scan(c) for c in children):
            return p, p, [p]                         # loose: the folder of scan folders is the project
        days = sorted(c for c in children if _DAY.fullmatch(c.name))
        if any(_holds_scans(d) for d in days):
            return p, p, days                        # the day folders without raw/: the folder is its own raw root
        raise FileNotFoundError(f"{p}: not a project folder (no raw/<day>/<scan> layout found): open the folder that "
                                "holds raw/, a day folder, a scan folder or a loose folder of scans")

    @classmethod
    def discover(cls, path: Path, progress=None, now: float | None = None,
                 overrides: dict[str, str] | None = None, work_folders: dict | None = None) -> "Project":
        """Read a beamtime: its configuration files (each optional and validated), its scans and their grouping into
        holders. `overrides`: {scan folder: 'sample' | 'test'}; None reads the session's (.aurora/session.json).
        `work_folders`: {beamtime root: working folder} (the window's setting): the root's entry becomes Project.work."""
        root, raw, day_dirs = cls.resolve(path)
        log.info("project %s (raw %s): %d day folders", root, raw, len(day_dirs))
        proj = cls(root=root, raw=raw, loose=day_dirs == [root])
        proj.work = work_folder_for(root, work_folders)
        if len(day_dirs) == 1 and not proj.loose:   # a day, scan or .h5 path: the other days of its raw folder
            proj.days_left_out = sorted(d.name for d in _day_dirs(raw) if d != day_dirs[0])
            if proj.days_left_out:
                log.info("only %s of %s opened: %d other day folders left out", day_dirs[0].name, root,
                         len(proj.days_left_out))
        proj.read_config()
        if overrides is None:
            overrides = session_ui_value(root, KIND_OVERRIDE, {}, work=proj.work)
        proj.kind_overrides = {str(k): str(v) for k, v in overrides.items()
                               if v in ("sample", "test")} if isinstance(overrides, dict) else {}
        children = {}
        for d in day_dirs:
            try:
                children[d] = sorted(x for x in d.iterdir() if x.is_dir() and not x.name.startswith("."))
            except OSError as err:
                children[d] = []
                proj.layout_notes.append(f"{proj.shown(d)}: could not be read ({err.strerror or err})")
        total = sum(len(c) for c in children.values())
        done = 0
        found: dict[str, tuple[Day, list[ScanInfo]]] = {}
        nested: list[tuple[Path, int]] = []
        for d in day_dirs:
            iso = bool(_DAY.fullmatch(d.name))
            from_h5 = 0
            for c in children[d]:
                s = ScanInfo.from_dir(c, day=d.name if iso else "", now=now, words=proj.naming_words,
                                      override=proj.kind_overrides.get(c.name, ""))
                if not iso:                         # a loose or renamed day folder: the day the scan's file was written
                    s.day = (h5io.file_day(s.h5) if s.h5 is not None else "") or ""
                    from_h5 += bool(s.day)
                    s.day = s.day or d.name
                if s.kind == "other" and _holds_scans(c):
                    nested.append((c, sum(1 for x in c.iterdir() if x.is_dir() and _looks_like_scan(x))))
                found.setdefault(s.day, (Day(date=s.day, path=d), []))[1].append(s)
                done += 1
                if progress:
                    progress(done, total, c.name)
            if not iso and not proj.loose and d != root and children[d]:
                n = len(children[d])
                proj.layout_notes.append(
                    f"{proj.shown(d)} is not a YYYY-MM-DD folder: {from_h5} of its {n} scan folder"
                    + ("s" if n != 1 else "") + " dated from their .h5 file"
                    + ("" if from_h5 == n else f", the others filed under '{d.name}'"))
        for d in day_dirs:                          # a day folder without any scan folder is still listed
            if _DAY.fullmatch(d.name) and d.name not in found:
                found[d.name] = (Day(date=d.name, path=d), [])
        for key in sorted(found):
            day, scans = found[key]
            day.summary = load_summary(proj.processed / key)
            by_scan = summary_by_scan(day.summary)
            for s in scans:
                s.summary = by_scan.get(s.name, {})
            proj.days.append(day)
        proj._edges_from_context([s for key in found for s in found[key][1]])
        group_days([found[key] for key in sorted(found)])
        proj._note_layout(nested)
        for day in proj.days:
            log.info("%s: %d scans, %d holders%s", day.date or day.path.name, len(day.scans), len(day.holders),
                     f", {len(day.continued)} continuing a holder of an earlier day" if day.continued else "")
        for n in proj.layout_notes:
            log.info("%s: %s", root, n)
        return proj

    # ------------------------------------------------------------------ configuration files (all optional)
    def shown(self, path: Path | None) -> str:
        """A path relative to the project root ('raw/Nov05', 'config/roi_bins.json'), else in full."""
        if path is None:
            return ""
        try:
            return Path(path).relative_to(self.root).as_posix() or "."
        except ValueError:
            return str(path)

    def read_config(self) -> None:
        """(Re)read the optional configuration files: the SDD calibration (config/ first, else the CLS toolkit's
        sdd_calibration.json next to the project folder, said so), the fixed ROI bins, the plate layout and the naming
        words. Their problems go to config_problems; nothing here raises."""
        cfg = self.config_dir
        own = cfg / CALIBRATION_FILE
        sibling = self.root.parent / SIBLING_LIBRARY / CALIBRATION_FILE
        try:
            use_sibling = not own.exists() and sibling.is_file()
        except OSError:
            use_sibling = False
        self.calibration = Calibration(sibling if use_sibling else own)
        self.calibration.sibling = use_sibling
        self.roi_bins = RoiBins(cfg / ROI_BINS_FILE)
        self.plate_layout, layout_problems = load_plate_layout_checked(cfg / PLATE_LAYOUT_FILE)
        self.naming_words, naming_problems = load_naming(cfg / NAMING_FILE)
        self.config_problems = {}
        for path, problems in ((self.calibration.path, self.calibration.problems),
                               (self.roi_bins.path, self.roi_bins.problems),
                               (cfg / PLATE_LAYOUT_FILE, layout_problems), (cfg / NAMING_FILE, naming_problems)):
            if problems:
                self.config_problems[self.shown(path)] = list(problems)

    def _edges_from_context(self, scans: list[ScanInfo]) -> None:
        """A stack whose edge came from its energies alone (its name states none, or one the energies do not cover)
        takes an edge the names of the beamtime's other stacks state when that edge lies within its energies ± 30 eV:
        a scan 'sample_edge1' over 700–730 eV in a beamtime of F K scans is F K, not Fe L3."""
        from collections import Counter
        stacks = [s for s in scans if s.kind == "stack"]
        known = Counter(s.edge for s in stacks if s.edge is not None and s.named_edge == s.edge and not s.edge_note)
        if not known:
            return
        for s in stacks:
            if s.edge_note:
                s.edge, s.edge_note = resolve_edge(s.named_edge, s.planned or [e for e, _ in s.energy_dirs], known)

    def _unknown_note(self) -> str:
        unknown = [s for d in self.days for h in d.holders if h.name == UNKNOWN_HOLDER and h.survey is None
                   for s in h.stacks + h.dropped]
        if not unknown:
            return ""
        n = len(unknown)
        return (f"{n} stack{'s were' if n != 1 else ' was'} recorded before any survey: they form holder "
                f"'{UNKNOWN_HOLDER}' (no survey map; Reduce works on the stacks directly)")

    def _note_layout(self, nested: list[tuple[Path, int]]) -> None:
        """The layout notes about the scans: none at all, folders one level too deep, folders that are neither surveys
        nor stacks, kinds read from the .h5, stacks before any survey, edges read from the energies."""
        notes = self.layout_notes
        scans = self.scans
        if not scans:
            try:
                empty = not any(c.is_dir() and not c.name.startswith(".") for c in self.raw.iterdir())
            except OSError:
                empty = False
            if self.raw != self.root and empty:
                notes.append(f"{self.shown(self.raw)}/ is empty: copy the day folders of the beamtime into it")
            elif not self.days:
                notes.append(f"{self.shown(self.raw)}/ holds no day folder with scans (a day folder holds the "
                             "<HHMMSS>_<name>_survey and _stack folders)")
        for d, n in nested:
            notes.append(f"{self.shown(d)}/ holds {n} scan folder{'s' if n != 1 else ''} one level deeper: open that "
                         "folder, or move them up next to it")
        nested_dirs = {d for d, _n in nested}
        others = [s for s in scans if s.kind == "other" and s.path not in nested_dirs]
        if others:
            n = len(others)
            notes.append(f"{n} folder{'s are' if n != 1 else ' is'} neither surveys nor stacks (names end in _survey / "
                         "_stack, or plan_name in the .h5): listed as other, not reduced")
        h5_kind = [s for s in scans if s.kind_from_h5]
        if h5_kind:
            n = len(h5_kind)
            notes.append(f"{n} scan folder{'s' if n != 1 else ''} without _survey / _stack in the name recognised "
                         "from the plan_name of the .h5: "
                         + ", ".join(f"{s.name} ({s.kind})" for s in h5_kind[:4]) + (" …" if n > 4 else ""))
        unknown = self._unknown_note()
        if unknown:
            notes.append(unknown)
        conflict = [s for s in scans if s.edge_note.startswith("the name says")]
        if conflict:
            n = len(conflict)
            notes.append(f"{n} stack{'s name' if n != 1 else ' names'} an edge the energies do not cover: the edge of "
                         "the energies is used (" + "; ".join(f"{s.name}: {s.edge_note}" for s in conflict[:2])
                         + (" …" if n > 2 else "") + ")")

    def apply_overrides(self, overrides: dict[str, str], now: float | None = None) -> None:
        """Set the kinds chosen by hand ({scan folder: 'sample' | 'test'}) and group the scans into holders again, in
        place (the window's Treat as a sample scan / Treat as a test)."""
        self.kind_overrides = {str(k): str(v) for k, v in (overrides or {}).items() if v in ("sample", "test")}
        # every scan once, in the sequence of its own day (a stack recorded after midnight is listed under the holder
        # of the previous day, but belongs to its own day's sequence, as at discovery)
        every = list({id(s): s for d in self.days for s in d.scans + d.continued}.values())
        pairs = [(day, [s for s in every if s.day == day.date]) for day in self.days]
        for _day, scans in pairs:
            for s in scans:
                s.override = self.kind_overrides.get(s.name, "")
                s.restatus(now, self.naming_words)
        group_days(pairs)
        self.layout_notes = [n for n in self.layout_notes if "before any survey" not in n]
        unknown = self._unknown_note()
        if unknown:
            self.layout_notes.append(unknown)

    def scan_date(self, scan: ScanInfo) -> dt.date | None:
        try:
            return dt.date.fromisoformat(scan.day)
        except ValueError:
            return None
