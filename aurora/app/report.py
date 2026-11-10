"""Report perspective: check before building, build the group-organised PDF from the session's zones into a new
folder, preview its pages, open the folder.

- Options: title, output folder, the author line; in Advanced the Draft / Final preset, the PNG dpi and the
  appendices. Each build goes into its own folder <output>/<YYYY-mm-dd_HHMM>_<scope>/.
- Check before building: a totals line '14 zones · 31 spectra · 3 warnings' and three tabs: Findings (the pre-flight,
  aurora.core.report.plan, with a 'Show' jump to Reduce and an 'Ignore' tick remembered with the session as
  'preflight_ignored'), Contents (group ▸ holder ▸ zone with the edge and the number of included scans; a zone left
  out carries a warning icon and the reason) and, in Advanced, Scope (the groups, and the holders by day with
  'Current holder' in one click). The check runs in the background (job 'preflight') whenever the zones or the
  scope change while the perspective is shown.
- The author is the person who builds: Settings > report author, else the OS account's full name, else a one-time
  prompt (blank = no author line).
"""
from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import QRect, QSize, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSpinBox,
                               QSplitter, QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from aurora.app import jobs, nav
from aurora.app.help_text import explain, explain_all, header_tips, labelled, say
from aurora.app.mode import is_advanced, mark_advanced, mark_advanced_tab, mark_easy, on_mode_changed
from aurora.app.session import Session
from aurora.app.vocab import plural
from aurora.app.widgets import (Card, ElidedLabel, EmptyState, TaskBar, default_dir, overlay_host, remember_dir,
                                set_enabled)
from aurora.core import report as core_report
from aurora.core.io.config import ZonesFile
from aurora.core.io.textio import read_json
from aurora.core.report import LATEST, PRESETS, UNGROUPED, Plan, ReportBuilder, ReportResult, group_key, group_rank

NO_PROJECT = "open a project first"
IGNORED_KEY = "preflight_ignored"               # session ui value: ids of the findings ticked 'ignore'
PROMPTED_KEY = "report_author_prompted"         # settings: the one-time author prompt was shown
PRESET_ITEMS = (("Final", "final"), ("Draft", "draft"))
CHECK_DELAY_MS = 400                            # debounce of the check after an edit


def _in_or_above(path, folder: Path) -> bool:
    """True when `path` is `folder`, inside it or one of its parents: a write error of the report's own folder."""
    if not path:
        return False
    try:
        p, f = Path(path).resolve(), Path(folder).resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        return False
    return p == f or f in p.parents or p in f.parents


OUT_READ_ONLY = "The report folder cannot be written: choose another with Browse…"
KIND_ROLE = int(Qt.UserRole) + 21               # the kind of a parent row of the Findings tree
# what several findings of one kind mean, in plain words ('{n}' is their number; the kind title is the row's tooltip)
FINDING_PLAIN = {
    "uncovered_stack": "{n} measurements (complete stacks) have no sample outlined: they will not be in the report",
    "no_included": "{n} samples have no scan kept for the report: they are left out",
    "no_pixels": "{n} outlines hold no pixel of a scan kept for them: that scan is left out",
    "no_settings": "{n} scans cover an outline but were never sent to Check: they are left out",
    "saturated_ticked": "{n} spectra still add a saturated detector",
    "good_i0_empty": "{n} scans ask for the average beam of the good scans, but no scan is marked good (Advanced: "
                     "Check)",
    "scan_missing": "{n} scans named in the outlines are not in this beamtime",
    "ungrouped": "{n} samples have no group (groups come from the sample sheet)",
    "label_not_in_sheet": "{n} sample names are not in the sample sheet",
    "duplicate_label": "{n} sample names appear on several holders of one group",
    "sheet_row_no_zone": "{n} samples of the sample sheet have no outline yet",
}


def finding_plain(kind: str, n: int) -> str:
    """The parent row of n findings of one kind ('12 measurements (complete stacks) have no sample outlined: …')."""
    text = FINDING_PLAIN.get(kind)
    if text is None:
        return f"{n} × {core_report.FINDING_KINDS.get(kind, kind)}"
    return text.format(n=n)
SUBTITLE = {False: "One PDF with every kept spectrum, one page per sample group.",
            True: "the final report, organised by sample group, from the zones and their settings"}


