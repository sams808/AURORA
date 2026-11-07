"""Main window: top bar, perspectives rail, stacked perspectives. Only Home is built in Phase 0."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QFont, QKeySequence
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMainWindow, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from aurora import APP_NAME, ORG_NAME, __version__
from aurora.app.about import AboutDialog
from aurora.app.home import HomePerspective
from aurora.app.reduce import ReducePerspective
from aurora.app.session import Session
from aurora.app.survey import SurveyPerspective
from aurora.app.icons import app_icon, mark_pixmap, two_state_icon
from aurora.app.theme import TOKENS, qss

PERSPECTIVES = [
    ("home", "Home"), ("survey", "Survey"), ("reduce", "Reduce"), ("check", "Check"), ("compare", "Compare"),
    ("fit", "Fit"), ("maps", "Maps"), ("report", "Report"),
]


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
        self.survey = SurveyPerspective(self.session)
        self.reduce = ReducePerspective(self.session)
        self.pages: dict[str, QWidget] = {"home": self.home, "survey": self.survey, "reduce": self.reduce}
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.survey)
        self.stack.addWidget(self.reduce)
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
            b.setIcon(two_state_icon(key, TOKENS["rail_text"], TOKENS["night"]))
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
        b.setIcon(two_state_icon("settings", TOKENS["rail_text"], TOKENS["night"]))
        b.setIconSize(QSize(22, 22))
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.clicked.connect(self.show_about)
        lay.addWidget(b)
        return rail

    def _menus(self) -> None:
        m = self.menuBar()
        file = m.addMenu("&File")
        a = QAction("&Open project…", self)
        a.setShortcut(QKeySequence.Open)
        a.triggered.connect(self.home.choose_project)
        file.addAction(a)
        a = QAction("&Reload", self)
        a.setShortcut(QKeySequence.Refresh)
        a.triggered.connect(self.home.reload_btn.click)
        file.addAction(a)
        file.addSeparator()
        a = QAction("&Quit", self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        file.addAction(a)
        view = m.addMenu("&View")
        a = QAction("&Full screen", self)
        a.setShortcut("F11")
        a.triggered.connect(self.toggle_fullscreen)
        view.addAction(a)
        help_ = m.addMenu("&Help")
        a = QAction(f"&About {APP_NAME}", self)
        a.triggered.connect(self.show_about)
        help_.addAction(a)

    # ------------------------------------------------------------------ behaviour
    def show_perspective(self, key: str) -> None:
        self.stack.setCurrentWidget(self.pages[key])
        self.rail_buttons[key].setChecked(True)

    def toggle_fullscreen(self) -> None:
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def show_about(self) -> None:
        AboutDialog(self).exec()

    def closeEvent(self, event) -> None:
        self.home.shutdown()
        if self.reduce._worker is not None:
            self.reduce._worker.wait(30000)
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
    app.setStyleSheet(qss())
    return app


def shutdown_app(app: QApplication, *windows: QWidget) -> None:
    """Destroy the windows and the QApplication in a fixed order. Left to interpreter teardown, PySide6 >= 6.12
    segfaults on exit (module globals and the application die in an arbitrary order)."""
    import gc
    import shiboken6
    for w in windows:
        w.close()
    for w in list(QApplication.topLevelWidgets()):
        w.close()
        w.deleteLater()
    app.processEvents()
    gc.collect()
    app.processEvents()
    shiboken6.delete(app)


def run(project: Path | None = None) -> int:
    app = create_app()
    win = MainWindow()
    win.show()
    if project is not None:
        win.home.open_project(Path(project))
    code = app.exec()
    shutdown_app(app, win)
    return code
