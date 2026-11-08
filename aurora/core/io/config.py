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
        from aurora.core.io.safety import assert_writable
        assert_writable(self.path)
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
# Keys added after the zone tool's format. They are written only when set (or when the file read had them), so a file
# without them reads and writes exactly as before, and the HTML zone tool keeps reading AURORA's files.


def _unknown(d: dict, keys) -> dict:
    """The keys of d that this version does not know, kept to be written back unchanged."""
    return {k: v for k, v in d.items() if k not in keys}


def json_default(o):
    """json.dumps default: numpy scalars (an np.int64 pixel index, an np.float32 coordinate) as Python values."""
    item = getattr(o, "item", None)
    if callable(item):
        return item()
    raise TypeError(f"{type(o).__name__} is not JSON serialisable")


def _floats(v) -> list[float]:
    return [float(a) for a in v]


@dataclass
class ScanSettings:
    """Per-zone, per-scan settings of the zone tool (docs/HANDOFF.md section 4), plus the fields AURORA adds."""
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
    # added by AURORA ('roi', 'sig', 'ipm', 'dsm', 'pin' in the file)
    roi: dict[str, list[int]] | None = None     # per-SDD emission window {sdd: [lo, hi)} in channels; None = the line's bins
    signal: str = "pfy"                         # pfy | ipfy
    ipfy_mode: str = "ratio"                    # IPFY definition: ratio (I0 / S) | cls | neg (compatibility forms)
    despike_mode: str = "classic"               # classic (the zone tool's rule) | coherent (across the four SDDs)
    pinned: list[str] = field(default_factory=list)     # fields set by hand, which rules leave alone
    extra: dict = field(default_factory=dict)            # keys this version does not know, written back unchanged
    _seen: frozenset = field(default=frozenset(), compare=False, repr=False)    # added keys present in the file read

    BASE_KEYS = ("inc", "n", "sdd", "line", "fn", "tey", "tn", "ds", "k", "i0src", "pre", "post", "stack")
    ADDED_KEYS = ("roi", "sig", "ipm", "dsm", "pin")
    # the processing choices compared by differs_from (include, n, stack, pinned and extra are bookkeeping)
    SETTINGS_FIELDS = ("sdd", "line", "fluo_norm", "tey", "tey_norm", "despike", "k", "i0_source", "pre", "post", "roi",
                       "signal", "ipfy_mode", "despike_mode")

    @classmethod
    def from_json(cls, d: dict) -> "ScanSettings":
        roi = d.get("roi")
        if isinstance(roi, dict):
            roi = {str(k): [int(v[0]), int(v[1])] for k, v in roi.items()}
        else:
            roi = None
        return cls(include=bool(d.get("inc", True)), n=int(d.get("n", 0)), sdd=[bool(v) for v in d.get("sdd", [1, 1, 1, 1])],
                   line=str(d.get("line", "")), fluo_norm=str(d.get("fn", "i0jump")), tey=bool(d.get("tey", True)),
                   tey_norm=str(d.get("tn", "i0jump")), despike=bool(d.get("ds", True)), k=float(d.get("k", 6)),
                   i0_source=str(d.get("i0src", "own")), pre=d.get("pre"), post=d.get("post"), stack=str(d.get("stack", "")),
                   roi=roi, signal=str(d.get("sig", "pfy")), ipfy_mode=str(d.get("ipm", "ratio")),
                   despike_mode=str(d.get("dsm", "classic")), pinned=[str(p) for p in d.get("pin", [])],
                   extra=_unknown(d, cls.BASE_KEYS + cls.ADDED_KEYS),
                   _seen=frozenset(k for k in cls.ADDED_KEYS if k in d))

    def to_json(self) -> dict:
        out = {"inc": self.include, "n": self.n, "sdd": self.sdd, "line": self.line, "fn": self.fluo_norm,
               "tey": self.tey, "tn": self.tey_norm, "ds": self.despike, "k": self.k, "i0src": self.i0_source,
               "pre": self.pre, "post": self.post, "stack": self.stack}
        added = {"roi": (self.roi, None), "sig": (self.signal, "pfy"), "ipm": (self.ipfy_mode, "ratio"),
                 "dsm": (self.despike_mode, "classic"), "pin": (self.pinned, [])}
        for key, (value, default) in added.items():
            if value != default or key in self._seen:
                out[key] = value
        for key, value in self.extra.items():
            out.setdefault(key, value)
        return out

    def differs_from(self, other: "ScanSettings", fields=None) -> list[str]:
        """Names of the processing fields whose values differ from `other` (SETTINGS_FIELDS unless `fields` is given),
        e.g. ['fluo_norm', 'sdd']. include, n, stack, pinned and extra never count."""
        out = []
        for f in (fields or self.SETTINGS_FIELDS):
            a, b = getattr(self, f), getattr(other, f)
            if isinstance(a, (list, tuple)) or isinstance(b, (list, tuple)):
                a = list(a) if a is not None else None
                b = list(b) if b is not None else None
            if a != b:
                out.append(f)
        return out


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
    polys: list[list[list[float]]] = field(default_factory=list)       # polygons [[x, y], ...] mm (AURORA)
    extra: dict = field(default_factory=dict)                           # keys this version does not know
    _seen: frozenset = field(default=frozenset(), compare=False, repr=False)

    BASE_KEYS = ("id", "label", "group", "comment", "rects", "erects", "paint", "erase", "st", "members")
    ADDED_KEYS = ("polys",)

    @classmethod
    def from_json(cls, d: dict) -> "Zone":
        return cls(id=int(d.get("id", 0)), label=str(d.get("label", "")), group=str(d.get("group", "")),
                   comment=str(d.get("comment", "")), rects=[_floats(r) for r in d.get("rects", [])],
                   erects=[_floats(r) for r in d.get("erects", [])],
                   paint=[_floats(r) for r in d.get("paint", [])],
                   erase=[_floats(r) for r in d.get("erase", [])],
                   settings={k: ScanSettings.from_json(v) for k, v in d.get("st", {}).items()},
                   members={k: [int(i) for i in v] for k, v in d.get("members", {}).items()},
                   polys=[[_floats(p) for p in poly] for poly in d.get("polys", [])],
                   extra=_unknown(d, cls.BASE_KEYS + cls.ADDED_KEYS),
                   _seen=frozenset(k for k in cls.ADDED_KEYS if k in d))

    def to_json(self) -> dict:
        out = {"id": self.id, "label": self.label, "group": self.group, "comment": self.comment, "rects": self.rects,
               "erects": self.erects, "paint": self.paint, "erase": self.erase,
               "st": {k: v.to_json() for k, v in self.settings.items()}, "members": self.members}
        if self.polys or "polys" in self._seen:
            out["polys"] = self.polys
        for key, value in self.extra.items():
            out.setdefault(key, value)
        return out


