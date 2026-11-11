"""Check perspective.

Easy mode is John Bussey's 'Check scans' tab: the 'Spectra to check' queue (one row per zone x scan sent from Reduce
with 'Add to Check'), his sanity messages (aurora.core.qc.check_zone) as ok / check / bad chips with an icon and a
word, the 'Looks OK' tick (ScanSettings.include, changed through session.edit), a preview of the selected row in his
five views, and 'Save ticked → Compare', which writes his file formats (aurora.core.export.write_john) into
<project>/processed/reduced/<sample>/ and adds the folders to the Compare library.

Advanced mode adds the checks of sgm_report v2.5 per scan of the holder (count rate, saturation, I0 validity against
the energy-local reference), the good-I0 picker, the agreement of the four detectors on the selected zone and the I0
explorer. The results of the checks persist in <project>/.aurora/qc.json (aurora.core.qc.save_qc), are read back when
the project opens, are re-checked when a scan changes, and reach new zones' defaults through session.add_flag_source
(and the settings made before them through session.follow_new_flags).
"""
from __future__ import annotations

import copy
import html
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from aurora.app import jobs, nav
from aurora.app.help_text import explain_all, header_tips, labelled
from aurora.app.mode import mark_advanced, on_mode_changed
from aurora.app.session import ALL_HOLDERS, DEFAULT_LABEL, CheckItem, Session
from aurora.app.theme import (curve, dash, make_plot, on_theme_changed, pc, refill_tones, restyle_plot, role_pen,
                              set_item_tone, status_text)
from aurora.app.vocab import DET_LABELS, plural
from aurora.app.widgets import (Card, Chip, ElidedLabel, EmptyState, TaskBar, breakable, fit_height, notify_saved,
                                overlay_host, page_scroll, set_enabled, held_file, set_plot_hint, write_error_text)
from aurora.core import export, qc
from aurora.core.constants import DETS
from aurora.core.lines import default_signal
from aurora.core.names import short_time, split_stack_id
from aurora.core.qc import CountRate, QcResult, i0_candidates, sdd_consistency, spike_summary
from aurora.core.zones import members, zone_traces

log = logging.getLogger("aurora.check")

COLS = ("Time", "Scan", "Edge", "Status", "I0 level", "I0", "SDD1 kcps", "SDD2 kcps", "SDD3 kcps", "SDD4 kcps", "Saturated", "High rate", "Notes")
COL_HELP = {0: "check.col.time", 1: "check.col.scan", 2: "check.col.edge", 3: "check.col.status", 4: "check.col.i0_level",
            5: "check.col.i0", 6: "check.col.kcps", 7: "check.col.kcps", 8: "check.col.kcps", 9: "check.col.kcps",
            10: "check.col.saturated", 11: "check.col.high", 12: "check.col.notes"}
QCOLS = ("Sample", "Scan", "Zone pts", "Bad pts", "Messages", "Looks OK")
QCOL_HELP = {0: "check.qcol.sample", 1: "check.qcol.scan", 2: "check.qcol.pts", 3: "check.qcol.bad", 4: "check.qcol.messages",
             5: "check.qcol.ok"}
Q_OK = QCOLS.index("Looks OK")
Q_MSG = QCOLS.index("Messages")
# John Bussey's five preview views of a checked scan (CheckTab._plot), in AURORA's words
VIEWS = (("signals", "detector signals, cleaned"), ("sum", "fluorescence sum and its bad points"),
         ("i0tey", "I0 / max and TEY / max"), ("ratio", "sum / I0 and TEY / I0"), ("minmax", "/ I0, min-max scaled"))
EASY_DESPIKE_MODE = "coherent"          # new zone x scan settings (I25); files without 'dsm' stay 'classic'
SEEN_KEY = "check_seen"                 # ui block: queue items whose first verdict was applied (saved with the queue)


def science_defaults(scan, st) -> None:
    """Settings default for every new zone x scan (registered with session.add_settings_default): the coherent
    de-spike, and lines.default_signal (IPFY for an 'IPFY …' summary mode or a non-resonant line). Fields the user
    pinned are left alone."""
    pinned = set(getattr(st, "pinned", []) or [])
    if not pinned & {"dsm", "despike_mode"}:
        st.despike_mode = EASY_DESPIKE_MODE
    if not pinned & {"sig", "signal"} and scan is not None:
        st.signal = default_signal(scan, getattr(scan, "summary", None) or {}, line=st.line or None)


@dataclass
class QueueRow:
    item: CheckItem
    zone: object
    settings: object
    frame: export.JohnFrame | None = None
    messages: list = field(default_factory=list)
    state: str = "ready"                # ready | loading | error
    error: str = ""
    n_total: int = 0

    @property
    def level(self) -> str:
        return qc.worst(self.messages) if self.state == "ready" else ("bad" if self.state == "error" else "muted")


def _minmax(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, float)
    fin = np.isfinite(y)
    if not fin.any():
        return y
    lo, hi = np.nanmin(y), np.nanmax(y)
    return (y - lo) / (hi - lo) if hi > lo else y - lo


def _by_max(y: np.ndarray) -> np.ndarray:
    y = np.asarray(y, float)
    m = np.nanmax(np.abs(y)) if np.isfinite(y).any() else 0.0
    return y / m if m else y


