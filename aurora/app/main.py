"""Main window: top bar (Easy / Advanced switch), perspectives rail, stacked perspectives, menus (manuals in Help)."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QFont, QKeySequence
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

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
from aurora.app.theme import TOKENS, apply_theme, follow_system, on_theme_changed
from aurora.app import mode as ui_mode

PERSPECTIVES = [
    ("home", "Home"), ("survey", "Survey"), ("reduce", "Reduce"), ("check", "Check"), ("compare", "Compare"),
    ("fit", "Fit"), ("maps", "Maps"), ("report", "Report"),
]
# perspectives shown only in Advanced mode (Easy mode = John Bussey's basic path: reduce, check, compare, fit, report)
ADVANCED_PERSPECTIVES = {"maps"}
MODE_STATUS = {False: "Easy mode: the basic path of John Bussey's GUI", True: "Advanced mode: every option"}


class Placeholder(QWidget):
    def __init__(self, title: str, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        h = QLabel(title)
        h.setObjectName("h1")
        lay.addWidget(h)
        t = QLabel(text)
        t.setObjectName("muted")
        t.setWordWrap(True)
        lay.addWidget(t)
        lay.addStretch(1)


PLACEHOLDER_TEXT = {
    "survey": "Plate map with pads, scan footprints, sketch overlay and manual zones. Comes with Phase 1.",
    "reduce": "Zones to spectra: XRF windows per detector, I0 / TEY channels, de-spiking, preview of the four SDDs. Phase 1.",
    "check": "Quality checks: SDD consistency, saturation, I0 validity, spikes removed. Phase 1.",
    "compare": "Library, normalisation, stacked plot, peak finding (John Bussey's Compare tab). Phase 2.",
    "fit": "LCF, PCA, MCR-ALS, ITFA (John Bussey's tabs 4-6). Phase 2.",
    "maps": "Hypercube explorer, elemental maps, PCA / clustering, PyMca export (CLS toolkit). Phase 4.",
    "report": "Holder reports and the group-organised final report. Phase 3.",
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1100, 700)
        self.resize(1440, 900)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._top_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._rail())
        self.stack = QStackedWidget()
        self.session = Session(self)
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
        self.reduce.library = self.library
        self.maps = MapsPerspective(self.session)
        self.pages: dict[str, QWidget] = {"home": self.home, "survey": self.survey, "reduce": self.reduce, "check": self.check,
                                          "compare": self.compare, "fit": self.fit, "maps": self.maps, "report": self.report}
        for w in (self.home, self.survey, self.reduce, self.check, self.compare, self.fit, self.maps, self.report):
            self.stack.addWidget(w)
        for key, title in PERSPECTIVES[1:]:
            if key in self.pages:
                continue
            w = Placeholder(title, PLACEHOLDER_TEXT.get(key, ""))
            self.pages[key] = w
            self.stack.addWidget(w)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage(f"{APP_NAME} {__version__}")

        self._menus()
        self._help = None
        for key in sorted(ADVANCED_PERSPECTIVES):
            ui_mode.mark_advanced_perspective(key)
        self._mode_seen: bool | None = None
        ui_mode.on_mode_changed(self, self._mode_changed)
        on_theme_changed(self, lambda _s: self._rail_icons())
        self.show_perspective("home")

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
        lay.addWidget(mark)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brand")
        lay.addWidget(brand)
        self.crumb = QLabel("no project")
        self.crumb.setObjectName("crumb")
        lay.addWidget(self.crumb)
        lay.addStretch(1)
        self.mode_btn = QToolButton()
        self.mode_btn.setObjectName("modebtn")
        self.mode_btn.setCheckable(True)
        self.mode_btn.setToolTip("Easy mode: the basic processing path of John Bussey's GUI (reduce, check, compare, "
                                 "fit, report).\nAdvanced mode: every option. Ctrl+Shift+A")
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
        for key, title in PERSPECTIVES:
            b = QToolButton()
            b.setObjectName("railbtn")
            b.setText(title)
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
        b.setProperty("aurora_icon", "settings")
        b.setIcon(two_state_icon("settings", TOKENS["rail_text"], TOKENS["on_accent"]))
        self.settings_rail_btn = b
        b.setIconSize(QSize(22, 22))
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.clicked.connect(self.show_settings)
        lay.addWidget(b)
        return rail

    def _menus(self) -> None:
        m = self.menuBar()
        self.menus: dict[str, object] = {}
        file = m.addMenu("&File")
        self.menus["file"] = file
        a = QAction("&Open project…", self)
        a.setShortcut(QKeySequence.Open)
        a.triggered.connect(self.home.choose_project)
        file.addAction(a)
        a = QAction("&Reload", self)
        a.setShortcut(QKeySequence.Refresh)
        a.triggered.connect(self.home.reload_btn.click)
        file.addAction(a)
        file.addSeparator()
        a = QAction("&Settings…", self)
        a.setShortcut(QKeySequence.Preferences)
        a.setMenuRole(QAction.PreferencesRole)          # macOS: the application menu's Settings entry
        a.triggered.connect(self.show_settings)
        file.addAction(a)
        file.addSeparator()
        a = QAction("&Quit", self)
        a.setShortcut(QKeySequence.Quit)
        a.setMenuRole(QAction.QuitRole)
        a.triggered.connect(self.close)
        file.addAction(a)

        view = m.addMenu("&View")
        self.menus["view"] = view
        self.mode_action = QAction("&Advanced mode", self)
        self.mode_action.setCheckable(True)
        self.mode_action.setShortcut("Ctrl+Shift+A")
        self.mode_action.setMenuRole(QAction.NoRole)
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
        a = QAction("&Full screen", self)
        a.setShortcut("F11")
        a.triggered.connect(self.toggle_fullscreen)
        view.addAction(a)

        help_ = m.addMenu("&Help")
        self.menus["help"] = help_
        from aurora.app.help import DOCS
        self.help_actions = {}
        for i, (name, title, tip) in enumerate(DOCS):
            a = QAction(title, self)
            a.setStatusTip(tip)
            a.setMenuRole(QAction.NoRole)
            if i == 0:
                a.setShortcut(QKeySequence.HelpContents)
            a.triggered.connect(lambda _=False, n=name: self.show_help(n))
            help_.addAction(a)
            self.help_actions[name] = a
        help_.addSeparator()
        a = QAction("Open the &demo beamtime", self)
        a.setStatusTip("a small synthetic beamtime to try every perspective (the tutorials use it)")
        a.setMenuRole(QAction.NoRole)
        a.triggered.connect(self.open_demo)
        help_.addAction(a)
        self.demo_action = a
        a = QAction("Open the &log folder", self)
        a.setStatusTip("the log file to attach to a bug report")
        a.setMenuRole(QAction.NoRole)
        a.triggered.connect(self.open_log_folder)
        help_.addAction(a)
        help_.addSeparator()
        a = QAction(f"&About {APP_NAME}", self)
        a.setMenuRole(QAction.AboutRole)
        a.triggered.connect(self.show_about)
        help_.addAction(a)

    # ------------------------------------------------------------------ behaviour
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

    @staticmethod
    def perspective_hint(key: str) -> str:
        title = dict(PERSPECTIVES).get(key, key)
        return f"{title} is part of Advanced mode (Ctrl+Shift+A)"

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
            a = getattr(self, "goto_actions", {}).get(key)       # View > Go to (when the menu has it)
            if a is not None:
                a.setVisible(advanced)
        current = next((k for k, w in self.pages.items() if w is self.stack.currentWidget()), "home")
        if not advanced and current in hidden:
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

    def open_demo(self) -> None:
        """Create (once) and open the synthetic demo beamtime in the user's data folder."""
        from platformdirs import user_data_dir
        from aurora.cli import cmd_demo
        root = Path(user_data_dir(APP_NAME, appauthor=False)) / "demo_beamtime"
        if not (root / "raw").is_dir():
            cmd_demo(root)
        self.home.open_project(root)
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

    def closeEvent(self, event) -> None:
        self.home.shutdown()
        for w in (self.reduce, self.check, self.maps, self.report):
            if w._worker is not None:
                w._worker.wait(30000)
        self.session.save()
        super().closeEvent(event)

    def _on_project(self, project) -> None:
        self.crumb.setText(f"{project.root.name}   ·   {len(project.days)} days   ·   {project.n_holders} holders")
        self.statusBar().showMessage(f"{len(project.scans)} scans in {project.root}")
        self.session.set_project(project)


def create_app(argv: list[str] | None = None) -> QApplication:
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
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
    segfaults on exit (module globals and the application die in an arbitrary order)."""
    import gc
    import shiboken6
    for w in windows:
        if shiboken6.isValid(w):
            w.close()
    for w in list(QApplication.topLevelWidgets()):
        if shiboken6.isValid(w):
            w.close()
            w.deleteLater()
    app.processEvents()
    gc.collect()
    app.processEvents()
    shiboken6.delete(app)


def run(project: Path | None = None) -> int:
    from aurora.logsetup import setup_logging
    from aurora.app import crash
    setup_logging()
    app = create_app()
    crash.install()
    win = MainWindow()
    win.show()
    if project is None and setting("open_last_project") and recent_projects() and Path(recent_projects()[0]).is_dir():
        project = Path(recent_projects()[0])
    if project is not None:
        win.home.open_project(Path(project))
    code = app.exec()
    shutdown_app(app, win)
    return code
