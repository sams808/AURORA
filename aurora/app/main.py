"""Main window: top bar (crumb 'project ▸ day ▸ holder ▾', Easy / Advanced switch), perspectives rail, stacked
perspectives, menus (File, Edit, View with Go to and the holder steps, Help with the manuals, the shortcuts list, the
self-test, Report a problem, updates and licences), the window's background jobs (window.jobs)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QFont, QFontDatabase, QKeySequence
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QMenu, QPushButton, QStackedWidget,
                               QToolButton, QVBoxLayout, QWhatsThis, QWidget)

from aurora import APP_NAME, ORG_NAME, __version__
from aurora.app.about import AboutDialog
from aurora.app.check import CheckPerspective
from aurora.app.compare import ComparePerspective
from aurora.app.fit import FitPerspective
from aurora.app.library import Library
from aurora.app.maps import MapsPerspective
from aurora.app.home import HomePerspective
from aurora.app.reduce import ReducePerspective
from aurora.app.report import ReportPerspective
from aurora.app.session import Session
from aurora.app.settings import recent_projects
from aurora.app.settings_dialog import SettingsDialog, get as setting
from aurora.app.survey import SurveyPerspective
from aurora.app.icons import app_icon, mark_pixmap, two_state_icon
from aurora.app.jobs import JobManager, all_managers
from aurora.app.theme import TOKENS, apply_theme, follow_system, on_theme_changed
from aurora.app.vocab import plural
from aurora.app import mode as ui_mode
from aurora.app import shortcuts
from aurora.app import stack_cache

PERSPECTIVES = [
    ("home", "Home"), ("survey", "Survey"), ("reduce", "Reduce"), ("check", "Check"), ("compare", "Compare"),
    ("fit", "Fit"), ("maps", "Maps"), ("report", "Report"),
]
# perspectives shown only in Advanced mode (Easy mode = John Bussey's basic path: reduce, check, compare, fit, report)
ADVANCED_PERSPECTIVES = {"maps"}
MODE_STATUS = {False: "Easy mode: the basic path of John Bussey's GUI", True: "Advanced mode: every option"}
MIN_SIZE = (1200, 760)                  # DESIGN.md: the smallest window every perspective fits without clipping
CLOSE_WAIT_S = 5.0                      # closing waits at most this long for cancelled jobs to stop
SEP = "▸"


def _section(a: QAction, name: str) -> QAction:
    a.setProperty(shortcuts.PROP_SECTION, name)
    return a


class Crumb(QWidget):
    """'project ▸ day ▸ holder ▾' in the top bar; the holder part is a menu of every holder of the project."""

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("crumbbox")
        self.session = session
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.project_label = QLabel("no project")
        self.sep1, self.day_label, self.sep2 = QLabel(SEP), QLabel(""), QLabel(SEP)
        for w in (self.project_label, self.sep1, self.day_label, self.sep2):
            w.setObjectName("crumb")
            lay.addWidget(w)
        self.holder_btn = QToolButton()
        self.holder_btn.setObjectName("holderbtn")
        self.holder_btn.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self.holder_btn)
        self.menu.aboutToShow.connect(self.fill_menu)
        self.holder_btn.setMenu(self.menu)
        from aurora.app.help_text import explain
        explain(self.holder_btn, "main.holder")
        lay.addWidget(self.holder_btn)
        session.project_changed.connect(lambda _p: self.refresh())
        session.holder_changed.connect(lambda _k: self.refresh())
        session.zones_changed.connect(self.fill_menu)
        self.refresh()

    def parts(self) -> list[str]:
        ses = self.session
        if ses.project is None:
            return []
        key = ses.current_holder
        h = ses.holder(key)
        day = ses.holder_day(key) or (ses.project.days[0].date if ses.project.days else "")
        return [p for p in (ses.project.root.name, day, h.name if h else "") if p]

    def text(self) -> str:
        parts = self.parts()
        return f" {SEP} ".join(parts) if parts else "no project"

    def entry(self, key: str) -> str:
        """'N5 · 2026-11-05 · 1 stack · 2 zones'."""
        day, h = self.session.holder_keys[key]
        hz = self.session.zones.holders.get(key)
        n_z = len(hz.zones) if hz else 0
        return f"{h.name} · {day} · {plural(len(h.stacks), 'stack')} · {plural(n_z, 'zone')}"

    def refresh(self) -> None:
        parts = self.parts()
        self.project_label.setText(parts[0] if parts else "no project")
        self.day_label.setText(parts[1] if len(parts) > 1 else "")
        holder = parts[2] if len(parts) > 2 else ""
        for w in (self.sep1, self.day_label):
            w.setVisible(len(parts) > 1)
        self.sep2.setVisible(bool(holder))
        self.holder_btn.setVisible(bool(holder))
        self.holder_btn.setText(f"{holder} ▾" if holder else "")
        self.fill_menu()

    def fill_menu(self) -> None:
        self.menu.clear()
        for key in self.session.holder_keys:
            a = self.menu.addAction(self.entry(key))
            a.setCheckable(True)
            a.setChecked(key == self.session.current_holder)
            a.setData(key)
            a.triggered.connect(lambda _=False, k=key: self.session.set_holder(k))

    def holder_actions(self) -> list[QAction]:
        self.fill_menu()
        return list(self.menu.actions())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(*MIN_SIZE)
        self.resize(1440, 900)
        self.jobs = JobManager(self)           # every background job of the window (jobs.of(widget) finds it)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.session = Session(self)
        outer.addWidget(self._top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._rail())
        self.stack = QStackedWidget()
        self.session.status.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.home = HomePerspective()
        self.home.project_opened.connect(self._on_project)
        self.home.scan_activated.connect(self.open_scan_in_reduce)
        self.survey = SurveyPerspective(self.session)
        self.survey.open_requested.connect(self.open_scan_in_reduce)
        self.reduce = ReducePerspective(self.session)
        self.report = ReportPerspective(self.session)
        self.check = CheckPerspective(self.session)
        self.library = Library(self)
        self.compare = ComparePerspective(self.library)
        self.fit = FitPerspective(self.library)
        self.maps = MapsPerspective(self.session)
        for w in (self.reduce, self.check, self.maps, self.fit):      # any perspective can send spectra to Compare
            w.library = self.library
        self.pages: dict[str, QWidget] = {"home": self.home, "survey": self.survey, "reduce": self.reduce, "check": self.check,
                                          "compare": self.compare, "fit": self.fit, "maps": self.maps, "report": self.report}
        for w in self.pages.values():
            self.stack.addWidget(w)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage(f"{APP_NAME} {__version__}")

        self._menus()
        shortcuts.adopt(self)                  # menu entries the perspectives added before they were inside the window
        stack_cache.install(self)              # File > Re-reduce the stack with the project ROI bins (Advanced, I49)
        self._help = None
        self.setAcceptDrops(True)              # a folder or an .h5 dropped on the window opens it (home.open_path)
        for key in sorted(ADVANCED_PERSPECTIVES):
            ui_mode.mark_advanced_perspective(key)
        self._mode_seen: bool | None = None
        ui_mode.on_mode_changed(self, self._mode_changed)
        on_theme_changed(self, lambda _s: self._rail_icons())
        self.show_perspective("home")
        central.setFocusPolicy(Qt.ClickFocus)      # at start the focus rests on the page, not on a top-bar button
        central.setFocus()
        self.home.tab_after(central)               # ... and the first Tab reaches the welcome card's Open project…

    # ------------------------------------------------------------------ chrome
    def _top_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setFixedHeight(48)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(14)
        mark = QLabel()
        mark.setPixmap(mark_pixmap(28))
        mark.setAccessibleName(f"{APP_NAME} logo")
        lay.addWidget(mark)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brand")
        lay.addWidget(brand)
        self.crumb = Crumb(self.session)
        lay.addWidget(self.crumb)
        lay.addStretch(1)
        self.mode_btn = QToolButton()
        self.mode_btn.setObjectName("modebtn")
        self.mode_btn.setCheckable(True)
        from aurora.app.help_text import explain
        explain(self.mode_btn, "main.mode")
        self.mode_btn.clicked.connect(lambda on: ui_mode.set_advanced(on))
        lay.addWidget(self.mode_btn)
        return bar

    def _rail(self) -> QWidget:
        rail = QWidget()
        rail.setObjectName("rail")
        rail.setFixedWidth(92)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(6, 10, 6, 10)
        lay.setSpacing(2)
        self.rail_buttons: dict[str, QToolButton] = {}
        for i, (key, title) in enumerate(PERSPECTIVES):
            b = QToolButton()
            b.setObjectName("railbtn")
            b.setText(title)
            b.setToolTip(shortcuts.native_text(f"{title} (Ctrl+{i + 1})"))
            b.setAccessibleName(title)
            b.setProperty("aurora_icon", key)
            b.setIcon(two_state_icon(key, TOKENS["rail_text"], TOKENS["on_accent"]))
            b.setIconSize(QSize(22, 22))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.clicked.connect(lambda _=False, k=key: self.show_perspective(k))
            lay.addWidget(b)
            self.rail_buttons[key] = b
        lay.addStretch(1)
        b = QToolButton()
        b.setObjectName("railbtn")
        b.setText("Settings")
        b.setToolTip("Settings (appearance, text size, report author, defaults)")
        b.setAccessibleName("Settings")
        b.setProperty("aurora_icon", "settings")
        b.setIcon(two_state_icon("settings", TOKENS["rail_text"], TOKENS["on_accent"]))
        self.settings_rail_btn = b
        b.setIconSize(QSize(22, 22))
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.clicked.connect(self.show_settings)
        lay.addWidget(b)
        return rail

    def _action(self, menu: QMenu | None, text: str, slot, key=None, section: str = "General", tip: str = "") -> QAction:
        a = QAction(text, self)
        if key is not None:
            a.setShortcut(key if isinstance(key, QKeySequence) else QKeySequence(key))
        a.setMenuRole(QAction.NoRole)
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(tip)
        a.triggered.connect(lambda _=False: slot())
        _section(a, section)
        if menu is not None:
            menu.addAction(a)
        else:
            self.addAction(a)
        return a

    def _menus(self) -> None:
        m = self.menuBar()
        self.menus: dict[str, object] = {}
        # ---- File
        file = m.addMenu("&File")
        self.menus["file"] = file
        self._action(file, "&Open project…", self.home.choose_project, QKeySequence.Open, "File",
                     "a beamtime folder, a loose folder of scans or one .h5 file")
        self._action(file, "&Reload", lambda: self.home.reload_btn.click(), QKeySequence.Refresh, "File",
                     "read the project folder again")
        self.menu_anchors = {"file": file.addSeparator()}      # shortcuts.add(menu='file') inserts above Settings
        a = self._action(file, "&Settings…", self.show_settings, QKeySequence.Preferences, "File")
        a.setMenuRole(QAction.PreferencesRole)          # macOS: the application menu's Settings entry
        file.addSeparator()
        a = self._action(file, "&Quit", self.close, QKeySequence.Quit, "File")
        a.setMenuRole(QAction.QuitRole)
        # ---- Edit (Undo / Redo / History arrive through shortcuts.add(menu='edit'))
        edit = m.addMenu("&Edit")
        self.menus["edit"] = edit
        # ---- View
        view = m.addMenu("&View")
        self.menus["view"] = view
        self.mode_action = QAction("&Advanced mode", self)
        self.mode_action.setCheckable(True)
        self.mode_action.setShortcut("Ctrl+Shift+A")
        self.mode_action.setMenuRole(QAction.NoRole)
        self.mode_action.setStatusTip("every option (Advanced) or the basic path of John Bussey's GUI (Easy)")
        _section(self.mode_action, "View")
        self.mode_action.toggled.connect(lambda on: on != ui_mode.is_advanced() and ui_mode.set_advanced(on))
        view.addAction(self.mode_action)
        view.addSeparator()
        appearance = view.addMenu("Appearance")
        group = QActionGroup(self)
        self.appearance_actions = {}
        for key, label in (("system", "Follow the system"), ("light", "Light"), ("dark", "Dark")):
            a = QAction(label, self, checkable=True)
            a.setActionGroup(group)
            a.triggered.connect(lambda _=False, k=key: self.set_appearance(k))
            appearance.addAction(a)
            self.appearance_actions[key] = a
        size = view.addMenu("Text size")
        group = QActionGroup(self)
        self.size_actions = {}
        for key, label in (("normal", "Normal"), ("large", "Large"), ("larger", "Larger")):
            a = QAction(label, self, checkable=True)
            a.setActionGroup(group)
            a.triggered.connect(lambda _=False, k=key: self.set_text_size(k))
            size.addAction(a)
            self.size_actions[key] = a
        self._sync_view_actions()
        view.addSeparator()
        self.prev_holder_action = self._action(view, "&Previous holder", lambda: self.step_holder(-1), "Ctrl+PgUp",
                                               "View", "the previous holder of the project (wraps around)")
        self.next_holder_action = self._action(view, "&Next holder", lambda: self.step_holder(1), "Ctrl+PgDown",
                                               "View", "the next holder of the project (wraps around)")
        self.prev_holder_action.setShortcuts(shortcuts.holder_keys(-1))     # + Option-Command-arrow on macOS
        self.next_holder_action.setShortcuts(shortcuts.holder_keys(1))
        goto = view.addMenu("&Go to")
        self.goto_actions: dict[str, QAction] = {}
        self._hint_actions: dict[str, QAction] = {}
        for i, (key, title) in enumerate(PERSPECTIVES):
            self.goto_actions[key] = self._action(goto, title, lambda k=key: self.go_to(k), f"Ctrl+{i + 1}", "Go to")
            if key in ADVANCED_PERSPECTIVES:      # its shortcut in Easy mode: a status hint, the entry being hidden
                h = self._action(None, f"{title} (Advanced mode only)", lambda k=key: self.go_to(k), f"Ctrl+{i + 1}",
                                 "Go to")
                self._hint_actions[key] = h
        self.menu_anchors["view"] = view.addSeparator()       # shortcuts.add(menu='view') inserts above Full screen
        self.fullscreen_action = self._action(view, "&Full screen", self.toggle_fullscreen, None, "View")
        self.fullscreen_action.setShortcuts(shortcuts.fullscreen_keys())      # F11; Ctrl+Cmd+F on macOS
        # ---- window actions without a menu entry
        self.primary_action = self._action(None, "Run the main action of the page", self.click_primary, "Ctrl+Return",
                                           "General", "clicks the highlighted button of the current perspective")
        # ---- Help
        help_ = m.addMenu("&Help")
        self.menus["help"] = help_
        from aurora.app.help import DOCS
        self.help_actions = {}
        for name, title, tip in DOCS:
            self.help_actions[name] = self._action(help_, title, lambda n=name: self.show_help(n), None, "Help", tip)
        help_.addSeparator()
        self.page_help_action = self._action(help_, "Help on this page", self.help_on_page, QKeySequence.HelpContents,
                                             "Help", "the manual section of the current perspective or tab")
        self.shortcuts_action = self._action(help_, "&Keyboard shortcuts", self.show_shortcuts, "Ctrl+/", "Help",
                                             "every shortcut of the window")
        self.whats_this_action = self._action(help_, "What's this?", QWhatsThis.enterWhatsThisMode, "Shift+F1", "Help",
                                              "click any control to read what it does")
        help_.addSeparator()
        self.demo_action = self._action(help_, "Open the &demo beamtime", self.open_demo, None, "Help",
                                        "a small synthetic beamtime to try every perspective (the tutorials use it)")
        self.selftest_action = self._action(help_, "Run self-test…", self.run_selftest, None, "Help",
                                            "check every part of this installation on a synthetic beamtime")
        help_.addSeparator()
        self.problem_action = self._action(help_, "Report a problem…", self.report_problem, None, "Help",
                                           "what to send, with the diagnostics to copy")
        self.update_action = self._action(help_, "Check for updates…", self.check_updates, None, "Help",
                                          "ask GitHub whether a newer AURORA exists (nothing is downloaded)")
        self.licences_action = self._action(help_, "Licences…", self.show_licences, None, "Help",
                                            "AURORA's licence, the credits and the third-party licences")
        self.menu_anchors["help"] = help_.addSeparator()      # shortcuts.add(menu='help') inserts above About
        a = self._action(help_, f"&About {APP_NAME}", self.show_about, None, "Help")
        a.setMenuRole(QAction.AboutRole)

    # ------------------------------------------------------------------ behaviour
    def current_key(self) -> str:
        return next((k for k, w in self.pages.items() if w is self.stack.currentWidget()), "home")

    def show_perspective(self, key: str) -> None:
        self.stack.setCurrentWidget(self.pages[key])
        self.rail_buttons[key].setChecked(True)

    def go_to(self, key: str) -> bool:
        """Open a perspective from a shortcut or a link: one hidden in Easy mode answers with a status hint."""
        if ui_mode.perspective_hidden(key):
            self.statusBar().showMessage(self.perspective_hint(key), 6000)
            return False
        self.show_perspective(key)
        return True

    def navigate(self, perspective: str, holder: str | None = None, zone: int | None = None, sid: str | None = None) -> bool:
        """Select the holder (or the holder of `sid`), the zone and the stack, then show the perspective (nav.go).
        Returns False when the perspective is hidden in Easy mode (a status hint says so instead)."""
        ses = self.session
        key = holder if holder in ses.holder_keys else (ses.holder_of_stack(sid) if sid else None)
        if key is not None:
            ses.set_holder(key)
        if zone is not None and ses.zone(zone) is not None:
            ses.select_zone(zone)
        if sid is not None:
            for i in range(self.reduce.scan_list.count()):
                if self.reduce.scan_list.item(i).data(Qt.UserRole) == sid:
                    self.reduce.scan_list.setCurrentRow(i)
                    break
        return self.go_to(perspective)

    @staticmethod
    def perspective_hint(key: str) -> str:
        title = dict(PERSPECTIVES).get(key, key)
        return shortcuts.native_text(f"{title} is part of Advanced mode (Ctrl+Shift+A)")

    def step_holder(self, step: int) -> None:
        """Previous / next holder of the project, wrapping around."""
        keys = list(self.session.holder_keys)
        if not keys:
            self.statusBar().showMessage("open a project first", 4000)
            return
        cur = self.session.current_holder
        i = keys.index(cur) if cur in keys else -1
        self.session.set_holder(keys[(i + step) % len(keys)])

    def click_primary(self) -> bool:
        """Ctrl+Return: click the visible, enabled primary button of the current perspective."""
        page = self.stack.currentWidget()
        for b in page.findChildren(QPushButton, "primary"):
            if b.isVisible() and b.isEnabled():
                b.click()
                return True
        return False

    def _rail_icons(self) -> None:
        for b in list(self.rail_buttons.values()) + [self.settings_rail_btn]:
            b.setIcon(two_state_icon(b.property("aurora_icon"), TOKENS["rail_text"], TOKENS["on_accent"]))

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def show_settings(self) -> None:
        SettingsDialog(self).exec()
        self._sync_view_actions()

    # ------------------------------------------------------------------ mode, theme, help
    def _mode_changed(self, advanced: bool) -> None:
        self.mode_btn.blockSignals(True)
        self.mode_btn.setChecked(advanced)
        self.mode_btn.setText("Advanced mode" if advanced else "Easy mode")
        self.mode_btn.blockSignals(False)
        self.mode_action.blockSignals(True)
        self.mode_action.setChecked(advanced)
        self.mode_action.blockSignals(False)
        hidden = ui_mode.advanced_perspectives() | ADVANCED_PERSPECTIVES
        for key in hidden:
            if key in self.rail_buttons:
                self.rail_buttons[key].setVisible(advanced)
            a = self.goto_actions.get(key)                  # View > Go to
            if a is not None:
                a.setVisible(advanced)
            h = self._hint_actions.get(key)
            if h is not None:
                h.setVisible(not advanced)
                h.setEnabled(not advanced)
        if not advanced and self.current_key() in hidden:
            self.show_perspective("home")
        if self._mode_seen is not None and self._mode_seen != advanced:
            self.statusBar().showMessage(MODE_STATUS[advanced], 5000)
        self._mode_seen = advanced

    def _sync_view_actions(self) -> None:
        from aurora.app.settings_dialog import get
        for key, a in self.appearance_actions.items():
            a.setChecked(key == get("appearance"))
        for key, a in self.size_actions.items():
            a.setChecked(key == get("text_size"))

    def set_appearance(self, key: str) -> None:
        from aurora.app.settings import settings
        s = settings(); s.setValue("appearance", key); s.sync()
        apply_theme()
        self._sync_view_actions()

    def set_text_size(self, key: str) -> None:
        from aurora.app.settings import settings
        s = settings(); s.setValue("text_size", key); s.sync()
        apply_theme()
        self._sync_view_actions()

    def show_help(self, doc: str = "USER_GUIDE.md", anchor: str | None = None) -> None:
        from aurora.app.help import HelpWindow
        if self._help is None:
            self._help = HelpWindow(self)
        self._help.show_doc(doc, anchor)

    def help_anchor(self) -> str:
        """The MANUAL.md section of the current perspective, or of its current tab."""
        from aurora.app.help import page_anchor
        return page_anchor(self.current_key(), self.stack.currentWidget())

    def help_on_page(self) -> None:
        """F1 / Help > Help on this page."""
        self.show_help("MANUAL.md", self.help_anchor())

    def show_shortcuts(self) -> None:
        from aurora.app.diagnostics import ShortcutsDialog
        d = ShortcutsDialog(self)
        self.shortcuts_dialog = d
        d.exec()

    def report_problem(self) -> None:
        from aurora.app.diagnostics import ReportProblemDialog
        ReportProblemDialog(self).exec()

    def show_licences(self) -> None:
        from aurora.app.diagnostics import LicencesDialog
        LicencesDialog(self).exec()

    def check_updates(self) -> None:
        from aurora.app import updates
        self.statusBar().showMessage("checking GitHub for a newer AURORA …", 5000)
        updates.check_now(self)

    def run_selftest(self) -> None:
        """Help > Run self-test…: aurora --selftest in a background job, then the summary (Copy, Open folder)."""
        from aurora.app import diagnostics

        def done(summary) -> None:
            self.statusBar().showMessage(diagnostics.summary_text(summary).splitlines()[0], 10000)
            d = diagnostics.SelftestDialog(summary, self)
            self.selftest_dialog = d
            d.show()                            # the job ended in the background: no modal interruption

        self.statusBar().showMessage("self-test running in the background …", 5000)
        self.jobs.submit("selftest", lambda progress: diagnostics.run_selftest(progress), on_done=done,
                         label="self-test")

    def open_demo(self) -> None:
        """Create (once) and open the synthetic demo beamtime in the user's data folder."""
        from platformdirs import user_data_dir
        from aurora.cli import cmd_demo
        root = Path(user_data_dir(APP_NAME, appauthor=False)) / "demo_beamtime"
        if not (root / "raw").is_dir():
            cmd_demo(root)
        elif not (root / "references").is_dir():          # a demo written by an earlier version: add its spectra
            from aurora.core.testdata import write_demo_extras
            write_demo_extras(root)
        self.home.open_path(root)
        self.show_perspective("home")

    def open_log_folder(self) -> None:
        from aurora.logsetup import log_dir
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))

    def open_scan_in_reduce(self, scan) -> None:
        """Home double-click / Survey footprint click: select the scan's holder and stack, show Reduce."""
        for key, (day, h) in self.session.holder_keys.items():
            if scan in h.stacks or scan in h.dropped or scan is h.survey:
                self.session.set_holder(key)
                break
        self.show_perspective("reduce")
        if scan.kind == "stack" and scan.status == "complete":
            sid = self.session.stack_id(scan)
            for i in range(self.reduce.scan_list.count()):
                if self.reduce.scan_list.item(i).data(Qt.UserRole) == sid:
                    self.reduce.scan_list.setCurrentRow(i)
                    break

    @staticmethod
    def _dropped_path(event) -> Path | None:
        md = event.mimeData()
        if md is None or not md.hasUrls():
            return None
        for url in md.urls():
            if url.isLocalFile():
                return Path(url.toLocalFile())
        return None

    def dragEnterEvent(self, event) -> None:
        if self._dropped_path(event) is not None:      # no stat here: open_path checks the path in its worker
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dropEvent(self, event) -> None:
        p = self._dropped_path(event)
        if p is None:
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        self.show_perspective("home")
        self.home.open_path(p)

    def closeEvent(self, event) -> None:
        """Cancel every job and wait at most CLOSE_WAIT_S in total behind a 'finishing…' status; a job still running
        after that is handed to the application (its thread is never destroyed while it runs)."""
        managers = all_managers(self)
        if any(not m.idle() for m in managers):
            self.statusBar().showMessage("finishing …")
            for m in managers:
                m.cancel_all()
            deadline = time.monotonic() + CLOSE_WAIT_S
            for m in managers:
                m.wait_idle(max(0, int((deadline - time.monotonic()) * 1000)))
            for m in managers:
                m.keep_running()
        self.session.flush()
        super().closeEvent(event)

    def _on_project(self, project) -> None:
        self.statusBar().showMessage(f"{plural(len(project.scans), 'scan')} in {project.root}")
        self.session.flush()                   # an edit still waiting for its autosave is written before the switch
        self.session.set_project(project)
        self.library.set_project(project)
        self.crumb.refresh()


