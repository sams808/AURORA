"""File > Re-reduce the stack with the project ROI bins: the offer that goes with a stack opened from sgm_report's cache.

sgm_report's `_reduced_cache.npz` is the beamtime's own reduction, so AURORA uses it as it is. When its XRF windows
differ from config/roi_bins.json the stack's source reads 'sgm_report cache with other windows' (aurora.core.stack),
and this action reduces the stack again from the raw energy folders with the project's ROI bins, in a job with
progress and Cancel. It is enabled whenever the current stack comes from an sgm_report cache (a cache holding only some
of the project's lines can be re-reduced as well) and is an Advanced entry of the File menu.

    stack_cache.install(window)          # once, after the window's menus exist (MainWindow.__init__)
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Slot
from PySide6.QtGui import QAction

from aurora.app import jobs, mode, shortcuts
from aurora.app.widgets import set_enabled

log = logging.getLogger("aurora.app")

TEXT = "Re-reduce the stack with the project ROI bins"
TIP = ("Reduce the current stack again from its raw energy folders with every line of config/roi_bins.json, instead "
       "of sgm_report's cache. Offered when that cache was reduced with other XRF windows.")


class StackCacheOffer(QObject):
    """Owns the File-menu action and keeps its state in step with the current stack."""

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("aurora_stack_cache_offer")
        self.window_ = window
        self.session = window.session
        self.action: QAction = shortcuts.add(window, TEXT, None, self.rereduce, menu="file", section="File")
        self.action.setToolTip(TIP)
        self.action.setStatusTip(TIP)
        self.action.setProperty("aurora_tip", TIP)
        mode.mark_advanced_action(self, self.action)
        self.session.stack_selected.connect(self.refresh)
        self.session.project_changed.connect(self.refresh)
        self.refresh()

    def current(self):
        sid = self.session.current_stack
        return sid, (self.session.stack_loaded(sid) if sid else None)

    @Slot()
    @Slot(object)
    def refresh(self, _arg=None) -> None:
        sid, sd = self.current()
        if sd is None:
            set_enabled(self.action, False, "open a stack in Reduce first")
        elif not getattr(sd, "from_report_cache", False):
            set_enabled(self.action, False, "this stack already uses the project ROI bins")
        elif jobs.of(self.window_).job(f"stack:{sid}") is not None and jobs.of(self.window_).job(f"stack:{sid}").is_running():
            set_enabled(self.action, False, "the stack is being read")
        else:
            set_enabled(self.action, True)
            if getattr(sd, "window_note", ""):
                self.action.setToolTip(f"{TIP}\n{sd.window_note}")

    def rereduce(self) -> None:
        sid, sd = self.current()
        if sd is None or not getattr(sd, "from_report_cache", False):
            return
        scan = self.session.scan_of(sid)
        project = self.session.project
        if scan is None or project is None:
            return
        from aurora.core.stack import StackData
        host = getattr(self.window_, "reduce", self.window_)
        self.session.status.emit(f"re-reducing {scan.name} with the project ROI bins …")
        jobs.of(self.window_).submit(
            f"stack:{sid}", lambda cb: StackData.load(project, scan, progress=cb, use_report_cache=False),
            on_done=lambda new, sid=sid: self._done(sid, new, project), on_end=lambda _s: self.refresh(),
            label=f"re-reducing {scan.name}", taskbar=getattr(host, "taskbar", None))
        self.refresh()

    def _done(self, sid: str, sd, project=None) -> None:
        cur = self.session.project
        if cur is None or (project is not None and cur is not project) or self.session.scan_of(sid) is None:
            log.info("%s: re-reduced for a project that is no longer open: not kept", sid)
            return                                         # another project was opened meanwhile
        self.session._stacks[sid] = sd                     # the session's stack table (aurora.app.session)
        log.info("%s: re-reduced with the project ROI bins (%s)", sid, sd.source)
        self.session.touch()                               # memberships for the new arrays, as after any stack load
        if self.session.current_stack == sid:
            self.session.select_stack(sid)
        self.session.status.emit(f"{sd.name}: reduced again with the project ROI bins")


def install(window) -> StackCacheOffer:
    """Add the action to the window (once). Returns the offer object, a child of the window."""
    found = window.findChild(StackCacheOffer, "aurora_stack_cache_offer")
    return found if found is not None else StackCacheOffer(window)
