"""Shared application state: the project, the manual zones, loaded stacks and surveys, the good-I0 set, autosave.

Holders are keyed like the zone tool keys them (the holder name, suffixed with the day when a name repeats);
stacks by '<day>_<scan folder>'. The zones live in `<root>/.aurora/session.json` (the zones_all.json format plus a
'ui' block) and can be imported from / exported to the tool's zones_all.json unchanged.

Extension points, so several perspectives share this state without editing this file:
- `with session.edit(description):` is the one way to change zones, settings or good-I0 (refresh, notify, autosave
  on exit; undo snapshots, merging and backups belong inside it);
- ui_value / set_ui_value: view memory saved with the session ('ui' block);
- check_queue, queue_for_check and check_queue_changed: the Easy 'Spectra to check' queue;
- add_flag_source(fn(scan) -> [Flag]) and flags_of(scan): scan flags beyond the sgm_report summary;
- add_settings_default(fn(scan, ScanSettings)): defaults applied to every new zone x scan settings;
- flush(), save_state(str) and read_only for the save indicator.
"""
from __future__ import annotations

import datetime as dt
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from aurora.core.constants import DETS
from aurora.core.flags import i0_invalid, merged_flags, saturated_dets
from aurora.core.io.config import HolderZones, ScanSettings, Zone, ZonesFile, read_sample_sheet
from aurora.core.model import Holder, Project, ScanInfo
from aurora.core.names import split_stack_id
from aurora.core.stack import StackData
from aurora.core.survey import SurveyData
from aurora.core.spectra import good_i0_curve
from aurora.core.zones import members

log = logging.getLogger("aurora.session")


@dataclass(frozen=True)
class CheckItem:
    """One row of the Easy 'Spectra to check' queue: a zone of a holder on one stack."""
    holder: str
    zone_id: int
    sid: str