def fix_working_dir() -> Path | None:
    """At start: a working directory inside the frozen bundle (sys._MEIPASS, an .app) or '/' (a Finder start) becomes
    the home folder, so no relative path or dialog ever points into the installation. Returns the new folder."""
    try:
        cwd = Path.cwd().resolve()
    except OSError:
        cwd = None
    bad = cwd is None or cwd == Path(cwd.anchor) and cwd.anchor in ("/", "\\")
    base = getattr(sys, "_MEIPASS", None)
    if cwd is not None and base:
        try:
            cwd.relative_to(Path(base).resolve())
            bad = True
        except ValueError:
            pass
    if cwd is not None and any(p.suffix == ".app" for p in [cwd, *cwd.parents]):
        bad = True
    if bad:
        home = Path.home()
        try:
            os.chdir(home)
            return home
        except OSError:
            return None
    return None


def create_app(argv: list[str] | None = None) -> QApplication:
    # the GUI thread gets the GIL back within 1 ms (not 5) while a job computes in pure Python: every Qt call it makes
    # releases the GIL, and waiting 5 ms each time froze the window for most of a second on slower machines (MCR-ALS)
    sys.setswitchinterval(min(sys.getswitchinterval(), 0.001))
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    f = QFont()
    from aurora.app.theme import UI_FAMILIES
    families = list(UI_FAMILIES)
    if sys.platform == "darwin":                     # the system font (San Francisco), not Helvetica Neue
        families.insert(0, QFontDatabase.systemFont(QFontDatabase.GeneralFont).family())
    f.setFamilies(families)
    f.setPointSize(10)
    app.setFont(f)
    apply_theme(app)                 # Fusion + palette + style sheet of the light or dark scheme (Settings > appearance)
    follow_system(app)
    return app