@dataclass
class HolderZones:
    holder: str
    day: str
    survey: str
    zones: list[Zone] = field(default_factory=list)
    extra: dict = field(default_factory=dict)                   # keys this version does not know

    BASE_KEYS = ("holder", "day", "survey", "zones")

    @classmethod
    def from_json(cls, d: dict, name: str = "") -> "HolderZones":
        return cls(holder=str(d.get("holder", name)), day=str(d.get("day", "")), survey=str(d.get("survey", "")),
                   zones=[Zone.from_json(z) for z in d.get("zones", [])], extra=_unknown(d, cls.BASE_KEYS))

    def to_json(self) -> dict:
        out = {"holder": self.holder, "day": self.day, "survey": self.survey, "zones": [z.to_json() for z in self.zones]}
        for key, value in self.extra.items():
            out.setdefault(key, value)
        return out


# The session file's header (AURORA's session.json only; the zone tool's zones_all.json never carries it).
SESSION_FORMAT = "aurora-session"
SESSION_SCHEMA = 2          # 1 = no header (AURORA <= 0.3 and the zone tool's files); 2 = header, kept unknown keys
TOOL_VERSION = 1            # 'version' of the zone tool's zones_all.json, written by every export


def file_schema(d: dict) -> int:
    """The session schema a parsed file declares: its 'schema' when it is an integer, else 1 (a file without header,
    as the zone tool and AURORA <= 0.3 wrote it). A 'schema' that is not an integer counts as newer than this
    version (read-only), never as older."""
    if "schema" not in d:
        return 1
    try:
        return int(d["schema"])
    except (TypeError, ValueError):
        return 10 ** 6


