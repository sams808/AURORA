"""About dialog: the mark, the version, the credits (CREDITS.md), the libraries."""
from __future__ import annotations

import platform
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from aurora import APP_NAME, __version__
from aurora.app.icons import mark_pixmap
from aurora.app.theme import TOKENS, on_theme_changed

REPO_URL = "https://github.com/sams808/AURORA"

from aurora.credits import CREDITS  # noqa: E402  (one source for About, README, report, CLI)


def build_info() -> dict:
    """aurora/_build.json written by the frozen build (version, commit, date), {} in a source checkout."""
    import json
    from pathlib import Path
    import aurora
    for p in (Path(aurora.__file__).resolve().parent / "_build.json",):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return {}


def versions_text() -> str:
    import numpy
    import h5py
    import PySide6
    import xraydb
    b = build_info()
    commit = b.get("describe") or b.get("commit") or ""
    build = f" · build {commit}" + (f" ({b['date']})" if b.get("date") else "") if commit else ""
    return (f"{APP_NAME} {__version__}{build} · Python {sys.version.split()[0]} · {platform.system()} {platform.machine()}\n"
            f"numpy {numpy.__version__} · h5py {h5py.__version__} · PySide6 {PySide6.__version__} · xraydb {xraydb.__version__}")


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        top = QHBoxLayout()
        mark = QLabel()
        mark.setPixmap(mark_pixmap(96))
        top.addWidget(mark)
        self.title = QLabel()
        self.title.setTextFormat(Qt.RichText)
        self.title.setWordWrap(True)
        top.addWidget(self.title, 1)
        lay.addLayout(top)
        self.credits = credits = QLabel(CREDITS)
        credits.setWordWrap(True)
        credits.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(credits)
        self.versions = ver = QLabel(versions_text())
        ver.setObjectName("muted")
        ver.setWordWrap(True)
        ver.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(ver)
        self.link = QLabel()
        self.link.setOpenExternalLinks(True)
        lay.addWidget(self.link)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.licences_btn = buttons.addButton("Licences…", QDialogButtonBox.ActionRole)
        self.licences_btn.setToolTip("AURORA's licence, the credits and the third-party licences")
        self.licences_btn.clicked.connect(self._licences)
        self.licences_btn.setAutoDefault(False)          # Enter closes About; Licences… needs a click or Tab
        buttons.button(QDialogButtonBox.Close).setDefault(True)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        from aurora.app.help_text import explain_buttons
        explain_buttons(buttons)
        lay.addWidget(buttons)
        self.setMinimumWidth(560)
        self._fill()
        on_theme_changed(self, lambda _s: self._fill())

    def _licences(self) -> None:
        from aurora.app.diagnostics import LicencesDialog
        LicencesDialog(self).exec()

    def text(self) -> str:
        return "\n".join(w.text() for w in (self.title, self.credits, self.versions, self.link))

    def _fill(self) -> None:
        """Texts whose colours come from the theme: the muted version line and the link in the palette Link colour."""
        self.title.setText(f"<b style='font-size:22px;letter-spacing:3px'>{APP_NAME}</b><br>"
                           "Soft X-ray XAS and XRF stacks from the SGM beamline, Canadian Light Source<br>"
                           f"<span style='color:{TOKENS['muted']}'>version {__version__} · John Bussey and Sami Soudani · MIT licence</span>")
        link = QApplication.palette().color(QPalette.Link).name()
        self.link.setText(f'<a href="{REPO_URL}" style="color:{link}">github.com/sams808/AURORA</a>')