class ReportPerspective(QWidget):
    built = Signal(str)                   # the folder of a finished build (the getting-started guide)

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._result: ReportResult | None = None
        self._builder: ReportBuilder | None = None
        self._plan: Plan | None = None
        self._plan_stale = True
        self._plan_again = False
        self._findings: dict = {}
        self._page = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Report"); h1.setObjectName("h1")
        self.subtitle = sub = ElidedLabel(SUBTITLE[False], Qt.ElideRight)
        sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub, 1)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        self.left_split = QSplitter(Qt.Vertical)
        left = Card("Options", help_anchor="91-options")
        r1 = QHBoxLayout()
        self.title = QLineEdit(); self.title.setPlaceholderText("AURORA report · <project>")
        r1.addWidget(labelled("title", self.title, "report.title")); r1.addWidget(self.title, 1)
        left.body.addLayout(r1)
        r2 = QHBoxLayout()
        self.out_dir = QLineEdit()
        r2.addWidget(labelled("output", self.out_dir, "report.out")); r2.addWidget(self.out_dir, 1)
        self.browse_btn = b = QPushButton("Browse…"); b.clicked.connect(self._choose_out); r2.addWidget(b)
        left.body.addLayout(r2)
        self.author_line = QLabel(""); self.author_line.setObjectName("muted"); self.author_line.setWordWrap(True)
        left.body.addWidget(self.author_line)
        self.out_note = QLabel(OUT_READ_ONLY); self.out_note.setObjectName("muted"); self.out_note.setWordWrap(True)
        self.out_note.hide()                           # the output folder cannot be written (Build is disabled)
        left.body.addWidget(self.out_note)

        self.scope_box = QWidget()                     # preset, dpi and appendices: Advanced
        sl = QVBoxLayout(self.scope_box); sl.setContentsMargins(0, 0, 0, 0)
        pr = QHBoxLayout()
        self.preset = QComboBox()
        for text, key in PRESET_ITEMS:
            self.preset.addItem(text, key)
        self.dpi = QSpinBox(); self.dpi.setRange(60, 1200); self.dpi.setSingleStep(10); self.dpi.setSuffix(" dpi")
        self.dpi.setValue(PRESETS["final"]["png_dpi"])
        pr.addWidget(labelled("preset", self.preset, "report.preset")); pr.addWidget(self.preset)
        pr.addSpacing(8)
        pr.addWidget(labelled("PNG", self.dpi, "report.dpi")); pr.addWidget(self.dpi)
        pr.addStretch(1)
        sl.addLayout(pr)
        self.appendix = QCheckBox("Appendix A: the four detectors per zone"); self.appendix.setChecked(True)
        self.maps = QCheckBox("Appendix B: plate maps with the zones"); self.maps.setChecked(True)
        sl.addWidget(self.appendix); sl.addWidget(self.maps)
        left.body.addWidget(self.scope_box)
        # groups and holders: an Advanced tab of 'Check before building', next to the contents they decide
        self.scope_page = QWidget()
        lists = QHBoxLayout(self.scope_page); lists.setContentsMargins(6, 6, 6, 6)
        gcol = QVBoxLayout()
        self.groups = QListWidget()
        gl = QLabel("Groups to include:"); gl.setWordWrap(True); gl.setBuddy(self.groups)
        gcol.addWidget(gl)
        gcol.addWidget(self.groups, 1)
        gb = QHBoxLayout()
        self.all_btn = QPushButton("All"); self.all_btn.clicked.connect(lambda: self._tick(True))
        self.none_btn = QPushButton("None"); self.none_btn.clicked.connect(lambda: self._tick(False))
        gb.addWidget(self.all_btn); gb.addWidget(self.none_btn); gb.addStretch(1)
        gcol.addLayout(gb)
        hcol = QVBoxLayout()
        self.holders = QTreeWidget(); self.holders.setHeaderHidden(True); self.holders.setColumnCount(1)
        hl = QLabel("Holders (by day):"); hl.setWordWrap(True); hl.setBuddy(self.holders)
        hcol.addWidget(hl)
        hcol.addWidget(self.holders, 1)
        hb = QHBoxLayout()
        self.current_holder_btn = QPushButton("Current holder"); self.current_holder_btn.clicked.connect(self.only_current_holder)
        self.all_holders_btn = QPushButton("All holders"); self.all_holders_btn.clicked.connect(lambda: self._tick_holders(None))
        hb.addWidget(self.current_holder_btn); hb.addWidget(self.all_holders_btn); hb.addStretch(1)
        hcol.addLayout(hb)
        lists.addLayout(gcol, 1); lists.addLayout(hcol, 1)
        self.scope_line = QLabel(""); self.scope_line.setObjectName("muted"); self.scope_line.setWordWrap(True)
        left.body.addWidget(self.scope_line)          # Easy mode: what the report contains, read-only
        self.build_btn = QPushButton("Build the report"); self.build_btn.setObjectName("primary")
        self.build_btn.clicked.connect(self.build)
        left.body.addWidget(self.build_btn)
        self.taskbar = TaskBar(self)
        left.body.addWidget(self.taskbar)
        self.status = QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        left.body.addWidget(self.status)
        left.body.addStretch(0)                        # takes the room of the hidden options in Easy mode only
        self.left_split.addWidget(left)
        self.options_card = left

        check = Card("Check before building", help_anchor="92-check-before-building")
        self.totals = QLabel("no project open"); self.totals.setWordWrap(True)
        check.body.addWidget(self.totals)
        self.check_tabs = QTabWidget()
        self.preflight_list = QTreeWidget()
        self.preflight_list.setColumnCount(3)
        self.preflight_list.setHeaderLabels(["Finding", "Ignore", ""])
        self.preflight_list.setRootIsDecorated(False)
        self.preflight_list.setUniformRowHeights(False)
        self.preflight_list.setWordWrap(True)                  # a finding reads in full; rows grow with the text
        hdr = self.preflight_list.header()
        self._wrap_timer = QTimer(self)
        self._wrap_timer.setSingleShot(True)
        self._wrap_timer.setInterval(0)
        self._wrap_timer.timeout.connect(self._wrap_rows)
        hdr.sectionResized.connect(lambda *_: self._wrap_timer.start())
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.preflight_list.itemChanged.connect(self._ignore_changed)
        self.preflight_list.itemDoubleClicked.connect(lambda it, _c: self._jump_item(it))
        self.contents = QTreeWidget()
        self.contents.setColumnCount(3)
        self.contents.setHeaderLabels(["Group / holder / zone", "Edge", "Scans"])
        ch = self.contents.header()
        ch.setStretchLastSection(False)
        ch.setSectionResizeMode(0, QHeaderView.Stretch)
        ch.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.check_tabs.addTab(self.preflight_list, "Findings")
        self.check_tabs.addTab(self.contents, "Contents")
        self.check_tabs.addTab(self.scope_page, "Scope")
        check.body.addWidget(self.check_tabs, 1)
        self.left_split.addWidget(check)
        self.left_split.setStretchFactor(0, 0); self.left_split.setStretchFactor(1, 1)
        self.left_split.setChildrenCollapsible(False)
        self.left_split.setMinimumWidth(400)
        split.addWidget(self.left_split)

        right = Card("Pages", help_anchor="93-what-a-build-writes")
        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton("◀"); self.prev_btn.clicked.connect(lambda: self._show_page(self._page - 1))
        self.next_btn = QPushButton("▶"); self.next_btn.clicked.connect(lambda: self._show_page(self._page + 1))
        self.page_label = QLabel("no report built yet"); self.page_label.setObjectName("muted")
        self.open_btn = QPushButton("Open folder")
        self.open_btn.clicked.connect(self._open_folder)
        self.pdf_btn = QPushButton("Open PDF")
        self.pdf_btn.clicked.connect(self._open_pdf)
        for w in (self.prev_btn, self.next_btn, self.page_label):
            nav_row.addWidget(w)
        nav_row.addStretch(1); nav_row.addWidget(self.open_btn); nav_row.addWidget(self.pdf_btn)
        right.body.addLayout(nav_row)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setObjectName("pageview")
        self.page_view = QLabel(); self.page_view.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.page_view.setAccessibleName("page preview")
        self.scroll.setWidget(self.page_view)
        right.body.addWidget(self.scroll, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 1); split.setStretchFactor(1, 2)
        split.setSizes([460, 900])
        self.body = overlay_host(split)
        root.addWidget(self.body, 1)
        self.empty_state = EmptyState("No project open: the report is built from the zones drawn in Reduce.",
                                      [("Open project…", lambda: nav.open_project_dialog(self)),
                                       ("Open the demo beamtime", lambda: nav.open_demo(self))], icon="report",
                                      parent=self.body)
        self.zone_state = EmptyState(say("report.zone_state"),
                                     [("Go to Reduce", lambda: nav.go(self, "reduce"))], icon="report", parent=self.body)
        self.groups.setAccessibleName("groups to include")
        self.holders.setAccessibleName("holders to include")
        explain_all([(self.browse_btn, "report.browse"), (self.appendix, "report.appendix"), (self.maps, "report.maps"),
                     (self.groups, "report.groups"), (self.all_btn, "report.all"), (self.none_btn, "report.none"),
                     (self.build_btn, "report.build"), (self.prev_btn, "report.prev"), (self.next_btn, "report.next"),
                     (self.open_btn, "report.open_folder"), (self.pdf_btn, "report.open_pdf"),
                     (self.holders, "report.holders"), (self.current_holder_btn, "report.current_holder"),
                     (self.all_holders_btn, "report.all_holders"), (self.preflight_list, "report.check"),
                     (self.contents, "report.contents")])
        header_tips(self.preflight_list, {0: "report.col.finding", 1: "report.col.ignore", 2: "report.col.show"})
        header_tips(self.contents, {0: "report.col.zone", 1: "report.col.edge", 2: "report.col.scans"})
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(CHECK_DELAY_MS)
        self._check_timer.timeout.connect(self.refresh_check)
        session.project_changed.connect(self._project_changed)
        session.zones_changed.connect(self._fill_groups)
        session.zones_changed.connect(self.update_actions)
        session.zones_changed.connect(self.schedule_check)
        session.holder_changed.connect(self._holder_changed)
        self.groups.itemChanged.connect(self._scope_text)
        self.groups.itemChanged.connect(self.schedule_check)
        self.holders.itemChanged.connect(self._holders_changed)
        self.appendix.toggled.connect(self._scope_text)
        self.maps.toggled.connect(self._scope_text)
        self.dpi.valueChanged.connect(self._scope_text)
        self.preset.currentIndexChanged.connect(self._preset_chosen)
        from aurora.app.settings_dialog import on_settings_changed
        on_settings_changed(self, self._author_text)
        from aurora.app.theme import on_theme_changed
        on_theme_changed(self, lambda _s: self._fill_plan())
        # Easy mode: all groups and both appendices, stated read-only; the choices are Advanced
        mark_advanced(self.scope_box)
        mark_advanced_tab(self.check_tabs, self.scope_page)
        mark_easy(self.scope_line)
        on_mode_changed(self, lambda _a: (self.subtitle.setText(SUBTITLE[is_advanced()]), self._scope_text(),
                                          self._author_text(),
                                          self.zone_state.set_content(say("report.zone_state"),
                                                                      [("Go to Reduce", lambda: nav.go(self, "reduce"))])),
                        call_now=False)
        self.out_dir.textChanged.connect(lambda _t: self.update_actions())
        self._scope_text()
        self.update_actions()

    def has_zones(self) -> bool:
        return any(h.zones for h in self.session.zones.holders.values())

    def update_actions(self) -> None:
        has = self.session.project is not None
        busy = jobs.of(self).job("report") is not None
        zones = self.has_zones()
        blocked = has and self.out_blocked()
        set_enabled(self.build_btn, has and zones and not busy and not blocked,
                    NO_PROJECT if not has else ("the report is being built" if busy else
                                                OUT_READ_ONLY if blocked else "draw a zone in Reduce first"))
        self.out_note.setVisible(blocked)
        built = self._result is not None
        pages = len(self._result.page_pngs) if built else 0
        set_enabled(self.prev_btn, built and self._page > 0, "build the report first" if not built else "this is the first page")
        set_enabled(self.next_btn, built and self._page < pages - 1, "build the report first" if not built else "this is the last page")
        for b in (self.open_btn, self.pdf_btn):
            set_enabled(b, built, "build the report first")
        set_enabled(self.current_holder_btn, has and self.session.current_holder is not None, NO_PROJECT)
        self.empty_state.set_shown(not has)
        self.zone_state.set_shown(has and not zones)

    def out_blocked(self) -> bool:
        """The output folder cannot be written: a protected folder (raw/, AURORA_PROTECT_DIRS) or a refused write."""
        text = self.out_dir.text().strip()
        if not text:
            return False
        from aurora.core.io.safety import is_protected
        out = Path(text)
        try:
            if is_protected(out):
                return True
        except (OSError, ValueError):
            return False
        probe = out
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        import os
        return probe.exists() and not os.access(probe, os.W_OK)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._author_text()
        if self._plan_stale:
            self.schedule_check()

    # ------------------------------------------------------------------ options and scope
    def preset_key(self) -> str:
        return str(self.preset.currentData() or "final")

    def _preset_chosen(self, _i: int = 0) -> None:
        """Final: every holder, both appendices, 600 dpi. Draft: the current holder, no Appendix A or B, 110 dpi."""
        key = self.preset_key()
        p = PRESETS[key]
        self.dpi.setValue(p["png_dpi"])
        self.appendix.setChecked(p["appendix"])
        self.maps.setChecked(p["maps"])
        if key == "draft":
            self.only_current_holder()
        else:
            self._tick_holders(None)
        self._scope_text()

    def _scope_text(self, *_) -> None:
        """What the next build contains, from the (possibly hidden) options in force."""
        n = self.groups.count()
        ticked = len(self.selected_groups())
        groups = "all groups" if ticked == n else f"{ticked} of {n} groups (chosen in Advanced mode)"
        hs = self.selected_holders()
        holders = "every holder" if hs is None else (f"{plural(len(hs), 'holder')} ({', '.join(hs[:4])}"
                                                    + (" …" if len(hs) > 4 else "") + ", chosen in Advanced mode)")
        apps = [name for name, box in (("Appendix A (the four detectors)", self.appendix), ("Appendix B (plate maps)", self.maps))
                if box.isChecked()]
        preset = "Final" if self.preset_key() == "final" else "Draft"
        if is_advanced():
            self.scope_line.setText(f"The report covers {holders} and {groups}"
                                    + (", with " + " and ".join(apps) if apps else ", without Appendix A or B")
                                    + f"; {preset} preset, figures at {self.dpi.value()} dpi.")
            return
        parts = []
        if self.appendix.isChecked():
            parts.append("each detector separately (Appendix A)")
        if self.maps.isChecked():
            parts.append("the holder maps with your outlines (Appendix B)")
        parts.append("the quality notes (Appendix C)")
        then = ", ".join(parts[:-1]) + f" and {parts[-1]}" if len(parts) > 1 else parts[0]
        quality = "print quality" if self.dpi.value() >= 300 else "draft quality"
        self.scope_line.setText(f"The report will hold one page per sample group ({groups}, {holders}) with the spectra "
                                f"ticked Looks OK, then {then}. Figures at {quality} ({self.dpi.value()} dpi). Compare "
                                "and Fit results are not in it: use Export… in Compare and Fit.")
        QTimer.singleShot(0, self, self.fit_options)

    def fit_options(self) -> None:
        """The Options card as tall as its wrapped lines at its width (the splitter ignores word wrap)."""
        from aurora.app.widgets import fit_to_content
        fit_to_content(getattr(self, "options_card", None))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        QTimer.singleShot(0, self, self.fit_options)

    def _project_changed(self, project) -> None:
        self.out_dir.setText(str(project.root / "reports" / "aurora"))
        self._result = None
        self._plan = None
        self._fill_groups()
        self._fill_holders()
        self._fill_plan()
        self._last_build_text()
        self.update_actions()
        self.schedule_check()

    def _last_build_text(self) -> None:
        lat = read_json(Path(self.out_dir.text()) / LATEST, None) if self.out_dir.text().strip() else None
        last = lat.get("latest") if isinstance(lat, dict) else None
        self.status.setText(f"last build in this folder: {last}" if last else "")

    def _fill_groups(self) -> None:
        ticked = {self.groups.item(i).text(): self.groups.item(i).checkState() == Qt.Checked for i in range(self.groups.count())}
        found = {(z.group.strip() or UNGROUPED) for h in self.session.zones.holders.values() for z in h.zones}
        self.groups.blockSignals(True)
        self.groups.clear()
        for g in sorted(found, key=group_key(group_rank(self.session.sheet_rows))):
            it = QListWidgetItem(g)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if ticked.get(g, True) else Qt.Unchecked)
            self.groups.addItem(it)
        self.groups.blockSignals(False)
        self._scope_text()

    def _tick(self, on: bool) -> None:
        for i in range(self.groups.count()):
            self.groups.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def selected_groups(self) -> list[str]:
        return [self.groups.item(i).text() for i in range(self.groups.count()) if self.groups.item(i).checkState() == Qt.Checked]

    def _holder_items(self) -> list[QTreeWidgetItem]:
        out = []
        for i in range(self.holders.topLevelItemCount()):
            day = self.holders.topLevelItem(i)
            out += [day.child(j) for j in range(day.childCount())]
        return out

    def _fill_holders(self) -> None:
        self.holders.blockSignals(True)
        self.holders.clear()
        days: dict[str, QTreeWidgetItem] = {}
        for key, (day, h) in self.session.holder_keys.items():
            top = days.get(day)
            if top is None:
                top = QTreeWidgetItem([day or "no day"])
                top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                top.setData(0, Qt.UserRole, None)
                top.setCheckState(0, Qt.Checked)
                self.holders.addTopLevelItem(top)
                days[day] = top
            it = QTreeWidgetItem([key])
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setData(0, Qt.UserRole, key)
            it.setCheckState(0, Qt.Checked)
            top.addChild(it)
        self.holders.expandAll()
        self.holders.blockSignals(False)
        if self.preset_key() == "draft":
            self.only_current_holder()

    def selected_holders(self) -> list[str] | None:
        """The ticked holder keys; None when every holder is ticked (no restriction)."""
        items = self._holder_items()
        keys = [it.data(0, Qt.UserRole) for it in items if it.checkState(0) == Qt.Checked]
        return None if len(keys) == len(items) else keys

    def _tick_holders(self, keys: list[str] | None) -> None:
        self.holders.blockSignals(True)
        for it in self._holder_items():
            on = keys is None or it.data(0, Qt.UserRole) in keys
            it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.holders.blockSignals(False)
        self._holders_changed()

    def only_current_holder(self) -> None:
        """Tick only the current holder (the top-bar holder): the 'current holder' scope in one click."""
        cur = self.session.current_holder
        if cur is not None:
            self._tick_holders([cur])

    def _holder_changed(self, _key: str) -> None:
        if self.preset_key() == "draft":
            self.only_current_holder()
        self.update_actions()

    def _holders_changed(self, *_) -> None:
        self._scope_text()
        self.schedule_check()

    def _choose_out(self) -> None:
        cur = Path(self.out_dir.text()) if self.out_dir.text().strip() else None
        start = str(cur) if cur is not None and cur.is_dir() else default_dir("report", self.session)
        d = QFileDialog.getExistingDirectory(self, "Report output folder", start)
        if d:
            remember_dir("report", Path(d))
            self.out_dir.setText(d)
            self._last_build_text()

    # ------------------------------------------------------------------ author
    def author(self) -> tuple[str, str]:
        """(name, where it comes from) without asking: Settings, else the OS account's full name."""
        from aurora.app.settings_dialog import get
        return core_report.resolve_author(get("report_author"))

    def _author_text(self) -> None:
        name, source = self.author()
        if name and not is_advanced():
            self.author_line.setText(f"Author: {name} (Settings > report author)" if source == "Settings" else
                                     f"Author: {name} (from your computer account; change it in Settings > report "
                                     "author)")
        elif name:
            self.author_line.setText(f"Author: {name}, from {source}")
        else:
            self.author_line.setText("No author line (Settings > report author)")
        self.author_line.setToolTip("Printed in the page footers and the PDF metadata: Settings > report author, "
                                    "else the full name of the system account; blank gives no author line.")

    def resolve_author(self, ask: bool = True) -> str:
        """The report author: Settings, else the OS account's full name, else (once per installation) a prompt.
        A name typed in the prompt is kept in Settings; blank or Cancel builds without an author line."""
        name, _source = self.author()
        if name or not ask:
            return name
        from aurora.app.settings import settings
        s = settings()
        if s.value(PROMPTED_KEY, False) in (True, "true", "True", 1, "1"):
            return ""
        s.setValue(PROMPTED_KEY, True)
        s.sync()
        text, ok = QInputDialog.getText(self, "Report author",
                                        "The name printed in the report footer and the PDF metadata.\n"
                                        "Leave it blank for no author line; Settings > report author changes it later.")
        name = str(text or "").strip() if ok else ""
        if name:
            s.setValue("report_author", name)
            s.sync()
        self._author_text()
        return name

    # ------------------------------------------------------------------ check before building
    def scope(self) -> dict:
        """holders / groups of the next build (None = every holder / every group)."""
        groups = self.selected_groups()
        return {"holders": self.selected_holders(),
                "groups": None if len(groups) == self.groups.count() else groups}

    def ignored(self) -> set[str]:
        return set(self.session.ui_value(IGNORED_KEY, []) or [])

    def schedule_check(self, *_) -> None:
        self._plan_stale = True
        if self.session.project is not None and self.isVisible():
            self._check_timer.start()

    def refresh_check(self) -> None:
        """Run the pre-flight in the background on a snapshot of the zones (only while the perspective is shown)."""
        proj = self.session.project
        if proj is None:
            self._plan = None
            self._fill_plan()
            return
        if not self.isVisible():
            self._plan_stale = True
            return
        self._check_timer.stop()            # a pending debounced check is covered by this one (it would re-run hidden)
        job = jobs.of(self).job("preflight")
        if job is not None and job.is_running():
            self._plan_again = True
            return
        self._plan_stale = False
        zones = ZonesFile.from_json(copy.deepcopy(self.session.zones.to_json()))
        stacks, rows, sc = dict(self.session._stacks), list(self.session.sheet_rows), self.scope()
        jobs.of(self).submit("preflight", lambda cb: core_report.plan(proj, zones, rows, sc["holders"], None, sc["groups"],
                                                                      stacks=stacks, progress=cb),
                             on_done=self._plan_ready, on_failed=self._plan_failed, on_end=self._plan_end,
                             label="checking before building", report=False)

    def _plan_ready(self, plan: Plan) -> None:
        if self.session.project is None:
            return
        self._plan = plan
        self._fill_plan()

    def _plan_failed(self, text: str) -> None:
        from aurora.app.widgets import plain_error
        root = self.session.project.root if self.session.project is not None else None
        self.totals.setText("the check before building could not run: " + plain_error(text, root))

    def _plan_end(self, _state: str) -> None:
        if self._plan_again:
            self._plan_again = False
            self.schedule_check()

    def _fill_plan(self) -> None:
        """Fill the findings, the contents tree and the totals line from the last plan (also after a theme change)."""
        from aurora.app.theme import set_item_tone, status_icon
        plan = self._plan
        ign = self.ignored()
        lst = self.preflight_list
        lst.blockSignals(True)
        lst.clear()
        self.contents.clear()
        if plan is None:
            lst.blockSignals(False)
            self.totals.setText("no project open" if self.session.project is None else "checking …")
            self.check_tabs.setTabText(0, "Findings")
            self.check_tabs.setTabText(1, "Contents")
            return
        order = sorted(plan.findings, key=lambda f: (f.id in ign, f.tone != "warn"))
        # one row per finding; a kind with several findings is one parent row '{n} × what it means' (collapsed when it
        # has more than three), the kinds of the holder on screen first
        cur = self.session.current_holder
        by_kind: dict[str, list] = {}
        for f in order:
            by_kind.setdefault(f.kind, []).append(f)
        kinds = sorted(by_kind, key=lambda k: (all(f.id in ign for f in by_kind[k]),      # ignored kinds last
                                               not any(f.holder == cur for f in by_kind[k]),
                                               [x.kind for x in order].index(k)))
        grouped = any(len(v) > 1 for v in by_kind.values())
        lst.setRootIsDecorated(grouped)
        rows = []
        for kind in kinds:
            fs = by_kind[kind]
            parent = None
            if len(fs) > 1:
                n_open = sum(1 for f in fs if f.id not in ign)
                parent = QTreeWidgetItem([finding_plain(kind, len(fs)), "", ""])
                parent.setToolTip(0, f"{core_report.FINDING_KINDS.get(kind, kind)} ({n_open} not ignored)")
                parent.setIcon(0, status_icon(fs[0].tone if n_open else "muted"))
                parent.setFlags(parent.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsUserCheckable)
                parent.setData(0, Qt.UserRole, None)
                parent.setData(0, KIND_ROLE, kind)
                lst.addTopLevelItem(parent)
            for f in fs:
                rows.append((parent, f))
        for parent, f in rows:
            it = QTreeWidgetItem([f.text, "", ""])
            it.setData(0, Qt.UserRole, f.id)
            it.setToolTip(0, f.text)
            it.setIcon(0, status_icon(f.tone))
            it.setFlags((it.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            it.setCheckState(1, Qt.Checked if f.id in ign else Qt.Unchecked)
            it.setToolTip(1, "ignored: not counted in the warnings" if f.id in ign else "tick to ignore this finding")
            if f.id in ign:
                set_item_tone(it, "muted", 0, icon=False)
            if parent is None:
                lst.addTopLevelItem(it)
            else:
                parent.addChild(it)
            cell = QWidget()                                 # keeps the button at its own height on a tall row
            cl = QVBoxLayout(cell); cl.setContentsMargins(2, 2, 4, 2)
            btn = QToolButton()
            btn.setText("Show")
            explain(btn, "report.jump")
            btn.clicked.connect(lambda _c=False, ff=f: self.jump(ff))
            cl.addWidget(btn); cl.addStretch(1)
            lst.setItemWidget(it, 2, cell)
        for i in range(lst.topLevelItemCount()):
            top = lst.topLevelItem(i)
            if top.childCount():
                top.setExpanded(top.childCount() <= 3)
        lst.blockSignals(False)
        self._wrap_rows()
        self._findings = {f.id: f for f in plan.findings}
        for g, zs in plan.by_group().items():
            gi = QTreeWidgetItem([g, "", str(sum(z.included for z in zs))])
            gi.setToolTip(0, f"{g}: {plural(sum(1 for z in zs if z.spectra), 'zone')} in the report")
            self.contents.addTopLevelItem(gi)
            holders: dict[str, QTreeWidgetItem] = {}
            for z in zs:
                hi = holders.get(z.holder_key)
                if hi is None:
                    hi = QTreeWidgetItem([f"{z.holder} · {z.day}" if z.day else z.holder, "", ""])
                    gi.addChild(hi)
                    holders[z.holder_key] = hi
                zi = QTreeWidgetItem([z.label, ", ".join(z.edges), str(z.included)])
                zi.setData(0, Qt.UserRole, (z.holder_key, z.zone_id))
                if z.skip:
                    zi.setIcon(0, status_icon("warn"))
                    zi.setToolTip(0, f"'{z.label}' is left out: {z.skip}")
                else:
                    zi.setToolTip(0, f"'{z.label}': {plural(z.included, 'scan')} in the report")
                hi.addChild(zi)
            for hi in holders.values():
                hi.setText(2, str(sum(int(hi.child(j).text(2)) for j in range(hi.childCount()))))
        self.contents.expandAll()
        self.totals.setText(plan.totals(ign))
        n_open = len(plan.open_findings(ign))
        self.check_tabs.setTabText(0, f"Findings ({n_open})" if n_open else "Findings")
        self.check_tabs.setTabText(1, f"Contents ({plan.n_zones})")

    def _wrap_rows(self) -> None:
        """Row heights that fit each finding's text wrapped at the width of the Finding column."""
        lst = self.preflight_list
        fm = lst.fontMetrics()
        width = max(60, lst.header().sectionSize(0) - lst.iconSize().width() - 24)
        btn_h = 0
        items = []
        for i in range(lst.topLevelItemCount()):
            top = lst.topLevelItem(i)
            items.append(top)
            items.extend(top.child(j) for j in range(top.childCount()))
        for it in items:
            cell = lst.itemWidget(it, 2)
            if cell is not None:
                btn_h = max(btn_h, cell.sizeHint().height())
            text_h = fm.boundingRect(QRect(0, 0, width, 10000), int(Qt.TextWordWrap), it.text(0)).height()
            it.setSizeHint(0, QSize(width, max(text_h + 10, btn_h)))       # a valid size (width > 0) or Qt ignores it
        lst.doItemsLayout()

    def _ignore_changed(self, item: QTreeWidgetItem, col: int) -> None:
        if col != 1:
            return
        fid = item.data(0, Qt.UserRole)
        if not fid:                                         # a kind's parent row has no tick of its own
            return
        ign = self.ignored()
        if item.checkState(1) == Qt.Checked:
            ign.add(fid)
        else:
            ign.discard(fid)
        self.session.set_ui_value(IGNORED_KEY, sorted(ign))
        QTimer.singleShot(0, self._fill_plan)                # re-sort and recount after the click is handled

    def _jump_item(self, item: QTreeWidgetItem) -> None:
        f = getattr(self, "_findings", {}).get(item.data(0, Qt.UserRole))
        if f is not None:
            self.jump(f)

    def jump(self, f) -> None:
        """Show the finding's holder, zone and scan in Reduce (nav.go)."""
        nav.go(self, "reduce", holder=f.holder, zone=f.zone_id, sid=f.sid)

    # ------------------------------------------------------------------ build
    def build(self) -> None:
        if self.session.project is None:
            return
        groups = self.selected_groups()
        if not groups:
            self.status.setText("tick at least one group")
            return
        holders = self.selected_holders()
        if holders == []:
            self.status.setText("tick at least one holder")
            return
        author = self.resolve_author(ask=True)
        preset = self.preset_key()
        p = PRESETS[preset]
        out = Path(self.out_dir.text()) if self.out_dir.text().strip() else self.session.project.root / "reports" / "aurora"
        # only the options that differ from the preset are passed: the preset decides the others
        extra = {k: v for k, v in (("appendix", self.appendix.isChecked()), ("maps", self.maps.isChecked()),
                                   ("png_dpi", self.dpi.value())) if v != p[k]}
        builder = ReportBuilder(self.session.project, self.session.zones, out,
                                title=self.title.text().strip() or None,
                                groups=None if len(groups) == self.groups.count() else groups, holders=holders,
                                preset=preset, author=author, stacks=dict(self.session._stacks),
                                sheet_rows=list(self.session.sheet_rows), **extra)
        self._builder = builder
        self.status.setText("building …")

        def job(cb):
            try:
                return builder.build(progress=cb)
            except OSError as err:                      # the output folder cannot be written: a calm message
                if not _in_or_above(getattr(err, "filename", None), out):
                    raise
                return err

        def done(result):
            if isinstance(result, OSError):
                self._not_written(out, result)
            else:
                self._on_done(result)
        jobs.of(self).submit("report", job, on_done=done,
                             on_failed=self._on_failed, on_end=lambda _s: self.update_actions(),
                             label="building the report", taskbar=self.taskbar, pause_gc=True)
        self.update_actions()

    def _not_written(self, out: Path, err: OSError) -> None:
        """The report's folder refused the build (read-only, protected, no space): one calm dialog that names it and
        points to Browse…, not the error dialog. The build left nothing behind (ReportBuilder.build)."""
        reason = (getattr(err, "strerror", None) or str(err) or type(err).__name__).strip().rstrip(".")
        text = f"Could not build the report: {out} cannot be written ({reason}). Choose another output folder with Browse…"
        self.status.setText(text)
        QMessageBox.warning(self, "Could not build the report", text)

    def _on_done(self, result: ReportResult) -> None:
        self._result = result
        self.built.emit(str(result.folder or result.pdf.parent))
        ch = ReportBuilder.changes_lines(result.changes, limit=3)
        self.status.setText(f"{result.folder.name if result.folder else result.pdf.parent.name}: "
                            f"{plural(result.pages, 'page')}, {plural(result.entries, 'zone spectrum', 'zone spectra')}, "
                            f"{len(result.csvs)} CSV, {plural(len(result.figures), 'figure')}"
                            + (f"; {len(result.skipped)} skipped" if result.skipped else "")
                            + "\nsince the previous build: " + "; ".join(ch)
                            + "".join(f"\n{n}" for n in result.notes))
        if result.plan is not None:
            self._plan = result.plan
            self._plan_stale = False
            self._fill_plan()
        self._show_page(0)

    def _on_failed(self, text: str) -> None:
        self.status.setText("report failed: " + (text.strip().splitlines() or ["?"])[-1])

    # ------------------------------------------------------------------ preview (the group figures)
    def _show_page(self, i: int) -> None:
        pages = self._result.page_pngs if self._result else []
        if not pages:
            self.page_label.setText("no pages"); return
        i = max(0, min(i, len(pages) - 1))
        self._page = i
        pm = QPixmap(str(pages[i]))
        if not pm.isNull():
            self.page_view.setPixmap(pm.scaledToWidth(min(820, pm.width()), Qt.SmoothTransformation))
        self.page_label.setText(f"page {i + 1} of {len(pages)}")
        self.update_actions()

    def _open_folder(self) -> None:
        if self._result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.folder or self._result.pdf.parent)))

    def _open_pdf(self) -> None:
        if self._result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.pdf)))
