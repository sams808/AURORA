"""Main window: top bar (crumb 'project ▸ day ▸ holder ▾', Easy / Advanced switch), perspectives rail, stacked
perspectives, menus (File, Edit, View with Go to and the holder steps, Help with the manuals, the shortcuts list, the
self-test, Report a problem, updates and licences), the window's background jobs (window.jobs)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QFont, QKeySequence
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
from aurora.app import guide
from aurora.app import mode as ui_mode
from aurora.app import shortcuts
from aurora.app import stack_cache

PERSPECTIVES = [
    ("home", "Home"), ("survey", "Survey"), ("reduce", "Reduce"), ("check", "Check"), ("compare", "Compare"),
    ("fit", "Fit"), ("maps", "Maps"), ("report", "Report"),
]
# perspectives shown only in Advanced mode (Easy mode: the main steps with standard settings)
ADVANCED_PERSPECTIVES = {"maps"}
MODE_STATUS = {False: "Easy mode: the main steps with standard settings", True: "Advanced mode: every option"}
NEXT_WIDTH = 420                        # the top bar's Next link is elided to this width (the full sentence: tooltip)
MIN_SIZE = (1200, 760)                  # DESIGN.md: the smallest window every perspective fits without clipping
BANNER_TEXT = ("This beamtime folder is read-only for AURORA (a locked drive, a share without write access, or a "
               "protected folder): your outlines and spectra cannot be saved inside it. Copy the beamtime folder to "
               "this computer and open the copy to keep your work.")
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
        outer.addWidget(self._banner())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._rail())
        self.stack = QStackedWidget()
        self.session.status.connect(lambda m: self.statusBar().showMessage(m, 6000))
        self.home = HomePerspective()
        self.home.project_opened.connect(self._on_project)
        self.home.can_leave = self._can_leave
        self.home.scan_activated.connect(self.open_scan_in_reduce)
        self.survey = SurveyPerspective(self.session)
        self.survey.open_requested.connect(self.open_scan_in_reduce)
        self.reduce = ReducePerspective(self.session)
        self.report = ReportPerspective(self.session)
        self.check = CheckPerspective(self.session)
        self.library = Library(self)
        self.library.warning.connect(lambda m: self.session.warned.emit("Compare library", m))
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
        self._visited: set[str] = set()
        self._report_folder = ""
        self._fit_ran = False
        self._guide_timer = QTimer(self)
        self._guide_timer.setSingleShot(True)
        self._guide_timer.setInterval(100)
        self._guide_timer.timeout.connect(self.refresh_guide)
        self._wire_guide()
        self.show_perspective("home")
        central.setFocusPolicy(Qt.ClickFocus)      # at start the focus rests on the page, not on a top-bar button
        central.setFocus()

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
        # Easy mode, once a project is open: 'Next: Check – look at each spectrum …' (a link to that page)
        self.next_label = QLabel("")
        self.next_label.setObjectName("nextlink")
        self.next_label.setTextFormat(Qt.RichText)
        self.next_label.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        self.next_label.setAccessibleName("next step")
        self.next_label.linkActivated.connect(self._next_link)
        self.next_label.hide()
        lay.addWidget(self.next_label)
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
            b.setToolTip(shortcuts.native_text(f"{title} (Ctrl+{i + 1}): {guide.RAIL_TIPS.get(key, '')}".rstrip(": ")))
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
                     "a beamtime folder, a loose folder of scans or a scan folder")
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
        self.mode_action.setStatusTip("every option (Advanced) or the main steps with standard settings (Easy)")
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
        self._action(view, "&Full screen", self.toggle_fullscreen, "F11", "View")
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
        if getattr(self, "_visited", None) is not None:
            if self.session.project is not None:
                self._visited.add(key)
            self.schedule_guide()

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
        self.schedule_guide()

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
            if cmd_demo(root):                              # the folder holds something else: never written into
                self.statusBar().showMessage(f"the demo beamtime needs a new or empty folder: {root} is not empty",
                                             15000)
                return
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
        """Zone edits that could not be saved are exported, discarded or kept (Cancel keeps the window open). Then
        cancel every job and wait at most CLOSE_WAIT_S in total behind a 'finishing…' status; a job still running
        after that is handed to the application (its thread is never destroyed while it runs)."""
        if not self.session.confirm_leave("close the window"):
            event.ignore()
            return
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
        self.session.release_lock()             # another AURORA may edit the project now
        super().closeEvent(event)

    def _can_leave(self, project) -> bool:
        """Home asks before another project replaces the open one: edits that could not be saved are exported,
        discarded or kept (Cancel keeps the open project). Reading the same folder again (Reload) loses nothing."""
        cur = self.session.project
        if cur is None or Path(cur.root) == Path(project.root):
            return True
        return self.session.confirm_leave("open another project")

    # ------------------------------------------------------------------ getting started (Easy mode)
    def _wire_guide(self) -> None:
        ses = self.session
        for sig in (ses.project_changed, ses.zones_changed, ses.check_queue_changed, ses.save_state,
                    self.library.changed, self.home.opening_changed, self.check.saved, self.report.built,
                    self.fit.fitted):
            sig.connect(self.schedule_guide)          # a bound method: disconnected with the window (never a lambda)
        self.report.built.connect(self._built)
        self.fit.fitted.connect(self._fitted)
        ses.project_changed.connect(self._project_for_guide)
        self.home.guide_strip.go.connect(self.guide_go)
        self.home.guide_strip.hidden_by_user.connect(self.hide_guide)
        from aurora.app.settings_dialog import on_settings_changed
        on_settings_changed(self, self.schedule_guide)

    def _built(self, folder: str) -> None:
        self._report_folder = folder

    def _fitted(self) -> None:
        self._fit_ran = True

    def _project_for_guide(self, _project) -> None:
        """A new project starts the guide again (the pages seen, the build and the fit belong to the last one)."""
        import shiboken6
        if not shiboken6.isValid(self):
            return
        self._visited = {self.current_key()} if self.session.project is not None else set()
        self._report_folder = ""
        self._fit_ran = False
        self.check.n_saved = 0
        self.schedule_guide()

    def schedule_guide(self, *_args) -> None:
        """Refresh the guide 100 ms after the last change (any signal's arguments are ignored)."""
        import shiboken6
        t = getattr(self, "_guide_timer", None)
        if t is not None and shiboken6.isValid(self) and shiboken6.isValid(t):
            t.start()

    def guide_state(self):
        """The getting-started state of the window (aurora.app.guide.GuideState)."""
        from aurora.app import guide as g
        ses = self.session
        p = ses.project
        n_stacks = sum(len(h.stacks) for _day, h in ses.holder_keys.values()) if p is not None else 0
        n_zones = 0
        if p is not None:
            for hz in ses.zones.holders.values():
                n_zones += sum(1 for z in hz.zones if z.rects or getattr(z, "polygons", None) or getattr(z, "pixels", None)
                               or getattr(z, "painted", None))
        lib = self.library
        rows = getattr(lib, "requests", None)
        return g.GuideState(
            has_project=p is not None, opening=self.home._opening if p is None else "",
            read_only=p is not None and self.read_only_beamtime(),
            n_complete_stacks=n_stacks, n_zones=n_zones, n_queued=len(ses.check_queue),
            n_saved=self.check.n_saved, n_library=len(rows) if rows is not None else 0, fit_ran=self._fit_ran,
            report_folder=self._report_folder, visited=frozenset(self._visited), current=self.current_key(),
            can_choose_work_folder=False)

    def read_only_beamtime(self) -> bool:
        """The beamtime folder cannot be written: a protected folder, or the session found it read-only."""
        p = self.session.project
        if p is None:
            return False
        try:
            from aurora.core.io.safety import is_protected
            if is_protected(Path(p.root)):
                return True
        except (ImportError, OSError, ValueError):
            pass
        return bool(getattr(self.session, "folder_read_only", False))

    def refresh_guide(self) -> None:
        """The guide strip on Home and the top bar's Next link (Easy mode, Settings 'show the getting-started guide')."""
        from aurora.app import guide as g
        step = g.next_step(self.guide_state())
        self.next_step = step
        show = (not ui_mode.is_advanced()) and bool(setting("show_guide"))
        has = self.session.project is not None
        here = self.current_key() == "home"
        strip = self.home.guide_strip
        strip.setVisible(show and has)
        if show and has:
            strip.show_step(step, here)
        self._paint_next(step, show and has)
        self._paint_banner()

    def _paint_next(self, step, show: bool) -> None:
        import html as _html
        from aurora.app import guide as g
        lab = self.next_label
        lab.setVisible(show)
        if not show:
            return
        title = g.PAGE_TITLES.get(step.page, step.page)
        short = g.short_sentence(step)
        if self.current_key() == step.page:
            plain = f"Here: {short}"
            shown = _html.escape(lab.fontMetrics().elidedText(plain, Qt.ElideRight, NEXT_WIDTH))
        else:
            plain = f"Next: {title} – {short}"
            el = _html.escape(lab.fontMetrics().elidedText(plain, Qt.ElideRight, NEXT_WIDTH))
            shown = (f"<a href='aurora:goto:{step.page}' style='color:{TOKENS['link']};text-decoration:none'>{el}</a>")
        lab.setText(shown)
        lab.setToolTip(step.sentence)
        lab.setProperty("aurora_plain", plain)
        lab.setAccessibleDescription(plain)

    def next_text(self) -> str:
        """The Next link as a reader sees it (tests)."""
        return str(self.next_label.property("aurora_plain") or "") if self.next_label.isVisible() else ""

    def _next_link(self, href: str) -> None:
        if href.startswith("aurora:goto:"):
            self.go_to(href[len("aurora:goto:"):])

    def guide_go(self, target: str) -> None:
        """The guide strip's button: a page, or an action ('open', 'demo', 'reload', 'folder')."""
        if target in self.pages:
            self.go_to(target)
        elif target == "open":
            self.home.choose_project()
        elif target == "demo":
            self.open_demo()
        elif target == "reload":
            self.home.reload()
        elif target == "folder" and self._report_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._report_folder))
        elif target == "workfolder" and hasattr(self, "choose_work_folder"):
            self.choose_work_folder()

    def hide_guide(self) -> None:
        from aurora.app.settings import settings
        s = settings()
        s.setValue("show_guide", False)
        s.sync()
        self.statusBar().showMessage("Getting started hidden: Settings > show the getting-started guide (Easy mode) "
                                     "shows it again", 8000)
        self.refresh_guide()

    # ------------------------------------------------------------------ read-only beamtime banner
    def _banner(self) -> QWidget:
        from PySide6.QtWidgets import QFrame
        from aurora.app.widgets import TermLabel
        b = QFrame()
        b.setObjectName("banner")
        b.setAccessibleName("read-only beamtime")
        lay = QHBoxLayout(b)
        lay.setContentsMargins(16, 6, 16, 6)
        lay.setSpacing(10)
        self.banner_text = TermLabel(BANNER_TEXT)
        self.banner_text.setObjectName("bannertext")
        lay.addWidget(self.banner_text, 1)
        self.banner_dismiss = QPushButton("Dismiss")
        self.banner_dismiss.setToolTip("Hide this note for this beamtime (the save indicator still says read-only)")
        self.banner_dismiss.setAccessibleName("Dismiss the read-only note")
        self.banner_dismiss.clicked.connect(self._dismiss_banner)
        lay.addWidget(self.banner_dismiss)
        self._banner_dismissed: str = ""
        b.hide()
        self.banner = b
        return b

    def _dismiss_banner(self) -> None:
        p = self.session.project
        self._banner_dismissed = str(p.root) if p is not None else ""
        self.banner.hide()

    def _paint_banner(self) -> None:
        p = self.session.project
        show = p is not None and self.read_only_beamtime() and self._banner_dismissed != str(p.root)
        self.banner.setVisible(show)

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