class CheckPerspective(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.library = None                   # the Compare library (MainWindow sets it)
        self._rates: dict[str, CountRate] = {}
        self._results: dict[str, QcResult] = {}     # sid -> the stored or fresh checks of every holder's scans
        self._cands = []
        self._x_curves = None                 # last I0-explorer read (scan name, curves), redrawn on a theme change
        self._rows: list[QueueRow] = []
        self._row_cache: dict = {}
        self._seen: set = set()               # queue items whose first verdict was applied (a bad one starts unticked)
        self._filling = False
        self._defer = False
        self._queue_dirty = False
        self._probed_holders: set = set()     # holders probed automatically for the queue (once each)
        self._load_errors: dict[str, str] = {}  # sid -> why its stack could not be read (queue rows say so)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Check"); h1.setObjectName("h1")
        self.sub = sub = ElidedLabel("", Qt.ElideRight)
        sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub, 1)
        root.addLayout(head)
        self.taskbar = TaskBar(self)
        root.addWidget(self.taskbar)

        split = QSplitter(Qt.Vertical)
        # ---------------------------------------------------------------- Easy: the queue and its preview
        top = QSplitter(Qt.Horizontal)
        self.queue_card = qcard = Card("Spectra to check", help_anchor="51-spectra-to-check")
        self.queue = QTableWidget(0, len(QCOLS))
        self.queue.setHorizontalHeaderLabels(QCOLS)
        self.queue.setAccessibleName("spectra to check")
        self.queue.setEditTriggers(QTableWidget.NoEditTriggers)
        self.queue.setSelectionBehavior(QTableWidget.SelectRows)
        self.queue.setSelectionMode(QTableWidget.SingleSelection)
        self.queue.verticalHeader().setVisible(False)
        self.queue.horizontalHeader().setSectionResizeMode(Q_MSG, QHeaderView.Stretch)
        self.queue.itemChanged.connect(self._queue_item_changed)
        self.queue.itemSelectionChanged.connect(self._draw_preview)
        self.queue.setContextMenuPolicy(Qt.CustomContextMenu)
        self.queue.customContextMenuRequested.connect(self._queue_menu)
        qcard.body.addWidget(self.queue, 1)
        qrow = QHBoxLayout()
        self.tick_all_btn = QPushButton("Tick all"); self.tick_all_btn.clicked.connect(lambda: self.set_all_ok(True))
        self.untick_all_btn = QPushButton("Untick all"); self.untick_all_btn.clicked.connect(lambda: self.set_all_ok(False))
        self.save_btn = QPushButton("Save ticked → Compare"); self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save_ticked)
        qrow.addWidget(self.tick_all_btn); qrow.addWidget(self.untick_all_btn); qrow.addStretch(1); qrow.addWidget(self.save_btn)
        qcard.body.addLayout(qrow)
        self.queue_note = QLabel(""); self.queue_note.setObjectName("muted"); self.queue_note.setWordWrap(True)
        qcard.body.addWidget(self.queue_note)
        top.addWidget(qcard)
        self.preview_card = pcard = Card("Preview of the selected spectrum", help_anchor="51-spectra-to-check")
        vrow = QHBoxLayout()
        self.view_combo = QComboBox()
        self.view_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)   # the longest view name
        self.view_combo.setMinimumContentsLength(16)                                           # set the page width
        for key, text in VIEWS:
            self.view_combo.addItem(text, key)
        self.view_combo.currentIndexChanged.connect(lambda _i: self._draw_preview())
        vrow.addWidget(labelled("view", self.view_combo, "check.view")); vrow.addWidget(self.view_combo, 1)
        pcard.body.addLayout(vrow)
        self.qplot = make_plot(x="energy (eV)", y="mean counts")
        self.qplot.addLegend(offset=(-10, -10))         # bottom right: rising XAS curves leave that corner free
        restyle_plot(self.qplot)                          # theme the legend now (make_plot's hook restyles it later)
        self.qplot.getViewBox().disableAutoRange()
        self.qplot.setMinimumHeight(170)
        pcard.body.addWidget(self.qplot, 1)
        self.msg_label = QLabel(""); self.msg_label.setWordWrap(True); self.msg_label.setObjectName("muted")
        self.msg_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.msg_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.msg_scroll = QScrollArea()                 # the messages scroll instead of squeezing the plot
        self.msg_scroll.setWidget(self.msg_label)
        self.msg_scroll.setWidgetResizable(True)
        self.msg_scroll.setFrameShape(QFrame.NoFrame)
        self.msg_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.msg_scroll.setFixedHeight(self.fontMetrics().lineSpacing() + 6)
        pcard.body.addWidget(self.msg_scroll)
        top.addWidget(pcard)
        top.setSizes([640, 520])
        split.addWidget(top)

        # ---------------------------------------------------------------- Advanced: the holder checks (sgm_report)
        self.table_card = tcard = Card("Scans of the holder", help_anchor="52-the-holder-checks-advanced")
        self.run_btn = QPushButton("Run the checks on this holder"); self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run_checks)
        self.every_btn = QPushButton("Run the checks on every holder")
        self.every_btn.clicked.connect(self.run_every_holder)
        tcard.header.addWidget(self.run_btn); tcard.header.addWidget(self.every_btn)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setAccessibleName("checks of the holder's scans")
        self.table.horizontalHeader().setSectionResizeMode(len(COLS) - 1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._row_selected)
        tcard.body.addWidget(self.table, 1)
        self.day_label = ElidedLabel("", Qt.ElideRight); self.day_label.setObjectName("muted")
        tcard.body.addWidget(self.day_label)
        split.addWidget(tcard)

        self.bottom = bottom = QSplitter(Qt.Horizontal)
        i0c = Card("I0 of the holder's scans (per-pixel mean)", help_anchor="52-the-holder-checks-advanced")
        opts = QHBoxLayout()
        self.i0_norm = QCheckBox("normalise to the level"); self.i0_norm.setChecked(True); self.i0_norm.toggled.connect(self._draw_i0)
        self.auto_btn = QPushButton("Auto-select good I0"); self.auto_btn.clicked.connect(self._auto_select)
        opts.addWidget(self.i0_norm); opts.addStretch(1); opts.addWidget(self.auto_btn)
        i0c.body.addLayout(opts)
        self.i0_list = QListWidget()
        self.i0_list.setAccessibleName("I0 of each scan; tick the good ones")
        self.i0_list.setTextElideMode(Qt.ElideMiddle)
        self.i0_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.i0_list.itemChanged.connect(self._good_toggled)
        i0c.body.addWidget(self.i0_list, 1)
        self.i0_plot = make_plot(x="energy (eV)", y="I0")
        self.i0_plot.getViewBox().disableAutoRange()
        i0c.body.addWidget(self.i0_plot, 2)
        bottom.addWidget(i0c)
        sddc = Card("The four detectors on the selected zone", help_anchor="52-the-holder-checks-advanced")
        self.sdd_note = QLabel("select a zone in Reduce and a scan above"); self.sdd_note.setObjectName("muted"); self.sdd_note.setWordWrap(True)
        sddc.body.addWidget(self.sdd_note)
        self.sdd_plot = make_plot(x="energy (eV)", y="normalised (own range)", legend="inside")
        self.sdd_plot.getViewBox().disableAutoRange()
        sddc.body.addWidget(self.sdd_plot, 1)
        self.spike_note = QLabel(""); self.spike_note.setObjectName("muted"); self.spike_note.setWordWrap(True)
        sddc.body.addWidget(self.spike_note)
        bottom.addWidget(fit_height(sddc))             # its wrapped zone/scan/line note keeps its lines at Larger
        i0x = Card("I0 explorer: mesh vs photodiode (any stack, incl. the photodiode tests)",
                   help_anchor="52-the-holder-checks-advanced")
        xrow = QHBoxLayout()
        self.x_scan = QComboBox()
        self.x_scan.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.x_scan.setMinimumContentsLength(12)
        xrow.addWidget(self.x_scan, 1)
        self.x_read_btn = xb = QPushButton("Read"); xb.clicked.connect(self.explore_i0); xrow.addWidget(xb)
        i0x.body.addLayout(xrow)
        self.x_plot = make_plot(x="energy (eV)", y="/ median", legend="inside")        # short: the card is low
        self.x_plot.getViewBox().disableAutoRange()
        i0x.body.addWidget(self.x_plot, 1)
        self.x_note = QLabel(""); self.x_note.setObjectName("muted"); self.x_note.setWordWrap(True); i0x.body.addWidget(self.x_note)
        bottom.addWidget(i0x)
        split.addWidget(bottom)
        self.split = split
        split.setSizes([400, 300, 420])
        # Advanced at 1200 x 760 needs more height than the window has: the page scrolls vertically instead of
        # squeezing the cards below their minimum (never sideways)
        self.page_scroll = page_scroll(split)
        self.body = overlay_host(self.page_scroll)
        root.addWidget(self.body, 1)
        self.empty_state = EmptyState("No project open: the quality checks of each holder's scans appear here.",
                                      [("Open project…", lambda: nav.open_project_dialog(self)),
                                       ("Open the demo beamtime", lambda: nav.open_demo(self))], icon="check",
                                      parent=self.body)
        self.queue_state = EmptyState("No spectra to check yet. In Reduce, draw the region of a sample and press "
                                      "'Add to Check'.", [("Go to Reduce", lambda: nav.go(self, "reduce"))],
                                      icon="check", parent=self.queue)
        self.run_state = EmptyState("The checks have not run on this holder yet.",
                                    [("Run the checks on this holder", self.run_checks)], icon="check", parent=self.table)
        explain_all([(self.run_btn, "check.run"), (self.every_btn, "check.every"), (self.i0_norm, "check.i0_norm"),
                     (self.auto_btn, "check.auto"), (self.x_scan, "check.x_scan"), (self.x_read_btn, "check.x_read"),
                     (self.tick_all_btn, "check.tick_all"), (self.untick_all_btn, "check.untick_all"),
                     (self.save_btn, "check.save"), (self.queue, "check.queue")])
        header_tips(self.table, COL_HELP)
        header_tips(self.queue, QCOL_HELP)
        session.project_changed.connect(self._project_opened)
        session.holder_changed.connect(self._holder_changed)
        session.zone_selected.connect(lambda z: self._update_sdd())
        session.stack_selected.connect(lambda sd: self._update_sdd())
        session.zones_changed.connect(self._update_sdd)
        session.zones_changed.connect(self._zones_changed)
        session.check_queue_changed.connect(self._refresh_queue)
        # AURORA's own verdicts reach new zones' defaults (saturated SDDs off, good-I0 for an invalid I0), and new
        # settings get the coherent de-spike and the default signal before any rule of Reduce applies
        session.add_flag_source(self._flags_for)
        session.add_settings_default(science_defaults)
        providers = getattr(session, "_settings_defaults", None)
        if isinstance(providers, list) and science_defaults in providers:
            providers.remove(science_defaults)
            providers.insert(0, science_defaults)
        on_theme_changed(self, lambda _s: self._theme_changed())
        # Easy: the queue and its preview. Advanced: the holder checks, the good-I0 picker, the four detectors and
        # the I0 explorer (the four cards of the earlier versions, unchanged)
        self.i0_card, self.sdd_card, self.explorer_card = i0c, sddc, i0x
        mark_advanced(self.table_card, self.i0_card, self.sdd_card, self.explorer_card, self.bottom)
        for plot in (self.i0_plot, self.sdd_plot, self.x_plot):     # readable in Advanced (the page scrolls if needed)
            plot.setMinimumHeight(140)
        on_mode_changed(self, self._mode_changed)
        self.update_actions()
        self._draw_i0(); self._update_sdd(); self._draw_x(); self._draw_preview()

    # ------------------------------------------------------------------ state
    def _mode_changed(self, adv: bool) -> None:
        if adv:                                 # room for the queue, the holder table and the three cards
            self.split.setSizes([430, 170, 300])
        self.sub.setText("spectra to check · Looks OK · save to Compare" + (
            " · count rate, saturation, I0 validity per scan · good I0 · the four detectors on a zone" if adv else ""))

    def _holder_busy(self, key: str | None) -> bool:
        m = jobs.of(self)
        return bool(key and m.job(f"check:{key}") is not None) or m.job("check:every") is not None

    def update_actions(self) -> None:
        ses = self.session
        has = ses.project is not None
        h = ses.holder()
        busy = h is not None and self._holder_busy(ses.current_holder)
        name = f"holder {h.name}" if h else "this holder"
        self.run_btn.setText(f"Run the checks on {name}")
        set_enabled(self.run_btn, has and bool(ses.stacks_of()) and not busy,
                    "open a project first" if not has else ("the checks are running" if busy else
                                                            "the holder has no complete stack"))
        any_stack = has and any(ses.stacks_of(k) for k in ses.holder_keys)
        set_enabled(self.every_btn, any_stack and jobs.of(self).job("check:every") is None,
                    "open a project first" if not has else ("the checks are running" if any_stack else
                                                            "no holder has a complete stack"))
        set_enabled(self.x_read_btn, has and self.x_scan.count() > 0, "open a project first" if not has else
                    "the holder has no stack")
        set_enabled(self.auto_btn, bool(self._cands), "run the checks or load the holder's scans first")
        self.table_card.set_title(f"Scans of holder {h.name}" if h else "Scans of the holder")
        self.empty_state.set_shown(not has)
        checked = any(ses.stack_id(s) in self._results for s in ses.stacks_of())
        show_run = has and h is not None and not checked and not busy and bool(ses.stacks_of())
        if show_run:
            self.run_state.set_content(f"The checks have not run on {name} yet.",
                                       [(f"Run the checks on {name}", self.run_checks)])
        self.run_state.set_shown(show_run)
        # the queue
        n = len(self._rows)
        empty_reason = "the queue is empty: send spectra from Reduce with 'Add to Check'"
        set_enabled(self.tick_all_btn, n > 0, empty_reason)
        set_enabled(self.untick_all_btn, n > 0, empty_reason)
        ticked = [r for r in self._rows if r.state == "ready" and r.settings.include]
        saving = jobs.of(self).job("check:save") is not None
        can = has and self.library is not None and bool(ticked) and not saving
        set_enabled(self.save_btn, can, "open a project first" if not has else (
            "saving …" if saving else (empty_reason if not n else ("tick 'Looks OK' for the spectra to save"
                                                                   if not ticked else "Compare is not available"))))
        self.queue_state.set_shown(has and n == 0)

    def _theme_changed(self) -> None:
        refill_tones(self.table)
        refill_tones(self.i0_list)
        refill_tones(self.queue)
        self._draw_i0()
        self._update_sdd()
        self._draw_x()
        self._draw_preview()

    # ------------------------------------------------------------------ persisted checks
    def _flags_for(self, scan) -> list:
        """Flag source registered with the session: this window's verdicts of the scan (fresh or from qc.json)."""
        if scan is None:
            return []
        r = self._results.get(f"{getattr(scan, 'day', '')}_{getattr(scan, 'name', '')}")
        return r.flags if r is not None else []

    def _all_scans(self) -> dict:
        ses = self.session
        return {ses.stack_id(s): s for key in ses.holder_keys for s in ses.stacks_of(key)}

    def _project_opened(self, project) -> None:
        """Read the stored checks (qc.json) and show them without re-running; a background job re-checks the scans
        whose files changed since."""
        self._rates.clear()
        self._rows, self._row_cache, self._probed_holders = [], {}, set()
        self._seen = set(self.session.ui_items(SEEN_KEY))     # the queue is saved with the session and kept by Reload
        self._load_errors = {}
        try:
            self._results = qc.load_qc(project) if project is not None else {}
        except (OSError, ValueError) as err:
            log.warning("checks of %s not read (%s)", getattr(project, "root", "?"), err)
            self._results = {}
        self._queue_dirty = True                # rebuilt when the page is shown
        if self.isVisible():
            self._refresh_queue()
        if self._results:
            log.info("%d stored checks read from %s", len(self._results), qc.qc_path(project))
            stored, scans = dict(self._results), self._all_scans()
            jobs.of(self).submit("check:verify", lambda cb: qc.stale(stored, scans),
                                 on_done=lambda changed, p=project: self._verified(changed, p),
                                 label="looking for scans changed since their check", taskbar=self.taskbar, report=False)
        self.update_actions()

    def _is_open(self, project) -> bool:
        """Whether `project` (the one a job started for) is still the open one: a job that ends after another project
        was opened has its results dropped, never shown or saved for that other project."""
        cur = self.session.project
        if cur is None or project is None or Path(cur.root) != Path(project.root):
            log.info("checks of %s dropped: the open project is now %s", getattr(project, "root", None),
                     getattr(cur, "root", None))
            return False
        return True

    def _verified(self, changed: list, project=None) -> None:
        if not changed or self.session.project is None or (project is not None and not self._is_open(project)):
            return
        holders = sorted({self.session.holder_of_stack(sid) for sid in changed} - {None})
        self.day_label.setText(f"{len(changed)} scan(s) changed since their check: checking them again")
        self._probe(holders, "check:changed", load=False)

    def _save_results(self, results: dict, project=None) -> None:
        project = project if project is not None else self.session.project
        if not results or project is None:
            return
        try:
            qc.save_qc(project, results)
        except OSError as err:
            log.warning("checks not saved: %s", err)
            self.day_label.setText(f"the checks are shown but not saved ({err})")

    def _day_text(self) -> str:
        res = [self._results[self.session.stack_id(s)] for s in self.session.stacks_of()
               if self.session.stack_id(s) in self._results]
        if not res:
            return ""
        with np.errstate(all="ignore"):
            k = np.array([[np.nan if v is None else v for v in r.kcps] for r in res], float)
            kc = np.nanmedian(k, axis=0) if np.isfinite(k).any() else np.full(len(DETS), np.nan)
            lv = np.array([np.nan if r.i0_level is None else r.i0_level for r in res], float)
            i0 = float(np.nanmedian(lv)) if np.isfinite(lv).any() else float("nan")
        when = max(r.probed_at for r in res)
        return ("median over the holder's scans: " + ", ".join(f"{d} {v:.0f} kcps" for d, v in zip(DET_LABELS, kc) if np.isfinite(v))
                + (f" · I0 level {i0:.3g}" if np.isfinite(i0) else "") + f" · checked {when.replace('T', ' ')}"
                + " · references within ±150 eV")

    # ------------------------------------------------------------------ run
    def _holder_changed(self, key: str) -> None:
        self._fill_table()
        self._fill_i0()
        self.x_scan.clear()
        h = self.session.holder(key)
        for s in (h.stacks + h.dropped) if h else []:
            if s.kind == "stack" and s.n_energies > 1:
                self.x_scan.addItem(f"{s.time} {s.label} · {s.status}", s.name)
        self.day_label.setText(self._day_text())
        self.update_actions()

    def explore_i0(self) -> None:
        name = self.x_scan.currentData()
        scan = self.session.project.find(name) if (name and self.session.project) else None
        if scan is None:
            return
        from aurora.core.qc import mcc_curves
        c = mcc_curves(self.session.project, scan)
        self._x_curves = c
        self._draw_x()
        if not c.E.size:
            self.x_note.setText("no mcc data read"); return
        r = c.ratio[np.isfinite(c.ratio)]
        if r.size:
            self.x_note.setText(f"{breakable(scan.name)}: mesh level {np.nanmedian(c.mesh):.3g}, diode level {np.nanmedian(c.diode):.3g}; diode/mesh spans "
                                f"{r.min():.3f}-{r.max():.3f} over the scan"
                                + (" (the mesh has its own spectral response: consider the diode or a good-I0 mean)" if r.max() - r.min() > 0.05 else ""))
        else:
            self.x_note.setText(f"{breakable(scan.name)}: no finite ratio")

    def _draw_x(self) -> None:
        """The I0 explorer curves, with their colours read now (they follow a theme change)."""
        self.x_plot.clear(); self.x_plot.plotItem.legend.clear()
        c = self._x_curves
        if c is None or not c.E.size:
            set_plot_hint(self.x_plot, "choose a stack and Read its I0 curves")
            return
        set_plot_hint(self.x_plot, None)

        def med(a):
            m = np.nanmedian(a) if np.isfinite(a).any() else 1.0
            return m if m else 1.0

        curves = [("mesh mcc1 (I0)", c.mesh / med(c.mesh), pc("i0"), Qt.SolidLine),
                  ("photodiode mcc2", c.diode / med(c.diode), pc("diode"), Qt.DashLine),
                  ("diode / mesh", c.ratio, pc("ratio"), Qt.DotLine), ("TEY mcc4", c.tey / med(c.tey), pc("tey"), Qt.DashDotLine)]
        ys = []
        for label, y, col, st in curves:
            self.x_plot.plot(c.E, y, pen=pg.mkPen(col, width=1.4, style=st), connect="finite", name=label); ys.append(y)
        v = np.concatenate(ys); v = v[np.isfinite(v)]
        if v.size:
            self.x_plot.getViewBox().setRange(xRange=(float(c.E[0]), float(c.E[-1])), yRange=(float(v.min()), float(v.max()) if v.max() > v.min() else float(v.min()) + 1), padding=0.05, disableAutoRange=True)

    def _probe(self, keys, job_key: str, load: bool) -> None:
        """One job: the count-rate probe of every stack of the holders `keys` and the verdicts (energy-local
        references); with load=True the stacks are read as well (the good-I0 list needs them)."""
        ses = self.session
        if ses.project is None:
            return
        holders = {k: list(ses.stacks_of(k)) for k in keys if ses.stacks_of(k)}
        if not holders:
            return
        project = ses.project
        n_scans = sum(len(v) for v in holders.values())

        def job(cb):
            total = (n_scans * (2 if load else 1) + 1) * 100              # per stack: 100 steps of its energies
            res = qc.probe_holders(project, holders, ses.stack_id,
                                   progress=lambda i, n, m: cb(i * 100, total, m))
            if load:
                for i, s in enumerate(s for v in holders.values() for s in v):
                    base = (n_scans + 1 + i) * 100
                    cb(base, total, f"loading {s.name}")
                    try:                                # each energy folder reports: Cancel stops within one
                        ses.stack(ses.stack_id(s), progress=lambda j, m, t="", b=base: cb(b + int(100 * j / max(m, 1)),
                                                                                           total, t))
                    except Exception as err:                    # noqa: BLE001  a damaged stack: listed without I0
                        log.warning("%s not loaded for the good-I0 list (%s)", s.name, err)
            return res

        jobs.of(self).submit(job_key, job, on_done=lambda res, p=project: self._probed(res, load, p),
                             on_failed=lambda t: self.day_label.setText(t.strip().splitlines()[-1]),
                             on_end=lambda _s: self.update_actions(), label="running the checks", taskbar=self.taskbar)
        self.update_actions()

    def run_checks(self) -> None:
        """'Run the checks on holder holderA': probe and load every stack of the current holder."""
        if self.session.project is None or not self.session.stacks_of():
            return
        self._probe([self.session.current_holder], f"check:{self.session.current_holder}", load=True)

    def run_every_holder(self) -> None:
        """'Run the checks on every holder': one job over every holder's stacks (probe only), with progress and Cancel."""
        if self.session.project is None:
            return
        self._probe(list(self.session.holder_keys), "check:every", load=False)

    def _probed(self, results: dict, loaded: bool, project=None) -> None:
        if project is not None and not self._is_open(project):
            return
        ses = self.session
        before = {sid: ses.flags_of(scan) for sid in results if (scan := ses.scan_of(sid)) is not None}
        self._results.update(results)
        self._save_results(results, project)
        self._fill_table(); self._fill_i0(); self._update_sdd()
        self.day_label.setText(self._day_text())
        self._row_cache.clear()
        ses.follow_new_flags(before)        # settings made before these verdicts follow them (unless pinned)
        self._refresh_queue()
        if loaded:
            self.session.touch()        # memberships for the stacks loaded here
        self.update_actions()

    def compute(self) -> None:
        """The verdicts of the current holder from self._rates (count-rate probes done elsewhere): saturation and I0
        validity against the energy-local reference of the holder's other scans, stored in qc.json."""
        scans = {self.session.stack_id(s): s for s in self.session.stacks_of()}
        rates = {sid: cr for sid, cr in self._rates.items() if sid in scans}
        mtimes = {}
        for sid in rates:
            try:
                mtimes[sid] = qc.scan_mtime(scans[sid])
            except OSError:
                continue
        res = qc.verdicts(rates, scans, self.session.current_holder or "", mtimes)
        self._probed(res, loaded=False)

    # ------------------------------------------------------------------ table
    def _fill_table(self) -> None:
        scans = list(self.session.stacks_of())
        self.table.setRowCount(len(scans))
        for r, s in enumerate(scans):
            sid = self.session.stack_id(s)
            res = self._results.get(sid)
            summ = s.summary or {}
            vals = [s.time, s.label, s.edge_text, s.status, "", "", "", "", "", "", "", "", ""]
            if res is not None:
                vals[4] = f"{res.i0_level:.3g}" if res.i0_level is not None else "?"
                vals[5] = "invalid" if res.i0_bad else "ok"
                for j in range(4):
                    vals[6 + j] = f"{res.kcps[j]:.0f}" if res.kcps[j] is not None else "?"
                vals[10] = ", ".join(d.upper() for d in res.saturated)
                vals[11] = ", ".join(d.upper() for d in res.high_rate)
                vals[12] = " ".join(res.notes)[:300]
            elif summ:
                vals[5] = "invalid" if summ.get("i0_bad") else ""
                vals[10] = ", ".join(summ.get("sdd_saturated") or []).upper()
                vals[12] = "from sgm_report: " + " ".join(summ.get("notes", []))[:280]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, sid)
                if c == 5 and v:                      # I0 verdict: icon plus word, never colour alone
                    set_item_tone(it, "bad" if v == "invalid" else "ok")
                elif c in (10, 11) and v:
                    set_item_tone(it, "bad" if c == 10 else "warn")
                if c == 12 and res is not None:
                    it.setToolTip(" ".join(res.notes) + (f"\nchecked {res.probed_at} with AURORA {res.aurora_version}; "
                                                         f"reference: {res.n_ref} scan(s) within ±150 eV"))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()

    def _row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            sid = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
            if self.session.stack_loaded(sid) is not None:
                self.session.select_stack(sid)

    # ------------------------------------------------------------------ I0
    def _fill_i0(self) -> None:
        stacks = {self.session.stack_id(s): self.session.stack_loaded(self.session.stack_id(s)) for s in self.session.stacks_of()}
        stacks = {k: v for k, v in stacks.items() if v is not None}
        labels = {self.session.stack_id(s): f"{s.time} {s.label} · {s.edge_text}" for s in self.session.stacks_of()}
        self._cands = i0_candidates(stacks, labels)
        self.i0_list.blockSignals(True)
        self.i0_list.clear()
        for c in self._cands:
            tone = "ok" if c.looks_good else "warn"
            # two lines: the scan and the verdict, then the numbers behind it (one line elided them in the middle)
            it = QListWidgetItem(f"{c.label}   {status_text(tone)[0]} {'looks good' if c.looks_good else 'check'}\n"
                                 f"level {c.level:.3g} · step {100 * c.max_step:.1f} % · rough {100 * c.rough:.1f} % · "
                                 f"≤0: {c.nonpos}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if self.session.zones.good_i0.get(c.sid) else Qt.Unchecked)
            it.setData(Qt.UserRole, c.sid)
            it.setToolTip(it.text())
            set_item_tone(it, tone, icon=False)       # the check box sits where an icon would
            self.i0_list.addItem(it)
        self.i0_list.blockSignals(False)
        self._draw_i0()

    def _good_toggled(self, item: QListWidgetItem) -> None:
        sid, good = item.data(Qt.UserRole), item.checkState() == Qt.Checked
        if bool(self.session.zones.good_i0.get(sid)) != good:      # a re-coloured row (theme change) is no edit
            with self.session.edit(f"{'mark' if good else 'unmark'} {split_stack_id(sid)[1]} as good I0"):
                self.session.zones.good_i0[sid] = good
        self._draw_i0()

    def _auto_select(self) -> None:
        cands = getattr(self, "_cands", [])
        good_i0 = self.session.zones.good_i0
        if any(c.sid not in good_i0 or bool(good_i0[c.sid]) != bool(c.looks_good) for c in cands):
            with self.session.edit("auto-select good I0"):
                for c in cands:
                    good_i0[c.sid] = c.looks_good
        self._fill_i0()

    def _draw_i0(self) -> None:
        self.i0_plot.clear()
        cands = getattr(self, "_cands", [])
        if not cands:
            set_plot_hint(self.i0_plot, "the I0 of the loaded scans appears here")
            return
        set_plot_hint(self.i0_plot, None)
        xs, ys = [], []
        curves = []
        for i, c in enumerate(cands):
            y = c.curve / c.level if (self.i0_norm.isChecked() and c.level) else c.curve
            good = bool(self.session.zones.good_i0.get(c.sid))
            pen = pg.mkPen(pc("good") if good else curve(i), width=2 if good else 1, style=Qt.SolidLine if good else Qt.DashLine)
            self.i0_plot.plot(c.E, y, pen=pen, connect="finite")
            xs += [float(c.E[0]), float(c.E[-1])]; ys.append(y)
            if good:
                curves.append((c.E, y))
        if curves:
            E = curves[0][0]
            from aurora.core.spectra import good_i0_curve
            m = good_i0_curve(E, curves)
            if m is not None:
                self.i0_plot.plot(E, m, pen=role_pen("ink", 2.5), connect="finite")
        vals = np.concatenate(ys); vals = vals[np.isfinite(vals)]
        if vals.size and xs:
            if self.i0_norm.isChecked():
                lo, hi = max(float(vals.min()), -0.2), min(float(vals.max()), 2.5)
            else:
                lo, hi = float(vals.min()), float(vals.max())
            self.i0_plot.getViewBox().setRange(xRange=(min(xs), max(xs)), yRange=(lo, hi if hi > lo else lo + 1), padding=0.05, disableAutoRange=True)

    # ------------------------------------------------------------------ SDD agreement
    def _update_sdd(self) -> None:
        self.sdd_plot.clear()
        if self.sdd_plot.plotItem.legend is not None:
            self.sdd_plot.plotItem.legend.clear()
        z = self.session.zone()
        sid = self.session.current_stack
        sd = self.session.stack_loaded(sid) if sid else None
        if z is None or sd is None or sid not in z.settings:
            self.sdd_note.setText("select a zone (Reduce) and a scan that covers it")
            self.spike_note.setText("")
            set_plot_hint(self.sdd_plot, "select a zone in Reduce and a scan that covers it")
            return
        st = z.settings[sid]
        if st.i0_source == "good" and not self.session.ensure_good_i0(self, self._update_sdd, taskbar=self.taskbar):
            self.sdd_note.setText("reading the good-I0 scans …")       # drawn again when they are in
            set_plot_hint(self.sdd_plot, "reading the good-I0 scans …")
            return
        set_plot_hint(self.sdd_plot, None)
        pix = members(z, sd.x, sd.y, sd.pitch)
        good = self.session.good_curve_for(sd.E) if st.i0_source == "good" else None
        t = zone_traces(sd, pix, st, good)
        if t is None:
            self.sdd_note.setText("no pixel of this scan inside the zone")
            return
        res = self._results.get(sid)
        scan = self.session.scan_of(sid)                  # None for a stitched stack
        sat = list(res.saturated) if res is not None else (((scan.summary or {}) if scan is not None else {}).get("sdd_saturated") or [])
        c = sdd_consistency(t, sat)
        self.sdd_note.setText(f"{z.label} · {breakable(split_stack_id(sid)[1])} · {t.n_pix} px, line {t.line}: {c.note}")
        if c.normalised is not None:
            xs = (float(c.energies[0]), float(c.energies[-1]))
            for k in range(4):
                flagged = any(d == DETS[k] for d, _ in c.flags)
                pen = pg.mkPen(curve(k), width=2 if flagged else 1, style=dash(k))
                self.sdd_plot.plot(c.energies, c.normalised[:, k], pen=pen, name=DET_LABELS[k] + (" !" if flagged else ""))
            vals = c.normalised[np.isfinite(c.normalised)]
            self.sdd_plot.getViewBox().setRange(xRange=xs, yRange=(float(vals.min()), float(vals.max())), padding=0.05, disableAutoRange=True)
        sp = spike_summary(t)
        self.spike_note.setText(("spikes interpolated (k = %g): " % st.k + ", ".join(f"{k} {v}" for k, v in sp.items())) if sp else
                                ("de-spiking off" if not st.despike else "no spikes found"))

    # ------------------------------------------------------------------ the 'Spectra to check' queue
    def _zone_of(self, item: CheckItem):
        hz = self.session.zones.holders.get(item.holder)
        return next((z for z in hz.zones if z.id == item.zone_id), None) if hz else None

    def _zones_changed(self) -> None:
        """A zone edit: queued rows whose zone or settings are gone leave the queue at once; the others are recomputed
        now when the page is shown, else when it is shown next (a brush stroke in Reduce is an edit too)."""
        ses = self.session
        if not ses.check_queue:
            return
        gone = [it for it in ses.check_queue
                if (z := self._zone_of(it)) is None or it.sid not in z.settings]
        if gone and not self._filling:
            ses.unqueue_for_check(gone)                 # check_queue_changed refreshes the rows
            return
        if self._defer or self._filling or not self.isVisible():
            self._queue_dirty = True
            return
        self._refresh_queue()

    def _refresh_if_dirty(self) -> None:
        if self._queue_dirty:
            self._refresh_queue()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if self._queue_dirty:
            self._refresh_queue()

    def _row_key(self, it: CheckItem, zone, st, sd, flags) -> tuple:
        geo = json.dumps({k: v for k, v in zone.to_json().items() if k in ("rects", "erects", "paint", "erase", "polys")},
                         sort_keys=True)
        good = tuple(sorted(k for k, v in self.session.zones.good_i0.items() if v)) if st.i0_source == "good" else ()
        return (it, id(sd), geo, json.dumps(st.to_json(), sort_keys=True, default=str), good,
                tuple(str(f) for f in flags))

    def _row_for(self, it: CheckItem, zone, st) -> QueueRow:
        ses = self.session
        sd = ses.stack_loaded(it.sid)
        row = QueueRow(item=it, zone=zone, settings=st)
        if sd is None:
            if ses.scan_of(it.sid) is None:
                row.state, row.error = "error", "the scan of this row is not in the project"
                return row
            if it.sid in self._load_errors:
                row.state, row.error = "error", f"the stack could not be read: {self._load_errors[it.sid]}"
                return row
            row.state = "loading"
            jobs.of(self).submit(f"stack:{it.sid}", lambda cb, s=it.sid: ses.stack(s, progress=cb),
                                 on_done=lambda _sd: self._refresh_queue(),
                                 on_failed=lambda t, s=it.sid: self._stack_failed(s, t), label="reading the stack",
                                 taskbar=self.taskbar)
            return row
        row.n_total = sd.n_pix
        if st.i0_source == "good" and not ses.ensure_good_i0(self, self._refresh_queue, taskbar=self.taskbar):
            row.state = "loading"                         # the good-I0 scans are read in a job, then the row
            return row
        scan = ses.scan_of(it.sid)
        flags = ses.flags_of(scan) if scan is not None else []
        key = self._row_key(it, zone, st, sd, flags)
        hit = self._row_cache.get(key)
        if hit is not None:
            row.frame, row.messages = hit
            return row
        good = ses.good_curve_for(sd.E) if st.i0_source == "good" else None
        args = dict(project=ses.project, scan=scan, good_curve=good)
        if st.roi:                                     # per-detector windows: read from the raw folders, off the GUI thread
            row.state = "loading"
            z2, st2 = copy.deepcopy(zone), copy.deepcopy(st)
            jobs.of(self).submit(f"checkrow:{it.holder}:{it.zone_id}:{it.sid}",
                                 lambda cb: export.john_frame(z2, it.sid, sd, st2, progress=cb, **args),
                                 on_done=lambda fr, k=key, f=flags, s=sd, p=ses.project: self._frame_done(k, fr, s, f, p),
                                 label="reading the detector windows", taskbar=self.taskbar)
            return row
        try:
            fr = export.john_frame(zone, it.sid, sd, st, **args)
        except (OSError, ValueError) as err:
            row.state, row.error = "error", str(err)
            return row
        row.frame, row.messages = fr, qc.check_zone(fr.traces, sd, flags)
        self._row_cache[key] = (row.frame, row.messages)
        return row

    def _stack_failed(self, sid: str, details: str) -> None:
        lines = [ln for ln in (details or "").strip().splitlines() if ln.strip()]
        self._load_errors[sid] = lines[-1] if lines else "unknown error"
        self._refresh_queue()

    def _frame_done(self, key, fr, sd, flags, project=None) -> None:
        if project is not None and not self._is_open(project):
            return
        self._row_cache[key] = (fr, qc.check_zone(fr.traces, sd, flags))
        self._refresh_queue()

    def _refresh_queue(self) -> None:
        """Rebuild the queue rows from session.check_queue: frames and messages (cached), the first verdict of a new
        row (a 'bad' one starts unticked), the table, the preview. Rows whose zone or settings are gone leave."""
        if self._filling:
            self._queue_dirty = True
            return
        self._filling = True
        self._queue_dirty = False
        try:
            ses = self.session
            rows, gone = [], []
            for it in list(ses.check_queue):
                z = self._zone_of(it)
                st = z.settings.get(it.sid) if z is not None else None
                if z is None or st is None:
                    gone.append(it)
                    continue
                rows.append(self._row_for(it, z, st))
            if gone:
                ses.unqueue_for_check(gone)
            self._rows = rows
            first_bad = [r for r in rows if r.state == "ready" and r.item not in self._seen and r.level == "bad"
                         and r.settings.include]
            for r in rows:
                if r.state == "ready":
                    self._seen.add(r.item)
            seen = [[it.holder, it.zone_id, it.sid] for it in ses.check_queue if it in self._seen]
            if seen != (ses.ui_value(SEEN_KEY) or []):          # an empty queue on a fresh project writes nothing
                ses.set_ui_value(SEEN_KEY, seen)
        finally:
            self._filling = False
        if first_bad:                           # the rows may span holders: one undo step restores every one
            with ses.edit(f"untick {len(first_bad)} spectra to check with a problem", holder=ALL_HOLDERS):
                for r in first_bad:
                    r.settings.include = False
        self._fill_queue()
        self.update_actions()
        self._auto_probe()

    def _auto_probe(self) -> None:
        """A queued scan whose holder was never checked (no sgm_report summary, no qc.json entry): run the holder's
        count-rate probe once in the background, so the messages name saturated SDDs and an invalid I0."""
        ses = self.session
        if ses.project is None:
            return
        todo = set()
        for r in self._rows:
            scan = ses.scan_of(r.item.sid)
            if scan is None or (scan.summary or {}) or r.item.sid in self._results:
                continue
            if r.item.holder in ses.holder_keys and r.item.holder not in self._probed_holders:
                todo.add(r.item.holder)
        for key in sorted(todo):
            if self._holder_busy(key):
                continue
            self._probed_holders.add(key)
            self._probe([key], f"check:{key}", load=False)

    def _scan_text(self, sid: str) -> str:
        scan = self.session.scan_of(sid)
        t = short_time(sid)
        return f"{t} {scan.label}" if scan is not None else (t or split_stack_id(sid)[1])

    def _messages_widget(self, row: QueueRow) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 1, 4, 1)
        lay.setSpacing(4)
        if row.state == "loading":
            chip = Chip(f"{status_text('muted')[0]} reading", "muted")
            chip.setToolTip("the stack is being read")
            lay.addWidget(chip)
            text = "reading the stack …"
        elif row.state == "error":
            chip = Chip(" ".join(status_text("bad")), "bad")
            chip.setToolTip(row.error)
            lay.addWidget(chip)
            text = row.error
        else:
            for level in ("bad", "warn", "ok"):
                msgs = [m for lv, m in row.messages if lv == level]
                if not msgs:
                    continue
                icon, word = status_text(level)
                chip = Chip(f"{icon} {word}", level)
                chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
                chip.setToolTip("\n".join(msgs))
                chip.setAccessibleName(f"{word}: " + "; ".join(msgs))
                lay.addWidget(chip)
            first = next((m for lv, m in row.messages if lv == "bad"), None) or \
                next((m for lv, m in row.messages if lv == "warn"), None) or "no problem found"
            text = first
        lab = ElidedLabel(text, Qt.ElideRight)
        lab.setObjectName("muted")
        lay.addWidget(lab, 1)
        w.setToolTip("\n".join(f"{status_text(lv)[0]} {m}" for lv, m in row.messages) or text)
        return w

    def _fill_queue(self) -> None:
        sel = self._selected_item()
        self.queue.blockSignals(True)
        self.queue.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            it, z, st = row.item, row.zone, row.settings
            fr = row.frame
            pts = f"{fr.n} / {fr.N}" if fr is not None else "…"
            bad = str(int(fr.bad.sum())) if fr is not None else "…"
            vals = [z.label, self._scan_text(it.sid), pts, bad, "", ""]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if c == 0:
                    cell.setData(Qt.UserRole, r)
                    cell.setToolTip(f"{z.label} · holder {it.holder} · zone {z.id}")
                if c == 1:
                    cell.setToolTip(split_stack_id(it.sid)[1])
                if c == 3 and fr is not None:
                    cell.setToolTip(self._bad_tip(fr))
                if c == Q_OK:
                    cell.setFlags((cell.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
                    cell.setCheckState(Qt.Checked if st.include else Qt.Unchecked)
                    cell.setText("looks OK" if st.include else "")
                    cell.setToolTip("Looks OK: the spectrum is saved and goes to the report (the scan's include tick)")
                self.queue.setItem(r, c, cell)
            self.queue.setCellWidget(r, Q_MSG, self._messages_widget(row))
        self.queue.blockSignals(False)
        for c in (0, 1, 2, 3, Q_OK):
            self.queue.resizeColumnToContents(c)
        if self._rows:
            idx = next((i for i, row in enumerate(self._rows) if row.item == sel), 0)
            self.queue.selectRow(idx)
        self._draw_preview()

    @staticmethod
    def _bad_tip(fr: export.JohnFrame) -> str:
        parts = []
        if fr.spikes_ev:
            parts.append("spikes: " + "; ".join(f"{k.upper() if k.startswith('sdd') else k} {', '.join(f'{e:.2f}' for e in v)} eV"
                                                 for k, v in fr.spikes_ev.items()))
        if fr.low_ev:
            parts.append("I0 below 20 % of its median: " + ", ".join(f"{e:.2f}" for e in fr.low_ev) + " eV")
        if fr.incomplete_ev:
            parts.append("incomplete: " + ", ".join(f"{e:.2f}" for e in fr.incomplete_ev) + " eV")
        return "\n".join(parts) or "no bad points"

    def _selected_item(self) -> CheckItem | None:
        rows = self.queue.selectionModel().selectedRows() if self.queue.selectionModel() else []
        if rows and 0 <= rows[0].row() < len(self._rows):
            return self._rows[rows[0].row()].item
        return None

    def _selected_row(self) -> QueueRow | None:
        it = self._selected_item()
        return next((r for r in self._rows if r.item == it), None) if it is not None else None

    def _queue_item_changed(self, cell: QTableWidgetItem) -> None:
        if cell.column() != Q_OK or not 0 <= cell.row() < len(self._rows):
            return
        row = self._rows[cell.row()]
        on = cell.checkState() == Qt.Checked
        if bool(row.settings.include) != on:
            # the edit notifies zones_changed; the rows are rebuilt after this signal, never inside it (the item that
            # emitted it would be deleted under Qt)
            self._defer = True
            try:
                with self.session.edit(f"mark {short_time(row.item.sid) or row.item.sid} in zone {row.zone.id} as "
                                       f"{'looks OK' if on else 'not OK'}", holder=row.item.holder):
                    row.settings.include = on
            finally:
                self._defer = False
            self._queue_dirty = True
            QTimer.singleShot(0, self, self._refresh_if_dirty)
        self.update_actions()

    def set_all_ok(self, on: bool) -> None:
        """Tick all / Untick all: every row's 'Looks OK' in one edit."""
        rows = [r for r in self._rows if bool(r.settings.include) != on]
        if rows:                                # the rows may span holders: one undo step restores every one
            with self.session.edit(f"{'tick' if on else 'untick'} all {len(self._rows)} spectra to check",
                                   holder=ALL_HOLDERS):
                for r in rows:
                    r.settings.include = on
        self._fill_queue()
        self.update_actions()

    def queue_rows(self) -> list[dict]:
        """The rows as data (tests, self-test): sample, scan, points, bad points, levels, looks_ok."""
        out = []
        for r in self._rows:
            out.append({"sample": r.zone.label, "sid": r.item.sid, "state": r.state,
                        "points": (r.frame.n, r.frame.N) if r.frame is not None else None,
                        "bad": int(r.frame.bad.sum()) if r.frame is not None else None,
                        "levels": sorted({lv for lv, _ in r.messages}), "messages": list(r.messages),
                        "looks_ok": bool(r.settings.include)})
        return out

    def _queue_menu(self, pos: QPoint) -> None:
        cell = self.queue.itemAt(pos)
        if cell is None or not 0 <= cell.row() < len(self._rows):
            return
        row = self._rows[cell.row()]
        m = QMenu(self)
        m.addAction("Show the region in Reduce", lambda: nav.go(self, "reduce", holder=row.item.holder,
                                                                zone=row.item.zone_id, sid=row.item.sid))
        m.addAction("Remove from the queue", lambda: self.session.unqueue_for_check([row.item]))
        m.exec(self.queue.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ preview (John's five views)
    def _draw_preview(self) -> None:
        p = self.qplot
        p.clear()
        if p.plotItem.legend is not None:
            p.plotItem.legend.clear()
        row = self._selected_row()
        fr = row.frame if row is not None else None
        if fr is None:
            set_plot_hint(p, "select a spectrum in the queue" if self._rows else "the spectra sent from Reduce appear here")
            self.msg_label.setText(row.error if row is not None and row.state == "error" else "")
            self._fit_messages()
            return
        set_plot_hint(p, None)
        view = self.view_combo.currentData()
        E = fr.E
        ticked = [DETS.index(d) for d in fr.dets_in_total]
        ys = []

        def line(y, colour, name, style=Qt.SolidLine, width=1.6):
            y = np.asarray(y, float)
            p.plot(E, y, pen=pg.mkPen(colour, width=width, style=style), connect="finite", name=name)
            ys.append(y)

        with np.errstate(all="ignore"):
            if view == "signals":
                for k in range(len(DETS)):
                    on = k in ticked
                    line(fr.det[:, k], curve(k), DET_LABELS[k] + ("" if on else " (off)"), dash(k), 1.6 if on else 0.8)
                ylab = "mean counts" if fr.mode == "mean" else "summed counts"
            elif view == "sum":
                line(fr.total, pc("sum"), "sum of the ticked SDDs", width=2.0)
                bad = fr.bad & np.isfinite(fr.total)
                measured = np.nansum(fr.det_measured[:, ticked], axis=1) if ticked else fr.total
                if bad.any():
                    p.addItem(pg.ScatterPlotItem(E[bad], measured[bad], size=8, symbol="x", brush=pg.mkBrush(pc("spike")),
                                                 pen=pg.mkPen(pc("spike"), width=1.4), name="bad points (as measured)"))
                    ys.append(measured[bad])
                ylab = "counts"
            elif view == "i0tey":
                line(_by_max(fr.i0), pc("i0"), "I0 / max")
                line(_by_max(fr.tey), pc("tey"), "TEY / max", Qt.DashLine)
                ylab = "/ maximum"
            elif view == "ratio":
                line(_by_max(fr.total / fr.i0), pc("sum"), "sum / I0 (÷ max)", width=2.0)
                line(_by_max(fr.tey / fr.i0), pc("tey"), "TEY / I0 (÷ max)", Qt.DashLine)
                ylab = "/ I0, divided by the maximum"
            else:
                line(_minmax(fr.total / fr.i0), pc("sum"), "sum / I0", width=2.0)
                line(_minmax(fr.tey / fr.i0), pc("tey"), "TEY / I0", Qt.DashLine)
                ylab = "/ I0, min-max scaled"
        p.plotItem.setLabel("left", ylab)
        vals = np.concatenate([y[np.isfinite(y)] for y in ys]) if ys else np.array([])
        if vals.size:
            lo, hi = float(vals.min()), float(vals.max())
            p.getViewBox().setRange(xRange=(float(E[0]), float(E[-1])) if len(E) > 1 else None,
                                    yRange=(lo, hi if hi > lo else lo + 1), padding=0.05, disableAutoRange=True)
        self.msg_label.setText(f"{row.zone.label} · {self._scan_text(row.item.sid)} · {fr.n} of {fr.N} points · "
                               f"{fr.source}\n" + "\n".join(f"{status_text(lv)[0]} {m}" for lv, m in row.messages))
        self._fit_messages()

    def _fit_messages(self) -> None:
        """The message box as tall as its text, up to six lines (it scrolls beyond)."""
        width = max(self.msg_scroll.viewport().width(), 200)
        line_h = self.fontMetrics().lineSpacing()
        need = self.msg_label.heightForWidth(width) if self.msg_label.text() else line_h
        self.msg_scroll.setFixedHeight(min(max(need, line_h) + 6, line_h * 6 + 6))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_messages()

    # ------------------------------------------------------------------ save ticked -> Compare
    def ask_overwrite(self, paths: list[Path]) -> str:
        """'yes' (overwrite), 'no' (skip those spectra) or 'cancel'. Tests replace this method."""
        names = ", ".join(p.name for p in paths[:4]) + (f" and {len(paths) - 4} more" if len(paths) > 4 else "")
        b = QMessageBox.question(self, "Files already exist",
                                 f"{len(paths)} file(s) already exist ({names}). Overwrite them?\n"
                                 "No skips those spectra.", QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                                 QMessageBox.No)
        return {QMessageBox.Yes: "yes", QMessageBox.No: "no"}.get(b, "cancel")

    @staticmethod
    def save_failure_text(failed: list) -> str:
        """One calm sentence for the spectra Save ticked could not write: the file that refused (never a temporary
        name), the system's reason and the next step."""
        label, err = failed[0]
        name = Path(err.filename).name if getattr(err, "filename", None) else label
        text = write_error_text(name, err, "save")
        if held_file(err):
            text += " Close it and press Save ticked again."
        text += f" The files of '{label}' were left as they were."
        if len(failed) > 1:
            text += " Not saved either: " + ", ".join(f"'{lab}'" for lab, _err in failed[1:]) + "."
        return text

    def save_ticked(self) -> None:
        """John's 'Save ticked scans' then 'Add saved scans to the Compare tab': every ticked row without a problem is
        written in his formats to <project>/processed/reduced/<sample>/ (write_john), then each folder is added to
        the Compare library."""
        ses = self.session
        if ses.project is None:
            return
        ticked = [r for r in self._rows if r.state == "ready" and r.settings.include and r.frame is not None]
        if not ticked:
            self.queue_note.setText("Tick 'Looks OK' for the spectra to save.")
            return
        if any(r.settings.i0_source == "good" for r in ticked) \
                and not ses.ensure_good_i0(self, self.save_ticked, taskbar=self.taskbar):
            self.queue_note.setText("reading the good-I0 scans first: the spectra are saved once they are in")
            return
        bad = [r for r in ticked if r.level == "bad"]
        good = [r for r in ticked if r.level != "bad"]

        def key(label: str) -> str:                             # Windows and macOS: '1 alpha' and '1 ALPHA' are one name
            return export.safe_label(label).casefold()
        counts: dict[str, int] = {}
        for r in good:
            counts[key(r.zone.label)] = counts.get(key(r.zone.label), 0) + 1
        plan, taken = [], set()
        for r in good:
            label = r.zone.label
            if counts[key(label)] > 1:                          # one sample on two scans (or two holders): unique names
                label = f"{label}_{r.item.holder}_{(short_time(r.item.sid) or '').replace(':', '')}"
            first, n = label, 2
            while key(label) in taken:                          # still one name (two such zones on one scan): numbered
                label, n = f"{first}_{n}", n + 1
            taken.add(key(label))
            sd = ses.stack_loaded(r.item.sid)
            out = export.default_folder(ses.project, label)
            pitch = getattr(sd, "pitch", None)
            # the four files, and the other ROI file of an earlier save, which the overwrite removes
            plan.append((r, label, out, export.target_paths(out, label, r.zone, pitch)
                         + [export.other_roi_path(out, label, r.zone, pitch)], sd))
        existing = [p for *_x, targets, _sd in plan for p in targets if p.exists()]
        if existing:
            ans = self.ask_overwrite(existing)
            if ans == "cancel":
                self.queue_note.setText("nothing saved")
                return
            if ans == "no":
                plan = [x for x in plan if not any(p.exists() for p in x[3])]
        notes = [f"'{r.zone.label}' ({self._scan_text(r.item.sid)}) has a problem and was not saved" for r in bad]
        unnamed = [lab for lab in dict.fromkeys(x[0].zone.label for x in plan) if DEFAULT_LABEL.match(lab)]
        if unnamed:
            notes.append(", ".join(f"'{lab}'" for lab in unnamed) + " saved under the default name: name the sample in "
                         "Reduce's sample box")
        if not plan:
            self.queue_note.setText("; ".join(notes) or "nothing to save")
            return
        project = ses.project
        tasks = []
        for r, label, out, _targets, sd in plan:
            scan = ses.scan_of(r.item.sid)
            survey = None
            h = ses.holder(r.item.holder)
            if h is not None and h.survey is not None:
                survey = h.survey.h5
            good_curve = ses.good_curve_for(sd.E) if r.settings.i0_source == "good" else None
            tasks.append(dict(zone=copy.deepcopy(r.zone), sid=r.item.sid, stack=sd, settings=copy.deepcopy(r.settings),
                              out_dir=out, label=label, project=project, scan=scan, survey_h5=survey, overwrite=True,
                              good_curve=good_curve, holder=r.item.holder,
                              frame=r.frame))

        def job(cb):
            written, failed = [], []
            for i, t in enumerate(tasks):
                cb(i, len(tasks), f"writing {t['label']}")
                try:
                    written.append(export.write_john(**t))
                except OSError as err:              # open in another program, a read-only or protected folder: the
                    failed.append((t["label"], err))    # sample's files stay as they were, the others are still saved
            return written, failed

        def done(result):
            written, failed = result
            folders = []
            for paths in written:
                if paths and paths[0].parent not in folders:
                    folders.append(paths[0].parent)
            n_added = 0
            if self.library is not None:
                for f in folders:
                    n_added += len(self.library.add_folder(f))
            flat = [p for paths in written for p in paths]
            parts = [f"{plural(len(written), 'spectrum')} saved in John Bussey's format and added to Compare "
                     f"({n_added} rows)"] if written else []
            if failed:
                parts.append("not saved: " + ", ".join(f"'{lab}'" for lab, _err in failed))
            msg = "; ".join(parts + notes)
            if flat:
                notify_saved(self.queue_note, flat)                 # 'wrote … → folder · Open folder' (rich text)
                self.queue_note.setText(html.escape(msg) + "<br>" + self.queue_note.text())
            else:
                self.queue_note.setText(msg)
            self.update_actions()
            if failed:
                QMessageBox.warning(self, "Could not save", self.save_failure_text(failed))

        jobs.of(self).submit("check:save", job, on_done=done,
                             on_failed=lambda t: self.queue_note.setText("not saved: " + t.strip().splitlines()[-1]),
                             on_end=lambda _s: self.update_actions(), label="saving the ticked spectra",
                             taskbar=self.taskbar)
        self.update_actions()
