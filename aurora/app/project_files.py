"""The optional files of a beamtime, picked or made inside AURORA (File menu, Easy and Advanced):

- Choose sample sheet…: the CSV AURORA uses as the sample sheet (any lab's spreadsheet export), stored in the session
  (one undo step); the automatic choice otherwise (aurora.core.io.config.find_sample_sheet);
- New sample sheet…: writes samples.csv at the beamtime folder (UTF-8; Sample ID, Holder, Group, Composition, Edges,
  Notes; one row per holder with the holder filled in) to be completed in a spreadsheet;
- Import SDD calibration…: copies a chosen sdd_calibration.json into config/ (validated first; the previous file kept
  as a backup), the project's own calibration from then on.

Home's scan menu (right-click a stack) sets a scan's kind by hand: Treat as a sample scan / Treat as a test, kept in
the session (ui 'kind_override') and applied at once (the holders are grouped again).

Nothing is written in a protected folder (a beamline raw/ folder, AURORA_PROTECT_DIRS): those entries are disabled
with the reason.

    project_files.install(window)        # once, after the window's menus exist (MainWindow.__init__)
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QFileDialog, QMessageBox

from aurora.app import shortcuts
from aurora.app.widgets import set_enabled

log = logging.getLogger("aurora.app")

CHOOSE_SHEET = "Choose sample sheet…"
NEW_SHEET = "New sample sheet…"
IMPORT_CALIBRATION = "Import SDD calibration…"
SHEET_NAME = "samples.csv"
SHEET_HEADER = ("Sample ID", "Holder", "Group", "Composition", "Edges", "Notes")
PROTECTED = "this beamtime folder is protected: AURORA never writes there"
NO_PROJECT = "open a project first"
TIPS = {
    CHOOSE_SHEET: "Pick the CSV that lists the samples (Sample ID, Holder, Group, Composition, Edges, Notes; other "
                  "column names of the same meaning are recognised). Without it AURORA picks the CSV at the beamtime "
                  "folder that has a sample and a holder column.",
    NEW_SHEET: "Write samples.csv at the beamtime folder, one row per holder with the holder filled in: complete it in "
               "a spreadsheet (one row per sample), save it as CSV, then Reload.",
    IMPORT_CALIBRATION: "Copy a detector energy calibration (sdd_calibration.json: gain and offset of each SDD) into "
                        "config/ of this beamtime; the previous file is kept as a backup.",
}


def template_text(holders: list[str]) -> str:
    """The text of a new sample sheet: the header, then one row per holder with its name filled in."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(SHEET_HEADER)
    for h in holders:
        w.writerow(["", h, "", "", "", ""])
    return buf.getvalue()


def free_name(folder: Path, name: str) -> Path:
    """`name` in `folder`, or name_2, name_3 … when it exists (a sheet is never written over)."""
    p = Path(folder) / name
    stem, suffix = p.stem, p.suffix
    k = 2
    while p.exists():
        p = p.with_name(f"{stem}_{k}{suffix}")
        k += 1
    return p


