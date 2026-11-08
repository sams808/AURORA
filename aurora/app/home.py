"""Home perspective: open a beamtime folder, see its days / holders / scans with status and flags, read a scan's metadata."""
from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from aurora.app import jobs
from aurora.app.help_text import explain, header_tips
from aurora.app.icons import icon
from aurora.app.mode import is_advanced, mark_advanced_column, on_mode_changed
from aurora.app.settings import recent_projects, remember_project
from aurora.app.theme import MONO_FONT, TOKENS, on_theme_changed, refill_tones, set_item_tone, tone_of
from aurora.app.vocab import plural
from aurora.app.widgets import Card, ElidedLabel, EmptyState, TaskBar, default_dir, remember_dir, set_enabled
from aurora.core.constants import sdd_geometry
from aurora.core.model import Project, ScanInfo

COLS = ("Scan", "Time", "Kind", "Edge", "Status", "Energies", "Points", "Flags")
KIND_COL, STATUS_COL, FLAGS_COL = 2, 4, 7
NO_PROJECT = "no project open"


class HomePerspective(QWidget):
    project_opened = Signal(object)
    scan_selected = Signal(object)
    scan_activated = Signal(object)       # double-click: open in Reduce

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.project: Project | None = None
        self._shown: ScanInfo | None = None           # the scan in the detail panel (re-rendered on a mode / theme change)
        self._quiet = False                           # the open in progress is the last project at start
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        h1 = QLabel("Home")
        h1.setObjectName("h1")
        sub = ElidedLabel("open a beamtime folder (raw/<day>/<scan>) and see what was measured", Qt.ElideRight)
        sub.setObjectName("muted")
        head.addWidget(h1)
        head.addWidget(sub, 1)
        self.open_btn = QPushButton(icon("folder", TOKENS["ink"]), " Open project…")
        self.open_btn.clicked.connect(self.choose_project)
        self.reload_btn = QPushButton(icon("refresh", TOKENS["ink"]), " Reload")
        self.reload_btn.clicked.connect(lambda: self.open_path(self.project.root) if self.project else None)
        head.addWidget(self.open_btn)
        head.addWidget(self.reload_btn)
        root.addLayout(head)

        self.taskbar = TaskBar(self)
        root.addWidget(self.taskbar)

        split = QSplitter(Qt.Horizontal)
        left = Card("Scans", help_anchor="2-home")
        self.path_label = ElidedLabel(NO_PROJECT, Qt.ElideMiddle)
        self.path_label.setObjectName("muted")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        left.fill_header(self.path_label)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLS))
        self.tree.setHeaderLabels(COLS)
        self.tree.setAccessibleName("days, holders and scans of the project")
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().resizeSection(0, 360)
        for i in range(1, len(COLS) - 1):
            self.tree.header().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.tree.header().setStretchLastSection(True)
        self.tree.currentItemChanged.connect(self._on_select)
        self.tree.itemDoubleClicked.connect(lambda it, col: self.scan_activated.emit(it.data(0, Qt.UserRole)) if isinstance(it.data(0, Qt.UserRole), ScanInfo) else None)
        left.body.addWidget(self.tree, 1)
        split.addWidget(left)
        self.scans_card = left

        right = Card("Scan", help_anchor="2-home")
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setAccessibleName("details of the selected scan")
        self.details.setHtml(self._welcome_html())
        right.body.addWidget(self.details, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        self.empty_state = EmptyState(
            "No project open. Open a beamtime folder (the one that holds raw/), a loose folder of scans or one .h5 file.",
            [("Open project…", self.choose_project), ("Open the demo beamtime", self._open_demo)], icon="folder",
            parent=self.tree)
        explain(self.open_btn, "home.open")
        explain(self.reload_btn, "home.reload")
        header_tips(self.tree, {i: f"home.col.{c.lower()}" for i, c in enumerate(COLS)})
        on_theme_changed(self, lambda _s: self._theme_changed())
        # Easy mode: the Kind column, the full h5 attribute table, the SDD geometry and the sgm_report notes are Advanced
        mark_advanced_column(self.tree, KIND_COL)
        on_mode_changed(self, lambda _adv: self._render_details(), call_now=False)
        self.update_actions()

    def update_actions(self) -> None:
        busy = jobs.of(self).job("discover") is not None
        set_enabled(self.reload_btn, self.project is not None and not busy, "open a project first")
        self.empty_state.set_shown(self.project is None)

    def _theme_changed(self) -> None:
        self.open_btn.setIcon(icon("folder", TOKENS["ink"]))
        self.reload_btn.setIcon(icon("refresh", TOKENS["ink"]))
        refill_tones(self.tree)
        self._render_details()

    def _render_details(self) -> None:
        if self._shown is not None:
            self.details.setHtml(self._scan_html(self._shown))
        elif self.project is None and self.path_label.text() == NO_PROJECT:
            self.details.setHtml(self._welcome_html())

    def _open_demo(self) -> None:
        from aurora.app.nav import window_of
        win = window_of(self)
        if win is not None and hasattr(win, "open_demo"):
            win.open_demo()

    # ------------------------------------------------------------------ opening
    def choose_project(self) -> None:
        rec = recent_projects()
        start = str(Path(rec[0]).parent) if rec else default_dir("project")
        d = QFileDialog.getExistingDirectory(self, "Open a beamtime project folder (contains raw/), a scan folder", start)
        if d:
            remember_dir("project", Path(d).parent)
            self.open_path(Path(d))

    def open_path(self, path: Path, quiet: bool = False) -> None:
        """Open a beamtime folder, a day or scan folder, or one .h5 file (its scan folder). The one entry point of
        Open…, Reload, the demo, a folder dropped on the window, the command-line folder and the last project at
        start. The path is only looked at in the discovery job, so an unreachable share never blocks the window.
        quiet: a failure is a calm note in the path line (the last project at start), not the failure panel."""
        mgr = jobs.of(self)
        if mgr.job("discover") is not None:
            return
        path = Path(path)
        target = path.parent if path.suffix.lower() == ".h5" else path       # a string test, no stat
        self._quiet = quiet
        self.path_label.setText(f"reading {path} …")
        self.open_btn.setEnabled(False)
        mgr.submit("discover", lambda cb: Project.discover(target, progress=cb), on_done=self._on_done,
                   on_failed=self._on_failed, on_end=lambda _s: self._open_ended(), label=f"reading {path.name}",
                   taskbar=self.taskbar, report=False)
        self.update_actions()

    def open_project(self, path: Path) -> None:
        """Former name of open_path."""
        self.open_path(path)

    def set_project(self, project: Project) -> None:
        """Show an already discovered project (tests, distcheck)."""
        self._on_done(project)

    def shutdown(self) -> None:
        """Let a running discovery finish before the widget goes away (a QThread destroyed while running aborts Qt)."""
        jobs.of(self).wait_idle(30000)

    def _open_ended(self) -> None:
        self.open_btn.setEnabled(True)
        self.update_actions()

    def _on_done(self, project: Project) -> None:
        self._quiet = False
        self.project = project
        self.open_btn.setEnabled(True)
        remember_project(project.root)
        self.path_label.setText(f"{project.root}   ·   {plural(len(project.days), 'day')}, "
                                f"{plural(project.n_holders, 'holder')}, {plural(len(project.scans), 'scan')}")
        self._fill_tree(project)
        self.update_actions()
        self.project_opened.emit(project)

    def _on_failed(self, text: str) -> None:
        self.open_btn.setEnabled(True)
        if self._quiet:                               # the last project at start: say so, keep the welcome page
            self._quiet = False
            last = text.strip().splitlines()[-1] if text.strip() else ""
            self.path_label.setText(f"the last project could not be opened ({last})" if last else
                                    "the last project could not be opened")
            return
        self.path_label.setText("could not open the project")
        self.details.setHtml(f"<h3>Could not open the project</h3><pre>{html.escape(text)}</pre>")

    # ------------------------------------------------------------------ tree
    def _fill_tree(self, project: Project) -> None:
        self._shown = None
        self.tree.clear()
        for day in project.days:
            d_item = QTreeWidgetItem([day.date or day.path.name, "", plural(len(day.holders), "holder"), "", "",
                                      "", plural(len(day.scans), "scan"), ""])
            d_item.setData(0, Qt.UserRole, None)
            self.tree.addTopLevelItem(d_item)
            for holder in day.holders:
                n_ok = len(holder.stacks)
                h_item = QTreeWidgetItem([f"holder {holder.name}", "", "", "", f"{n_ok} complete {'stack' if n_ok == 1 else 'stacks'}",
                                          "", "", f"{len(holder.dropped)} dropped" if holder.dropped else ""])
                d_item.addChild(h_item)
                for s in holder.scans:
                    h_item.addChild(self._scan_item(s))
            for s in day.others:
                d_item.addChild(self._scan_item(s))
            d_item.setExpanded(True)
            for i in range(d_item.childCount()):
                d_item.child(i).setExpanded(True)

    def _scan_item(self, s: ScanInfo) -> QTreeWidgetItem:
        it = QTreeWidgetItem([s.name, s.time, s.kind, s.edge_text, s.status.replace("_", " "), s.energy_text,
                              str(s.n_points) if s.n_points else "", ", ".join(s.flags) + (f"  {s.error}" if s.error else "")])
        it.setData(0, Qt.UserRole, s)
        set_item_tone(it, tone_of(s.status), STATUS_COL)          # word plus icon, colour from the tokens
        if s.flags or s.error:
            set_item_tone(it, "warn", FLAGS_COL)
        if s.status_reason:
            it.setToolTip(4, s.status_reason)
        return it

    def _on_select(self, cur: QTreeWidgetItem | None, _prev=None) -> None:
        s = cur.data(0, Qt.UserRole) if cur is not None else None
        if isinstance(s, ScanInfo):
            self._shown = s
            self.details.setHtml(self._scan_html(s))
            self.scan_selected.emit(s)

    # ------------------------------------------------------------------ details
    @staticmethod
    def _welcome_html() -> str:
        rec = recent_projects()
        items = "".join(f"<li><code>{html.escape(p)}</code></li>" for p in rec[:5])
        return ("<h3>No project open</h3><p>Open the beamtime folder that holds <code>raw/</code> "
                "(for example <code>my_beamtime</code>). A single day folder or a scan folder works too.</p>"
                + (f"<p>Recent:</p><ul>{items}</ul>" if items else ""))

    def _scan_html(self, s: ScanInfo) -> str:
        e = html.escape
        rows = [("Folder", str(s.path)), ("Kind", s.kind), ("Holder", s.holder), ("Label", s.label),
                ("Edge", s.edge_text), ("Status", f"{s.status} ({s.status_reason})" if s.status_reason else s.status),
                ("Energies", f"{s.n_energies} measured" + (f" of {s.n_planned} planned" if s.n_planned else "")
                 + (f", {s.energy_text}" if s.energy_text else "")),
                ("Points", str(s.n_points) if s.n_points else "")]
        if s.error:
            rows.append(("Error", s.error))
        date = self.project.scan_date(s) if self.project else None
        geo = sdd_geometry(date)
        body = "".join(f"<tr><td style='color:{TOKENS['muted']};padding:2px 10px 2px 0'>{e(k)}</td>"
                       f"<td style='font-family:{MONO_FONT}'>{e(v)}</td></tr>" for k, v in rows if v)
        if not is_advanced():                     # Easy: the scan summary only
            return f"<h3>{e(s.name)}</h3><table>{body}</table>"
        md = "".join(f"<tr><td style='color:{TOKENS['muted']};padding:1px 10px 1px 0'>{e(k)}</td>"
                     f"<td style='font-family:{MONO_FONT}'>{e(v[:160])}</td></tr>" for k, v in sorted(s.md.items()))
        flags = ""
        if s.summary:
            notes = s.summary.get("notes", [])
            flags = "<h4>sgm_report summary</h4>" + "".join(f"<p>{e(str(n))}</p>" for n in notes)
            if s.summary.get("mode"):
                flags += (f"<p>mode {e(str(s.summary['mode']))}, {e(str(s.summary.get('type', '')))}, "
                          f"{plural(len(s.summary.get('zones', [])), 'zone')}</p>")
            if s.summary.get("dropped"):
                flags += f"<p>dropped: {e(str(s.summary['dropped']))}</p>"
        geo_html = ", ".join(f"{k.upper()} {v}" for k, v in geo.items())
        return (f"<h3>{e(s.name)}</h3><table>{body}</table>{flags}"
                f"<h4>Metadata</h4><table>{md or '<tr><td>none read</td></tr>'}</table>"
                f"<p style='color:{TOKENS['muted']}'>SDD geometry for this date: {e(geo_html)}</p>")
