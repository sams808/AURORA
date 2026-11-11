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
import logging
import os
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QSplitter, QStyledItemDelegate, QTextBrowser,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from aurora.app import jobs, shortcuts
from aurora.app.help_text import explain, header_tips
from aurora.app.icons import icon
from aurora.app.mode import is_advanced, mark_advanced_column, on_mode_changed
from aurora.app.settings import recent_projects, remember_project, settings, work_folders
from aurora.app.theme import MONO_FONT, TOKENS, on_theme_changed, refill_tones, set_item_tone, tone_of
from aurora.app import guide
from aurora.app.vocab import edge_words, flag_words, plural
from aurora.app.widgets import (Card, ElidedLabel, EmptyState, FlowLayout, TaskBar, TermLabel, default_dir,
                                fit_columns, remember_dir, set_enabled)
from aurora.core import overview
from aurora.core.constants import sdd_geometry
from aurora.core.model import Project, ScanInfo

log = logging.getLogger("aurora.home")

COLS = ("Scan", "Time", "Kind", "Edge", "Status", "Energies", "Pixels", "Flags")
COL_KEYS = ("scan", "time", "kind", "edge", "status", "energies", "points", "flags")     # HELP keys home.col.<key>
KIND_COL, TIME_COL, STATUS_COL, FLAGS_COL = 2, 1, 4, 7
NO_PROJECT = "no project open"
MAX_RECENT = 10                                   # File > Open recent and the welcome list (settings keep 10)
RECENT_ROWS = 6                                   # rows of the welcome list shown before it scrolls
WELCOME_TEXT = ("AURORA turns the maps recorded at the SGM beamline into one X-ray absorption spectrum per sample: you "
                "outline each sample, AURORA adds up its pixels, checks the result and builds a report. Open the folder "
                "of your beamtime (it contains a folder named raw), or try the demo first.")
SUBTITLE = {   # (Advanced, project open) -> the line next to the page title
    (False, False): "Open your beamtime, or the demo, to begin.",
    (False, True): "The days, holders and scans of this beamtime.",
    (True, False): "open a beamtime folder (raw/<day>/<scan>), a loose folder of scans or one .h5 file",
    (True, True): "The days, holders and scans of this beamtime.",
}
NOT_USED_TIP = "stopped early (aborted) or beamline tests: drawn on the Survey map, not offered in Reduce"
FLAG_ROLE = int(Qt.UserRole) + 7              # the raw flag words of a scan row (the cell shows them by mode)
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


POLL_S = 0.1                                      # how often a job waiting for a share checks for Cancel


def _asked(fn) -> tuple[threading.Thread, dict]:
    """fn() in a daemon thread: {'value': ...} or {'error': ...} once it answers. One look at a share that does not
    answer (a lab NAS off VPN) blocks for about 20 s and cannot be interrupted, so the job that waits for it keeps
    checking for Cancel, and a closing window never waits for the call: the thread is left to end on its own."""
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except Exception as err:                      # noqa: BLE001  handed to the job
            box["error"] = err
    t = threading.Thread(target=run, name="aurora-path-check", daemon=True)
    t.start()
    return t, box


def check_paths(paths: list[str], progress) -> dict[str, bool]:
    """{path: found} for the recent projects (a folder, or a scan's .h5 file). Runs in a job: an unreachable share
    may take long to answer, and the GUI thread never waits for it. The paths are looked at together, and progress
    (which raises Cancelled once the job is cancelled) is called while they answer."""
    asked = {p: _asked(lambda p=p: os.path.isdir(p) or (p.lower().endswith(".h5") and os.path.isfile(p)))
             for p in paths}
    for i, (p, (t, _box)) in enumerate(asked.items()):
        progress(i, len(paths), p)
        while t.is_alive():
            t.join(POLL_S)
            progress(i, len(paths), p)
    return {p: bool(box.get("value")) for p, (_t, box) in asked.items()}


def discover(path: Path, progress, work: dict | None = None) -> tuple[Project, overview.ProjectFacts]:
    """The discovery job: the project and the files found next to it (sample sheet). The path is looked at first in
    a thread of its own (see _asked), so Cancel and a closing window never wait for a share that does not answer.
    `work`: the remembered working folders ({beamtime root: folder}, read in the window's thread)."""
    path = Path(path)
    t, box = _asked(lambda: path.is_dir() or path.is_file())
    while t.is_alive():
        t.join(POLL_S)
        progress(0, 0, f"reading {path}")
    if "error" in box:
        raise box["error"]
    if not box.get("value"):
        raise FileNotFoundError(f"{path}: no such folder or file, or the share that holds it does not answer")
    project = Project.discover(path, progress=progress, work_folders=work)
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
        from aurora.app.widgets import plain_error
        why = plain_error(last) if last else "unknown error"
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
    work = f"   ·   working folder {project.work}" if getattr(project, "work", None) else ""
    return (f"{project.root}   ·   {days}, {plural(project.n_holders, 'holder')}, "
            f"{plural(len(project.scans), 'scan')}{work}")


