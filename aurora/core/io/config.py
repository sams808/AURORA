"""Project configuration files (config/*.json), the sample sheet, summary.json and the manual zones file.

Formats are those of sgm_report.py v2.5 and the HTML zone tool (docs/HANDOFF.md sections 2.3 and 4). Every
configuration file is optional, and every one is validated when it is read: an entry of the wrong shape is left out
(the nominal value takes its place) and its reason goes into the reader's `problems`, so a malformed file never stops a
project from opening, and Home and `aurora project` can say what was wrong.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import math
import os
import re
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
        return json.loads(path.read_text(encoding="utf-8-sig"))         # a BOM (Notepad, PowerShell 5) is accepted
    except (OSError, ValueError) as err:
        log.warning("%s unreadable (%s); ignored", path, err)
        return default


def load_json_checked(path: Path | None) -> tuple[object, str]:
    """(parsed JSON, problem) of an optional file: (None, '') when it does not exist, (None, 'not valid JSON (line 3,
    column 5: Expecting ...)') or (None, 'unreadable (...)') when it cannot be used, else (value, '')."""
    if path is None:
        return None, ""
    path = Path(path)
    try:
        if not path.is_file():
            return None, ""
        text = path.read_text(encoding="utf-8-sig")         # a BOM (Notepad, PowerShell 5) is accepted
    except UnicodeDecodeError as err:
        log.warning("%s unreadable (%s); ignored", path, err)
        return None, f"not a UTF-8 text file ({err.reason} at byte {err.start})"
    except OSError as err:
        log.warning("%s unreadable (%s); ignored", path, err)
        return None, f"unreadable ({err.strerror or err})"
    try:
        return json.loads(text), ""
    except json.JSONDecodeError as err:
        log.warning("%s is not valid JSON (%s); ignored", path, err)
        return None, f"not valid JSON (line {err.lineno}, column {err.colno}: {err.msg})"


