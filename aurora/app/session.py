"""Shared application state: the project, the manual zones, loaded stacks and surveys, the good-I0 set, autosave,
backups and undo.

Holders are keyed like the zone tool keys them (the holder name, suffixed with the day when a name repeats);
stacks by '<day>_<scan folder>'. The zones live in `<root>/.aurora/session.json` (the zones_all.json format plus a
header {'format': 'aurora-session', 'schema': 2, 'app': 'AURORA <version>'}, a 'ui' block and a 'rules' block) and can
be imported from / exported to the tool's zones_all.json unchanged.

Zones attach to every complete stack of their holder from the stage coordinates alone (read from each stack's .h5 at
project open, no binary is read), so a zone gets settings for a stack that was never loaded.

Durability: session.json is written atomically; the previous good file is kept as session.json.bak; snapshots go to
`.aurora/backups/session-YYYYmmdd-HHMMSS.json` (at most one per SNAPSHOT_EVERY_S, the last SNAPSHOTS_KEPT kept, plus
one before every import, proposal, rule application and restore). A damaged session.json is kept as
session.corrupt-<time>.json and the session is restored from the newest readable state among the .bak and the
snapshots. A file written by a newer AURORA (a higher schema) opens read-only.

One AURORA at a time edits a project: the session takes the project's lock (.aurora/session.lock,
aurora.core.io.lock) when it opens it. A project another AURORA holds, or whose session cannot be written (a protected
or read-only folder, a read-only session.json), opens read-only and says why; Reload tries again. A save first checks
that the lock is still this session's, and keeps a session.json changed on disk by another program as a snapshot
before writing over it. Edits that could not be saved are never dropped without asking: confirm_leave() offers
Export zones… / Discard / Cancel before the window closes or another project opens.

Extension points, so several perspectives share this state without editing this file:
- `with session.edit(description):` is the one way to change zones, settings or good-I0: it refreshes the memberships,
  notifies, records one undo step (repeats of the same description within 1.5 s merge), marks the session dirty and
  schedules the autosave. Pass holder=ALL_HOLDERS when an edit changes several holders;
- ui_value / set_ui_value: view memory saved with the session ('ui' block);
- check_queue, queue_for_check and check_queue_changed: the Easy 'Spectra to check' queue;
- add_flag_source(fn(scan) -> [Flag]) and flags_of(scan): scan flags beyond the sgm_report summary;
- add_settings_default(fn(scan, ScanSettings)): defaults applied to every new zone x scan settings;
- flush(), save_state(str) and read_only for the save indicator; undo_stack, undo(), redo();
- confirm_leave(action), release_lock() and the `warned` signal (a non-modal message: read-only, a foreign change);
- backup_now(reason), backups(), restore_backup(path) and import_zones(path, policy).
"""
from __future__ import annotations

import datetime as dt
import errno
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMainWindow

from aurora import __version__
from aurora.app.shortcuts import native_text
from aurora.app.undo import Snapshot, SnapshotCommand, dumps, make_stack
from aurora.core.io.config import (SESSION_SCHEMA, HolderZones, ScanSettings, Zone, ZonesFile, file_schema,
                                   json_default, read_sample_sheet)
from aurora.core.model import Holder, Project, ScanInfo
from aurora.core.names import split_stack_id
from aurora.core.stack import TOTAL_LABEL, StackData
from aurora.core.survey import SurveyData
from aurora.core.spectra import good_i0_curve
from aurora.core.zones import export_json, grid_pitch, members

log = logging.getLogger("aurora.session")

ALL_HOLDERS = "*"                       # edit(holder=ALL_HOLDERS): the edit may change every holder (import, restore)
SNAPSHOT_EVERY_S = 600                  # at most one automatic snapshot per 10 min of saving
SNAPSHOTS_KEPT = 30                     # snapshots kept in .aurora/backups (the oldest are deleted)
SAVE_DELAY_MS = 800                     # autosave this long after the last change
BACKUP_DIR = "backups"
NEWER_SCHEMA_TEXT = "written by a newer AURORA: edits here are not saved; export them to a file"
READ_ONLY_TEXT = "this session is read-only: edits here are not saved; export them to a file"
LOCKED_TEXT = ("this project is open in another AURORA ({who}): this window is read-only, edits here are not saved; "
               "close the other one, then Reload")
UNWRITABLE_TEXT = "the session cannot be saved here ({why}): edits here are not saved; export them to a file"
LOST_TEXT = ("another AURORA took this project over ({who}): edits here are no longer saved; export them to a file, "
             "or Reload to read its session")
FOREIGN_TEXT = ("session.json was changed by another program since this window read it: that version was kept as "
                "{name}, and the zones of this window were saved over it")
# edits that get a snapshot of the session first (large changes made in one click)
SNAPSHOT_BEFORE = re.compile(r"^(propose|apply|restore)\b|\brules?\b", re.IGNORECASE)
_SNAP_NAME = re.compile(r"^session-(\d{8}-\d{6})(?:_(\d+))?\.json$")     # '_2' sorts after '.json'
_STAMP_FMT = "%Y%m%d-%H%M%S"
IMPORT_POLICIES = ("merge", "replace", "skip")


@dataclass(frozen=True)
class CheckItem:
    """One row of the Easy 'Spectra to check' queue: a zone of a holder on one stack."""
    holder: str
    zone_id: int
    sid: str


@dataclass
class BackupInfo:
    """One file File > Restore zones from a backup… offers."""
    path: Path
    time: dt.datetime
    kind: str                           # 'snapshot' | 'previous save'
    reason: str = ""                    # 'autosave', 'import zones_all.json', 'propose 3 zones', ...
    holders: int = 0
    zones: int = 0
    ok: bool = True                     # the file parses
    error: str = ""


@dataclass
class ImportResult:
    """What the last import_zones did (the status line says the same)."""
    imported: int = 0
    duplicates: int = 0
    holders_skipped: int = 0
    unmatched: int = 0                  # zones of holders this project does not have (kept under their own key)
    backup: Path | None = None
    cancelled: bool = False
    error: str = ""


@dataclass
class ImportRow:
    """A holder of an imported file that already has other zones in the session: the import dialog asks about it."""
    key: str
    holder: str
    existing: int
    incoming: int
    duplicates: int


@dataclass
class StitchedScan:
    """The ScanInfo stand-in of a stitched stack, for the settings defaults (no folder, no summary, no flags)."""
    name: str
    day: str
    edge: tuple[str, str] | None = None
    summary: dict = field(default_factory=dict)
    kind: str = "stack"
    status: str = "complete"
    time: str = "stitch"
    label: str = ""
    path: Path | None = None
    h5: Path | None = None
    md: dict = field(default_factory=dict)
    energy_dirs: list = field(default_factory=list)
    n_points: int = 0
    holder: str = ""

    @property
    def edge_text(self) -> str:
        from aurora.core.names import pretty_edge
        return pretty_edge(self.edge)


@dataclass
class _EditState:
    """The edit in progress: the holders it covers with their JSON before it, where the user was, the scans the
    current zone was attached to."""
    all_: bool
    place: tuple
    attached: frozenset | None
    before: Snapshot


