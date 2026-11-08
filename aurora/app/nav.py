"""Jump to a perspective, holder, zone or stack from anywhere: empty-state buttons, report pre-flight findings, F1.

    nav.go(self, "reduce", holder="N5", zone=3)

The main window is found from the widget (also through a dialog's parent), so no perspective needs a reference to it.
"""
from __future__ import annotations

import logging

from PySide6.QtWidgets import QApplication, QWidget

log = logging.getLogger("aurora.nav")


def window_of(widget: QWidget | None):
    """The main window (the one with navigate()) that holds `widget`, else the application's only main window."""
    w = widget
    while w is not None:
        top = w.window()
        if hasattr(top, "navigate"):
            return top
        w = top.parentWidget()
    app = QApplication.instance()
    wins = [t for t in (app.topLevelWidgets() if app else []) if hasattr(t, "navigate")]
    return wins[0] if len(wins) == 1 else None


def go(widget: QWidget | None, perspective: str, holder: str | None = None, zone: int | None = None,
       sid: str | None = None) -> None:
    """Show `perspective`, after selecting the holder, zone and stack when given (MainWindow.navigate)."""
    win = window_of(widget)
    if win is None:
        log.info("nav.go(%s): no main window found", perspective)
        return
    win.navigate(perspective, holder=holder, zone=zone, sid=sid)


def open_project_dialog(widget: QWidget | None) -> None:
    """The Open project… dialog of the main window (empty-state buttons)."""
    win = window_of(widget)
    if win is not None and hasattr(win, "home"):
        win.home.choose_project()
    else:
        log.info("open project: no main window found")


def open_demo(widget: QWidget | None) -> None:
    """Help > Open the demo beamtime, from an empty-state button."""
    win = window_of(widget)
    if win is not None and hasattr(win, "open_demo"):
        win.open_demo()
    else:
        log.info("open demo: no main window found")


def call(widget: QWidget | None, perspective: str, method: str) -> None:
    """Call a method of another perspective (e.g. compare's _add_folder from the Compare empty state)."""
    win = window_of(widget)
    page = getattr(win, perspective, None) if win is not None else None
    fn = getattr(page, method, None)
    if callable(fn):
        fn()