class Session(QObject):
    project_changed = Signal(object)        # Project
    zones_changed = Signal()                # any zone edit
    holder_changed = Signal(str)            # holder key
    zone_selected = Signal(object)          # Zone | None
    stack_selected = Signal(object)         # StackData | None
    status = Signal(str)
    check_queue_changed = Signal()          # items added to / removed from check_queue
    save_state = Signal(str)                # 'unsaved' | 'saved HH:MM' | 'not saved' | 'read-only'

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
        self.read_only = False                  # True: edits stay in memory, save() refuses (a newer session schema)
        self._flag_sources: list = []
        self._settings_defaults: list = []
        self._edit_depth = 0
        self._edit_info: tuple[str, str | None] | None = None      # (description, holder) of the edit in progress
        self._touch_pending = False
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.save)

    # ------------------------------------------------------------------ project
    def set_project(self, project: Project) -> None:
        self.project = project
        self._stacks.clear(); self._surveys.clear()
        self.holder_keys.clear()
        seen: dict[str, int] = {}
        for day in project.days:
            for h in day.holders:
                seen[h.name] = seen.get(h.name, 0) + 1
        for day in project.days:
            for h in day.holders:
                key = h.name if seen[h.name] == 1 else f"{h.name}_{day.date}"
                self.holder_keys[key] = (day.date, h)
        self.zones = ZonesFile(project=self._project_id())
        sf = self.session_file
        if sf.exists():
            try:
                self.zones = ZonesFile.load(sf)
                log.info("session restored from %s", sf)
            except Exception as err:
                log.warning("%s unreadable (%s); starting empty", sf, err)
        sheets = sorted(project.root.glob("*.csv"))
        self.sheet_rows = []
        for s in sheets:
            try:
                self.sheet_rows = read_sample_sheet(s)
                if self.sheet_rows:
                    break
            except Exception:
                continue
        self.current_holder = next(iter(self.holder_keys), None)
        self.current_zone = None
        self.current_stack = None
        if self.check_queue:
            self.check_queue = []
            self.check_queue_changed.emit()
        self.project_changed.emit(project)
        if self.current_holder:
            self.holder_changed.emit(self.current_holder)

    def _project_id(self) -> str:
        for s in self.project.scans if self.project else []:
            if s.md.get("project"):
                return s.md["project"]
        return self.project.root.name if self.project else ""

    @property
    def session_file(self) -> Path:
        return self.project.root / ".aurora" / "session.json"

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
        if sid not in self._stacks:
            scan = self.scan_of(sid)
            if scan is None:
                raise KeyError(sid)
            self._stacks[sid] = StackData.load(self.project, scan, progress=progress)
        return self._stacks[sid]

    def stack_loaded(self, sid: str) -> StackData | None:
        return self._stacks.get(sid)

    def select_stack(self, sid: str | None) -> None:
        self.current_stack = sid
        self.stack_selected.emit(self._stacks.get(sid) if sid else None)

    # ------------------------------------------------------------------ editing
    @contextmanager
    def edit(self, description: str, holder: str | None = None):
        """The one way to change zones, settings or good-I0:

            with session.edit("add rectangle to zone 3"):
                z.rects.append(rect)

        `holder` names the holder whose zones change (the current holder when None). Edits nest: an inner edit is
        part of the outer one, and touch() calls inside an edit are deferred to its end. On exit (also after an
        exception) the memberships are refreshed, listeners notified and an autosave scheduled. Undo snapshots,
        merging of repeated edits and backups belong here; callers never need to change."""
        outer = self._edit_depth == 0
        if outer:
            self._edit_info = (str(description), holder or self.current_holder)
            self._touch_pending = False
        self._edit_depth += 1
        try:
            yield self
        finally:
            self._edit_depth -= 1
            if outer:
                self._edit_info = None
                self._touch_pending = False
                self.touch()

    @property
    def editing(self) -> str | None:
        """The description of the edit in progress, None outside an edit."""
        return self._edit_info[0] if self._edit_info else None

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

    def new_zone(self, label: str = "") -> Zone:
        hz = self.holder_zones()
        zid = max((z.id for z in hz.zones), default=0) + 1
        with self.edit(f"new zone {zid}"):
            z = Zone(id=zid, label=label or f"zone {zid}")
            hz.zones.append(z)
            self.current_zone = zid
        self.zone_selected.emit(z)
        return z

    def delete_zone(self, zid: int) -> None:
        hz = self.holder_zones(create=False)
        if hz is None:
            return
        with self.edit(f"delete zone {zid}"):
            hz.zones = [z for z in hz.zones if z.id != zid]
            if self.current_zone == zid:
                self.current_zone = None
                self.zone_selected.emit(None)

    def select_zone(self, zid: int | None) -> None:
        self.current_zone = zid
        self.zone_selected.emit(self.zone(zid) if zid is not None else None)

    def attach_scans(self, z: Zone) -> None:
        """Recompute the zone's member pixels for every loaded stack of the holder and create default settings."""
        pairs = [(self.stack_id(s), s) for s in self.stacks_of()]
        pairs += [(sid, None) for sid in self._stacks if sid.startswith("stitched_")]
        for sid, scan in pairs:
            sd = self._stacks.get(sid)
            if sd is None:
                continue
            pix = members(z, sd.x, sd.y, sd.pitch)
            if pix.size:
                z.members[sid] = pix.tolist()
                st = z.settings.get(sid)
                if st is None and scan is None:
                    st = ScanSettings(stack=sd.name, line=sd.main)
                    z.settings[sid] = st
                elif st is None:
                    st = self.default_settings(scan, sd)
                    z.settings[sid] = st
                st.n = int(pix.size)
            else:
                z.members.pop(sid, None)
                z.settings.pop(sid, None)

    def default_settings(self, scan: ScanInfo, sd: StackData) -> ScanSettings:
        """New zone x scan settings: the Settings defaults, saturated SDDs off and the good-I0 mean for an invalid I0
        (from flags_of), then every registered settings-default provider in order."""
        from aurora.app.settings_dialog import get as setting
        st = ScanSettings(stack=scan.name, line=sd.main, k=setting("despike_k"), fluo_norm=setting("fluo_norm"),
                          tey_norm=setting("tey_norm"))
        flags = self.flags_of(scan)
        sat = saturated_dets(flags)
        if sat:
            st.sdd = [d not in sat for d in DETS]
        if i0_invalid(flags):
            st.i0_source = "good"
        for fn in list(self._settings_defaults):
            try:
                fn(scan, st)
            except RuntimeError:                # a provider whose Qt owner is gone
                continue
            except Exception:
                log.exception("settings-default provider %r failed on %s", fn, scan.name)
        return st

    def touch(self) -> None:
        """A zone changed: refresh memberships, notify, schedule an autosave. Inside an edit: at the edit's end."""
        if self._edit_depth:
            self._touch_pending = True
            return
        hz = self.holder_zones(create=False)
        if hz:
            for z in hz.zones:
                self.attach_scans(z)
        self.zones_changed.emit()
        self._schedule_save()

    def _schedule_save(self) -> None:
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
            self._schedule_save()

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
    def flush(self) -> None:
        """Write a pending autosave now (closing the window, opening another project)."""
        if self._save_timer.isActive():
            self._save_timer.stop()
            self.save()

    def save(self) -> None:
        if self.project is None:
            return
        if self.read_only:
            self.status.emit("this session is read-only: edits here are not saved; export them to a file")
            self.save_state.emit("read-only")
            return
        self.zones.exported = dt.datetime.now().isoformat(timespec="seconds")
        try:
            from aurora.core.io.safety import assert_writable
            assert_writable(self.session_file)
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            self.zones.save(self.session_file)
            self.status.emit(f"zones saved to {self.session_file}")
            self.save_state.emit(f"saved {dt.datetime.now():%H:%M}")
        except OSError as err:
            self.status.emit(f"could not save the session: {err}")
            self.save_state.emit("not saved")

    def import_zones(self, path: Path) -> int:
        zf = ZonesFile.load(path)
        n = 0
        with self.edit(f"import zones from {Path(path).name}"):
            for key, hz in zf.holders.items():
                self.zones.holders[key] = hz
                n += len(hz.zones)
            self.zones.good_i0.update(zf.good_i0)
            if zf.notes:
                self.zones.notes = zf.notes
            self.current_zone = None
        self.status.emit(f"imported {n} zones from {path}")
        return n

    def export_zones(self, path: Path) -> None:
        """The zone tool's zones_all.json: the session without its 'ui' and 'rules' blocks."""
        self.zones.exported = dt.datetime.now().isoformat(timespec="seconds")
        self.zones.save(path, export=True)
        self.status.emit(f"exported zones to {path}")