def _number(v) -> float | None:
    """A finite JSON number as a float; None for a string, a boolean, null, NaN or anything else."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _ranges(names: list[str]) -> str:
    """'sdd2–sdd4' for consecutive detectors, 'sdd1, sdd3' otherwise."""
    idx = sorted(DETS.index(n) for n in names if n in DETS)
    if len(idx) > 2 and idx == list(range(idx[0], idx[-1] + 1)):
        return f"{DETS[idx[0]]}–{DETS[idx[-1]]}"
    return ", ".join(DETS[i] for i in idx)


def _shown(path: Path | None, root: Path | None) -> str:
    """A file name relative to the project root when given ('config/sdd_calibration.json', '../X/y.json'), else in
    full; 'sdd_calibration.json' without a path."""
    if path is None:
        return "sdd_calibration.json"
    if root is not None:
        try:
            return Path(os.path.relpath(path, root)).as_posix()
        except ValueError:                          # another drive (Windows): the full path
            pass
    return str(path)


# ----------------------------------------------------------------------------- SDD calibration
CALIBRATION_FILE = "sdd_calibration.json"


class Calibration:
    """Linear channel -> eV calibration per detector (`sdd_calibration.json`: {sdd: {gain, offset}, _metadata}).

    Every detector entry is checked: a JSON object with a finite number `gain` > 0 (eV per channel) and a finite number
    `offset` (eV). A detector whose entry is missing or wrong keeps the nominal UNCALIBRATED_EV_PER_CH gain and offset 0,
    and the reason goes into `problems` ('sdd2: missing', 'sdd1: gain is not a number', 'the file is not a JSON
    object'). `calibrated` is True only when the four detectors are valid. ev(), ch(), bounds() and describe() never
    raise. `sibling` is set by Project.discover when the file is the one next to the project folder."""

    def __init__(self, path: Path | None = None, data: dict | None = None):
        self.path = Path(path) if path else None
        self.problems: list[str] = []
        self.sibling = False                        # the file next to the project (Project.discover), not config/'s
        if data is not None:
            raw, self.exists = data, bool(data)
        else:
            raw, problem = load_json_checked(self.path)
            self.exists = raw is not None or bool(problem)
            if problem:
                self.problems.append(f"the file is {problem}")
        if raw is not None and not isinstance(raw, dict):
            self.problems.append(f"the file is not a JSON object (a {type(raw).__name__})")
            raw = {}
        raw = dict(raw or {})
        self.data: dict = {k: v for k, v in raw.items() if k not in DETS}       # _metadata and keys AURORA does not use
        if "_metadata" in self.data and not isinstance(self.data["_metadata"], dict):
            self.problems.append("_metadata is not a JSON object: ignored")
            self.data["_metadata"] = {}
        self.missing: list[str] = []                # detectors on the nominal gain
        absent = []
        for d in DETS:
            entry = raw.get(d)
            why = self._why_invalid(entry)
            if why is None:
                self.data[d] = {**entry, "gain": float(entry["gain"]), "offset": float(entry["offset"])}
                continue
            self.missing.append(d)
            if entry is None:
                absent.append(d)
            else:
                self.problems.append(f"{d}: {why}")
            self.data[d] = {"gain": UNCALIBRATED_EV_PER_CH, "offset": 0.0, "note": "nominal, not calibrated"}
        if absent and self.exists and raw:
            self.problems.append(f"{_ranges(absent)} missing")
        self.calibrated = not self.missing

    @staticmethod
    def _why_invalid(entry) -> str | None:
        """Why a detector entry cannot be used, None when it can."""
        if entry is None:
            return "missing"
        if not isinstance(entry, dict):
            return f"not a JSON object ({entry!r})"
        for key in ("gain", "offset"):
            if key not in entry:
                return f"{key} missing"
            if _number(entry[key]) is None:
                return f"{key} is not a number ({entry[key]!r})"
        if float(entry["gain"]) <= 0:
            return f"gain must be above 0 eV per channel (it is {entry['gain']!r})"
        return None

    def ev(self, det: str, ch) -> np.ndarray:
        d = self.data.get(det) or {"gain": UNCALIBRATED_EV_PER_CH, "offset": 0.0}
        return d["gain"] * np.asarray(ch, float) + d["offset"]

    def ch(self, det: str, ev) -> np.ndarray:
        d = self.data.get(det) or {"gain": UNCALIBRATED_EV_PER_CH, "offset": 0.0}
        return (np.asarray(ev, float) - d["offset"]) / d["gain"]

    def bounds(self, det: str, e_lo: float, e_hi: float) -> tuple[int, int]:
        """Channel slice (lo, hi) covering e_lo..e_hi, hi exclusive, clipped to 0..256."""
        a, b = float(self.ch(det, e_lo)), float(self.ch(det, e_hi))
        lo = int(max(0, min(N_CH - 1, np.floor(min(a, b))))) if np.isfinite(min(a, b)) else 0
        hi = int(min(N_CH, max(lo + 1, np.ceil(max(a, b))))) if np.isfinite(max(a, b)) else N_CH
        return lo, hi

    @property
    def date(self) -> str:
        """The file's `_metadata.last_updated`, '' when it records none."""
        md = self.data.get("_metadata")
        return str(md.get("last_updated") or "") if isinstance(md, dict) else ""

    @property
    def last_updated(self) -> str:
        return self.date or "no date recorded"

    def summary(self, root: Path | None = None) -> str:
        """The state in words, without the gains (Home's calibration row; describe() adds the gains):
        'none (no config/sdd_calibration.json), nominal 10 eV per channel',
        'config/sdd_calibration.json, incomplete (sdd2–sdd4 missing: nominal)',
        'config/sdd_calibration.json (updated 2026-11-04 14:06:43)'; the sibling file adds where it comes from."""
        where = _shown(self.path, root)
        nominal = f"nominal {UNCALIBRATED_EV_PER_CH:g} eV per channel"
        if not self.exists:
            return f"none (no {where}), {nominal}"
        date = f" (updated {self.date})" if self.date else ""
        if len(self.missing) == len(DETS):
            why = "; ".join(self.problems) or "no detector entry"
            text = f"{where}{date}, not usable ({why}): {nominal}"
        elif self.missing:
            text = f"{where}{date}, incomplete ({'; '.join(self.problems)}: nominal)"
        else:
            text = f"{where}{date}" + (f" ({'; '.join(self.problems)})" if self.problems else "")
        if self.sibling:
            text += f", from beside the project (the project has no config/{CALIBRATION_FILE})"
        return text

    def describe(self, root: Path | None = None) -> str:
        """One provenance line (aurora project, the report cover, every report CSV): 'SDD calibration: ' + summary(),
        then the gain and offset of each detector when at least one comes from the file."""
        text = f"SDD calibration: {self.summary(root)}"
        if not self.exists or len(self.missing) == len(DETS):
            return text
        return text + ": " + "; ".join(f"{d} {self.data[d]['gain']:.3f} eV/ch {self.data[d]['offset']:+.1f}"
                                       + (" (nominal)" if d in self.missing else "") for d in DETS)

    def save(self, meta: dict | None = None) -> None:
        if self.path is None:
            raise ValueError("calibration has no file path")
        md = {**(self.data.get("_metadata") or {}), **(meta or {}),
              "last_updated": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        self.data["_metadata"] = md
        from aurora.core.io.textio import write_text_atomic
        write_text_atomic(self.path, json.dumps(self.data, indent=4))


# ----------------------------------------------------------------------------- fixed ROI bins
ROI_BINS_FILE = "roi_bins.json"


class RoiBins:
    """`roi_bins.json`: line label -> {sdd: [first channel, last channel + 1]}, identical for every scan and day.

    The windows are defined once and never refitted silently. A line is kept only when it is a JSON object with the
    four detectors, each two integer channels 0 <= lo < hi <= 256; `_meta.gains` (positive numbers) and `_meta.n_sigma`
    (a positive number) are checked the same way. Everything left out is named in `problems` ('F Kα: sdd4 missing',
    'the file is not a JSON object'); `labels` lists the valid lines only. `raw` keeps the file as it was read, so a
    line saved by AURORA (add_line) never drops what another tool wrote."""

    def __init__(self, path: Path | None = None, data: dict | None = None):
        self.path = Path(path) if path else None
        self.problems: list[str] = []
        if data is not None:
            raw, self.exists = data, bool(data)
        else:
            raw, problem = load_json_checked(self.path)
            self.exists = raw is not None or bool(problem)
            if problem:
                self.problems.append(f"the file is {problem}")
        if raw is not None and not isinstance(raw, dict):
            self.problems.append(f"the file is not a JSON object (a {type(raw).__name__})")
            raw = None
        self.raw: dict = dict(raw or {})
        self.data: dict = {}
        for k, v in self.raw.items():
            if str(k).startswith("_"):
                self.data[k] = self._meta(v) if k == "_meta" else v
                continue
            why = self._why_invalid(v)
            if why is None:
                self.data[k] = {**v, **{d: [int(v[d][0]), int(v[d][1])] for d in DETS}}
            else:
                self.problems.append(f"{k}: {why}")

    def _meta(self, v) -> dict:
        if not isinstance(v, dict):
            self.problems.append("_meta is not a JSON object: ignored")
            return {}
        out = dict(v)
        gains = out.get("gains")
        if gains is not None:
            if not isinstance(gains, dict):
                self.problems.append("_meta.gains is not a JSON object: ignored")
                out.pop("gains")
            else:
                bad = [d for d, g in gains.items() if _number(g) is None or _number(g) <= 0]
                if bad:
                    self.problems.append(f"_meta.gains: {', '.join(map(str, bad))} not a positive number: ignored")
                out["gains"] = {d: float(g) for d, g in gains.items() if d not in bad}
        if "n_sigma" in out and (_number(out["n_sigma"]) is None or _number(out["n_sigma"]) <= 0):
            self.problems.append(f"_meta.n_sigma is not a positive number ({out['n_sigma']!r}): ignored")
            out.pop("n_sigma")
        return out

    @staticmethod
    def _why_invalid(v) -> str | None:
        if not isinstance(v, dict):
            return f"not a JSON object ({v!r})"
        missing = [d for d in DETS if d not in v]
        if missing:
            return f"{_ranges(missing)} missing"
        for d in DETS:
            b = v[d]
            if not isinstance(b, (list, tuple)) or len(b) != 2:
                return f"{d} must be [first channel, last channel + 1], not {b!r}"
            n = [_number(x) for x in b]
            if any(x is None or x != int(x) for x in n):
                return f"{d} must be two whole channel numbers, not {b!r}"
            lo, hi = int(n[0]), int(n[1])
            if not 0 <= lo < hi <= N_CH:
                return f"{d} channels {lo}, {hi} are outside 0 <= first < last + 1 <= {N_CH}"
        return None

    @property
    def labels(self) -> list[str]:
        return [k for k in self.data if not str(k).startswith("_")]

    def has(self, label: str) -> bool:
        return label in self.data and not str(label).startswith("_")

    def bounds(self, label: str) -> dict[str, tuple[int, int]]:
        return {d: (int(self.data[label][d][0]), int(self.data[label][d][1])) for d in DETS}

    def line_ev(self, label: str) -> float | None:
        v = self.data.get(label, {}).get("line_ev") if self.has(label) else None
        return _number(v)

    def gains(self) -> dict[str, float]:
        meta = self.data.get("_meta")
        return dict(meta.get("gains", {})) if isinstance(meta, dict) else {}

    def describe(self, label: str) -> str:
        if not self.has(label):
            return f"{label}: not defined"
        b = self.bounds(label)
        return f"{label}: " + "/".join(f"{b[d][0]}-{b[d][1] - 1}" for d in DETS) + " ch"

    def add_line(self, label: str, windows: dict, path: Path | None = None, extra: dict | None = None,
                 created_by: str = "") -> Path:
        """Write `label` -> windows ({sdd: [lo, hi)}) into roi_bins.json (self.path unless `path` is given): the file
        is created with a `_meta` block when it does not exist, the previous file is kept as
        roi_bins.backup-<time>.json, every other entry of the file is kept as it was, and the write is atomic.
        ProtectedPathError in a protected folder; ValueError for windows of the wrong shape. Returns the file."""
        label = str(label).strip()
        if not label or label.startswith("_"):
            raise ValueError("a line name must not be empty or start with '_'")
        entry = {d: [int(windows[d][0]), int(windows[d][1])] for d in DETS}
        why = self._why_invalid(entry)
        if why is not None:
            raise ValueError(why)
        target = Path(path or self.path or "")
        if not str(target):
            raise ValueError("roi_bins.json has no file path")
        from aurora.core.io.safety import assert_writable
        from aurora.core.io.textio import write_text_atomic
        assert_writable(target)
        raw, problem = load_json_checked(target)
        if problem or (raw is not None and not isinstance(raw, dict)):
            raise ValueError(f"{target.name} is {problem or 'not a JSON object'}: fix or move it first, AURORA does "
                             "not write over it")
        raw = dict(raw or {})
        if target.exists():
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = target.with_name(f"{target.stem}.backup-{stamp}{target.suffix}")
            write_text_atomic(backup, target.read_text(encoding="utf-8-sig"))
        else:
            raw["_meta"] = {"help": "line -> {sdd: [first channel, last channel + 1]}, the same for every scan; "
                                    "written by " + (created_by or "AURORA")}
        raw[label] = {**entry, **(extra or {})}
        write_text_atomic(target, json.dumps(raw, indent=1, ensure_ascii=False))
        log.info("%s: line %r saved (%s)", target, label, entry)
        return target


# ----------------------------------------------------------------------------- plate layouts
PLATE_LAYOUT_FILE = "plate_layout.json"


def _check_layout_group(g) -> str | None:
    """Why one group of plate_layout.json cannot be used, None when it can: rows and cols positive integers (when
    given), cells a list of rows of plate numbers or null, match a list of words, names a list of names."""
    if not isinstance(g, dict):
        return f"not a JSON object ({g!r})"
    for key in ("rows", "cols"):
        if key in g and g[key] is not None:
            v = g[key]
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                return f"{key} must be a whole number of at least 1, not {v!r}"
    cells = g.get("cells")
    if cells is not None:
        if not isinstance(cells, list) or not all(isinstance(r, list) for r in cells):
            return f"cells must be a list of rows, not {cells!r}"
        for row in cells:
            for n in row:
                if n is not None and (isinstance(n, bool) or not isinstance(n, (int, str))):
                    return f"cells must hold plate numbers or null, not {n!r}"
    for key in ("match", "names"):
        v = g.get(key)
        if v is not None and (not isinstance(v, list) or not all(isinstance(w, str) or w is None for w in v)):
            return f"{key} must be a list of words, not {v!r}"
    return None


def check_plate_layout(d) -> tuple[dict, list[str]]:
    """(holder -> layout, problems) of a parsed plate_layout.json: a holder must be a JSON object, its 'samples' an
    object, its 'groups' a list; a group of the wrong shape is left out and named. Keys starting with '_' are help."""
    problems: list[str] = []
    if d is None:
        return {}, problems
    if not isinstance(d, dict):
        return {}, [f"the file is not a JSON object (a {type(d).__name__})"]
    out = {}
    for holder, lay in d.items():
        if str(holder).startswith("_"):
            continue
        if not isinstance(lay, dict):
            problems.append(f"{holder}: not a JSON object")
            continue
        lay = dict(lay)
        if "samples" in lay and not isinstance(lay["samples"], dict):
            problems.append(f"{holder}: samples must be a JSON object (plate number -> name): ignored")
            lay["samples"] = {}
        groups = lay.get("groups")
        if groups is not None and not isinstance(groups, list):
            problems.append(f"{holder}: groups must be a list: ignored")
            groups = []
        kept = []
        for i, g in enumerate(groups or []):
            why = _check_layout_group(g)
            if why is None:
                kept.append(g)
            else:
                name = g.get("id", i + 1) if isinstance(g, dict) else i + 1
                problems.append(f"{holder}, group {name}: {why}: left out")
        if groups is not None:
            lay["groups"] = kept
        out[holder] = lay
    return out, problems


def load_plate_layout(path: Path) -> dict:
    """`plate_layout.json`: holder -> {hole, samples {plate number: name}, details, groups [{id, rows, cols, cells,
    match, ...}]}, validated (check_plate_layout; the problems are logged, Project.discover keeps them)."""
    return load_plate_layout_checked(path)[0]


def load_plate_layout_checked(path: Path) -> tuple[dict, list[str]]:
    """(layout, problems) of an optional plate_layout.json, never raising."""
    d, problem = load_json_checked(path)
    layout, problems = check_plate_layout(d)
    if problem:
        problems.insert(0, f"the file is {problem}")
    for p in problems:
        log.warning("%s: %s", path, p)
    return layout, problems


# ----------------------------------------------------------------------------- scan naming (optional)
NAMING_FILE = "naming.json"


def load_naming(path: Path) -> tuple[list[str], list[str]]:
    """(label suffix words, problems) of an optional config/naming.json: {"label_suffix_words": ["run", "a_coarse"]},
    the beamtime's own plan words that are removed from the end of a scan name to give the sample label."""
    d, problem = load_json_checked(path)
    problems = [f"the file is {problem}"] if problem else []
    if d is None:
        return [], problems
    words = d.get("label_suffix_words") if isinstance(d, dict) else None
    if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
        problems.append("label_suffix_words must be a list of words")
        return [], problems
    return [w.strip().strip("_") for w in words if w.strip().strip("_")], problems


def session_ui_value(root: Path, key: str, default=None, work: Path | None = None):
    """One value of the 'ui' block of <root>/.aurora/session.json, read without opening the session (Home's project
    summary, the command line); `default` when the file, the block or the key is missing or unreadable. With a working
    folder (`work`), its session is read when it has one."""
    path = Path(root) / ".aurora" / "session.json"
    if work is not None and (Path(work) / ".aurora" / "session.json").is_file():
        path = Path(work) / ".aurora" / "session.json"
    d = _read_json(path, None)
    ui = d.get("ui") if isinstance(d, dict) else None
    return ui.get(key, default) if isinstance(ui, dict) else default


# ----------------------------------------------------------------------------- the sample sheet (a CSV of any lab)
def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h).lower())