class AuroraApplication(QApplication):
    """The application object. On macOS a folder sent with Finder's Open With, or dropped on the Dock icon, arrives as
    a QFileOpenEvent (an Apple Event, not in argv, which cli._as_gui_path reads): it opens like `AURORA <folder>`. One
    that arrives before the window is up waits in `pending` for open_at_start."""

    def __init__(self, argv: list[str]):
        super().__init__(argv)
        self.pending: list[Path] = []
        self.sent = 0                                   # folders sent so far (the last project yields to one)

    def event(self, e) -> bool:
        if e.type() == QEvent.FileOpen:
            path = e.file() or e.url().toLocalFile()
            if path:
                self.open_sent(Path(path))
            return True
        return super().event(e)

    def open_sent(self, path: Path) -> None:
        from aurora.app.nav import top_level_widgets
        self.sent += 1
        wins = [w for w in top_level_widgets() if isinstance(w, MainWindow) and w.isVisible()]
        win = self.activeWindow() if self.activeWindow() in wins else (wins[-1] if wins else None)
        if win is None:
            self.pending.append(path)
        else:
            open_sent_path(win, path)


def open_sent_path(win: MainWindow, path: Path) -> None:
    """Open a folder the system sent: at once, or when the project being read now (the last one, at start) is shown."""
    win.show_perspective("home")
    job = win.jobs.job("discover")
    if job is not None:
        job.add_callbacks(on_end=lambda _state: win.home.open_path(path))
    else:
        win.home.open_path(path)


