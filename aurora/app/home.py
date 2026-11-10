"""Home perspective: open a beamtime folder, a loose folder of scans or one .h5 file; see its days / holders / scans
with status and flags; read the project summary and a scan's summary (John Bussey's 'Scan Analysis Summary').

Before a project is open, a welcome panel offers Open project…, the demo beamtime, the tutorials and the recent
projects (File > Open recent lists the same ten). A failed open says so with Retry and Choose another (a file that is
no project gets Choose another only). A day or scan folder opens that day only: the path line says so, and Show every
day opens the whole beamtime. The GUI thread never looks at a project path: discovery, the recent-projects check and
the incomplete-maps check run as jobs.
"""
from __future__ import annotations

import datetime as dt
import html
import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QSplitter, QTextBrowser, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from aurora.app import jobs, shortcuts
from aurora.app.help_text import explain, header_tips
from aurora.app.icons import icon
from aurora.app.mode import is_advanced, mark_advanced_column, on_mode_changed
from aurora.app.settings import recent_projects, remember_project, settings
from aurora.app.theme import MONO_FONT, TOKENS, on_theme_changed, refill_tones, set_item_tone, tone_of
from aurora.app.vocab import plural
from aurora.app.widgets import Card, ElidedLabel, EmptyState, TaskBar, default_dir, remember_dir, set_enabled
from aurora.core import overview
from aurora.core.constants import sdd_geometry
from aurora.core.model import Project, ScanInfo

COLS = ("Scan", "Time", "Kind", "Edge", "Status", "Energies", "Points", "Flags")
KIND_COL, TIME_COL, STATUS_COL, FLAGS_COL = 2, 1, 4, 7
NO_PROJECT = "no project open"
MAX_RECENT = 10                                   # File > Open recent and the welcome list (settings keep 10)
RECENT_ROWS = 6                                   # rows of the welcome list shown before it scrolls
WELCOME_TEXT = ("No project open. Open a beamtime folder (the one that holds raw/), a loose folder of scans or a scan "
                "folder, or try the demo beamtime.")
MONO_KEYS = {"folder", "scan", "scan name", "layout"}


# ----------------------------------------------------------------------------- recent projects (settings)
def forget_project(path) -> None:
    """Remove one entry from the recent projects."""
    s = settings()
    s.setValue("recent_projects", [p for p in recent_projects() if p != str(path)])
    s.sync()


def clear_recent() -> None:
    s = settings()
    s.setValue("recent_projects", [])
    s.sync()


def check_paths(paths: list[str], progress) -> dict[str, bool]:
    """{path: found} for the recent projects (a folder, or a scan's .h5 file). Runs in a job: an unreachable share
    may take long to answer, and the GUI thread never waits for it."""
    out = {}
    for i, p in enumerate(paths):
        progress(i, len(paths), p)
        try:
            out[p] = os.path.isdir(p) or (p.lower().endswith(".h5") and os.path.isfile(p))
        except OSError:
            out[p] = False
    return out


def discover(path: Path, progress) -> tuple[Project, overview.ProjectFacts]:
    """The discovery job: the project and the files found next to it (sample sheet)."""
    project = Project.discover(path, progress=progress)
    return project, overview.project_facts(project)


def failure_reason(text: str) -> str:
    """One calm sentence for a failed open, from the job's error text (its last line)."""
    last = text.strip().splitlines()[-1].strip() if text.strip() else ""
    name, _sep, msg = last.partition(": ")
    if name.endswith("NotAProjectError"):            # a file that is no project: its own sentence, which starts with
        return msg                                   # the file name (kept as it is written)
    if "not a project folder" in last:
        why = "this is not a beamtime: no raw/<day>/<scan> layout and no scan folders in it"
    elif name in ("FileNotFoundError", "NotADirectoryError"):
        why = "the folder does not exist, or the share that holds it is not reachable now"
    elif name == "PermissionError":
        why = "access to the folder is denied"
    else:
        why = msg or last or "unknown error"
    return why[:1].upper() + why[1:]


def retry_helps(text: str) -> bool:
    """False for a file that is no project (model.NotAProjectError): opening it again can never work."""
    last = text.strip().splitlines()[-1].strip() if text.strip() else ""
    return not last.partition(": ")[0].endswith("NotAProjectError")