# recognised column names, compared case-, space- and punctuation-insensitive (the first synonym found wins)
SHEET_SYNONYMS = {
    "sample": ("sample id", "sample", "sample name", "name", "label", "id"),
    "holder": ("holder", "plate", "final plate#", "plate #", "mount"),
    "group": ("group", "sample group", "series"),
    "composition": ("composition", "formula"),
}
YES = {"y", "yes", "x", "true", "1", "oui", "ja", "si", "sí"}
NO = {"n", "no", "false", "0", "-", "non", "nein"}


@dataclass(init=False)
class SheetRow:
    """One sample of a sample sheet: its ID, the holder it sits on, its group, composition, edges and notes, plus every
    cell of the row (`raw`, header -> text). `plate` and `f_k_edge` are read-only aliases of holder and edges."""
    holder: str
    group: str
    sample_id: str
    composition: str
    edges: str
    notes: str
    raw: dict

    def __init__(self, holder: str = "", group: str = "", sample_id: str = "", composition: str = "", edges: str = "",
                 notes: str = "", raw: dict | None = None, *, plate: str | None = None, f_k_edge: str | None = None,
                 other_edges: str | None = None):
        self.holder = str(plate if plate is not None else holder)
        self.group, self.sample_id, self.composition = str(group), str(sample_id), str(composition)
        self.edges = "; ".join(e for e in (str(edges), f_k_edge or "", other_edges or "") if e)
        self.notes = str(notes)
        self.raw = dict(raw or {})

    @property
    def plate(self) -> str:
        return self.holder

    @property
    def f_k_edge(self) -> str:
        return self.edges