def create_app(argv: list[str] | None = None) -> QApplication:
    # the GUI thread gets the GIL back within 1 ms (not 5) while a job computes in pure Python: every Qt call it makes
    # releases the GIL, and waiting 5 ms each time froze the window for most of a second on slower machines (MCR-ALS)
    sys.setswitchinterval(min(sys.getswitchinterval(), 0.001))
    app = QApplication.instance() or AuroraApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    f = QFont()
    f.setFamilies(["Inter", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Arial"])
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
    """The project to open once the window is up: the command-line folder, else a folder macOS sent before the window
    was up, else the last project when Settings asks for it (unless a sent folder came first). All go through
    home.open_path, which looks at the path only in its worker, so an unreachable share never blocks the start; the
    last project is opened from the event loop and fails quietly."""
    app = QApplication.instance()
    pending = app.pending if isinstance(app, AuroraApplication) else []
    sent = getattr(app, "sent", 0)
    if project is not None:                     # aurora <folder>, aurora gui <folder>
        win.home.open_path(Path(project))
    elif pending:                               # Finder's Open With, the Dock icon (the last one sent)
        open_sent_path(win, pending[-1])
    elif setting("open_last_project"):
        rec = recent_projects()
        if rec:
            last = Path(rec[0])
            QTimer.singleShot(0, win, lambda: None if getattr(app, "sent", 0) != sent
                              else win.home.open_path(last, quiet=True))
    pending.clear()


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
