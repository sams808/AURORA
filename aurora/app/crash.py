"""Unexpected errors: logged to the log file and shown in a dialog with the details, instead of a silent failure
(the frozen app has no console). Installed by `aurora.app.main.run()` only, never in tests."""
from __future__ import annotations

import logging
import sys
import threading
import traceback

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

log = logging.getLogger("aurora.crash")
_STATE = {"showing": False}


def open_log_folder() -> None:
    from aurora.logsetup import log_dir
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))


def show_error(title: str, text: str, details: str = "") -> None:
    """A dialog with the message, the details (traceback) and 'Copy details' / 'Open log folder'."""
    if _STATE["showing"] or QApplication.instance() is None:
        return
    _STATE["showing"] = True
    try:
        box = QMessageBox(QApplication.activeWindow())
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(text)
        from aurora.logsetup import log_file
        box.setInformativeText(f"The details are in the log file:\n{log_file()}")
        if details:
            box.setDetailedText(details)
        copy = box.addButton("Copy details", QMessageBox.ActionRole)
        logs = box.addButton("Open log folder", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Close)
        box.exec()
        if box.clickedButton() is copy:
            QGuiApplication.clipboard().setText(f"{title}\n{text}\n\n{details}")
        elif box.clickedButton() is logs:
            open_log_folder()
    finally:
        _STATE["showing"] = False


def _hook(exc_type, exc, tb) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    details = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("unexpected error\n%s", details)
    QTimer.singleShot(0, lambda: show_error("AURORA: unexpected error", f"{exc_type.__name__}: {exc}", details))


def _thread_hook(args) -> None:
    details = "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    log.error("unexpected error in thread %s\n%s", getattr(args.thread, "name", "?"), details)


def install() -> None:
    sys.excepthook = _hook
    threading.excepthook = _thread_hook
