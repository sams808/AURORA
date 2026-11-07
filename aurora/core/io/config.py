"""Project configuration files (config/*.json), the sample sheet, summary.json and the manual zones file.

Formats are those of sgm_report.py v2.5 and the HTML zone tool (docs/HANDOFF.md sections 2.3 and 4).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from aurora.core.constants import DETS, N_CH, UNCALIBRATED_EV_PER_CH

log = logging.getLogger("aurora.io")


def _read_json(path: Path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        log.warning("%s unreadable (%s); ignored", path, err)
        return default


# ----------------------------------------------------------------------------- SDD calibration
class Calibration:
    """Linear channel -> eV calibration per detector (`sdd_calibration.json`: {sdd: {gain, offset}, _metadata})."""

    def __init__(self, path: Path | None = None, data: dict | None = None):
        self.path = Path(path) if path else None
        self.data = dict(data) if data is not None else _read_json(self.path, {}) if self.path else {}
        self.calibrated = all(d in self.data for d in DETS)
        for d in DETS:
            self.data.setdefault(d, {"gain": UNCALIBRATED_EV_PER_CH, "offset": 0.0, "note": "DEFAULT, not calibrated"})

    def ev(self, det: str, ch) -> np.ndarray:
        return self.data[det]["gain"] * np.asarray(ch, float) + self.data[det]["offset"]

    def ch(self, det: str, ev) -> np.ndarray:
        return (np.asarray(ev, float) - self.data[det]["offset"]) / self.data[det]["gain"]

    def bounds(self, det: str, e_lo: float, e_hi: float) -> tuple[int, int]:
        """Channel slice (lo, hi) covering e_lo..e_hi, hi exclusive, clipped to 0..256."""
        a, b = float(self.ch(det, e_lo)), float(self.ch(det, e_hi))
        return int(max(0, np.floor(min(a, b)))), int(min(N_CH, np.ceil(max(a, b))))

    @property
    def last_updated(self) -> str:
        return str(self.data.get("_metadata", {}).get("last_updated", "?"))

    def describe(self) -> str:
        return (f"SDD calibration ({self.last_updated}): " +
                "; ".join(f"{d} {self.data[d]['gain']:.3f} eV/ch {self.data[d]['offset']:+.1f}" for d in DETS))

    def save(self, meta: dict | None = None) -> None:
        if self.path is None:
            raise ValueError("calibration has no file path")
        md = {**self.data.get("_metadata", {}), **(meta or {}),
              "last_updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        self.data["_metadata"] = md
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=4), encoding="utf-8")


# ----------------------------------------------------------------------------- fixed ROI bins
class RoiBins:
    """`roi_bins.json`: line label -> {sdd: [first channel, last channel + 1]}, identical for every scan and day.

    Read-only here: the windows are defined once and never refitted silently .
    """

    def __init__(self, path: Path | None = None, data: dict | None = None):
        self.path = Path(path) if path else None
        self.data = dict(data) if data is not None else _read_json(self.path, {}) if self.path else {}

    @property
    def labels(self) -> list[str]:
        return [k for k in self.data if not k.startswith("_")]

    def has(self, label: str) -> bool:
        return label in self.data and all(d in self.data[label] for d in DETS)

    def bounds(self, label: str) -> dict[str, tuple[int, int]]:
        return {d: (int(self.data[label][d][0]), int(self.data[label][d][1])) for d in DETS}

    def line_ev(self, label: str) -> float | None:
        v = self.data.get(label, {}).get("line_ev")
        return float(v) if v is not None else None

    def gains(self) -> dict[str, float]:
        return dict(self.data.get("_meta", {}).get("gains", {}))

    def describe(self, label: str) -> str:
        b = self.bounds(label)
        return f"{label}: " + "/".join(f"{b[d][0]}-{b[d][1] - 1}" for d in DETS) + " ch"


# ----------------------------------------------------------------------------- plate layouts and sample elements
def load_plate_layout(path: Path) -> dict:
    """`plate_layout.json`: holder -> {hole, samples {plate number: name}, details, groups [{id, rows, cols, cells,
    match, ...}]}. Keys starting with '_' are help text."""
    d = _read_json(path, {})
    return {k: v for k, v in d.items() if not k.startswith("_") and isinstance(v, dict)}


def load_sample_elements(path: Path) -> dict[str, list[str]]:
    d = _read_json(path, {})
    return {k: list(v) for k, v in d.items() if not k.startswith("_") and isinstance(v, list)}


# ----------------------------------------------------------------------------- the sample sheet (cp1252 csv)
@dataclass
class SheetRow:
    plate: str                      # 'Final plate#' (N7, R2 ...)
    group: str                      # 'Sample group'
    sample_id: str                  # 'Sample ID'
    composition: str
    f_k_edge: str
    other_edges: str
    notes: str
    raw: dict = field(default_factory=dict)


def read_sample_sheet(path: Path) -> list[SheetRow]:
    """The sample sheet: cp1252, a junk first line, the header on line 2, one row per sample."""
    path = Path(path)
    with open(path, encoding="cp1252", newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return []
    # the header is the first line that contains 'Sample ID'
    hi = next((i for i, r in enumerate(rows[:5]) if any("Sample ID" in c for c in r)), 1)
    header = [c.strip() for c in rows[hi]]
    idx = {h: i for i, h in enumerate(header)}

    def get(r, key):
        i = idx.get(key)
        return r[i].strip() if i is not None and i < len(r) else ""

    out = []
    for r in rows[hi + 1:]:
        if not any(c.strip() for c in r):
            continue
        sid = get(r, "Sample ID")
        if not sid:
            continue
        notes_key = next((h for h in header if h.startswith("Notes")), "")
        out.append(SheetRow(plate=get(r, "Final plate#"), group=get(r, "Sample group"), sample_id=sid,
                            composition=get(r, "Composition"), f_k_edge=get(r, "F K edge"),
                            other_edges=get(r, "Other edges?"), notes=get(r, notes_key) if notes_key else "",
                            raw={h: (r[i].strip() if i < len(r) else "") for h, i in idx.items() if h}))
    log.info("%s: %d sample rows, header line %d", path, len(out), hi + 1)
    return out


# ----------------------------------------------------------------------------- processed/<day>/summary.json
def load_summary(day_proc_dir: Path) -> dict | None:
    """sgm_report's per-day summary: {day, holders: [{holder, survey, pads, scans: [{scan, label, type, mode, E,
    zones, sdd_saturated, i0_bad, notes, ...}], dropped: [[name, reason], ...]}]}."""
    return _read_json(Path(day_proc_dir) / "summary.json", None)


def summary_by_scan(summary: dict | None) -> dict[str, dict]:
    """scan folder name -> its summary entry (plus 'holder'), including dropped scans as {'dropped': reason}."""
    out: dict[str, dict] = {}
    if not summary:
        return out
    for h in summary.get("holders", []):
        for s in h.get("scans", []):
            out[s["scan"]] = {**s, "holder": h.get("holder")}
        for item in h.get("dropped", []):
            name, reason = (item[0], item[1]) if isinstance(item, (list, tuple)) else (str(item), "")
            out.setdefault(name, {"holder": h.get("holder")})["dropped"] = reason
    return out


# ----------------------------------------------------------------------------- zones_all.json (manual zones)
@dataclass
class ScanSettings:
    """Per-zone, per-scan settings of the zone tool (docs/HANDOFF.md section 4)."""
    include: bool = True
    n: int = 0
    sdd: list[bool] = field(default_factory=lambda: [True, True, True, True])
    line: str = ""
    fluo_norm: str = "i0jump"       # raw | i0 | i0pre | i0jump | area | max
    tey: bool = True
    tey_norm: str = "i0jump"
    despike: bool = True
    k: float = 6.0
    i0_source: str = "own"          # own | good
    pre: list[float] | None = None
    post: list[float] | None = None
    stack: str = ""

    @classmethod
    def from_json(cls, d: dict) -> "ScanSettings":
        return cls(include=bool(d.get("inc", True)), n=int(d.get("n", 0)), sdd=[bool(v) for v in d.get("sdd", [1, 1, 1, 1])],
                   line=str(d.get("line", "")), fluo_norm=str(d.get("fn", "i0jump")), tey=bool(d.get("tey", True)),
                   tey_norm=str(d.get("tn", "i0jump")), despike=bool(d.get("ds", True)), k=float(d.get("k", 6)),
                   i0_source=str(d.get("i0src", "own")), pre=d.get("pre"), post=d.get("post"), stack=str(d.get("stack", "")))

    def to_json(self) -> dict:
        return {"inc": self.include, "n": self.n, "sdd": self.sdd, "line": self.line, "fn": self.fluo_norm,
                "tey": self.tey, "tn": self.tey_norm, "ds": self.despike, "k": self.k, "i0src": self.i0_source,
                "pre": self.pre, "post": self.post, "stack": self.stack}


@dataclass
class Zone:
    id: int
    label: str
    group: str = ""
    comment: str = ""
    rects: list[list[float]] = field(default_factory=list)      # [x0, x1, y0, y1] mm
    erects: list[list[float]] = field(default_factory=list)
    paint: list[list[float]] = field(default_factory=list)      # [cx, cy, pitchx, pitchy] mm
    erase: list[list[float]] = field(default_factory=list)
    settings: dict[str, ScanSettings] = field(default_factory=dict)     # '<day>_<scan>' -> settings
    members: dict[str, list[int]] = field(default_factory=dict)         # '<day>_<scan>' -> pixel indices

    @classmethod
    def from_json(cls, d: dict) -> "Zone":
        return cls(id=int(d.get("id", 0)), label=str(d.get("label", "")), group=str(d.get("group", "")),
                   comment=str(d.get("comment", "")), rects=[list(map(float, r)) for r in d.get("rects", [])],
                   erects=[list(map(float, r)) for r in d.get("erects", [])],
                   paint=[list(map(float, r)) for r in d.get("paint", [])],
                   erase=[list(map(float, r)) for r in d.get("erase", [])],
                   settings={k: ScanSettings.from_json(v) for k, v in d.get("st", {}).items()},
                   members={k: [int(i) for i in v] for k, v in d.get("members", {}).items()})

    def to_json(self) -> dict:
        return {"id": self.id, "label": self.label, "group": self.group, "comment": self.comment, "rects": self.rects,
                "erects": self.erects, "paint": self.paint, "erase": self.erase,
                "st": {k: v.to_json() for k, v in self.settings.items()}, "members": self.members}


@dataclass
class HolderZones:
    holder: str
    day: str
    survey: str
    zones: list[Zone] = field(default_factory=list)


@dataclass
class ZonesFile:
    """`zones_all.json` exported by the zone tool; AURORA reads it, edits it and can export it again unchanged."""
    version: int = 1
    exported: str = ""
    project: str = ""
    built: str = ""
    notes: str = ""
    good_i0: dict[str, bool] = field(default_factory=dict)
    holders: dict[str, HolderZones] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "ZonesFile":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        zf = cls(version=int(d.get("version", 1)), exported=str(d.get("exported", "")), project=str(d.get("project", "")),
                 built=str(d.get("built", "")), notes=str(d.get("notes", "")),
                 good_i0={k: bool(v) for k, v in d.get("goodI0", {}).items()})
        for name, h in d.get("holders", {}).items():
            zf.holders[name] = HolderZones(holder=str(h.get("holder", name)), day=str(h.get("day", "")),
                                           survey=str(h.get("survey", "")),
                                           zones=[Zone.from_json(z) for z in h.get("zones", [])])
        n = sum(len(h.zones) for h in zf.holders.values())
        log.info("%s: %d holders, %d zones", path, len(zf.holders), n)
        return zf

    def to_json(self) -> dict:
        return {"version": self.version, "exported": self.exported, "project": self.project, "built": self.built,
                "notes": self.notes, "goodI0": self.good_i0,
                "holders": {k: {"holder": h.holder, "day": h.day, "survey": h.survey,
                                "zones": [z.to_json() for z in h.zones]} for k, h in self.holders.items()}}

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self.to_json(), indent=1, ensure_ascii=False), encoding="utf-8")