class ProjectFiles(QObject):
    """Owns the File-menu actions and Home's kind override, and keeps them in step with the project."""

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("aurora_project_files")
        self.window_ = window
        self.session = window.session
        self.actions = {}
        for text, slot in ((CHOOSE_SHEET, self.choose_sheet), (NEW_SHEET, self.new_sheet),
                           (IMPORT_CALIBRATION, self.import_calibration)):
            a = shortcuts.add(window, text, None, slot, menu="file", section="File")
            a.setToolTip(TIPS[text])
            a.setStatusTip(TIPS[text])
            a.setProperty("aurora_tip", TIPS[text])
            self.actions[text] = a
        self.session.project_changed.connect(self.refresh)
        self.session.sheet_changed.connect(self._files_changed)
        self.session.config_changed.connect(self._files_changed)
        home = getattr(window, "home", None)
        if home is not None and hasattr(home, "kind_override_requested"):
            home.kind_override_requested.connect(self.set_kind)
        self.refresh()

    # ------------------------------------------------------------------ state
    def root(self) -> Path | None:
        p = self.session.project
        return Path(p.root) if p is not None else None

    @Slot()
    @Slot(object)
    def refresh(self, _arg=None) -> None:
        from aurora.core.io.safety import is_protected
        root = self.root()
        if root is None:
            for a in self.actions.values():
                set_enabled(a, False, NO_PROJECT)
            return
        set_enabled(self.actions[CHOOSE_SHEET], True)
        set_enabled(self.actions[NEW_SHEET], not is_protected(root / SHEET_NAME), PROTECTED)
        set_enabled(self.actions[IMPORT_CALIBRATION], not is_protected(root / "config" / "sdd_calibration.json"),
                    PROTECTED)

    def _files_changed(self) -> None:
        home = getattr(self.window_, "home", None)
        if home is not None and hasattr(home, "refresh_facts"):
            home.refresh_facts(self.session.ui_value("sample_sheet") or "")
        self.refresh()

    # ------------------------------------------------------------------ the sample sheet
    def choose_sheet(self, path: Path | None = None) -> bool:
        """Use a CSV as the sample sheet (a file dialog when `path` is None)."""
        root = self.root()
        if root is None:
            return False
        if path is None:
            p, _ = QFileDialog.getOpenFileName(self.window_, "Choose the sample sheet", str(root),
                                               "CSV files (*.csv);;All files (*)")
            if not p:
                return False
            path = Path(p)
        ok = self.session.choose_sample_sheet(Path(path))
        if not ok:
            QMessageBox.warning(self.window_, "Choose sample sheet", self._last_status() or
                                f"{Path(path).name} is not a sample sheet.")
        return ok

    def _last_status(self) -> str:
        bar = self.window_.statusBar() if hasattr(self.window_, "statusBar") else None
        return bar.currentMessage() if bar is not None else ""

    def new_sheet(self) -> Path | None:
        """Write a sample sheet template at the beamtime folder; returns it (None when refused)."""
        from aurora.core.io.safety import assert_writable
        from aurora.core.io.textio import write_text_atomic
        from aurora.app.session import _os_text
        root = self.root()
        if root is None:
            return None
        holders = [h.name for _key, (_day, h) in self.session.holder_keys.items()]
        target = free_name(root, SHEET_NAME)
        try:
            assert_writable(target)
            write_text_atomic(target, template_text(holders))
        except OSError as err:
            text = f"the sample sheet was not written: {_os_text(err)}"
            self.session.status.emit(text)
            QMessageBox.warning(self.window_, "New sample sheet", text[:1].upper() + text[1:] + ".")
            return None
        log.info("%s written (%d holders)", target, len(holders))
        self.session.reread_sheet()
        self.session.status.emit(f"wrote {target.name} in the beamtime folder: one row per sample (Sample ID, "
                                 "Holder, Group, …), save it as CSV, then Reload")
        return target

    # ------------------------------------------------------------------ the SDD calibration
    def import_calibration(self, path: Path | None = None) -> Path | None:
        """Copy a sdd_calibration.json into config/ (a file dialog when `path` is None); None when refused."""
        from aurora.core.io.config import CALIBRATION_FILE, Calibration
        from aurora.core.io.textio import write_text_atomic
        from aurora.app.session import _os_text
        proj = self.session.project
        if proj is None:
            return None
        if path is None:
            p, _ = QFileDialog.getOpenFileName(self.window_, "Import the SDD calibration", str(proj.root),
                                               "JSON files (*.json);;All files (*)")
            if not p:
                return None
            path = Path(p)
        path = Path(path)
        from aurora.core.constants import DETS
        cal = Calibration(path)
        if not cal.exists or len(cal.missing) == len(DETS):
            why = "; ".join(cal.problems) or "it has no detector entry"
            text = f"{path.name} is not an SDD calibration AURORA can use ({why}): nothing was imported"
            self.session.status.emit(text)
            QMessageBox.warning(self.window_, "Import SDD calibration", text[:1].upper() + text[1:] + ".")
            return None
        target = proj.config_dir / CALIBRATION_FILE
        try:
            if path.resolve() == target.resolve():
                self.session.status.emit(f"{target.name} is already this beamtime's calibration")
                return target
            text = path.read_text(encoding="utf-8-sig")
            if target.exists():
                stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                write_text_atomic(target.with_name(f"{target.stem}.backup-{stamp}{target.suffix}"),
                                  target.read_text(encoding="utf-8-sig"))
            write_text_atomic(target, text)
        except OSError as err:
            text = f"the calibration was not imported: {_os_text(err)}"
            self.session.status.emit(text)
            QMessageBox.warning(self.window_, "Import SDD calibration", text[:1].upper() + text[1:] + ".")
            return None
        proj.read_config()
        partial = f" ({'; '.join(proj.calibration.problems)}: nominal)" if proj.calibration.missing else ""
        self.session.status.emit(f"SDD calibration imported into config/{CALIBRATION_FILE}{partial}")
        self.session.config_changed.emit()
        return target

    # ------------------------------------------------------------------ a scan's kind set by hand (Home)
    def set_kind(self, scan, kind: str) -> None:
        """Treat a stack as a sample scan ('sample'), a test ('test') or by the rules again (''): kept in the session,
        applied at once (Home and the holders are refreshed)."""
        from aurora.core.model import KIND_OVERRIDE
        proj = self.session.project
        if proj is None or scan is None:
            return
        ov = dict(self.session.ui_value(KIND_OVERRIDE, {}) or {})
        if kind in ("sample", "test"):
            ov[scan.name] = kind
        else:
            ov.pop(scan.name, None)
        self.session.set_ui_value(KIND_OVERRIDE, ov)
        self.session.flush()
        proj.apply_overrides(ov)
        home = getattr(self.window_, "home", None)
        if home is not None:
            home.show_again()
        what = {"sample": "a sample scan", "test": "a test"}.get(kind, "by the rules of its name and energies")
        self.session.status.emit(f"{scan.name} is treated as {what} now ({scan.status.replace('_', ' ')})")


def install(window) -> ProjectFiles:
    """Add the actions to the window (once). Returns the owner object, a child of the window."""
    found = window.findChild(ProjectFiles, "aurora_project_files")
    return found if found is not None else ProjectFiles(window)