@dataclass
class ZonesFile:
    """`zones_all.json` exported by the zone tool; AURORA reads it, edits it and can export it again unchanged.

    AURORA's session.json is the same format plus a header ('format', 'schema', 'app', written when set, see
    SESSION_SCHEMA) and two blocks the tool ignores: 'ui' (view memory, Session.ui_value) and 'rules' (batch-settings
    rules). The header and both blocks are left out of an export (to_json(export=True)), which is always version 1."""
    version: int = 1
    exported: str = ""
    project: str = ""
    built: str = ""
    notes: str = ""
    good_i0: dict[str, bool] = field(default_factory=dict)
    holders: dict[str, HolderZones] = field(default_factory=dict)
    ui: dict = field(default_factory=dict)                      # session view memory (AURORA)
    rules: list = field(default_factory=list)                   # batch-settings rules, as JSON (AURORA)
    format: str = ""                                            # header: 'aurora-session' (session.json only)
    schema: int | None = None                                   # header: SESSION_SCHEMA of the writer
    app: str = ""                                               # header: 'AURORA <version>' of the writer
    extra: dict = field(default_factory=dict)                   # keys this version does not know
    _seen: frozenset = field(default=frozenset(), compare=False, repr=False)

    BASE_KEYS = ("version", "exported", "project", "built", "notes", "goodI0", "holders")
    ADDED_KEYS = ("ui", "rules")
    HEADER_KEYS = ("format", "schema", "app")

    @classmethod
    def from_json(cls, d: dict) -> "ZonesFile":
        ui = d.get("ui")
        rules = d.get("rules")
        known = cls.BASE_KEYS + cls.ADDED_KEYS + cls.HEADER_KEYS
        schema = d.get("schema")
        if schema is not None:
            try:
                schema = int(schema)
            except (TypeError, ValueError):     # kept as it was written (in extra), never guessed
                known = tuple(k for k in known if k != "schema")
                schema = None
        try:
            version = int(d.get("version", 1))
        except (TypeError, ValueError):
            raise ValueError(f"'version' must be an integer, not {d.get('version')!r}") from None
        return cls(version=version, exported=str(d.get("exported", "")), project=str(d.get("project", "")),
                   built=str(d.get("built", "")), notes=str(d.get("notes", "")),
                   good_i0={k: bool(v) for k, v in d.get("goodI0", {}).items()},
                   holders={name: HolderZones.from_json(h, name) for name, h in d.get("holders", {}).items()},
                   ui=dict(ui) if isinstance(ui, dict) else {}, rules=list(rules) if isinstance(rules, list) else [],
                   format=str(d.get("format", "")), schema=schema, app=str(d.get("app", "")),
                   extra=_unknown(d, known),
                   _seen=frozenset(k for k in cls.ADDED_KEYS if k in d))

    @classmethod
    def load(cls, path: Path) -> "ZonesFile":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError(f"{path}: not a zones file (a JSON object is expected)")
        zf = cls.from_json(d)
        n = sum(len(h.zones) for h in zf.holders.values())
        log.info("%s: %d holders, %d zones", path, len(zf.holders), n)
        return zf

    def to_json(self, export: bool = False) -> dict:
        """The file content. export=True gives the zone tool's zones_all.json: version 1, no header ('format',
        'schema', 'app'), no 'ui' and no 'rules' block."""
        out: dict = {}
        if not export:
            if self.format:
                out["format"] = self.format
            if self.schema is not None:
                out["schema"] = self.schema
            if self.app:
                out["app"] = self.app
        out.update({"version": TOOL_VERSION if export else self.version, "exported": self.exported,
                    "project": self.project, "built": self.built, "notes": self.notes, "goodI0": self.good_i0,
                    "holders": {k: h.to_json() for k, h in self.holders.items()}})
        if not export:
            if self.ui or "ui" in self._seen:
                out["ui"] = self.ui
            if self.rules or "rules" in self._seen:
                out["rules"] = self.rules
        for key, value in self.extra.items():
            if export and key in self.HEADER_KEYS:
                continue
            out.setdefault(key, value)
        return out

    def stamp(self, app: str) -> None:
        """Mark this file as a current AURORA session (the header written by Session.save)."""
        self.format, self.schema, self.app = SESSION_FORMAT, SESSION_SCHEMA, app
        self.extra.pop("schema", None)          # a non-integer 'schema' read from an older writer is replaced

    def save(self, path: Path, export: bool = False) -> None:
        """Atomic write (aurora.core.io.textio): a crash mid-way never leaves a half-written file."""
        from aurora.core.io.textio import write_text_atomic
        write_text_atomic(path, json.dumps(self.to_json(export=export), indent=1, ensure_ascii=False,
                                           default=json_default))