def _file_stamp(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def _os_text(err: Exception) -> str:
    """A failure in words: 'Access is denied: C:\\…\\session.json', never the repr of the exception (which doubles
    every backslash of a Windows path)."""
    reason = getattr(err, "strerror", None)
    if not reason:
        return str(err) or type(err).__name__
    name = getattr(err, "filename2", None) or getattr(err, "filename", None)     # a rename names its target second
    return f"{reason}: {name}" if name else str(reason)


def _content_digest(d: dict) -> bytes:
    """A digest of a session's content except its time stamp and header (equal content, equal digest)."""
    d = dict(d)
    for k in ("exported",) + ZonesFile.HEADER_KEYS:
        d.pop(k, None)
    return hashlib.blake2b(dumps(d).encode("utf-8"), digest_size=20).digest()


def _local_naive(text: str) -> dt.datetime | None:
    """An ISO time read from a file as a naive local time (an aware one is converted), None when it does not parse."""
    try:
        t = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t.astimezone().replace(tzinfo=None) if t.tzinfo is not None else t


def _geometry(z: Zone) -> tuple:
    """What makes two zones the same area (import duplicates): rectangles, erase rectangles, painted and erased
    squares and polygons, rounded to 1 nm."""
    def r(rows):
        return tuple(tuple(round(float(v), 6) for v in row) for row in rows)
    return (r(z.rects), r(z.erects), r(z.paint), r(z.erase), tuple(r(p) for p in z.polys))


class Session(QObject):
    project_changed = Signal(object)        # Project
    zones_changed = Signal()                # any zone edit
    holder_changed = Signal(str)            # holder key
    zone_selected = Signal(object)          # Zone | None
    stack_selected = Signal(object)         # StackData | None
    status = Signal(str)
    check_queue_changed = Signal()          # items added to / removed from check_queue
    save_state = Signal(str)                # '' (no session file) | 'unsaved' | 'saved HH:MM' | 'not saved' | 'read-only'
    recovered = Signal(str)                 # a damaged session.json was set aside; the text says what was restored
    reloaded = Signal(str)                  # Reload of the same project kept the place; the stack id kept ('' = none)
    warned = Signal(str, str)               # (title, text): opened read-only, the lock taken over, a foreign change

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.project: Project | None = None
        self.zones = ZonesFile()
        self.holder_keys: dict[str, tuple[str, Holder]] = {}        # key -> (day, Holder)
        self.current_holder: str | None = None
        self.current_zone: int | None = None
        self.current_stack: str | None = None
        self._stacks: dict[str, StackData] = {}
        self._surveys: dict[str, SurveyData] = {}
        self.sheet_rows: list = []
        self.check_queue: list[CheckItem] = []
        self.folder_read_only = False           # the project folder refused the lock / session file (a banner says so)
        self.read_only = False                  # True: edits stay in memory, save() refuses (a newer session schema,
        #                                         the project open in another AURORA, a folder that cannot be written)
        self.read_only_reason = ""              # why, in words ('' when the session saves)
        self.last_import = ImportResult()
        self._flag_sources: list = []
        self._settings_defaults: list = []
        self._edit_depth = 0
        self._edit_info: tuple[str, str | None] | None = None      # (description, holder) of the edit in progress
        self._edit_state: _EditState | None = None
        self._touch_pending = False
        # attachment from coordinates: sid -> (x, y, pitch, n_pix), from the .h5 at open, from the stack once loaded
        self._coords: dict[str, tuple[np.ndarray, np.ndarray, tuple[float, float], int]] = {}
        self._stack_stamp: dict[str, tuple] = {}        # sid -> (folder mtime, energy folders) when it was loaded
        self._fresh: set[str] = set()                   # stacks loaded since the last touch()
        self._generation = 0                            # +1 at every project open: a stack read for the previous one
        self._cache_lock = threading.Lock()             # is never stored (stack() runs in a worker)
        # saving
        self._dirty = False
        self._user_edits = False                        # dirty through an edit of the user (not only a stack load)
        self._newer = False                             # session.json has a newer schema (read-only)
        self._save_error = ""                           # why the last save did not happen ('' after a good save)
        self._lock = None                               # aurora.core.io.lock.ProjectLock while this session may save
        self._lock_timer = QTimer(self)                 # refreshes the lock (a live holder is never taken over)
        from aurora.core.io.lock import LOCK_REFRESH_S
        self._lock_timer.setInterval(LOCK_REFRESH_S * 1000)
        self._lock_timer.timeout.connect(self._refresh_lock)
        self._known_sig: bytes | None = None            # content signature of what is on disk (or was at open)
        self._pending_path: Path | None = None          # the session file the pending autosave belongs to
        self._file_stamp: tuple | None = None           # (mtime_ns, size) of session.json after our last read / write
        self._file_schema: int | None = None            # schema of the file read at open (None: no file)
        self._file_bytes: bytes | None = None           # its bytes, for session.v<old>.bak.json before the first save
        self._last_snapshot: dt.datetime | None = None
        self.clock = dt.datetime.now                    # backup times and names (tests use a fake clock)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._autosave)
        # undo
        self.undo_stack = make_stack(self)
        self.undo_clock = time.monotonic                # merge window of repeated edits
        self._ui = None
        if isinstance(parent, QMainWindow):             # Edit > Undo / Redo / History, File > Restore, the indicator
            from aurora.app.session_ui import SessionUI
            self._ui = SessionUI(self, parent)

    # ------------------------------------------------------------------ project
    def set_project(self, project: Project) -> None:
        """Open a project. The pending autosave of the previous one is written first. Opening the same folder again
        (Reload) keeps the place (holder, zone, stack), the loaded stacks that did not change and the undo history."""
        self.flush()
        if self.project is not None and Path(self.project.root) == Path(project.root):
            self._reload(project)
        else:
            self._open(project)

    def _open(self, project: Project) -> None:
        self.release_lock()
        with self._cache_lock:
            self.project = project
            self._generation += 1
            for cache in (self._stacks, self._surveys, self._coords, self._stack_stamp, self._fresh):
                cache.clear()
        self.undo_stack.clear()
        self.read_only = self._newer = False
        self.read_only_reason = self._save_error = ""
        self.folder_read_only = False
        self._dirty = self._user_edits = False
        self._pending_path = None
        self._file_stamp = self._file_schema = self._file_bytes = None
        self._last_snapshot = self._newest_snapshot_time()
        self.zones = ZonesFile(project=self._project_id())
        locked = self._take_lock()                  # before reading: a recovery writes only when this session may
        notice = "; ".join(t for t in (self._read_session_file(), locked) if t)
        self.holder_keys = self._keys_for(project, self._file_preferences())
        self._read_sheet()
        self.current_holder = next(iter(self.holder_keys), None)
        self.current_zone = None
        self.current_stack = None
        self._clear_queue()
        self._build_coords()
        self._attach_all()
        self._known_sig = self._signature()
        self.project_changed.emit(project)
        if self.current_holder:
            self.holder_changed.emit(self.current_holder)
        self.save_state.emit(self._idle_state())
        if notice:
            self.status.emit(notice)
        if locked:
            self.warned.emit("Read-only session", locked)

    def _reload(self, project: Project) -> None:
        keep_holder, keep_zone, keep_stack = self.current_holder, self.current_zone, self.current_stack
        old_keys = dict(self.holder_keys)
        self.project = project
        self.holder_keys = self._keys_for(project, {**self._file_preferences(),
                                                    **{(day, h.name): k for k, (day, h) in old_keys.items()}})
        kept = dropped = 0
        for sid in list(self._stacks):
            if sid.startswith("stitched_"):
                continue
            scan = self.scan_of(sid)
            if scan is not None and scan.status == "complete" and self._stack_stamp.get(sid) == self._scan_stamp(scan):
                kept += 1
                continue
            self._stacks.pop(sid, None); self._stack_stamp.pop(sid, None)
            dropped += 1
        for key in list(self._surveys):
            old, new = old_keys.get(key), self.holder_keys.get(key)
            if not (old and new and old[1].survey and new[1].survey and old[1].survey.path == new[1].survey.path):
                self._surveys.pop(key, None)
        regained = ""
        if self._lock is None:                      # read-only for want of the lock: try again
            if self._take_lock():
                regained = self.read_only_reason
            else:
                self.read_only = self._newer
                self.read_only_reason = NEWER_SCHEMA_TEXT if self._newer else ""
                regained = "this window may save the session again"
        notice = "; ".join(t for t in (self._reread_if_changed(), regained) if t)
        self._read_sheet()
        self.current_holder = keep_holder if keep_holder in self.holder_keys else next(iter(self.holder_keys), None)
        same = self.current_holder == keep_holder
        self.current_zone = keep_zone if same and keep_zone is not None and self.zone(keep_zone) is not None else None
        self.current_stack = keep_stack if keep_stack in self._stacks and self.current_holder == keep_holder else None
        self._clear_queue()
        self._coords.clear(); self._fresh.clear()
        self._build_coords()
        self._attach_all()
        if self._known_sig is None or not self._dirty:
            self._known_sig = self._signature()
        log.info("reload of %s: %d loaded stacks kept, %d read again later", project.root, kept, dropped)
        zone, stack = self.current_zone, self.current_stack
        self.project_changed.emit(project)
        if self.current_holder:
            self.holder_changed.emit(self.current_holder)
        # the perspectives reset their views on holder_changed: put the place back
        self.current_zone = zone if zone is not None and self.zone(zone) is not None else None
        if stack is not None and stack in self._stacks:
            self.select_stack(stack)
        self.zone_selected.emit(self.zone() if self.current_zone is not None else None)
        self.reloaded.emit(stack or "")
        self.save_state.emit(self._idle_state())
        if notice:
            self.status.emit(notice)

    def _project_id(self) -> str:
        for s in self.project.scans if self.project else []:
            if s.md.get("project"):
                return s.md["project"]
        return self.project.root.name if self.project else ""

    def _read_sheet(self) -> None:
        self.sheet_rows = []
        for s in sorted(self.project.root.glob("*.csv")):
            try:
                self.sheet_rows = read_sample_sheet(s)
                if self.sheet_rows:
                    break
            except Exception:
                continue

    def _clear_queue(self) -> None:
        if self.check_queue:
            self.check_queue = []
            self.check_queue_changed.emit()

    @staticmethod
    def _keys_for(project: Project, prefer: dict[tuple[str, str], str]) -> dict[str, tuple[str, Holder]]:
        """Holder keys in project order: the holder name, '<name>_<day>' when a name repeats. A holder already known
        under another key (`prefer`: (day, name) -> key, from the session file or before a Reload) keeps that key,
        so its zones stay attached when a later day brings a holder of the same name."""
        pairs = [(day.date, h) for day in project.days for h in day.holders]
        seen: dict[str, int] = {}
        for _day, h in pairs:
            seen[h.name] = seen.get(h.name, 0) + 1
        chosen: dict[int, str] = {}
        used: set[str] = set()
        for i, (day, h) in enumerate(pairs):
            k = prefer.get((day, h.name))
            if k and k not in used:
                chosen[i] = k
                used.add(k)
        for i, (day, h) in enumerate(pairs):
            if i in chosen:
                continue
            k = h.name if seen[h.name] == 1 else f"{h.name}_{day}"
            n = 2
            base = k
            while k in used:
                k = f"{base}_{n}"
                n += 1
            chosen[i] = k
            used.add(k)
        return {chosen[i]: (day, h) for i, (day, h) in enumerate(pairs)}

    def _file_preferences(self) -> dict[tuple[str, str], str]:
        """(day, holder name) -> the session key of those zones. When two keys claim one holder (a session written by
        AURORA 0.4.0 after importing zones keyed by a bare holder name), the first keeps it: the zones drawn there
        stay on the holder, the others stay in the file under their key (logged)."""
        out: dict[tuple[str, str], str] = {}
        for key, hz in self.zones.holders.items():
            if not (hz.day or hz.holder):
                continue
            pair = (hz.day, hz.holder)
            if pair in out:
                log.warning("zones under %r and %r both belong to holder %s of %s: %r is shown, %r kept in the file",
                            out[pair], key, hz.holder, hz.day, out[pair], key)
                continue
            out[pair] = key
        return out

    @property
    def session_file(self) -> Path:
        return self.project.root / ".aurora" / "session.json"

    @property
    def backup_dir(self) -> Path:
        return self.project.root / ".aurora" / BACKUP_DIR

    @property
    def dirty(self) -> bool:
        """Changes not written to session.json yet."""
        return self._dirty

    # ------------------------------------------------------------------ one AURORA per project
    def _take_lock(self) -> str:
        """Take the project's lock (aurora.core.io.lock) and check that session.json can be written. '' when this
        session may save; otherwise the session is read-only and the text says why."""
        from aurora.core.io.lock import ProjectLock
        try:
            lock, other = ProjectLock.acquire(self.project.root, f"AURORA {__version__}")
            if lock is None:
                text = LOCKED_TEXT.format(who=other.describe())
            elif self.session_file.exists() and not os.access(self.session_file, os.W_OK):
                lock.release()
                raise PermissionError(errno.EACCES, "session.json is a read-only file", str(self.session_file))
            else:
                self._lock = lock
                self._lock_timer.start()
                return ""
        except OSError as err:              # a protected folder, a read-only share or file
            reason = getattr(err, "strerror", None) or str(err)
            text = UNWRITABLE_TEXT.format(why=reason)
            self.folder_read_only = True        # the beamtime folder itself cannot be written (the window's banner)
        log.warning("%s: %s", self.project.root, text)
        self.read_only = True
        self.read_only_reason = text
        return text

    def release_lock(self) -> None:
        """Let the project go (the window closes, another project opens): another AURORA may then edit it."""
        self._lock_timer.stop()
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def _check_lock(self) -> bool:
        """Before a save: the lock is still this session's. A lock file gone missing while session.json did not change
        is created again; a lock another AURORA holds now makes this session read-only, and says so."""
        lock = self._lock
        if lock is None:
            text = self._take_lock()
            if text:
                self.status.emit(text)
            return not text
        if lock.still_ours():
            return True
        other = lock.holder()
        if other is None and _file_stamp(self.session_file) == self._file_stamp and lock.reclaim():
            return True
        self._lock_timer.stop()
        lock.release()
        self._lock = None
        text = LOST_TEXT.format(who=other.describe() if other is not None else "its lock file was removed")
        log.warning("%s: %s", self.session_file, text)
        self.read_only = True
        self.read_only_reason = text
        self.status.emit(text)
        self.warned.emit("Project open elsewhere", text)
        return False

    def _refresh_lock(self) -> None:
        if self._lock is not None and not self._lock.refresh():
            if self._check_lock():
                return
            self.save_state.emit("read-only")

    def confirm_leave(self, action: str = "close the window") -> bool:
        """Before the window closes or another project opens: write a pending change, and when edits remain that could
        not be saved (a failed save, a read-only session), ask in the window: Export zones… / Discard / Cancel.
        True when it is fine to go on; without a window (tests, the command line) it always is."""
        if self.flush() or self._ui is None or not self._user_edits:
            return True
        return self._ui.ask_unsaved(action, self._save_error or self.read_only_reason or READ_ONLY_TEXT)

    def discard_unsaved(self) -> None:
        """The user let the edits that could not be saved go (or exported them): nothing is pending any more."""
        self._save_timer.stop()
        self._dirty = self._user_edits = False
        self._pending_path = None

    # ------------------------------------------------------------------ reading session.json
    def _read_session_file(self) -> str:
        """Read session.json into self.zones. Returns a status text for the user ('' when all is normal)."""
        sf = self.session_file
        if not sf.exists():
            return ""
        try:
            data = sf.read_bytes()
        except OSError as err:
            self.read_only = True                   # never overwrite a file that could not even be read
            log.warning("%s could not be read (%s): the session is read-only", sf, err)
            self.read_only_reason = (f"session.json could not be read ({_os_text(err)}): edits here are not saved; "
                                     f"export them to a file")
            return self.read_only_reason
        try:
            d = json.loads(data.decode("utf-8-sig"))         # a BOM (Notepad, PowerShell 5) is not damage
            if not isinstance(d, dict):
                raise ValueError("a JSON object is expected")
        except (UnicodeDecodeError, ValueError) as err:
            return self._recover(sf, err)
        schema = file_schema(d)
        if schema > SESSION_SCHEMA:
            self.read_only = self._newer = True
            self.read_only_reason = NEWER_SCHEMA_TEXT
            try:
                self.zones = ZonesFile.from_json(d)
            except Exception as err:            # a newer layout this version cannot even read: show nothing, keep it
                log.warning("%s: schema %s, not readable here (%s)", sf, d.get("schema"), err)
            log.info("%s: schema %s is newer than %d: read-only", sf, d.get("schema"), SESSION_SCHEMA)
            self._file_stamp = _file_stamp(sf)
            return NEWER_SCHEMA_TEXT
        try:
            zf = ZonesFile.from_json(self._migrate(d, schema))
        except Exception as err:
            return self._recover(sf, err)
        self.zones = zf
        self._file_schema = schema
        self._file_bytes = data if schema < SESSION_SCHEMA else None
        self._file_stamp = _file_stamp(sf)
        log.info("session restored from %s (schema %d)", sf, schema)
        return ""

    @staticmethod
    def _migrate(d: dict, schema: int) -> dict:
        """Bring an older session in memory up to SESSION_SCHEMA. 1 -> 2: no key changes (2 adds the header, written
        by the next save, and keeps every unknown key)."""
        return d

    def _recover(self, sf: Path, err: Exception) -> str:
        """session.json does not parse: keep it as session.corrupt-<time>.json, restore from the .bak or the newest
        snapshot, and write the restored session at once (so a valid session.json exists again)."""
        log.warning("%s is damaged (%s)", sf, err)
        locked = self.read_only                 # another AURORA holds the project (or it cannot be written): no write
        kept = None if locked else self._set_aside(sf)
        restored = None
        for cand in self._recovery_candidates():
            zf = self._load_backup(cand.path)
            if zf is not None:
                restored = (cand, zf)
                break
        if locked:
            where = "the damaged file was left as it is (this session is read-only)"
        elif kept is None:
            self.read_only = True
            self.read_only_reason = "session.json is damaged and could not be moved aside: edits here are not saved"
            where = "the damaged file could not be moved aside, so this session is read-only"
        else:
            where = f"the damaged file was kept as {kept.name}"
        if restored is None:
            msg = f"session.json was damaged and no backup could be read; the session starts empty; {where}"
        else:
            cand, zf = restored
            self.zones = zf
            n = sum(len(h.zones) for h in zf.holders.values())
            from aurora.app.vocab import plural
            msg = (f"session.json was damaged; {plural(n, 'zone')} restored from the backup of "
                   f"{cand.time:%Y-%m-%d %H:%M}; {where}")
            if not self.read_only:
                self._dirty = True
                self.save()
        log.warning(msg)
        self.recovered.emit(msg)
        return msg

    def _set_aside(self, sf: Path) -> Path | None:
        """Move a damaged session.json to session.corrupt-<time>.json (never overwritten); None when impossible."""
        from aurora.core.io.safety import assert_writable
        when = self.clock()
        target = sf.with_name(f"session.corrupt-{when:{_STAMP_FMT}}.json")
        n = 2
        while target.exists():
            target = sf.with_name(f"session.corrupt-{when:{_STAMP_FMT}}-{n}.json")
            n += 1
        try:
            assert_writable(target)
            os.replace(sf, target)
            return target
        except OSError as err:
            log.warning("could not rename %s (%s); copying it", sf, err)
        try:
            assert_writable(target)
            target.write_bytes(sf.read_bytes())
            return target
        except OSError as err:
            log.warning("could not keep a copy of %s (%s)", sf, err)
            return None

    def _recovery_candidates(self) -> list[BackupInfo]:
        """The .bak and the snapshots, the newest state first. Each is timed by the state it holds: a snapshot by its
        name, the .bak by the 'exported' stamp inside it (it holds the save before the last one, so its file time,
        that of the last save, would put an older state before a newer snapshot)."""
        bak = self.session_file.with_name("session.json.bak")
        out = []
        if bak.exists():
            try:
                when = None
                try:
                    d = json.loads(bak.read_bytes().decode("utf-8-sig"))
                    when = _local_naive(d.get("exported", "")) if isinstance(d, dict) else None
                except (UnicodeDecodeError, ValueError):
                    pass
                out.append(BackupInfo(bak, when or dt.datetime.fromtimestamp(bak.stat().st_mtime), "previous save"))
            except OSError:
                pass
        out += self._snapshot_files()
        return sorted(out, key=lambda b: b.time, reverse=True)

    def _load_backup(self, path: Path) -> ZonesFile | None:
        """A backup or snapshot as a ZonesFile of this schema (its 'backup' note removed), None when unreadable."""
        try:
            d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if not isinstance(d, dict) or file_schema(d) > SESSION_SCHEMA:
                return None
            d.pop("backup", None)
            return ZonesFile.from_json(self._migrate(d, file_schema(d)))
        except Exception as err:
            log.info("backup %s unreadable (%s)", path, err)
            return None

    def _reread_if_changed(self) -> str:
        """Reload: session.json changed on disk since this session last read or wrote it (another program, a sync
        tool). A readable file is taken; a damaged one is kept aside and the zones in memory are written again."""
        sf = self.session_file
        stamp = _file_stamp(sf)
        if stamp is None or stamp == self._file_stamp:
            return ""
        try:
            d = json.loads(sf.read_bytes().decode("utf-8-sig"))
            if not isinstance(d, dict):
                raise ValueError("a JSON object is expected")
            schema = file_schema(d)
            zf = ZonesFile.from_json(self._migrate(d, schema) if schema <= SESSION_SCHEMA else d)
        except Exception as err:
            log.warning("%s changed on disk and is damaged (%s)", sf, err)
            if self.read_only:                  # another AURORA holds the project: its file is not ours to move
                return "session.json on disk is damaged; this window is read-only and left it as it is"
            kept = self._set_aside(sf)
            if kept is None:
                self.read_only = True
                return "session.json on disk is damaged and could not be moved aside: edits here are not saved"
            self._dirty = True
            self.save()
            msg = f"session.json on disk was damaged; the zones in memory were saved again; it was kept as {kept.name}"
            self.recovered.emit(msg)
            return msg
        kept = None
        if self._dirty and self._user_edits and _content_digest(zf.to_json()) != self._signature():
            # edits of this window that were never saved (read-only meanwhile): kept before the file replaces them
            kept = self.backup_now("unsaved edits of this window, replaced by session.json changed on disk")
            if kept is None:                    # nowhere to keep them (read-only): they stay on screen
                log.warning("%s changed on disk; the unsaved edits of this window are kept on screen", sf)
                return ("session.json changed on disk, but this window has edits that are not saved: they stay on "
                        "screen; export them (closing the window offers it), then Reload")
        self.zones = zf
        self.undo_stack.clear()                 # the history belongs to the state that was replaced
        self.read_only = self._newer = schema > SESSION_SCHEMA
        if self.read_only:
            self.read_only_reason = NEWER_SCHEMA_TEXT
        self._file_schema = schema
        self._file_bytes = None
        self._file_stamp = stamp
        self._dirty = self._user_edits = False
        msg = NEWER_SCHEMA_TEXT if self.read_only else "session.json changed on disk: read again"
        return msg + (f"; the unsaved edits of this window were kept as {kept.name}" if kept is not None else "")

    def _idle_state(self) -> str:
        if self.read_only:
            return "read-only"
        if self._dirty:
            return "unsaved"
        stamp = self._file_stamp or _file_stamp(self.session_file)
        if stamp is None:
            return ""
        when = dt.datetime.fromtimestamp(stamp[0] / 1e9)
        today = (self.clock() if callable(getattr(self, "clock", None)) else dt.datetime.now()).date()
        return f"saved {when:%H:%M}" if when.date() == today else f"saved {when:%Y-%m-%d %H:%M}"

    # ------------------------------------------------------------------ holders / scans
    def holder(self, key: str | None = None) -> Holder | None:
        key = key or self.current_holder
        return self.holder_keys[key][1] if key in self.holder_keys else None

    def holder_day(self, key: str | None = None) -> str:
        key = key or self.current_holder
        return self.holder_keys[key][0] if key in self.holder_keys else ""

    def stacks_of(self, key: str | None = None) -> list[ScanInfo]:
        h = self.holder(key)
        return list(h.stacks) if h else []

    def stack_id(self, scan: ScanInfo) -> str:
        return f"{scan.day}_{scan.name}"

    def scan_of(self, sid: str) -> ScanInfo | None:
        return self.project.find(split_stack_id(sid)[1]) if self.project else None

    def holder_of_stack(self, sid: str) -> str | None:
        """The holder key whose complete stacks include `sid`, None when no holder has it."""
        for key, (_day, h) in self.holder_keys.items():
            if any(self.stack_id(s) == sid for s in h.stacks):
                return key
        return None

    def set_holder(self, key: str) -> None:
        if key not in self.holder_keys or key == self.current_holder:
            return
        self.current_holder = key
        self.current_zone = None
        self.current_stack = None
        self.holder_changed.emit(key)
        self.zone_selected.emit(None)
        self.stack_selected.emit(None)

    def survey(self, key: str | None = None) -> SurveyData | None:
        key = key or self.current_holder
        h = self.holder(key)
        if h is None or h.survey is None:
            return None
        if key not in self._surveys:
            self._surveys[key] = SurveyData.load(h.survey, self.project.roi_bins)
        return self._surveys[key]

    def stack(self, sid: str, progress=None) -> StackData:
        """The stack, loaded on first use (runs in the loading worker). Its own coordinates replace those read from
        the .h5 (an sgm_report cache may hold fewer pixels); the next touch() recomputes the members from them.
        A stack is only stored in the session of the project it was read from: when another project was opened while
        it was read, it is read again from the open project (the job's callbacks get that project's data), and a job
        for a scan the open project does not have ends as cancelled."""
        with self._cache_lock:
            sd, project, gen = self._stacks.get(sid), self.project, self._generation
        if sd is not None:
            return sd
        scan = project.find(split_stack_id(sid)[1]) if project is not None else None
        if scan is None:
            raise KeyError(sid)
        sd = StackData.load(project, scan, progress=progress)
        stamp = self._scan_stamp(scan)
        with self._cache_lock:
            if self._generation == gen:
                self._stack_stamp[sid] = stamp
                self._coords[sid] = (sd.x, sd.y, sd.pitch, sd.n_pix)
                self._stacks[sid] = sd
                self._fresh.add(sid)
                return sd
        log.info("%s was read for %s, which is no longer open: not kept", sid, project.root)
        if self.scan_of(sid) is None:
            from aurora.core.cancel import Cancelled
            raise Cancelled(f"{sid}: the project changed while it was read")
        return self.stack(sid, progress)

    @staticmethod
    def _scan_stamp(scan) -> tuple:
        """(folder mtime, energy folders) of a stack: a Reload keeps its loaded data while both are unchanged."""
        try:
            mtime = os.stat(scan.path).st_mtime_ns
        except (OSError, TypeError):
            mtime = None
        return mtime, len(getattr(scan, "energy_dirs", []) or [])

    def stack_loaded(self, sid: str) -> StackData | None:
        return self._stacks.get(sid)

    def select_stack(self, sid: str | None) -> None:
        self.current_stack = sid
        self.stack_selected.emit(self._stacks.get(sid) if sid else None)

    # ------------------------------------------------------------------ coordinates and attachment
    def _build_coords(self) -> None:
        """Stage coordinates of every complete stack of every holder, from its .h5 (no binary is read); a loaded
        stack gives its own."""
        from aurora.core.io.h5 import find_h5, xy
        for _key, (_day, h) in self.holder_keys.items():
            for s in h.stacks:
                sid = self.stack_id(s)
                sd = self._stacks.get(sid)
                if sd is not None:
                    self._coords[sid] = (sd.x, sd.y, sd.pitch, sd.n_pix)
                    continue
                try:
                    h5 = s.h5 or find_h5(s.path)
                    if h5 is None:
                        continue
                    x, y = xy(h5)
                except Exception as err:                # a truncated or still-written .h5: attached once loaded
                    log.info("%s: no coordinates (%s)", s.name, err)
                    continue
                self._coords[sid] = (x, y, grid_pitch(x, y), int(len(x)))

    def coordinates(self, sid: str):
        """(x, y, pitch, n_pix) of a stack: the loaded stack's own, else those read from its .h5, else None."""
        sd = self._stacks.get(sid)
        if sd is not None:
            return sd.x, sd.y, sd.pitch, sd.n_pix
        return self._coords.get(sid)

    def _attach_all(self) -> None:
        self._attach_holders(list(self.zones.holders))

    def _attach_holders(self, keys) -> None:
        """attach_scans for every zone of these holders. A zone that cannot be placed (a geometry this version cannot
        read) is logged and left as it is, so it never blocks the others, an edit's undo step or a project open."""
        for key in keys:
            hz = self.zones.holders.get(key) if key else None
            for z in (hz.zones if hz else []):
                try:
                    self.attach_scans(z, key)
                except Exception as err:            # noqa: BLE001
                    log.warning("zone %s of %s not attached to its scans (%s)", z.id, key, err)

    def _holder_of_zone(self, z: Zone) -> str | None:
        for key, hz in self.zones.holders.items():
            if any(zz is z for zz in hz.zones):
                return key
        return None

    def attach_scans(self, z: Zone, holder: str | None = None) -> None:
        """Recompute the zone's member pixels for every complete stack of its holder, loaded or not, from the stage
        coordinates, and create default settings for every stack it covers (stitched stacks of the holder too)."""
        key = holder or self._holder_of_zone(z) or self.current_holder
        pairs: list[tuple[str, object]] = [(self.stack_id(s), s) for s in self.stacks_of(key)]
        for sid, sd in list(self._stacks.items()):
            if sid.startswith("stitched_"):
                owner = self.holder_of_stack(sid[len("stitched_"):])
                if owner == key or (owner is None and key == self.current_holder):
                    pairs.append((sid, None))
        for sid, scan in pairs:
            sd = self._stacks.get(sid)
            c = (sd.x, sd.y, sd.pitch, sd.n_pix) if sd is not None else self._coords.get(sid)
            if c is None:
                continue
            x, y, pitch, _n = c
            pix = members(z, x, y, pitch)
            if pix.size:
                z.members[sid] = pix.tolist()
                st = z.settings.get(sid)
                if st is None:
                    st = self.default_settings(scan if scan is not None else self._stitched_scan(sd), sd)
                    z.settings[sid] = st
                elif sd is not None and st.line not in sd.labels and sd.labels and "line" not in st.pinned:
                    log.info("zone %s on %s: line %r is not in the stack (%s); %r used", z.id, sid, st.line,
                             ", ".join(sd.labels), sd.main)
                    st.line = sd.main
                st.n = int(pix.size)
            else:
                z.members.pop(sid, None)
                z.settings.pop(sid, None)

    @staticmethod
    def _stitched_scan(sd: StackData) -> StitchedScan:
        return StitchedScan(name=sd.name, day=sd.day, edge=sd.edge, label=sd.name, n_points=sd.n_pix)

    def _labels_for(self, scan) -> list[str]:
        """The ROI labels a stack will have when it is loaded: the project's fixed bins plus the total."""
        bins = self.project.roi_bins if self.project else None
        labels = [lab for lab in bins.labels if bins.has(lab)] if bins is not None else []
        return labels + [TOTAL_LABEL]

    def default_settings(self, scan, sd: StackData | None = None, labels: list[str] | None = None) -> ScanSettings:
        """New zone x scan settings, for a loaded stack or one known by its coordinates only: the Settings defaults
        and the Easy rules (mode.easy_scan_settings: saturated SDDs off, the good-I0 mean for an invalid I0, the main
        line), then every registered settings-default provider in order (per-edge memory, rules)."""
        from aurora.app.mode import easy_scan_settings
        if labels is None:
            labels = list(sd.labels) if sd is not None else self._labels_for(scan)
        st = easy_scan_settings(scan, self.flags_of(scan), labels)
        if sd is not None and sd.labels and st.line not in sd.labels:
            st.line = sd.main
        for fn in list(self._settings_defaults):
            try:
                fn(scan, st)
            except RuntimeError:                # a provider whose Qt owner is gone
                continue
            except Exception:
                log.exception("settings-default provider %r failed on %s", fn, getattr(scan, "name", scan))
        return st

    # ------------------------------------------------------------------ editing
    @contextmanager
    def edit(self, description: str, holder: str | None = None):
        """The one way to change zones, settings or good-I0:

            with session.edit("add rectangle to zone 3"):
                z.rects.append(rect)

        `holder` names the holder whose zones change (the current holder when None; ALL_HOLDERS for an edit that may
        change several). Edits nest: an inner edit is part of the outer one, and touch() calls inside an edit are
        deferred to its end. On exit (also after an exception) the memberships are refreshed, listeners notified,
        and, when something changed, one undo step is recorded (merged with the previous one when it has the same
        description and came less than 1.5 s before), the session is marked dirty and an autosave scheduled."""
        key = holder or self.current_holder
        outer = self._edit_depth == 0
        if outer:
            self._edit_info = (str(description), key)
            self._touch_pending = False
            self._edit_state = _EditState(all_=key == ALL_HOLDERS, place=(self.current_holder, self.current_zone),
                                          attached=self._attached_set(), before=Snapshot(holders={}, good_i0=""))
            self._widen_edit(key)
            self._edit_state.before["good_i0"] = dumps(self.zones.good_i0)
            self._edit_state.before["rules"] = dumps(self.zones.rules)
            if SNAPSHOT_BEFORE.search(str(description)) and self.project is not None:
                self.backup_now(str(description))
        elif self._edit_state is not None:
            self._widen_edit(key)               # an inner edit of other holders (an import inside a Survey edit)
        self._edit_depth += 1
        try:
            yield self
        finally:
            self._edit_depth -= 1
            if outer:
                state, self._edit_state = self._edit_state, None
                self._edit_info = None
                self._touch_pending = False
                try:
                    self._finish_edit(str(description), state)
                except Exception:
                    log.exception("finishing the edit %r failed", description)
                    if self.project is not None:
                        self._mark_dirty()          # the change is in memory: it is still saved

    @property
    def editing(self) -> str | None:
        """The description of the edit in progress, None outside an edit."""
        return self._edit_info[0] if self._edit_info else None

    def _widen_edit(self, key: str | None) -> None:
        """Add the holders `key` names (and the current one) to the edit in progress, with their state as it is now."""
        st = self._edit_state
        if key == ALL_HOLDERS:
            st.all_ = True
            keys = list(self.zones.holders) + list(self.holder_keys)
        else:
            keys = [key, self.current_holder]
        hz = self.zones.holders
        for k in dict.fromkeys(keys):
            if k and k not in st.before["holders"]:
                st.before["holders"][k] = dumps(hz[k].to_json()) if k in hz else None

    def _snapshot(self, keys) -> Snapshot:
        hz = self.zones.holders
        return Snapshot(holders={k: (dumps(hz[k].to_json()) if k in hz else None) for k in keys},
                        good_i0=dumps(self.zones.good_i0), rules=dumps(self.zones.rules))

    def _attached_set(self) -> frozenset | None:
        z = self.zone() if self.current_zone is not None else None
        return frozenset(z.settings) if z is not None else None

    def _finish_edit(self, description: str, state: "_EditState") -> None:
        keys = list(state.before["holders"])
        if state.all_:
            keys = list(dict.fromkeys(keys + list(self.zones.holders)))
        self._attach_holders(keys)
        after = self._snapshot(keys)
        before = Snapshot(holders={k: state.before["holders"].get(k) for k in keys}, good_i0=state.before["good_i0"],
                          rules=state.before["rules"])
        self.zones_changed.emit()
        if after == before:
            # nothing changed in the holders the edit covers; a change elsewhere (a holder the edit did not name, the
            # notes) is still saved, though not undoable
            if self.project is not None and self._signature() != self._known_sig:
                self._mark_dirty()
            return
        cmd = SnapshotCommand(self, description, before, after, state.place, (self.current_holder, self.current_zone),
                              self.undo_clock())
        self.undo_stack.push(cmd)
        self._mark_dirty()
        z = self.zone() if self.current_zone is not None else None
        if z is not None and frozenset(z.settings) != state.attached:
            self.status.emit(self.attach_text(z))

    def attach_text(self, z: Zone) -> str:
        """'zone 3 attached to 2 scans (1 not loaded)'."""
        from aurora.app.vocab import plural
        n = len(z.settings)
        off = sum(1 for sid in z.settings if sid not in self._stacks)
        return f"zone {z.id} attached to {plural(n, 'scan')}" + (f" ({off} not loaded)" if off else "")

    def _apply_snapshot(self, snap: Snapshot, place: tuple, message: str) -> None:
        """Undo / redo: put a snapshot back, then refresh, notify and autosave like an edit (without a new step)."""
        for k, text in snap["holders"].items():
            if text is None:
                self.zones.holders.pop(k, None)
            else:
                self.zones.holders[k] = HolderZones.from_json(json.loads(text), k)
        self.zones.good_i0 = {k: bool(v) for k, v in json.loads(snap["good_i0"]).items()}
        if snap.get("rules") is not None:           # the settings rules (Save rules, Remember the XRF window)
            self.zones.rules = json.loads(snap["rules"])
        self._attach_holders(list(snap["holders"]))
        holder, zid = place
        if holder in self.holder_keys and holder != self.current_holder:
            self.set_holder(holder)
        self.current_zone = zid if zid is not None and self.zone(zid) is not None else (
            self.current_zone if self.zone(self.current_zone) is not None else None)
        self.zones_changed.emit()
        self.zone_selected.emit(self.zone() if self.current_zone is not None else None)
        self._mark_dirty()
        self.status.emit(message)

    def undo(self) -> bool:
        """Edit > Undo (Ctrl+Z): the last edit is reverted."""
        if not self.undo_stack.canUndo():
            self.status.emit("nothing to undo")
            return False
        self.undo_stack.undo()
        return True

    def redo(self) -> bool:
        """Edit > Redo (Ctrl+Y, Ctrl+Shift+Z)."""
        if not self.undo_stack.canRedo():
            self.status.emit("nothing to redo")
            return False
        self.undo_stack.redo()
        return True

    # ------------------------------------------------------------------ zones
    def holder_zones(self, key: str | None = None, create: bool = True) -> HolderZones | None:
        key = key or self.current_holder
        if key is None:
            return None
        hz = self.zones.holders.get(key)
        if hz is None and create:
            h = self.holder(key)
            hz = HolderZones(holder=h.name if h else key, day=self.holder_day(key),
                             survey=h.survey.name if h and h.survey else "")
            self.zones.holders[key] = hz
        return hz

    def zone(self, zid: int | None = None) -> Zone | None:
        zid = self.current_zone if zid is None else zid
        hz = self.holder_zones(create=False)
        if hz is None or zid is None:
            return None
        return next((z for z in hz.zones if z.id == zid), None)

    def _no_holder(self) -> bool:
        """No project (or a project without a holder): say so in the status line instead of failing."""
        if self.project is None or self.current_holder is None:
            self.status.emit("open a project first")
            return True
        return False

    def new_zone(self, label: str = "") -> Zone | None:
        """A new empty zone of the current holder, selected; None (and a status line) without a project or holder."""
        if self._no_holder():
            return None
        hz = self.holder_zones()
        zid = max((z.id for z in hz.zones), default=0) + 1
        with self.edit(f"new zone {zid}"):
            z = Zone(id=zid, label=label or f"zone {zid}")
            hz.zones.append(z)
            self.current_zone = zid
        self.zone_selected.emit(z)
        return z

    def delete_zone(self, zid: int) -> None:
        """Delete a zone of the current holder. No confirmation: Ctrl+Z brings it back."""
        if self._no_holder():
            return None
        hz = self.holder_zones(create=False)
        if hz is None or not any(z.id == zid for z in hz.zones):
            return
        with self.edit(f"delete zone {zid}"):
            hz.zones = [z for z in hz.zones if z.id != zid]
            if self.current_zone == zid:
                self.current_zone = None
                self.zone_selected.emit(None)
        self.status.emit(native_text(f"zone {zid} deleted · Ctrl+Z to undo"))

    def select_zone(self, zid: int | None) -> None:
        if zid is not None and self._no_holder():
            return None
        self.current_zone = zid
        self.zone_selected.emit(self.zone(zid) if zid is not None else None)

    def touch(self) -> None:
        """Stacks were loaded (or zones changed outside an edit): refresh the memberships and notify. Inside an edit:
        at the edit's end. Never an undo step; the session is saved only when its content really changed."""
        if self._edit_depth:
            self._touch_pending = True
            return
        fresh = set(list(self._fresh))
        self._fresh.difference_update(fresh)
        keys = {self.current_holder} | {self.holder_of_stack(sid) for sid in fresh}
        self._attach_holders([k for k in keys if k])
        self.zones_changed.emit()
        if self.project is not None and self._signature() != self._known_sig:
            self._mark_dirty(user=False)

    def _signature(self) -> bytes:
        """A digest of everything session.json holds except the time stamp and the header."""
        try:
            return _content_digest(self.zones.to_json())
        except (TypeError, ValueError) as err:      # a ui value that is not JSON: the save will say so
            log.warning("session content not serialisable (%s)", err)
            return b""

    def _mark_dirty(self, user: bool = True) -> None:
        """Something to save. user=False: only memberships recomputed after a stack load (touch), which the next open
        recomputes anyway, so they alone never make closing ask about unsaved edits."""
        self._dirty = True
        self._user_edits = self._user_edits or user
        self._schedule_save()

    def _schedule_save(self) -> None:
        if not self._save_timer.isActive():
            self._pending_path = self.session_file if self.project is not None else None
        self._save_timer.start()
        self.save_state.emit("read-only" if self.read_only else "unsaved")

    # ------------------------------------------------------------------ extension points
    def ui_value(self, key: str, default=None):
        """View memory saved with the session (the 'ui' block of session.json), e.g. ignored report findings."""
        return self.zones.ui.get(key, default)

    def set_ui_value(self, key: str, value) -> None:
        """Remember a JSON-serialisable view value with the session; nothing is written when it did not change."""
        if key in self.zones.ui and self.zones.ui[key] == value:
            return
        self.zones.ui[key] = value
        if self.project is not None:
            self._mark_dirty()

    def queue_for_check(self, items) -> int:
        """Add zone x stack items (CheckItem) to the Easy check queue; items already queued are skipped. Returns the
        number added."""
        added = 0
        for it in items:
            if not isinstance(it, CheckItem):
                it = CheckItem(*it)
            if it not in self.check_queue:
                self.check_queue.append(it)
                added += 1
        if added:
            self.check_queue_changed.emit()
        return added

    def unqueue_for_check(self, items) -> int:
        """Remove items from the check queue. Returns the number removed."""
        gone = {it if isinstance(it, CheckItem) else CheckItem(*it) for it in items}
        before = len(self.check_queue)
        self.check_queue = [it for it in self.check_queue if it not in gone]
        n = before - len(self.check_queue)
        if n:
            self.check_queue_changed.emit()
        return n

    def add_flag_source(self, fn) -> None:
        """Register fn(scan) -> [Flag]: flags merged into flags_of, for example AURORA's own Check verdicts."""
        if fn not in self._flag_sources:
            self._flag_sources.append(fn)

    def flags_of(self, scan) -> list:
        """Every flag of a scan: aurora.core.flags.merged_flags plus every registered flag source, without repeats."""
        from aurora.core.flags import merged_flags
        out = list(merged_flags(self.project, scan))
        for fn in list(self._flag_sources):
            try:
                more = fn(scan) or []
            except RuntimeError:
                continue
            except Exception:
                log.exception("flag source %r failed on %s", fn, getattr(scan, "name", scan))
                continue
            for f in more:
                if f not in out:
                    out.append(f)
        return out

    def add_settings_default(self, fn) -> None:
        """Register fn(scan, ScanSettings) -> None, applied in order to every new zone x scan settings."""
        if fn not in self._settings_defaults:
            self._settings_defaults.append(fn)

    # ------------------------------------------------------------------ good I0
    def good_curve_for(self, E: np.ndarray) -> np.ndarray | None:
        curves = []
        for sid, good in self.zones.good_i0.items():
            if not good:
                continue
            sd = self._stacks.get(sid)
            if sd is None:
                try:
                    sd = self.stack(sid)
                except Exception:
                    continue
            curves.append((sd.E, sd.i0_mean()))
        return good_i0_curve(E, curves) if curves else None

    # ------------------------------------------------------------------ persistence
    def flush(self) -> bool:
        """Write a pending change now (closing the window, opening another project, importing): the timer stops and,
        when the session is dirty, it is saved to the file of the project it belongs to. True when nothing unsaved
        remains (False: the save failed, or the session is read-only and has edits)."""
        self._save_timer.stop()
        if self._dirty and self.project is not None:
            self.save()
        self._pending_path = None
        return not (self._dirty and self.project is not None)

    def _autosave(self) -> None:
        if self._dirty:
            self.save()

    def save(self) -> bool:
        """Write session.json now (atomically), keeping the previous good file as session.json.bak, the original of an
        older schema as session.v<old>.bak.json (once), and a snapshot at most every 10 min. Never raises; False and
        a 'not saved' state when the file could not be written."""
        if self.project is None:
            return False
        path = self.session_file
        pending, self._pending_path = self._pending_path, None
        if pending is not None and pending != path:     # never write one project's zones into another's file
            log.warning("autosave for %s dropped: the project is now %s", pending, path)
            return False
        if self.read_only or not self._check_lock():
            self.status.emit(self.read_only_reason or READ_ONLY_TEXT)
            self.save_state.emit("read-only")
            return False
        from aurora.core.io.safety import assert_writable
        from aurora.core.io.textio import write_text_atomic
        now = self.clock()
        try:
            assert_writable(path)
            foreign = self._keep_foreign(path, now)
            self._keep_old_schema(path)
            self._keep_previous(path)
            self.zones.exported = now.isoformat(timespec="seconds")
            self.zones.stamp(f"AURORA {__version__}")
            d = self.zones.to_json()
            write_text_atomic(path, json.dumps(d, indent=1, ensure_ascii=False, default=json_default))
        except Exception as err:            # OSError (disk, permission, protected), TypeError (a ui value)
            self._remove_tmp(path)
            log.warning("could not save the session to %s: %s", path, err)
            self._save_error = f"the last save failed ({_os_text(err)})"
            from aurora.app.widgets import plain_error
            self.status.emit(f"Could not save your outlines: {plain_error(err, self.project.root if self.project else None)}")
            self.save_state.emit("not saved")
            return False
        self._file_stamp = _file_stamp(path)
        self._file_schema = SESSION_SCHEMA
        self._file_bytes = None
        self._dirty = self._user_edits = False
        self._save_error = ""
        self._known_sig = self._signature()
        self.status.emit(f"zones saved to {path}")
        self.save_state.emit(f"saved {now:%H:%M}")
        last = self._last_snapshot
        # a newest snapshot 'in the future' (named before a move to a time zone further west, or before a DST change)
        # never holds the next one back
        if last is None or last > now or (now - last).total_seconds() >= SNAPSHOT_EVERY_S:
            if self._write_snapshot(d, "autosave", now) is not None:
                self._last_snapshot = now
        if foreign is not None:
            text = FOREIGN_TEXT.format(name=foreign.relative_to(path.parent).as_posix())
            log.warning("%s: %s", path, text)
            self.status.emit(text)
            self.warned.emit("Session changed on disk", text)
        return True

    def _keep_foreign(self, path: Path, now: dt.datetime) -> Path | None:
        """session.json changed on disk since this session last read or wrote it (another program, a second session,
        a sync tool) and holds other zones than this session knows: keep that version as a snapshot before it is
        written over. Returns the snapshot, None when nothing needed keeping."""
        stamp = _file_stamp(path)
        if stamp is None or stamp == self._file_stamp:
            return None
        try:
            d = json.loads(path.read_bytes().decode("utf-8-sig"))
            if not isinstance(d, dict) or file_schema(d) > SESSION_SCHEMA:
                return None
            if _content_digest(ZonesFile.from_json(self._migrate(d, file_schema(d))).to_json()) == self._known_sig:
                return None                     # the same content (touched, or copied back): nothing to keep
        except Exception as err:                # noqa: BLE001  unreadable: kept as session.corrupt-<time>.json
            log.warning("%s changed on disk and does not parse (%s)", path, err)
            return self._set_aside(path)
        d.pop("backup", None)
        return self._write_snapshot(d, "changed on disk by another program", now)

    def _keep_old_schema(self, path: Path) -> None:
        """Before the first save over an older-schema file: its original bytes as session.v<old>.bak.json (once)."""
        if self._file_bytes is None or self._file_schema is None or self._file_schema >= SESSION_SCHEMA:
            return
        target = path.with_name(f"session.v{self._file_schema}.bak.json")
        if not target.exists():
            self._write_bytes_atomic(target, self._file_bytes)
            log.info("the schema-%d session was kept as %s", self._file_schema, target)
        self._file_bytes = None

    def _keep_previous(self, path: Path) -> None:
        """The current session.json, when it is a good file, becomes session.json.bak. A failure is logged only."""
        stamp = _file_stamp(path)
        if stamp is None:
            return
        try:
            data = path.read_bytes()
            if stamp != self._file_stamp:               # not the file this session wrote or read: check it parses
                d = json.loads(data.decode("utf-8-sig"))
                if not isinstance(d, dict):
                    return
            self._write_bytes_atomic(path.with_name("session.json.bak"), data)
        except (OSError, UnicodeDecodeError, ValueError) as err:
            log.info("no session.json.bak written (%s)", err)

    @staticmethod
    def _write_bytes_atomic(target: Path, data: bytes) -> None:
        from aurora.core.io.safety import assert_writable
        assert_writable(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, target)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    @staticmethod
    def _remove_tmp(path: Path) -> None:
        """A temporary file textio left behind when the final rename failed."""
        for p in path.parent.glob(f".{path.name}.*.tmp") if path.parent.is_dir() else []:
            try:
                p.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ backups
    def _snapshot_files(self) -> list[BackupInfo]:
        """The snapshots in .aurora/backups, oldest first (time and order from their names)."""
        d = self.backup_dir if self.project is not None else None
        out = []
        try:
            names = [p for p in d.iterdir() if p.is_file()] if d is not None and d.is_dir() else []
        except OSError:
            names = []
        for p in names:
            m = _SNAP_NAME.match(p.name)
            if not m:
                continue
            try:
                t = dt.datetime.strptime(m.group(1), _STAMP_FMT)
            except ValueError:
                continue
            out.append((t, int(m.group(2) or 1), p))
        out.sort(key=lambda r: (r[0], r[1]))
        return [BackupInfo(p, t, "snapshot") for t, _n, p in out]

    def _newest_snapshot_time(self) -> dt.datetime | None:
        snaps = self._snapshot_files()
        return snaps[-1].time if snaps else None

    def _write_snapshot(self, d: dict, reason: str, when: dt.datetime) -> Path | None:
        from aurora.core.io.textio import write_text_atomic
        base = f"session-{when:{_STAMP_FMT}}"
        target = self.backup_dir / f"{base}.json"
        n = 2
        while target.exists():
            target = self.backup_dir / f"{base}_{n}.json"
            n += 1
        body = {**d, "backup": {"reason": reason, "time": when.isoformat(timespec="seconds"),
                                "app": f"AURORA {__version__}"}}
        try:
            write_text_atomic(target, json.dumps(body, indent=1, ensure_ascii=False, default=json_default))
        except Exception as err:
            self._remove_tmp(target)
            log.warning("could not write the snapshot %s: %s", target, err)
            return None
        self._prune_snapshots()
        log.info("snapshot %s (%s)", target.name, reason)
        return target

    def _prune_snapshots(self) -> None:
        snaps = self._snapshot_files()
        for b in snaps[:max(0, len(snaps) - SNAPSHOTS_KEPT)]:
            try:
                b.path.unlink()
            except OSError as err:
                log.info("could not delete the old snapshot %s (%s)", b.path, err)

    def backup_now(self, reason: str) -> Path | None:
        """A snapshot of the session as it is in memory now (before an import, a proposal, a rule application or a
        restore). Returns its path; None when nothing could be written (read-only, protected folder, full disk)."""
        if self.project is None or self.read_only:
            return None
        now = self.clock()
        d = self.zones.to_json()
        d.update({"format": "aurora-session", "schema": SESSION_SCHEMA, "app": f"AURORA {__version__}"})
        return self._write_snapshot(d, reason, now)

    def backups(self) -> list[BackupInfo]:
        """Every backup File > Restore zones from a backup… offers, newest first, with its holder and zone counts."""
        if self.project is None:
            return []
        out = self._recovery_candidates()
        for b in out:
            try:
                d = json.loads(b.path.read_text(encoding="utf-8-sig"))
                if not isinstance(d, dict):
                    raise ValueError("not a JSON object")
                holders = d.get("holders", {}) or {}
                b.holders = len(holders)
                b.zones = sum(len(h.get("zones", []) or []) for h in holders.values() if isinstance(h, dict))
                note = d.get("backup")
                b.reason = str(note.get("reason", "")) if isinstance(note, dict) else ""
            except Exception as err:
                b.ok, b.error = False, str(err)
        return sorted(out, key=lambda b: b.time, reverse=True)

    def restore_backup(self, path: Path) -> int:
        """Replace the zones of every holder, the good-I0 choices and the settings rules by a backup's (a backup without
        a rules block keeps the current rules). The current state is snapshotted first and the restore is one undo
        step. Returns the number of zones restored (0 and a status line when the backup cannot be read)."""
        path = Path(path)
        zf = self._load_backup(path)
        if zf is None:
            self.status.emit(f"{path.name} could not be read: the zones are unchanged")
            return 0
        self.flush()
        m = _SNAP_NAME.match(path.name)
        when = (dt.datetime.strptime(m.group(1), _STAMP_FMT) if m else
                dt.datetime.fromtimestamp(path.stat().st_mtime))
        with self.edit(f"restore zones from the backup of {when:%Y-%m-%d %H:%M}", holder=ALL_HOLDERS):
            self.zones.holders = dict(zf.holders)
            self.zones.good_i0 = dict(zf.good_i0)
            if zf.rules or "rules" in zf._seen:        # the settings rules of the backup (an old one may have none)
                self.zones.rules = list(zf.rules)
            if zf.notes:
                self.zones.notes = zf.notes
            self.current_zone = None
        n = sum(len(h.zones) for h in zf.holders.values())
        from aurora.app.vocab import plural
        self.status.emit(native_text(f"restored {plural(n, 'zone')} from the backup of {when:%Y-%m-%d %H:%M} · Ctrl+Z to undo"))
        self.zone_selected.emit(None)
        return n

    # ------------------------------------------------------------------ import / export
    def import_zones(self, path: Path, policy="ask") -> int:
        """Import a zones_all.json (the browser zone tool's, or another AURORA's export or session).

        policy, per holder of the file that already has other zones here: 'merge' (the default: the file's zones are
        appended with free ids, and a zone with the same rectangles, erase rectangles, painted squares and polygons
        as an existing one is skipped as a duplicate), 'replace' (the file's zones replace the holder's) or 'skip';
        'ask' shows the per-holder dialog when the session is in a window (otherwise merge); a dict gives one policy
        per holder key. A holder of the file is matched to a holder of this project by its key, else by its holder name
        and day, else by its name alone when only one holder has it (zones exported from a one-day project, keyed
        'N5', land on 'N5_2026-11-05' here); the zones of a holder that matches none are kept under their own key and
        counted apart. The pending autosave is written and a snapshot saved first; the import is one edit (one undo
        step). A file that cannot be read changes nothing, never raises, and says so. Returns the zones imported."""
        path = Path(path)
        self.last_import = res = ImportResult()
        try:
            zf = ZonesFile.load(path)
            zf.holders, unmatched = self._import_targets(zf)
        except Exception as err:
            res.error = str(err)
            text = f"Could not read {path.name}: {err}. The zones are unchanged."
            log.warning("import of %s failed: %s", path, err)
            self.status.emit(text)
            if self._ui is not None:
                self._ui.warn("Import zones", text)
            return 0
        self.flush()
        policies = self._import_policies(path, zf, policy)
        if policies is None:
            res.cancelled = True
            self.status.emit("import cancelled: the zones are unchanged")
            return 0
        res.backup = self.backup_now(f"import {path.name}")
        with self.edit(f"import zones from {path.name}", holder=ALL_HOLDERS):
            for key, hz in zf.holders.items():
                pol = policies.get(key, "merge")
                if pol == "skip":
                    res.holders_skipped += 1
                    continue
                cur = self.zones.holders.get(key)
                if cur is None or pol == "replace":
                    self.zones.holders[key] = hz
                    if key in unmatched:
                        res.unmatched += len(hz.zones)
                    else:
                        res.imported += len(hz.zones)
                    continue
                for k, v in hz.extra.items():
                    cur.extra.setdefault(k, v)
                have = {_geometry(z) for z in cur.zones}
                ids = {z.id for z in cur.zones}
                for z in hz.zones:
                    g = _geometry(z)
                    if g in have:
                        res.duplicates += 1
                        continue
                    if z.id in ids:
                        z.id = max(ids, default=0) + 1
                    ids.add(z.id)
                    have.add(g)
                    cur.zones.append(z)
                    if key in unmatched:
                        res.unmatched += 1
                    else:
                        res.imported += 1
            for sid, good in zf.good_i0.items():
                owner = self.holder_of_stack(sid) or next(
                    (k for k, h in zf.holders.items() if any(sid in z.settings or sid in z.members for z in h.zones)),
                    None)
                pol = policies.get(owner, "merge") if owner else "merge"
                if pol == "skip":
                    continue
                if pol == "replace" or sid not in self.zones.good_i0:
                    self.zones.good_i0[sid] = good
            if zf.notes and not self.zones.notes:
                self.zones.notes = zf.notes
            if self.zone(self.current_zone) is None:
                self.current_zone = None
        from aurora.app.vocab import plural
        msg = f"imported {res.imported}, skipped {plural(res.duplicates, 'duplicate')}"
        if res.holders_skipped:
            msg += f", {plural(res.holders_skipped, 'holder')} left as they were"
        if res.unmatched:
            msg += (f", {plural(res.unmatched, 'zone')} of holders this project does not have "
                    f"({', '.join(sorted(unmatched))}) kept in the session but not shown")
        msg += f", backup saved as {res.backup.name}" if res.backup is not None else ", no backup could be saved"
        self.status.emit(msg)
        return res.imported

    def _project_key(self, key: str, hz: HolderZones) -> str | None:
        """The holder key of this project an imported holder belongs to: the same key, else the holder of the same name
        on the same day, else the only holder of that name; None when no holder of the project matches."""
        if key in self.holder_keys:
            return key
        name = hz.holder or key
        for pairs in ([k for k, (day, h) in self.holder_keys.items() if h.name == name and day == hz.day],
                      [k for k, (_day, h) in self.holder_keys.items() if h.name == name]):
            if len(pairs) == 1:
                return pairs[0]
        return None

    def _import_targets(self, zf: ZonesFile) -> tuple[dict[str, HolderZones], set[str]]:
        """The holders of an imported file under this project's keys (_project_key), with the holder name and day of
        the holder they land on; two of them landing on one holder are joined (ids made unique). Returns the holders
        and the keys that match no holder of the project (kept as they are)."""
        out: dict[str, HolderZones] = {}
        unmatched: set[str] = set()
        for key, hz in zf.holders.items():
            target = self._project_key(key, hz)
            if target is None:
                target = key
                unmatched.add(key)
            else:
                day, h = self.holder_keys[target]
                hz.holder, hz.day = h.name, day
                if target != key:
                    log.info("imported holder %r matched to %r of this project", key, target)
            have = out.get(target)
            if have is None:
                out[target] = hz
                continue
            ids = {z.id for z in have.zones}
            for z in hz.zones:
                if z.id in ids:
                    z.id = max(ids, default=0) + 1
                ids.add(z.id)
                have.zones.append(z)
            for k, v in hz.extra.items():
                have.extra.setdefault(k, v)
        return out, unmatched

    def _import_policies(self, path: Path, zf: ZonesFile, policy) -> dict[str, str] | None:
        if isinstance(policy, dict):
            bad = {v for v in policy.values() if v not in IMPORT_POLICIES}
            if bad:
                raise ValueError(f"import policy must be one of {IMPORT_POLICIES}, not {sorted(bad)}")
            return {k: policy.get(k, "merge") for k in zf.holders}
        if policy in IMPORT_POLICIES:
            return {k: policy for k in zf.holders}
        if policy != "ask":
            raise ValueError(f"import policy must be 'ask' or one of {IMPORT_POLICIES}, not {policy!r}")
        rows = []
        for key, hz in zf.holders.items():
            cur = self.zones.holders.get(key)
            if cur is None or not cur.zones:
                continue
            if [z.to_json() for z in cur.zones] == [z.to_json() for z in hz.zones]:
                continue                # every choice gives the same zones: nothing to ask
            have = {_geometry(z) for z in cur.zones}
            rows.append(ImportRow(key=key, holder=hz.holder or key, existing=len(cur.zones), incoming=len(hz.zones),
                                  duplicates=sum(1 for z in hz.zones if _geometry(z) in have)))
        if not rows or self._ui is None:
            return {k: "merge" for k in zf.holders}
        answer = self._ui.ask_import(path, rows)
        if answer is None:
            return None
        return {k: answer.get(k, "merge") for k in zf.holders}

    def export_zones(self, path: Path) -> None:
        """The zone tool's zones_all.json: version 1, the session without its header, 'ui' and 'rules' blocks. Polygon
        zones are written as painted squares on the pixels inside them (aurora.core.zones.export_json), at the pitch
        of every stack known by its coordinates, loaded or not, so the tool finds the same members; 'polys' is kept.
        Atomic write, as ZonesFile.save."""
        from aurora.core.io.textio import write_text_atomic
        self.zones.exported = self.clock().isoformat(timespec="seconds")
        stacks: dict = {sid: (c[0], c[1], c[2]) for sid, c in self._coords.items()}
        stacks.update(self._stacks)
        write_text_atomic(path, json.dumps(export_json(self.zones, stacks), indent=1, ensure_ascii=False,
                                           default=json_default))
        self.status.emit(f"exported zones to {path}")
