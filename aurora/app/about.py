"""About dialog: the mark, the version, the credits (CREDITS.md), the libraries."""
from __future__ import annotations

import platform
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from aurora import APP_NAME, __version__
from aurora.app.icons import mark_pixmap

CREDITS = (
    "AURORA builds on the SGM XAS GUI by John Bussey (Washington State University), on the SGM_Beamline_Analysis "
    "toolkit by James J. Dynes and the SGM beamline staff (Canadian Light Source), and on the beamtime reporting "
    "pipeline by Sami Soudani (Washington State University). Edge and emission-line energies from xraydb. "
    "Data from the SGM beamline, Canadian Light Source."
)


def versions_text() -> str:
    import numpy
    import h5py
    import PySide6
    import xraydb
    return (f"{APP_NAME} {__version__} · Python {sys.version.split()[0]} · {platform.system()} {platform.machine()}\n"
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
        title = QLabel(f"<b style='font-size:22px;letter-spacing:3px'>{APP_NAME}</b><br>"
                       "Soft X-ray XAS and XRF stacks from the SGM beamline, Canadian Light Source<br>"
                       f"<span style='color:#5B6B8C'>version {__version__} · Sami Soudani · MIT licence</span>")
        title.setTextFormat(Qt.RichText)
        top.addWidget(title, 1)
        lay.addLayout(top)
        credits = QLabel(CREDITS)
        credits.setWordWrap(True)
        lay.addWidget(credits)
        ver = QLabel(versions_text())
        ver.setObjectName("muted")
        ver.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(ver)
        link = QLabel('<a href="https://github.com/sams808/AURORA">github.com/sams808/AURORA</a>')
        link.setOpenExternalLinks(True)
        lay.addWidget(link)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)
        self.setMinimumWidth(560)
