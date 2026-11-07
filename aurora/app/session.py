"""Shared application state: the project, the manual zones, loaded stacks and surveys, the good-I0 set, autosave.

Holders are keyed like the zone tool keys them (the holder name, suffixed with the day when a name repeats);
stacks by '<day>_<scan folder>'. The zones live in `<root>/.aurora/session.json` (the zones_all.json format plus a
'ui' block) and can be imported from / exported to the tool's zones_all.json unchanged.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from aurora.core.io.config import HolderZones, ScanSettings, Zone, ZonesFile, read_sample_sheet
from aurora.core.model import Holder, Project, ScanInfo
from aurora.core.stack import StackData
from aurora.core.survey import SurveyData
from aurora.core.zones import good_i0_curve, members

log = logging.getLogger("aurora.session")


class Session(QObject):
    project_changed = Signal(object)        # Project
    zones_changed = Signal()                # any zone edit
    holder_changed = Signal(str)            # holder key
    zone_selected = Signal(object)          # Zone | None
    stack_selected = Signal(object)         # StackData | None
    status = Signal(str)

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
        return self.project.find(sid[11:]) if self.project else None

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
        z = Zone(id=zid, label=label or f"zone {zid}")
        hz.zones.append(z)
        self.current_zone = zid
        self.touch()
        self.zone_selected.emit(z)
        return z

    def delete_zone(self, zid: int) -> None:
        hz = self.holder_zones(create=False)
        if hz is None:
            return
        hz.zones = [z for z in hz.zones if z.id != zid]
        if self.current_zone == zid:
            self.current_zone = None
            self.zone_selected.emit(None)
        self.touch()

    def select_zone(self, zid: int | None) -> None:
        self.current_zone = zid
        self.zone_selected.emit(self.zone(zid) if zid is not None else None)

    def attach_scans(self, z: Zone) -> None:
        """Recompute the zone's member pixels for every loaded stack of the holder and create default settings."""
        for scan in self.stacks_of():
            sid = self.stack_id(scan)
            sd = self._stacks.get(sid)
            if sd is None:
                continue
            pix = members(z, sd.x, sd.y, sd.pitch)
            if pix.size:
                z.members[sid] = pix.tolist()
                st = z.settings.get(sid)
                if st is None:
                    from aurora.app.settings_dialog import get as setting
                    st = ScanSettings(stack=scan.name, line=sd.main, k=setting("despike_k"), fluo_norm=setting("fluo_norm"), tey_norm=setting("tey_norm"))
                    sat = scan.summary.get("sdd_saturated") if scan.summary else None
                    if sat:
                        st.sdd = [d not in sat for d in ("sdd1", "sdd2", "sdd3", "sdd4")]
                    if scan.summary.get("i0_bad") if scan.summary else False:
                        st.i0_source = "good"
                    z.settings[sid] = st
                st.n = int(pix.size)
            else:
                z.members.pop(sid, None)
                z.settings.pop(sid, None)

    def touch(self) -> None:
        """A zone changed: refresh memberships, notify, schedule an autosave."""
        hz = self.holder_zones(create=False)
        if hz:
            for z in hz.zones:
                self.attach_scans(z)
        self.zones_changed.emit()
        self._save_timer.start()

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
    def save(self) -> None:
        if self.project is None:
            return
        self.zones.exported = dt.datetime.now().isoformat(timespec="seconds")
        try:
            self.session_file.parent.mkdir(parents=True, exist_ok=True)
            self.zones.save(self.session_file)
            self.status.emit(f"zones saved to {self.session_file}")
        except OSError as err:
            self.status.emit(f"could not save the session: {err}")

    def import_zones(self, path: Path) -> int:
        zf = ZonesFile.load(path)
        n = 0
        for key, hz in zf.holders.items():
            self.zones.holders[key] = hz
            n += len(hz.zones)
        self.zones.good_i0.update(zf.good_i0)
        if zf.notes:
            self.zones.notes = zf.notes
        self.current_zone = None
        self.touch()
        self.status.emit(f"imported {n} zones from {path}")
        return n

    def export_zones(self, path: Path) -> None:
        self.zones.exported = dt.datetime.now().isoformat(timespec="seconds")
        self.zones.save(path)
        self.status.emit(f"exported zones to {path}")
