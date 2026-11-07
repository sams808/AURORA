"""Small shared widgets: cards, chips, a generic worker thread."""
from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QThread, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from aurora.app.theme import chip_style


class Card(QFrame):
    """White rounded panel with a numbered / plain title, like the design canvas cards."""

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(14, 12, 14, 12)
        self.body.setSpacing(8)
        self.header = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setObjectName("cardtitle")
        self.header.addWidget(self.title)
        self.header.addStretch(1)
        self.body.addLayout(self.header)


class Chip(QLabel):
    def __init__(self, text: str, kind: str = "other", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setStyleSheet(chip_style(kind))
        self.setAlignment(Qt.AlignCenter)


class Worker(QThread):
    """Run fn(progress_cb) off the GUI thread. Connect `done` / `failed` to slots of main-thread objects with
    Qt.QueuedConnection (a slot connected as a plain function would run on this thread)."""
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, fn, parent: QObject | None = None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn(lambda i, n, msg="": self.progress.emit(int(i), int(n), str(msg)))
        except Exception:
            self.failed.emit(traceback.format_exc())
            return
        self.done.emit(result)