HOLDER_ROLE = Qt.UserRole + 7                 # a holder row of the tree: the holder


class ScanNameDelegate(QStyledItemDelegate):
    """The Scan column of a scan row when its folder name does not fit: the sample part of the name (the time has its
    own column; the holder tag, the edge and the lab suffix go), elided at its end; the full name stays in the text
    and the tooltip (persona S-04: '095242_…5_stack' hid the one word that finds a sample)."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if index.column() != 0:
            return
        s = index.data(Qt.UserRole)
        if not isinstance(s, ScanInfo):
            return
        fm = option.fontMetrics
        room = option.rect.width() - 8
        if room <= 0 or fm.horizontalAdvance(option.text) <= room:
            return
        from aurora.core.names import sample_part
        option.text = fm.elidedText(sample_part(s.name, s.holder or None), Qt.ElideRight, room)


def short_energies(s) -> str:
    """'670–735 eV' (Easy): the range without decimals, never cut in the middle of a number (M01)."""
    if s.kind == "survey":
        return f"{s.e_min:.0f} eV" if s.e_min == s.e_min else ""
    if s.n_energies and s.e_min == s.e_min:
        return f"{s.e_min:.0f}–{s.e_max:.0f} eV"
    return s.energy_text


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


class GuideStrip(QFrame):
    """Getting started (Easy mode, once a project is open): the steps as chips (done ones ticked, the next one in the
    accent tone), the next step in one sentence, its button, and Hide. MainWindow.refresh_guide() fills it."""
    go = Signal(str)                    # the target of the button: a page key, 'open', 'demo', 'reload', 'folder', …
    hidden_by_user = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("guidestrip")
        self.setAccessibleName("getting started: next step")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)
        self.title = QLabel("Getting started")
        self.title.setObjectName("cardtitle")
        lay.addWidget(self.title)
        box = QWidget()
        self.flow = FlowLayout(box, spacing=4)
        self.chips: dict[str, QLabel] = {}
        for i, (key, title) in enumerate(guide.STEPS):
            if i:
                sep = QLabel("›")
                sep.setObjectName("muted")
                self.flow.addWidget(sep)
            c = QLabel(title)
            c.setProperty("aurora_step", key)
            c.setToolTip(guide.STEP_LINES[key])
            self.flow.addWidget(c)
            self.chips[key] = c
        lay.addWidget(box, 2)
        self.sentence = TermLabel("")
        lay.addWidget(self.sentence, 3)
        self.action_btn = QPushButton("")
        self.action_btn.clicked.connect(lambda: self.go.emit(self._target))
        lay.addWidget(self.action_btn)
        self.hide_btn = QPushButton("Hide")
        self.hide_btn.setFlat(True)
        self.hide_btn.setToolTip("Hide the getting-started guide; Settings shows it again")
        self.hide_btn.setAccessibleName("Hide the getting-started guide")
        self.hide_btn.clicked.connect(self.hidden_by_user.emit)
        lay.addWidget(self.hide_btn)
        self._target = ""
        self.step: guide.NextStep | None = None

    def show_step(self, step: "guide.NextStep", here: bool) -> None:
        """Show a next step; `here`: Home is the current page (the button is then the page's primary one)."""
        from aurora.app.theme import set_tone
        self.step = step
        for key, c in self.chips.items():
            title = dict(guide.STEPS)[key]
            done = key in step.done
            c.setText(f"✓ {title}" if done else title)
            set_tone(c, "muted" if done else None)
            c.setProperty("chip", "ok" if key == step.page and not done else "")
            c.style().unpolish(c)
            c.style().polish(c)
        self.sentence.set_markup(step.sentence)
        act = step.actions[0] if step.actions else None
        self._target = act[1] if act else ""
        self.action_btn.setText(act[0] if act else "")
        self.action_btn.setVisible(act is not None)
        self.action_btn.setObjectName("primary" if here else "")
        self.action_btn.style().unpolish(self.action_btn)
        self.action_btn.style().polish(self.action_btn)
        self.action_btn.setToolTip(step.sentence)


class HomePerspective(QWidget):
    project_opened = Signal(object)
    opening_changed = Signal(str)         # the name of the folder being read ('' once the read ended)
    scan_selected = Signal(object)
    scan_activated = Signal(object)       # double-click: open in Reduce
    kind_override_requested = Signal(object, str)     # right-click a stack: (scan, 'sample' | 'test' | '' = the rules)

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
        self._request = 0                             # +1 per open_path: the result of a superseded open is dropped
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        h1 = QLabel("Home")
        h1.setObjectName("h1")
        sub = ElidedLabel(SUBTITLE[(False, False)], Qt.ElideRight)
        sub.setObjectName("muted")
        self.subtitle = sub
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
        self.guide_strip = GuideStrip(self)        # Easy mode, once a project is open (MainWindow.refresh_guide)
        self.guide_strip.hide()
        root.addWidget(self.guide_strip)

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
        # every column in view at 1200 px: the Scan column takes the width the others leave and elides long names
        # in the middle (the full name is its tooltip); a fixed 360 px pushed Energies, Points and Flags off-screen
        self.tree.setTextElideMode(Qt.ElideMiddle)
        self.tree.setItemDelegateForColumn(0, ScanNameDelegate(self.tree))       # names lead with the sample (S-04)
        self._columns = fit_columns(self.tree, first_chars=16, keep=(COLS.index("Edge"), COLS.index("Status")))
        self.tree.currentItemChanged.connect(self._on_select)
        self.tree.itemDoubleClicked.connect(self._activated)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu_at)
        left.body.addWidget(self.tree, 1)
        split.addWidget(left)
        self.scans_card = left

        self.details_card = right = Card("Project", help_anchor="22-project-and-scan-summaries")
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        self.details.setOpenLinks(False)              # glossary:, aurora:help: and aurora:goto: links (on_link)
        self.details.anchorClicked.connect(self.on_link)
        self.details.setAccessibleName("summary of the project or of the selected scan")
        self.details.setHtml(self._welcome_html())
        right.body.addWidget(self.details, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 16)                  # the tree about 64 % of the width (M01): its words whole
        split.setStretchFactor(1, 9)
        root.addWidget(split, 1)

        self.empty_state = WelcomePanel(self, parent=self.tree)
        self.recent_list = self.empty_state.recent
        self.recent_list.itemActivated.connect(lambda it: self._open_recent(it.data(Qt.UserRole)))
        self.recent_list.customContextMenuRequested.connect(self._recent_menu_at)
        self.failure_state = EmptyState("", [], icon="folder", parent=self.tree)
        # while a beamtime is read: its name and the count of scans read (the welcome panel is hidden meanwhile)
        self.opening_state = EmptyState("", [], icon="folder", parent=self.tree)
        self._opening = ""
        self.recent_menu = shortcuts.add_menu(self, "Open recent", "file")     # File > Open recent
        self.recent_menu.aboutToShow.connect(self.fill_recent_menu)
        explain(self.open_btn, "home.open")
        explain(self.reload_btn, "home.reload")
        explain(self.all_days_btn, "home.all_days")
        header_tips(self.tree, {i: f"home.col.{c}" for i, c in enumerate(COL_KEYS)})
        on_theme_changed(self, lambda _s: self._theme_changed())
        # Easy mode: the Kind column, the full h5 attribute table, the SDD geometry and the sgm_report notes are Advanced
        mark_advanced_column(self.tree, KIND_COL)
        on_mode_changed(self, lambda _advanced: self._columns.schedule())      # Kind shown or hidden
        on_mode_changed(self, lambda _adv: (self._render_details(), self._mode_words()), call_now=False)
        self.fill_recent()
        self.fill_recent_menu()
        self.update_actions()
        QTimer.singleShot(0, self, self.refresh_recent)          # once the window is up: which recent ones exist

    def reading(self) -> bool:
        """An open (a discovery job) is in progress."""
        return any(k.startswith("discover:") for k in jobs.of(self).running())

    def tab_after(self, first: QWidget) -> None:
        """The welcome card's buttons and the recent list right after `first` in the Tab order: with no project open
        the first Tab lands on Open project…, not after eight other stops. Hidden (a project open), they are skipped."""
        chain = [first, *self.empty_state.buttons, self.recent_list]
        for a, b in zip(chain, chain[1:]):
            QWidget.setTabOrder(a, b)

    def update_actions(self) -> None:
        busy = self.reading()
        set_enabled(self.reload_btn, self.project is not None and not busy, "open a project first")
        self.all_days_btn.setVisible(self.project is not None and bool(self.project.days_left_out) and not self._failed)
        self.all_days_btn.setEnabled(not busy)
        opening = busy and bool(self._opening) and self.project is None
        self.empty_state.set_shown(self.project is None and not self._failed and not opening)
        self.failure_state.set_shown(bool(self._failed) and not opening)
        self.opening_state.set_shown(opening)
        self.subtitle.setText(SUBTITLE[(is_advanced(), self.project is not None)])

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
        quiet: a failure is a calm note in the path line (the last project at start), not the failure panel.
        A new request supersedes one still in progress: that one is cancelled and its late result dropped (a share
        that does not answer can hold a discovery for 20 s in one call, and the new path must not wait for it)."""
        mgr = jobs.of(self)
        for key in mgr.running():
            if key.startswith("discover:"):
                mgr.cancel(key)
        self._request += 1
        req = self._request
        path = Path(path)
        self._quiet = quiet
        self._last_path = path
        self.path_label.setText(f"reading {path} …")
        self.open_btn.setEnabled(False)
        self._opening = path.name or str(path)
        self.opening_state.set_content(f"Reading {self._opening} …")
        work = work_folders()
        job = mgr.submit(f"discover:{req}", lambda cb: discover(path, cb, work),
                         on_done=lambda r: req == self._request and self._discovered(r),
                         on_failed=lambda t: req == self._request and self._on_failed(t),
                         on_end=lambda _s: self._open_ended(), label=f"reading {path.name}", taskbar=self.taskbar,
                         report=False)
        job.progressed.connect(self._opening_progress)
        self.update_actions()
        self.opening_changed.emit(self._opening)

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

    def refresh_facts(self, chosen: str | None = None) -> None:
        """Read the sample sheet and the configuration rows again (a sheet chosen or written, a fixed line saved, a
        calibration imported) and show the summary. `chosen`: the session's chosen sheet ('' = none; None reads the
        session file)."""
        if self.project is not None:
            self.facts = overview.project_facts(self.project, chosen)
            self._render_details()

    def show_again(self) -> None:
        """Show the open project again after its scans were grouped anew in place (a kind set by hand): the tree, the
        summary, and the window's session (project_opened)."""
        if self.project is not None:
            self._show_project(self.project, overview.project_facts(self.project))

    def shutdown(self) -> None:
        """Let running jobs finish before the widget goes away (a QThread destroyed while running aborts Qt)."""
        jobs.of(self).wait_idle(30000)

    def _opening_progress(self, i: int, n: int, _msg: str = "") -> None:
        if self._opening:
            self.opening_state.label.setText(f"Reading {self._opening} … {i} of {plural(n, 'scan')}" if n else
                                             f"Reading {self._opening} …")

    def _open_ended(self) -> None:
        self.open_btn.setEnabled(not self.reading())
        if not self.reading():
            self._opening = ""
        self.update_actions()
        self.opening_changed.emit("")

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
        if getattr(project, "work", None) is None:      # a project discovered elsewhere (tests, the demo, distcheck)
            from aurora.core.model import work_folder_for
            project.work = work_folder_for(project.root, work_folders())
        if project.work is not None and not Path(project.work).parent.is_dir():
            log.warning("%s: the working folder %s is not reachable: not used", project.root, project.work)
            project.work = None
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
            # the day's and the holder's counts in the first column, which has the room (M01: '28…ns', '4 co…acks')
            counts = f"{plural(len(day.holders), 'holder')}, {plural(len(day.scans) + len(day.continued), 'scan')}"
            d_item = QTreeWidgetItem([f"{day.date or day.path.name}  ·  {counts}", "", "", "", note, "", "", ""])
            d_item.setToolTip(0, f"{day.date or day.path.name}: {counts}")
            d_item.setData(0, Qt.UserRole, None)
            if day.continued:
                d_item.setToolTip(STATUS_COL, f"{plural(len(day.continued), 'stack')} of this day continue a holder of "
                                              "an earlier day (recorded after midnight): listed under that holder")
            self.tree.addTopLevelItem(d_item)
            d_item.setFirstColumnSpanned(not note)           # the day and its counts across the row (M01)
            for holder in day.holders:
                n_ok = len(holder.stacks)
                title = f"holder {holder.name}" + (f" ({holder.display})" if holder.display != holder.name else "")
                summary = (f"{title}  ·  {n_ok} complete {'stack' if n_ok == 1 else 'stacks'}"
                           + (f", {len(holder.dropped)} not used" if holder.dropped else ""))
                h_item = QTreeWidgetItem([summary, "", "", "", "", "", "", ""])
                h_item.setToolTip(0, summary + (f"\n{len(holder.dropped)} not used: {NOT_USED_TIP}" if holder.dropped
                                                else ""))
                h_item.setData(0, HOLDER_ROLE, holder)
                d_item.addChild(h_item)
                h_item.setFirstColumnSpanned(True)           # its summary across the row: never cut (M01)
                for s in holder.scans:
                    h_item.addChild(self._scan_item(s))
            for s in day.others:
                d_item.addChild(self._scan_item(s))
            d_item.setExpanded(True)
            for i in range(d_item.childCount()):
                d_item.child(i).setExpanded(True)
        self._columns.refit()

    def _scan_item(self, s: ScanInfo) -> QTreeWidgetItem:
        when = f"{s.time} {later_note(s.day, s.continues)}" if s.continues else s.time
        it = QTreeWidgetItem([s.name, when, s.kind, edge_words(s.edge_text), s.status.replace("_", " "),
                              s.energy_text if is_advanced() else short_energies(s),
                              str(s.n_points) if s.n_points else "", ""])
        it.setToolTip(5, s.energy_text)
        it.setData(0, Qt.UserRole, s)
        it.setToolTip(0, s.name)
        if s.edge_text:
            it.setToolTip(3, s.edge_text)
        it.setData(FLAGS_COL, FLAG_ROLE, (list(s.flags), s.error or ""))
        self._flag_cell(it)
        set_item_tone(it, tone_of(s.status), STATUS_COL)          # word plus icon, colour from the tokens
        if s.flags or s.error:
            set_item_tone(it, "warn", FLAGS_COL)
        if s.status_reason:
            it.setToolTip(STATUS_COL, s.status_reason)
        if s.continues:
            it.setToolTip(TIME_COL, f"recorded on {s.day}, after midnight: it continues holder {s.holder}, "
                                    f"which started on {s.continues}")
        return it

    @staticmethod
    def _flag_cell(it: QTreeWidgetItem) -> None:
        """The Flags cell: plain words in Easy mode ('detectors 1 and 2 saturated'), the codes in Advanced; the full
        flag text in the cell's tooltip."""
        flags, error = it.data(FLAGS_COL, FLAG_ROLE) or ([], "")
        codes = ", ".join(flags)
        shown = codes if is_advanced() else "; ".join(flag_words(f) for f in flags)
        it.setText(FLAGS_COL, shown + (f"  {error}" if error else ""))
        if flags or error:
            it.setToolTip(FLAGS_COL, codes + (f"  {error}" if error else ""))

    def _mode_words(self) -> None:
        """The cells that read differently by mode (Flags), and the subtitle."""
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(FLAGS_COL, FLAG_ROLE) is not None:
                self._flag_cell(it)
            s = it.data(0, Qt.UserRole)
            if isinstance(s, ScanInfo):             # the energies: '670–735 eV' in Easy, with decimals in Advanced
                it.setText(5, s.energy_text if is_advanced() else short_energies(s))
            stack.extend(it.child(i) for i in range(it.childCount()))
        self.subtitle.setText(SUBTITLE[(is_advanced(), self.project is not None)])
        self._columns.schedule()

    def on_link(self, url) -> None:
        """Links of the summary panel: 'glossary:<key>' opens the glossary (empty key: its top), 'aurora:help:<doc>#<a>'
        a manual, 'aurora:goto:<page>' a perspective; anything else is opened by the system."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        text = url.toString() if isinstance(url, QUrl) else str(url)
        if text.startswith("glossary:"):
            from aurora.app.help import open_glossary
            open_glossary(self, text.split(":", 1)[1] or None)
        elif text.startswith("aurora:help:"):
            doc, _sep, anchor = text[len("aurora:help:"):].partition("#")
            from aurora.app.help import open_help
            open_help(self, doc or "USER_GUIDE.md", anchor or None)
        elif text.startswith("aurora:goto:"):
            from aurora.app.nav import window_of
            win = window_of(self)
            if win is not None and hasattr(win, "go_to"):
                win.go_to(text[len("aurora:goto:"):])
        elif isinstance(url, QUrl) and url.isValid():
            QDesktopServices.openUrl(url)

    def _on_select(self, cur: QTreeWidgetItem | None, _prev=None) -> None:
        s = cur.data(0, Qt.UserRole) if cur is not None else None
        if isinstance(s, ScanInfo):
            self._shown = s
            self._render_details()
            self.scan_selected.emit(s)
            self._check_maps(s)
        elif self.project is not None and cur is not None and cur.data(0, HOLDER_ROLE) is not None:
            self._shown = None                        # a holder row: what the holder holds (M22)
            self._shown_holder = cur.data(0, HOLDER_ROLE)
            self.details_card.set_title("Holder")
            self.details.setHtml(self._holder_html(self._shown_holder))
        elif self.project is not None and (self._shown is not None or getattr(self, "_shown_holder", None)):
            self._shown = None                        # a day row: the project summary again
            self._shown_holder = None
            self._render_details()

    def scan_context_menu(self, item: QTreeWidgetItem) -> QMenu | None:
        """The right-click menu of a stack: Treat as a sample scan / Treat as a test, and back to the rules of its
        name and energies when its kind was set by hand. None for any other row."""
        s = item.data(0, Qt.UserRole) if item is not None else None
        if not isinstance(s, ScanInfo) or s.kind != "stack" or s.status == "empty":
            return None
        menu = QMenu(self.tree)
        if s.status == "test":
            a = menu.addAction("Treat as a sample scan", lambda sc=s: self.kind_override_requested.emit(sc, "sample"))
            a.setStatusTip("a sample, not a beamline test: it is offered in Reduce like the other stacks")
        else:
            a = menu.addAction("Treat as a test", lambda sc=s: self.kind_override_requested.emit(sc, "test"))
            a.setStatusTip("a beamline test, not a sample: it is left out of Reduce and the report")
        if s.override:
            menu.addAction("Use the rules again", lambda sc=s: self.kind_override_requested.emit(sc, ""))
        return menu

    def _tree_menu_at(self, pos) -> None:
        menu = self.scan_context_menu(self.tree.itemAt(pos))
        if menu is not None:
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            menu.deleteLater()

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
        """Getting started in Easy mode; in Advanced mode, the kinds of folder that open (aurora.app.guide)."""
        return guide.getting_started_html(is_advanced(), TOKENS)

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

    def set_project_notes(self, notes: list[str]) -> None:
        """Sentences the window adds to the project summary (outlines saved earlier in the beamtime: M03)."""
        self.project_notes = [n for n in notes if n]
        if self.project is not None and self._shown is None:
            self._render_details()

    def _project_html(self) -> str:
        p = self.project
        e = html.escape
        rows = overview.project_rows(p, self.facts) if is_advanced() else overview.easy_project_rows(p, self.facts)
        notes = "".join(f"<p><b>{e(n)}</b></p>" for n in getattr(self, "project_notes", []))
        return (f"<h3>{e(p.root.name)}</h3>{notes}{self._table(rows)}"
                f"<p style='color:{TOKENS['muted']}'>Select a scan to read its summary; double-click a complete stack "
                "to outline a sample on it in Reduce.</p>")

    def _holder_html(self, h) -> str:
        """A holder's summary: its survey, its stacks with their status, the ones not used and why."""
        e = html.escape
        rows = [("survey", h.survey.name if h.survey is not None else "none (the stacks were recorded before any "
                                                                       "survey)")]
        for s in h.stacks:
            rows.append(("complete stack", f"{s.time} {s.label} · {edge_words(s.edge_text)} · {short_energies(s)}"))
        for s in h.dropped:
            why = s.status_reason or s.status.replace("_", " ")
            rows.append(("not used", f"{s.time} {s.label} · {why}"))
        return (f"<h3>holder {e(h.display)}</h3>{self._table(rows)}"
                f"<p style='color:{TOKENS['muted']}'>Double-click a complete stack to outline a sample on it in "
                "Reduce; the Survey shows them on the map of the holder.</p>")

    def _scan_html(self, s: ScanInfo) -> str:
        e = html.escape
        key = str(s.path)
        if is_advanced():
            rows = overview.scan_rows(s, self.project, self._map_checks.get(key), self._map_errors.get(key, ""))
        else:                                     # plain words: the edge and the flags as on the tree
            rows = [(k, edge_words(v) if k == "edge" else ("; ".join(flag_words(f) for f in s.flags) if k == "flags"
                                                            else v))
                    for k, v in overview.easy_scan_rows(s, self.project, self._map_checks.get(key),
                                                        self._map_errors.get(key, ""))]
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