@dataclass
class SheetChoice:
    """The sample sheet a project uses (find_sample_sheet): its file, rows, the columns recognised (role -> header),
    a note on its encoding, and every other CSV at the root with the reason it was not used."""
    path: Path | None = None
    rows: list[SheetRow] = field(default_factory=list)
    columns: dict[str, str] = field(default_factory=dict)
    note: str = ""
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    chosen: bool = False                        # picked with File > Choose sample sheet… (the session's ui value)

    @property
    def holders(self) -> list[str]:
        return sorted({r.holder.strip() for r in self.rows if r.holder.strip()})

    def columns_text(self) -> str:
        """'sample = Sample ID, holder = Final plate#, group = Sample group'."""
        order = ("sample", "holder", "group", "composition", "edges", "notes")
        return ", ".join(f"{k} = {self.columns[k]}" for k in order if self.columns.get(k))


class SheetError(ValueError):
    """A CSV that is no sample sheet, with the reason in words ('no sample column', 'not a text file')."""


def _decode(data: bytes) -> tuple[str, str]:
    """(text, note) of a CSV: UTF-16 by its byte-order mark, UTF-8 (with or without a BOM), cp1252, else latin-1 with
    a note. SheetError for binary content (a spreadsheet saved under a .csv name)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "UTF-16"
        except UnicodeDecodeError as err:
            raise SheetError(f"not a text CSV (UTF-16 that does not decode: {err.reason})") from None
    if b"\x00" in data[:4096]:
        raise SheetError("not a text CSV (binary content: a spreadsheet file renamed .csv?)")
    for enc, note in (("utf-8-sig", ""), ("cp1252", "")):
        try:
            return data.decode(enc), note
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "read as latin-1 (neither UTF-8 nor Windows-1252): check accented names"


def _delimiter(lines: list[str]) -> str:
    """',' unless ';' or a tab separates more cells in the first lines (spreadsheets of some locales write ';')."""
    counts = {d: sum(line.count(d) for line in lines) for d in (",", ";", "\t")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] > counts[","] else ","


def sheet_columns(header: list[str]) -> dict[str, int]:
    """role -> column index of a header row: sample, holder, group, composition, notes (the first match of
    SHEET_SYNONYMS; notes = a header starting with 'note' or 'comment'); edges = every header containing 'edge',
    as role 'edges:<i>'."""
    normed = [_norm_header(h) for h in header]
    out: dict[str, int] = {}
    for role, names in SHEET_SYNONYMS.items():
        for name in names:
            n = _norm_header(name)
            if n in normed:
                out[role] = normed.index(n)
                break
    note = next((i for i, n in enumerate(normed) if n.startswith(("note", "comment"))), None)
    if note is not None:
        out["notes"] = note
    for i, n in enumerate(normed):
        if "edge" in n and i not in out.values():
            out[f"edges:{i}"] = i
    return out


def _edge_text(header: str, value: str) -> str:
    """A cell of an edge column: a yes-mark under 'F K edge' gives 'F K', a no-mark gives '', any other text itself."""
    v = value.strip()
    if v.lower() in YES:
        return re.sub(r"(?i)\bedges?\b|\?", "", header).strip(" :_-") or header
    if v.lower() in NO:
        return ""
    return v


def parse_sample_sheet(path: Path) -> tuple[list[SheetRow], dict[str, str], str]:
    """(rows, columns role -> header, encoding note) of one CSV. The header is the first of the first 5 lines that
    holds a sample column (title lines above it are allowed); rows without a sample ID are skipped. SheetError when
    the file is no sample sheet, OSError when it cannot be read."""
    path = Path(path)
    text, note = _decode(path.read_bytes())
    lines = text.splitlines()
    delim = _delimiter(lines[:5])
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delim))
    except csv.Error as err:
        raise SheetError(f"not a CSV ({err})") from None
    hi = next((i for i, r in enumerate(rows[:5]) if "sample" in sheet_columns(r)), None)
    if hi is None:
        raise SheetError("no sample column (Sample ID, Sample, Name, Label or ID) in its first 5 lines")
    header = [c.strip() for c in rows[hi]]
    cols = sheet_columns(header)
    edge_cols = [i for k, i in cols.items() if k.startswith("edges:")]

    def get(r, role):
        i = cols.get(role)
        return r[i].strip() if i is not None and i < len(r) else ""

    out = []
    for r in rows[hi + 1:]:
        if not any(c.strip() for c in r):
            continue
        sid = get(r, "sample")
        if not sid:
            continue
        edges = "; ".join(e for e in (_edge_text(header[i], r[i]) if i < len(r) else "" for i in edge_cols) if e)
        out.append(SheetRow(holder=get(r, "holder"), group=get(r, "group"), sample_id=sid,
                            composition=get(r, "composition"), edges=edges, notes=get(r, "notes"),
                            raw={h: (r[i].strip() if i < len(r) else "") for i, h in enumerate(header) if h}))
    named = {k: header[i] for k, i in cols.items() if not k.startswith("edges:")}
    if edge_cols:
        named["edges"] = ", ".join(header[i] for i in edge_cols)
    log.info("%s: %d sample rows, header line %d, columns %s", path, len(out), hi + 1, named)
    return out, named, note


def read_sample_sheet(path: Path) -> list[SheetRow]:
    """The rows of one sample sheet (parse_sample_sheet); [] for a file that is no sample sheet."""
    try:
        return parse_sample_sheet(path)[0]
    except SheetError as err:
        log.info("%s: not a sample sheet (%s)", path, err)
        return []


def find_sample_sheet(root: Path, chosen: str | None = None) -> SheetChoice:
    """The sample sheet of a project folder, never raising:
    1. `chosen` (a path relative to the root, the session's File > Choose sample sheet…) when it reads;
    2. otherwise the CSV at the root whose header has a sample and a holder column, preferring a name containing
       'sample';
    3. otherwise the first CSV with a sample column.
    Every other CSV is listed in `skipped` with its reason ('no holder column', 'no sample column …', 'not a text
    CSV …'). A chosen file that is missing or unreadable is listed first, and the rules 2 and 3 apply."""
    root = Path(root)
    out = SheetChoice()
    try:
        csvs = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".csv")
    except OSError:
        csvs = []
    parsed: list[tuple[Path, list[SheetRow], dict, str]] = []
    reasons: dict[Path, str] = {}
    want = None
    if chosen:
        want = Path(chosen) if Path(chosen).is_absolute() else root / chosen
        if want.is_file() and want not in csvs:
            csvs.insert(0, want)
    for p in csvs:
        try:
            rows, cols, note = parse_sample_sheet(p)
        except SheetError as err:
            reasons[p] = str(err)
            continue
        except OSError as err:
            reasons[p] = f"unreadable ({err.strerror or err})"
            continue
        if not rows:
            reasons[p] = "a header but no sample row"
            continue
        parsed.append((p, rows, cols, note))
    pick = None
    if want is not None:
        pick = next((x for x in parsed if x[0] == want), None)
        if pick is None:
            out.skipped.append((want, reasons.get(want, "chosen for this beamtime, but not found")))
        else:
            out.chosen = True
    if pick is None:
        both = [x for x in parsed if "holder" in x[2]]
        both.sort(key=lambda x: "sample" not in x[0].name.lower())
        pick = both[0] if both else (parsed[0] if parsed else None)
    if pick is not None:
        out.path, out.rows, out.columns, out.note = pick
    for p in csvs:
        if pick is not None and p == pick[0]:
            continue
        if want is not None and p == want and out.skipped and out.skipped[0][0] == want:
            continue
        if p in reasons:
            out.skipped.append((p, reasons[p]))
        else:
            cols = next(x[2] for x in parsed if x[0] == p)
            out.skipped.append((p, "no holder column" if "holder" not in cols else "another sample sheet"))
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


def _numbers(v, n: int, what: str) -> list[float]:
    """A geometry entry read from a file: exactly n finite numbers (a rectangle [x0, x1, y0, y1], a painted square
    [cx, cy, pitch x, pitch y], a polygon vertex [x, y]), else ValueError naming it."""
    if not isinstance(v, (list, tuple)) or len(v) != n:
        got = f"{len(v)} values" if isinstance(v, (list, tuple)) else repr(v)
        raise ValueError(f"{what} needs {n} numbers, not {got}")
    try:
        out = [float(a) for a in v]
    except (TypeError, ValueError):
        raise ValueError(f"{what} needs {n} numbers, not {v!r}") from None
    if not all(math.isfinite(a) for a in out):
        raise ValueError(f"{what} needs {n} finite numbers, not {v!r}")
    return out


def _of(v, kind, what: str, default):
    """A value read from a file that must be a dict or a list (`kind`): `default` when it is absent (None, unless
    default is None too), else ValueError naming it, so a file of the wrong shape is refused whole, never half-read."""
    if v is None and default is not None:
        return default
    if not isinstance(v, kind):
        raise ValueError(f"{what} must be {'an object' if kind is dict else 'a list'}, not {type(v).__name__}")
    return v


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
    epolys: list[list[list[float]]] = field(default_factory=list)      # erase polygons (lasso / polygon erase, AURORA)
    extra: dict = field(default_factory=dict)                           # keys this version does not know
    _seen: frozenset = field(default=frozenset(), compare=False, repr=False)

    BASE_KEYS = ("id", "label", "group", "comment", "rects", "erects", "paint", "erase", "st", "members")
    ADDED_KEYS = ("polys", "epolys")

    @classmethod
    def from_json(cls, d: dict) -> "Zone":
        """A zone of a file. Its geometry is checked here (ValueError 'zone 3: a rectangle needs 4 numbers, not 3
        values'): a malformed entry would otherwise fail later, inside every edit of its holder."""
        d = _of(d, dict, "a zone", None)
        try:
            zid = int(d.get("id", 0))
        except (TypeError, ValueError):
            raise ValueError(f"a zone id must be a number, not {d.get('id')!r}") from None
        z = f"zone {zid}"

        def rows(key: str, n: int, what: str) -> list[list[float]]:
            return [_numbers(r, n, f"{z}: {what}") for r in _of(d.get(key), list, f"{z}: '{key}'", [])]
        polys = [[_numbers(p, 2, f"{z}: a polygon vertex") for p in _of(poly, list, f"{z}: a polygon", None)]
                 for poly in _of(d.get("polys"), list, f"{z}: 'polys'", [])]
        epolys = [[_numbers(p, 2, f"{z}: an erase polygon vertex") for p in _of(poly, list, f"{z}: an erase polygon", None)]
                  for poly in _of(d.get("epolys"), list, f"{z}: 'epolys'", [])]
        return cls(id=zid, label=str(d.get("label", "")), group=str(d.get("group", "")),
                   comment=str(d.get("comment", "")), rects=rows("rects", 4, "a rectangle"),
                   erects=rows("erects", 4, "an erase rectangle"),
                   paint=rows("paint", 4, "a painted square"),
                   erase=rows("erase", 4, "an erased square"),
                   settings={k: ScanSettings.from_json(_of(v, dict, f"{z}: the settings of {k}", None))
                             for k, v in _of(d.get("st"), dict, f"{z}: 'st'", {}).items()},
                   members={k: [int(i) for i in _of(v, list, f"{z}: the members of {k}", None)]
                            for k, v in _of(d.get("members"), dict, f"{z}: 'members'", {}).items()},
                   polys=polys, epolys=epolys,
                   extra=_unknown(d, cls.BASE_KEYS + cls.ADDED_KEYS),
                   _seen=frozenset(k for k in cls.ADDED_KEYS if k in d))

    def to_json(self) -> dict:
        out = {"id": self.id, "label": self.label, "group": self.group, "comment": self.comment, "rects": self.rects,
               "erects": self.erects, "paint": self.paint, "erase": self.erase,
               "st": {k: v.to_json() for k, v in self.settings.items()}, "members": self.members}
        if self.polys or "polys" in self._seen:
            out["polys"] = self.polys
        if self.epolys or "epolys" in self._seen:
            out["epolys"] = self.epolys
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
        d = _of(d, dict, f"holder {name}", None)
        try:
            zones = [Zone.from_json(z) for z in _of(d.get("zones"), list, f"the zones of {name}", [])]
        except (ValueError, TypeError) as err:
            raise ValueError(f"holder {name}, {err}") from None
        return cls(holder=str(d.get("holder", name)), day=str(d.get("day", "")), survey=str(d.get("survey", "")),
                   zones=zones, extra=_unknown(d, cls.BASE_KEYS))

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
    host: str = ""                                              # header: the writer's computer, as a short hash
    extra: dict = field(default_factory=dict)                   # keys this version does not know
    _seen: frozenset = field(default=frozenset(), compare=False, repr=False)

    BASE_KEYS = ("version", "exported", "project", "built", "notes", "goodI0", "holders")
    ADDED_KEYS = ("ui", "rules")
    HEADER_KEYS = ("format", "schema", "app", "host")

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
                   good_i0={k: bool(v) for k, v in _of(d.get("goodI0"), dict, "'goodI0'", {}).items()},
                   holders={name: HolderZones.from_json(h, name)
                            for name, h in _of(d.get("holders"), dict, "'holders'", {}).items()},
                   ui=dict(ui) if isinstance(ui, dict) else {}, rules=list(rules) if isinstance(rules, list) else [],
                   format=str(d.get("format", "")), schema=schema, app=str(d.get("app", "")),
                   host=str(d.get("host", "") or ""),
                   extra=_unknown(d, known),
                   _seen=frozenset(k for k in cls.ADDED_KEYS if k in d))

    @classmethod
    def load(cls, path: Path) -> "ZonesFile":
        """Read a zones_all.json or session.json (a leading BOM is accepted). ValueError for a file that is not
        valid JSON or not of this shape (holders, zones, geometry), with what is wrong."""
        d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
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
            if self.host:
                out["host"] = self.host
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

    def stamp(self, app: str, host: str = "") -> None:
        """Mark this file as a current AURORA session (the header written by Session.save); `host`: the writer's
        computer (Session's host_id, a hash: no computer name is written)."""
        self.format, self.schema, self.app = SESSION_FORMAT, SESSION_SCHEMA, app
        if host:
            self.host = host
        self.extra.pop("schema", None)          # a non-integer 'schema' read from an older writer is replaced

    def save(self, path: Path, export: bool = False) -> None:
        """Atomic write (aurora.core.io.textio): a crash mid-way never leaves a half-written file."""
        from aurora.core.io.textio import write_text_atomic
        write_text_atomic(path, json.dumps(self.to_json(export=export), indent=1, ensure_ascii=False,
                                           default=json_default))
