"""Project model: a beamtime folder (raw/<day>/<scan>, processed/, config/) discovered into days, holders and scans.

Discovery reads only folder names and .h5 attributes, never the binaries, so a 130-scan beamtime opens in seconds
on a local disk. The grouping into holders is the one of sgm_report.py `process_day`: a survey starts a holder, a
survey re-taken before any stack replaces the earlier one.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from aurora.core.io import h5 as h5io
from aurora.core.io.config import (Calibration, RoiBins, load_plate_layout, load_sample_elements, load_summary,
                                   summary_by_scan)
from aurora.core.io.raw import scan_kind, stack_energy_dirs, stack_status
from aurora.core.names import (holder_from_survey, pretty_edge, strip_scan_suffix, target_edge, time_label)

log = logging.getLogger("aurora.model")

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


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
    def from_dir(cls, path: Path, day: str = "", now: float | None = None) -> "ScanInfo":
        path = Path(path)
        kind = scan_kind(path.name)
        info = cls(path=path, day=day, kind=kind, name=path.name, time=time_label(path.name),
                   label=strip_scan_suffix(path.name))
        info.h5 = h5io.find_h5(path)
        try:
            if kind == "survey":
                info.holder = holder_from_survey(path.name)
                info.status = "survey"
                if info.h5 is not None:
                    info.md = h5io.attrs(info.h5, "scan_metadata")
                    e = info.md.get("energy")
                    if e is None:
                        m = re.search(r"_(\d+\.\d+)eV\.h5$", info.h5.name)
                        e = m.group(1) if m else None
                    info.e_min = info.e_max = float(e) if e is not None else float("nan")
                    info.n_points = len(h5io.xy(info.h5)[0])
            elif kind == "stack":
                info.edge = target_edge(path.name)
                info.energy_dirs = stack_energy_dirs(path)
                info.n_energies = len(info.energy_dirs)
                if info.energy_dirs:
                    info.e_min, info.e_max = info.energy_dirs[0][0], info.energy_dirs[-1][0]
                planned: list[float] = []
                if info.h5 is not None:
                    info.md, planned = h5io.stack_plan(path)
                    info.n_points = len(h5io.xy(info.h5)[0])
                info.n_planned = len(planned)
                info.status, info.status_reason = stack_status(path, info.energy_dirs, planned, now=now)
            else:
                info.status = "other"
        except Exception as err:  # a scan still being written, a truncated h5: list it, say why
            info.error = f"{type(err).__name__}: {err}"
            info.status = info.status or "unreadable"
            log.warning("%s: %s", path, info.error)
        return info


@dataclass
class Holder:
    name: str
    survey: ScanInfo | None = None
    stacks: list[ScanInfo] = field(default_factory=list)       # complete stacks
    dropped: list[ScanInfo] = field(default_factory=list)      # aborted / in progress / test / empty
    superseded_surveys: list[ScanInfo] = field(default_factory=list)

    @property
    def scans(self) -> list[ScanInfo]:
        out = list(self.superseded_surveys)
        if self.survey is not None:
            out.append(self.survey)
        out += sorted(self.stacks + self.dropped, key=lambda s: s.name)
        return out


@dataclass
class Day:
    date: str
    path: Path
    holders: list[Holder] = field(default_factory=list)
    others: list[ScanInfo] = field(default_factory=list)
    summary: dict | None = None

    @property
    def scans(self) -> list[ScanInfo]:
        return [s for h in self.holders for s in h.scans] + self.others


def group_holders(scans: list[ScanInfo]) -> tuple[list[Holder], list[ScanInfo]]:
    """sgm_report grouping: a survey starts a holder; a survey re-acquired before any stack replaces the previous."""
    holders: list[Holder] = []
    others: list[ScanInfo] = []
    cur: Holder | None = None
    for s in sorted(scans, key=lambda s: s.name):
        if s.kind == "survey":
            if cur is not None and cur.name == s.holder and not cur.stacks and not cur.dropped:
                cur.superseded_surveys.append(cur.survey) if cur.survey else None
                cur.survey = s
                continue
            cur = Holder(name=s.holder, survey=s)
            holders.append(cur)
        elif s.kind == "stack":
            if cur is None:
                cur = Holder(name="unknown")
                holders.append(cur)
            s.holder = cur.name
            (cur.stacks if s.status == "complete" else cur.dropped).append(s)
        else:
            others.append(s)
    return holders, others


@dataclass
class Project:
    """A beamtime folder: raw/<day>/<scan>, processed/<day>/..., config/*.json, reports/, sketches/."""
    root: Path
    raw: Path
    days: list[Day] = field(default_factory=list)
    calibration: Calibration | None = None
    roi_bins: RoiBins | None = None
    plate_layout: dict = field(default_factory=dict)
    sample_elements: dict = field(default_factory=dict)

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
        """(project root, raw root, day folders) for a project folder, a raw folder, a day folder or a scan folder."""
        p = Path(path).resolve()
        if (p / "raw").is_dir():
            raw = p / "raw"
            return p, raw, sorted(d for d in raw.iterdir() if d.is_dir() and _DAY.fullmatch(d.name))
        if p.name == "raw" and p.is_dir():
            return p.parent, p, sorted(d for d in p.iterdir() if d.is_dir() and _DAY.fullmatch(d.name))
        if _DAY.fullmatch(p.name):
            root = p.parent.parent if p.parent.name == "raw" else p.parent
            return root, p.parent, [p]
        if scan_kind(p.name) != "other":
            return Project.resolve(p.parent)
        if any(scan_kind(c.name) != "other" for c in p.iterdir() if c.is_dir()):
            return p.parent, p.parent, [p]
        raise FileNotFoundError(f"{p}: not a project folder (no raw/<day>/<scan> layout found)")

    @classmethod
    def discover(cls, path: Path, progress=None, now: float | None = None) -> "Project":
        root, raw, day_dirs = cls.resolve(path)
        log.info("project %s (raw %s): %d day folders", root, raw, len(day_dirs))
        proj = cls(root=root, raw=raw)
        cfg = proj.config_dir
        proj.calibration = Calibration(cfg / "sdd_calibration.json")
        proj.roi_bins = RoiBins(cfg / "roi_bins.json")
        proj.plate_layout = load_plate_layout(cfg / "plate_layout.json")
        proj.sample_elements = load_sample_elements(cfg / "sample_elements.json")
        total = sum(1 for d in day_dirs for c in d.iterdir() if c.is_dir())
        done = 0
        for d in day_dirs:
            day = Day(date=d.name if _DAY.fullmatch(d.name) else "", path=d)
            scans = []
            for c in sorted(x for x in d.iterdir() if x.is_dir()):
                scans.append(ScanInfo.from_dir(c, day=day.date, now=now))
                done += 1
                if progress:
                    progress(done, total, c.name)
            day.summary = load_summary(proj.processed / d.name)
            by_scan = summary_by_scan(day.summary)
            for s in scans:
                s.summary = by_scan.get(s.name, {})
            day.holders, day.others = group_holders(scans)
            proj.days.append(day)
            log.info("%s: %d scans, %d holders", day.date or d.name, len(scans), len(day.holders))
        return proj

    def scan_date(self, scan: ScanInfo) -> dt.date | None:
        try:
            return dt.date.fromisoformat(scan.day)
        except ValueError:
            return None