def shutdown_app(app: QApplication, *windows: QWidget) -> None:
    """Destroy the windows and the QApplication in a fixed order. Left to interpreter teardown, PySide6 >= 6.12
    segfaults on exit (module globals and the application die in an arbitrary order). Threads still running (jobs a
    closing window handed to the application) are waited for first: a running QThread is never destroyed."""
    import gc
    import shiboken6
    from PySide6.QtCore import QThread
    for w in windows:
        if shiboken6.isValid(w):
            w.close()
    from aurora.app.nav import top_level_widgets
    for w in top_level_widgets():
        if shiboken6.isValid(w):
            w.close()
            w.deleteLater()
    app.processEvents()
    for t in app.findChildren(QThread):
        if shiboken6.isValid(t) and t.isRunning():
            t.wait(30000)
    app.processEvents()
    gc.collect()
    app.processEvents()
    shiboken6.delete(app)


def open_at_start(win: MainWindow, project: Path | None = None) -> None:
    """The project to open once the window is up: the command-line folder, else the last project when Settings asks
    for it. Both go through home.open_path, which looks at the path only in its worker, so an unreachable share
    never blocks the start; the last project is opened from the event loop and fails quietly."""
    if project is not None:                     # aurora <folder>, aurora gui <folder>
        win.home.open_path(Path(project))
    elif setting("open_last_project"):
        rec = recent_projects()
        if rec:
            last = Path(rec[0])
            QTimer.singleShot(0, win, lambda: win.home.open_path(last, quiet=True))


def run(project: Path | None = None) -> int:
    from aurora.logsetup import setup_logging
    from aurora.app import crash, updates
    setup_logging()
    fix_working_dir()
    app = create_app()
    crash.install()
    win = MainWindow()
    win.show()
    open_at_start(win, project)
    QTimer.singleShot(1500, win, lambda: updates.maybe_check_at_start(win))     # opt-in, once a day
    code = app.exec()
    shutdown_app(app, win)
    return code