def path_line(project: Project) -> str:
    """The Scans card's path line: the root, days, holders and scans; '1 of 4 days' when a day or scan path opened one
    day of a beamtime with more."""
    n_days = len(project.days) + len(project.days_left_out)
    days = (f"{project.days[0].date} only (1 of {n_days} days)" if project.days_left_out and project.days
            else plural(len(project.days), "day"))
    return (f"{project.root}   ·   {days}, {plural(project.n_holders, 'holder')}, "
            f"{plural(len(project.scans), 'scan')}")


def breakable(text: str) -> str:
    """A path that may wrap after its separators in a text panel (zero-width spaces after / and \\)."""
    return str(text).replace("\\", "\\\u200b").replace("/", "/\u200b")


def elide_middle(text: str, n: int = 90) -> str:
    return text if len(text) <= n else text[:n // 2 - 1] + "…" + text[-(n - n // 2):]


def later_note(day: str, holder_day: str) -> str:
    """'(next day)' for a stack recorded the day after its holder started, '(+2 days)' ..., '(later day)'."""
    try:
        n = (dt.date.fromisoformat(day) - dt.date.fromisoformat(holder_day)).days
    except ValueError:
        return "(later day)"
    return "(next day)" if n == 1 else f"(+{n} days)"


class RecentList(QListWidget):
    """The recent projects: 'name  (not found)  ·  parent folder', elided in the middle to the list's width (no
    horizontal scroll bar); the full path is the tooltip and Qt.UserRole."""
    FULL = Qt.UserRole + 1

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setUniformItemSizes(True)
        self.setMinimumWidth(380)

    def relabel(self) -> None:
        fm = self.fontMetrics()
        width = max(self.viewport().width(), self.minimumWidth()) - 2 * fm.horizontalAdvance("m")
        for i in range(self.count()):
            it = self.item(i)
            it.setText(fm.elidedText(str(it.data(self.FULL) or ""), Qt.ElideMiddle, width))

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.relabel()


class WelcomePanel(EmptyState):
    """The start page over the empty tree: one sentence, Open project… / Open the demo beamtime / Read the tutorials,
    and the recent projects (double-click opens, a missing one is greyed 'not found', right-click forgets it)."""

    def __init__(self, home: "HomePerspective", parent: QWidget):
        super().__init__(WELCOME_TEXT, [("Open project…", home.choose_project), ("Open the demo beamtime", home._open_demo),
                                        ("Read the tutorials", home.open_tutorials)], icon="folder", parent=parent)
        for b, key in zip(self.buttons, ("home.open", "home.demo", "home.tutorials")):
            explain(b, key)
        self.recent_caption = QLabel("Recent projects")
        self.recent_caption.setObjectName("muted")
        self.recent = RecentList(self)
        self.recent.setSelectionMode(QAbstractItemView.SingleSelection)
        self.recent.setContextMenuPolicy(Qt.CustomContextMenu)
        self.recent_caption.setBuddy(self.recent)
        explain(self.recent, "home.recent")
        self.layout().addWidget(self.recent_caption)
        self.layout().addWidget(self.recent)


class HomePerspective(QWidget):
    project_opened = Signal(object)
    scan_selected = Signal(object)
    scan_activated = Signal(object)       # double-click: open in Reduce

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.project: Project | None = None
        self.facts: overview.ProjectFacts | None = None
        self._shown: ScanInfo | None = None           # the scan in the detail panel (re-rendered on a mode / theme change)
        self._quiet = False                           # the open in progress is the last project at start
        self._last_path: Path | None = None           # the last path asked for (Retry)
        self._failed = ""                             # the error of the last failed open ('' = none shown)
        self._recent_found: dict[str, bool] = {}      # recent path -> found, from the last check job
        self._map_checks: dict[str, list] = {}        # scan folder -> incomplete_energies result
        self._map_errors: dict[str, str] = {}
        self._check_pending: ScanInfo | None = None
        self._generation = 0                          # +1 per project shown: a map check of an earlier one is dropped
        self.can_leave = None                         # fn(new project) -> bool, asked before another project replaces
        #                                               the open one (the window: zone edits that could not be saved)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        h1 = QLabel("Home")
        h1.setObjectName("h1")
        sub = ElidedLabel("open a beamtime folder (raw/<day>/<scan>), a loose folder of scans or a scan folder",
                          Qt.ElideRight)
        sub.setObjectName("muted")
        head.addWidget(h1)
        head.addWidget(sub, 1)
        self.open_btn = QPushButton(icon("folder", TOKENS["ink"]), " Open project…")
        self.open_btn.clicked.connect(self.choose_project)
        self.reload_btn = QPushButton(icon("refresh", TOKENS["ink"]), " Reload")
        self.reload_btn.clicked.connect(self.reload)
        # a day or scan path opened one day of a beamtime with more: this opens all of them (hidden otherwise)
        self.all_days_btn = QPushButton("Show every day")
        self.all_days_btn.clicked.connect(self.show_every_day)
        self.all_days_btn.hide()
        head.addWidget(self.open_btn)
        head.addWidget(self.reload_btn)
        head.addWidget(self.all_days_btn)
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
        self.tree.itemDoubleClicked.connect(self._activated)
        left.body.addWidget(self.tree, 1)
        split.addWidget(left)
        self.scans_card = left

        self.details_card = right = Card("Project", help_anchor="22-project-and-scan-summaries")
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setAccessibleName("summary of the project or of the selected scan")
        self.details.setHtml(self._welcome_html())
        right.body.addWidget(self.details, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        self.empty_state = WelcomePanel(self, parent=self.tree)
        self.recent_list = self.empty_state.recent
        self.recent_list.itemActivated.connect(lambda it: self._open_recent(it.data(Qt.UserRole)))
        self.recent_list.customContextMenuRequested.connect(self._recent_menu_at)
        self.failure_state = EmptyState("", [], icon="folder", parent=self.tree)
        self.recent_menu = shortcuts.add_menu(self, "Open recent", "file")     # File > Open recent
        self.recent_menu.aboutToShow.connect(self.fill_recent_menu)
        explain(self.open_btn, "home.open")
        explain(self.reload_btn, "home.reload")
        explain(self.all_days_btn, "home.all_days")
        header_tips(self.tree, {i: f"home.col.{c.lower()}" for i, c in enumerate(COLS)})
        on_theme_changed(self, lambda _s: self._theme_changed())
        # Easy mode: the Kind column, the full h5 attribute table, the SDD geometry and the sgm_report notes are Advanced
        mark_advanced_column(self.tree, KIND_COL)
        on_mode_changed(self, lambda _adv: self._render_details(), call_now=False)
        self.fill_recent()
        self.fill_recent_menu()
        self.update_actions()
        QTimer.singleShot(0, self, self.refresh_recent)          # once the window is up: which recent ones exist

    def update_actions(self) -> None:
        busy = jobs.of(self).job("discover") is not None
        set_enabled(self.reload_btn, self.project is not None and not busy, "open a project first")
        self.all_days_btn.setVisible(self.project is not None and bool(self.project.days_left_out) and not self._failed)
        self.all_days_btn.setEnabled(not busy)
        self.empty_state.set_shown(self.project is None and not self._failed)
        self.failure_state.set_shown(bool(self._failed))

    def _theme_changed(self) -> None:
        self.open_btn.setIcon(icon("folder", TOKENS["ink"]))
        self.reload_btn.setIcon(icon("refresh", TOKENS["ink"]))
        refill_tones(self.tree)
        self.fill_recent()
        self._render_details()

    def _render_details(self) -> None:
        if self._shown is not None:
            self.details_card.set_title("Scan")
            self.details.setHtml(self._scan_html(self._shown))
        elif self._failed:
            self.details_card.set_title("Project")
            self.details.setHtml(self._failure_html())
        elif self.project is not None:
            self.details_card.set_title("Project")
            self.details.setHtml(self._project_html())
        else:
            self.details_card.set_title("Project")
            self.details.setHtml(self._welcome_html())

    def _open_demo(self) -> None:
        from aurora.app.nav import window_of
        win = window_of(self)
        if win is not None and hasattr(win, "open_demo"):
            win.open_demo()

    def open_tutorials(self) -> None:
        from aurora.app.help import open_help
        open_help(self, "TUTORIALS.md")

    # ------------------------------------------------------------------ opening
    def choose_project(self) -> None:
        rec = recent_projects()
        start = str(Path(rec[0]).parent) if rec else default_dir("project")
        # a folder dialog: it lists no files, so a scan's .h5 opens by a drop or from the command line instead
        d = QFileDialog.getExistingDirectory(self, "Open a beamtime folder, a loose folder of scans or a scan folder",
                                             start)
        if d:
            remember_dir("project", Path(d).parent)
            self.open_path(Path(d))

    def open_path(self, path: Path, quiet: bool = False) -> None:
        """Open a beamtime folder, a loose folder of scan folders, a day or scan folder, or one .h5 file (its scan
        folder's project). The one entry point of Open…, Reload, Retry, the recent projects, the demo, a folder
        dropped on the window, the command-line folder and the last project at start. The path is only looked at in
        the discovery job, so an unreachable share never blocks the window.
        quiet: a failure is a calm note in the path line (the last project at start), not the failure panel."""
        mgr = jobs.of(self)
        if mgr.job("discover") is not None:
            return
        path = Path(path)
        self._quiet = quiet
        self._last_path = path
        self.path_label.setText(f"reading {path} …")
        self.open_btn.setEnabled(False)
        mgr.submit("discover", lambda cb: discover(path, cb), on_done=self._discovered, on_failed=self._on_failed,
                   on_end=lambda _s: self._open_ended(), label=f"reading {path.name}", taskbar=self.taskbar,
                   report=False)
        self.update_actions()

    def open_project(self, path: Path) -> None:
        """Former name of open_path."""
        self.open_path(path)

    def reload(self) -> None:
        """Read the open project again (Reload, F5): the same scope, so a day opened alone stays alone."""
        if self.project is not None:
            self.open_path(self.project.days[0].path if self.project.days_left_out else self.project.root)

    def show_every_day(self) -> None:
        """Open the whole beamtime of the one day shown (Show every day)."""
        if self.project is not None:
            self.open_path(self.project.root)

    def retry(self) -> None:
        """Open the last path asked for again (the failure panel's Retry)."""
        if self._last_path is not None:
            self.open_path(self._last_path)

    def set_project(self, project: Project) -> None:
        """Show an already discovered project (tests, distcheck)."""
        self._show_project(project, overview.project_facts(project))

    def shutdown(self) -> None:
        """Let running jobs finish before the widget goes away (a QThread destroyed while running aborts Qt)."""
        jobs.of(self).wait_idle(30000)

    def _open_ended(self) -> None:
        self.open_btn.setEnabled(True)
        self.update_actions()

    def _discovered(self, result) -> None:
        project, facts = result
        self._show_project(project, facts)

    def _show_project(self, project: Project, facts: overview.ProjectFacts | None) -> None:
        if self.project is not None and self.can_leave is not None and not self.can_leave(project):
            self._quiet = False                       # the user kept the open project (Cancel): nothing changes
            self.path_label.setText(f"{self.project.root}   ·   still open: {project.root} was not opened")
            return
        self._quiet = False
        self._failed = ""
        self.project = project
        self.facts = facts
        self._map_checks.clear()
        self._map_errors.clear()
        self._check_pending = None
        self._generation += 1
        self.open_btn.setEnabled(True)
        remember_project(project.root)
        self._recent_found[str(project.root)] = True
        self.path_label.setText(path_line(project))
        self._fill_tree(project)
        self._render_details()
        self.update_actions()
        self.fill_recent()
        self.fill_recent_menu()
        self.project_opened.emit(project)

    def _on_failed(self, text: str) -> None:
        self.open_btn.setEnabled(True)
        self.taskbar.hide()                           # the panel below (or the path line) says it in one sentence
        if self._quiet:                               # the last project at start: say so, keep the welcome page
            self._quiet = False
            name = f", {self._last_path.name}," if self._last_path is not None and self._last_path.name else ""
            why = failure_reason(text)
            self.path_label.setText(f"the last project{name} could not be opened: {why[:1].lower()}{why[1:]}. "
                                    "Pick another one below.")
            self.refresh_recent()
            return
        self._failed = text.strip() or "unknown error"
        self.path_label.setText("could not open the project")
        buttons = [("Retry", self.retry, "home.retry")] if retry_helps(self._failed) else []
        buttons.append(("Choose another…", self.choose_project, "home.choose_another"))
        if self.project is not None:
            buttons.append(("Keep the open project", self._dismiss_failure, "home.keep"))
        where = breakable(self._last_path) if self._last_path is not None else "the folder"
        self.failure_state.set_content(f"Could not open the project\n{where}\n{failure_reason(self._failed)}.",
                                       [b[:2] for b in buttons])
        for b, (_text, _fn, key) in zip(self.failure_state.buttons, buttons):
            explain(b, key)
        self._shown = None
        self._render_details()
        self.update_actions()
        self.refresh_recent()

    def _dismiss_failure(self) -> None:
        self._failed = ""
        if self.project is not None:
            self.path_label.setText(path_line(self.project))
        self._render_details()
        self.update_actions()

    # ------------------------------------------------------------------ recent projects
    def refresh_recent(self) -> None:
        """Check in a job which recent projects exist, then grey the missing ones."""
        paths = recent_projects()[:MAX_RECENT]
        if not paths:
            self._recent_found = {}
            self.fill_recent()
            return
        jobs.of(self).submit("recent", lambda cb: check_paths(paths, cb), on_done=self._recent_checked,
                             label="checking the recent projects", report=False)

    def _recent_checked(self, found: dict) -> None:
        self._recent_found.update(found)
        self.fill_recent()
        self.fill_recent_menu()

    def _recent_text(self, p: str) -> str:
        """'name  (not found)  ·  parent folder' (the menu and the welcome list)."""
        path = Path(p)
        miss = "  (not found)" if self._recent_found.get(p) is False else ""
        return f"{path.name}{miss}  ·  {path.parent}" if path.name else f"{p}{miss}"

    def fill_recent(self) -> None:
        """The welcome list: the recent projects, the missing ones greyed and marked 'not found'."""
        lst = self.recent_list
        lst.clear()
        paths = recent_projects()[:MAX_RECENT]
        for p in paths:
            it = QListWidgetItem(self._recent_text(p))
            it.setData(Qt.UserRole, p)
            it.setData(RecentList.FULL, self._recent_text(p))
            if self._recent_found.get(p) is False:
                it.setForeground(QBrush(QColor(TOKENS["disabled"])))
                it.setToolTip(f"{p}\nnot found: the folder is missing or the share is not reachable now. "
                              "Double-click tries again; right-click forgets it.")
            else:
                it.setToolTip(f"{p}\nDouble-click to open it; right-click to forget it.")
            lst.addItem(it)
        shown = bool(paths)
        self.empty_state.recent_caption.setVisible(shown)
        lst.setVisible(shown)
        if shown:
            lst.relabel()
            row = lst.sizeHintForRow(0) if lst.count() else lst.fontMetrics().height() + 6
            lst.setFixedHeight(min(len(paths), RECENT_ROWS) * max(row, 1) + 2 * lst.frameWidth() + 2)
        self.empty_state.updateGeometry()
        self.empty_state._place()

    def fill_recent_menu(self) -> None:
        """File > Open recent: the last ten projects, then Clear list."""
        m = self.recent_menu
        m.clear()
        paths = recent_projects()[:MAX_RECENT]
        for i, p in enumerate(paths):
            n = i + 1
            mnemonic = f"&{n}" if n < 10 else "1&0"
            a = m.addAction(f"{mnemonic} {elide_middle(self._recent_text(p)).replace('&', '&&')}")
            a.setData(p)
            a.setStatusTip(f"open {p}")
            a.triggered.connect(lambda _=False, path=p: self._open_recent(path))
        if not paths:
            a = m.addAction("no recent projects")
            a.setEnabled(False)
        m.addSeparator()
        clear = m.addAction("Clear list")
        clear.setStatusTip("forget every recent project (the folders are not touched)")
        clear.setEnabled(bool(paths))
        clear.triggered.connect(lambda _=False: self.clear_recent_list())

    def recent_actions(self) -> list:
        """The project entries of File > Open recent (for tests and the self-test)."""
        return [a for a in self.recent_menu.actions() if a.data()]

    def _open_recent(self, path) -> None:
        if path:
            self.open_path(Path(path))

    def forget_recent(self, path) -> None:
        forget_project(path)
        self._recent_found.pop(str(path), None)
        self.fill_recent()
        self.fill_recent_menu()

    def clear_recent_list(self) -> None:
        clear_recent()
        self._recent_found = {}
        self.fill_recent()
        self.fill_recent_menu()

    def recent_context_menu(self, item: QListWidgetItem) -> QMenu:
        """The right-click menu of a recent project: Open, Forget."""
        path = item.data(Qt.UserRole)
        menu = QMenu(self.recent_list)
        menu.addAction("Open", lambda: self._open_recent(path))
        menu.addAction("Forget", lambda: self.forget_recent(path))
        return menu

    def _recent_menu_at(self, pos) -> None:
        it = self.recent_list.itemAt(pos)
        if it is not None:
            menu = self.recent_context_menu(it)
            menu.exec(self.recent_list.viewport().mapToGlobal(pos))
            menu.deleteLater()

    # ------------------------------------------------------------------ tree
    def _fill_tree(self, project: Project) -> None:
        self._shown = None
        self.tree.clear()
        for day in project.days:
            note = ""
            if day.continued:
                holders = sorted({s.holder for s in day.continued})
                note = f"continues holder {', '.join(holders)}"
            d_item = QTreeWidgetItem([day.date or day.path.name, "", plural(len(day.holders), "holder"), "", note,
                                      "", plural(len(day.scans) + len(day.continued), "scan"), ""])
            d_item.setData(0, Qt.UserRole, None)
            if day.continued:
                d_item.setToolTip(STATUS_COL, f"{plural(len(day.continued), 'stack')} of this day continue a holder of "
                                              "an earlier day (recorded after midnight): listed under that holder")
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
        when = f"{s.time} {later_note(s.day, s.continues)}" if s.continues else s.time
        it = QTreeWidgetItem([s.name, when, s.kind, s.edge_text, s.status.replace("_", " "), s.energy_text,
                              str(s.n_points) if s.n_points else "", ", ".join(s.flags) + (f"  {s.error}" if s.error else "")])
        it.setData(0, Qt.UserRole, s)
        set_item_tone(it, tone_of(s.status), STATUS_COL)          # word plus icon, colour from the tokens
        if s.flags or s.error:
            set_item_tone(it, "warn", FLAGS_COL)
        if s.status_reason:
            it.setToolTip(STATUS_COL, s.status_reason)
        if s.continues:
            it.setToolTip(TIME_COL, f"recorded on {s.day}, after midnight: it continues holder {s.holder}, "
                                    f"which started on {s.continues}")
        return it

    def _on_select(self, cur: QTreeWidgetItem | None, _prev=None) -> None:
        s = cur.data(0, Qt.UserRole) if cur is not None else None
        if isinstance(s, ScanInfo):
            self._shown = s
            self._render_details()
            self.scan_selected.emit(s)
            self._check_maps(s)
        elif self.project is not None and self._shown is not None:
            self._shown = None                        # a day or holder row: the project summary again
            self._render_details()

    def _activated(self, it: QTreeWidgetItem, _col: int) -> None:
        s = it.data(0, Qt.UserRole)
        if isinstance(s, ScanInfo):
            self.scan_activated.emit(s)

    # ------------------------------------------------------------------ incomplete maps (a job per stack)
    def _check_maps(self, s: ScanInfo) -> None:
        key = str(s.path)
        if s.kind != "stack" or not s.energy_dirs or key in self._map_checks or key in self._map_errors:
            return
        mgr = jobs.of(self)
        if mgr.job("mapcheck") is not None:
            self._check_pending = s
            return
        gen = self._generation
        mgr.submit("mapcheck", lambda cb, s=s: (gen, str(s.path), overview.incomplete_energies(s, cb)),
                   on_done=self._maps_checked, on_failed=lambda text, k=key, g=gen: self._maps_failed(k, text, g),
                   on_end=lambda _state: self._next_check(), label=f"checking the maps of {s.name}", report=False)

    def _maps_checked(self, result) -> None:
        gen, key, incomplete = result
        if gen != self._generation:                   # the project was reloaded or changed meanwhile
            return
        self._map_checks[key] = incomplete
        if self._shown is not None and str(self._shown.path) == key:
            self._render_details()

    def _maps_failed(self, key: str, text: str, gen: int) -> None:
        if gen != self._generation:
            return
        self._map_errors[key] = text.strip().splitlines()[-1] if text.strip() else "unknown error"
        if self._shown is not None and str(self._shown.path) == key:
            self._render_details()

    def _next_check(self) -> None:
        s, self._check_pending = self._check_pending, None
        if self.project is not None:
            if s is None and self._shown is not None and str(self._shown.path) not in self._map_checks:
                s = self._shown                       # a check of an earlier project ended: check the one on screen
            if s is not None:
                self._check_maps(s)

    def map_check(self, s: ScanInfo):
        """The incomplete-maps result of a stack (None while not checked)."""
        return self._map_checks.get(str(s.path))

    # ------------------------------------------------------------------ details
    @staticmethod
    def _table(rows) -> str:
        e = html.escape
        out = []
        for k, v in rows:
            if not v:
                continue
            font = f"font-family:{MONO_FONT}" if k in MONO_KEYS else ""
            out.append(f"<tr><td style='color:{TOKENS['muted']};padding:2px 12px 2px 0;white-space:nowrap'>"
                       f"{e(k[:1].upper() + k[1:])}</td><td style='{font}'>{e(breakable(v))}</td></tr>")
        return "<table>" + "".join(out) + "</table>"

    @staticmethod
    def _welcome_html() -> str:
        return ("<h3>No project open</h3>"
                "<p>Open one of these with <b>Open project…</b>, by dropping it on the window, or from the command "
                "line (<code>aurora &lt;folder&gt;</code>):</p><ul>"
                "<li>a beamtime folder that holds <code>raw/&lt;day&gt;/&lt;scan&gt;</code> (for example "
                "<code>my_beamtime</code>), or its day folders without <code>raw/</code>;</li>"
                "<li>one day or scan folder of a beamtime: that day opens alone, and <b>Show every day</b> opens "
                "the others;</li>"
                "<li>a loose folder of scan folders, the way John Bussey's GUI kept them (<code>CLS GUI</code>);</li>"
                "<li>one scan's <code>.h5</code> file, dropped on the window or given on the command line (the "
                "folder dialog lists folders only): its scan folder's project opens.</li></ul>"
                "<p>The demo beamtime is synthetic: every perspective has data without a real beamtime. The "
                "tutorials walk through it.</p>")

    def _failure_html(self) -> str:
        e = html.escape
        where = e(breakable(self._last_path)) if self._last_path is not None else "the folder"
        detail = (f"<h4>Details</h4><pre>{e(self._failed)}</pre>" if is_advanced() else
                  f"<p style='color:{TOKENS['muted']}'>Advanced mode shows the full error.</p>")
        why = ("<p>The folder may be missing, on a share that is not reachable now, or not a beamtime "
               "(no raw/&lt;day&gt;/&lt;scan&gt; and no scan folders in it). <b>Retry</b> tries the same path "
               "again; <b>Choose another…</b> opens the folder dialog.</p>" if retry_helps(self._failed) else
               "<p><b>Choose another…</b> opens the folder dialog.</p>")
        return (f"<h3>Could not open the project</h3><p>{where}</p><p><b>{e(failure_reason(self._failed))}.</b></p>"
                + why + detail)

    def _project_html(self) -> str:
        p = self.project
        e = html.escape
        return (f"<h3>{e(p.root.name)}</h3>{self._table(overview.project_rows(p, self.facts))}"
                f"<p style='color:{TOKENS['muted']}'>Select a scan for its summary; double-click a stack to open it in "
                "Reduce on its holder.</p>")

    def _scan_html(self, s: ScanInfo) -> str:
        e = html.escape
        key = str(s.path)
        rows = overview.scan_rows(s, self.project, self._map_checks.get(key), self._map_errors.get(key, ""))
        body = (f"<h3>{e(s.name)}</h3><h4>Scan analysis summary</h4>{self._table(rows)}")
        if not is_advanced():                     # Easy: the scan summary only
            return body
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
        date = self.project.scan_date(s) if self.project else None
        geo = sdd_geometry(date)
        geo_html = ", ".join(f"{k.upper()} {v}" for k, v in geo.items())
        extra = [("folder", str(s.path)), ("kind", s.kind), ("label", s.label)]       # breakable in _table
        return (f"{body}{self._table(extra)}{flags}"
                f"<h4>Metadata</h4><table>{md or '<tr><td>none read</td></tr>'}</table>"
                f"<p style='color:{TOKENS['muted']}'>SDD geometry for this date: {e(geo_html)}</p>")
