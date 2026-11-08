"""The window side of the Session, installed when a Session is created inside the main window (no main.py hook):

- Edit > Undo (Ctrl+Z) and Redo (Ctrl+Y, Ctrl+Shift+Z) naming the edit, Edit > History… (Advanced, a QUndoView);
- File > Restore zones from a backup… (Advanced);
- the save indicator in the top bar ('unsaved', 'saved 12:03', 'read-only'), driven by Session.save_state;
- the per-holder import dialog and the import error message (Session.import_zones);
- the 'session restored' message after a damaged session.json (Session.recovered);
- putting the Reduce scan list back on the kept stack after a Reload (Session.reloaded).

The menu entries go through aurora.app.shortcuts (their owner is this object, a child of the Session, itself a child
of the window, so shortcuts.adopt places them). The indicator is inserted before the Easy / Advanced switch as soon as
the top bar is inside the window (an event filter on the window watches for it during MainWindow.__init__).
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox, QToolButton, QUndoView, QVBoxLayout,
                               QWidget)

from aurora.app import shortcuts
from aurora.app.help_text import HELP, explain, explain_buttons

log = logging.getLogger("aurora.session")

STATE_DETAIL = {
    "unsaved": "A change waits for the autosave: it is written to {path} within a second.",
    "saved": "The zones are saved in {path}.",
    "not saved": "The last save failed (the status line said why); AURORA tries again at the next change and when "
                 "the window closes.",
    "read-only": "This session.json was written by a newer AURORA, or cannot be written: edits here are not saved. "
                 "Survey > Export zones… keeps them in a file.",
}


def _menu_text(text: str) -> str:
    return text.replace("&", "&&")


class HistoryDialog(QDialog):
    """Edit > History…: the undo stack as a list; clicking a line goes back (or forward) to that point."""

    def __init__(self, stack, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit history")
        lay = QVBoxLayout(self)
        intro = QLabel("Every zone, settings and good-I0 edit since the project was opened, oldest first (the last "
                       "100). Click a line to go back to that point; Ctrl+Z and Ctrl+Y step one edit.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        lay.addWidget(intro)
        self.view = QUndoView(stack)
        self.view.setEmptyLabel("project opened")
        explain(self.view, "main.history_list")
        lay.addWidget(self.view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close | QDialogButtonBox.Help)
        buttons.rejected.connect(self.close)
        buttons.helpRequested.connect(self._help)
        explain_buttons(buttons)
        lay.addWidget(buttons)
        self.resize(460, 420)

    def _help(self) -> None:
        from aurora.app.help import open_help
        open_help(self, "MANUAL.md", HELP["main.history"][2])


class SessionUI(QObject):
    def __init__(self, session, window: QWidget):
        super().__init__(session)
        self.session = session
        self.window = window
        self.indicator: QLabel | None = None
        self.state = ""
        self.last_message = ""                  # the last 'session restored' text shown (tests read it)
        self._history: HistoryDialog | None = None
        self.undo_action = shortcuts.add(self, "Undo", "Ctrl+Z", session.undo, menu="edit", section="Edit")
        self.redo_action = shortcuts.add(self, "Redo", "Ctrl+Y", session.redo, menu="edit", section="Edit")
        self.redo_action.setShortcuts([QKeySequence("Ctrl+Y"), QKeySequence("Ctrl+Shift+Z")])
        self.history_action = shortcuts.add(self, "History…", None, self.show_history, menu="edit", section="Edit")
        self.restore_action = shortcuts.add(self, "Restore zones from a backup…", None, self.show_restore,
                                            menu="file", section="File")
        for a, key in ((self.history_action, "main.history"), (self.restore_action, "main.restore")):
            a.setToolTip(HELP[key][0])
            a.setStatusTip(HELP[key][0].split("\n")[0])
        from aurora.app.mode import mark_advanced_action
        mark_advanced_action(self, self.history_action, self.restore_action)
        st = session.undo_stack
        for sig in (st.canUndoChanged, st.canRedoChanged, st.undoTextChanged, st.redoTextChanged):
            sig.connect(self._sync)
        session.save_state.connect(self._on_state)
        session.recovered.connect(self._on_recovered)
        session.reloaded.connect(self._on_reloaded)
        session.project_changed.connect(self._sync)
        window.installEventFilter(self)
        self._sync()

    # ------------------------------------------------------------------ menu entries
    def _sync(self, *_args) -> None:
        try:
            self._sync_actions()
        except RuntimeError:            # the undo stack emits while it is destroyed with the window
            pass

    def _sync_actions(self) -> None:
        st = self.session.undo_stack
        for action, can, text, word, key, nothing in (
                (self.undo_action, st.canUndo(), st.undoText(), "Undo", "main.undo", "nothing to undo"),
                (self.redo_action, st.canRedo(), st.redoText(), "Redo", "main.redo", "nothing to redo")):
            action.setText(_menu_text(f"{word} {text}") if can and text else word)
            action.setEnabled(bool(can))
            tip = HELP[key][0]
            action.setToolTip(tip if can else f"{tip}\nNot available: {nothing}")
            action.setStatusTip(tip.split("\n")[0])
        has = self.session.project is not None
        self.restore_action.setEnabled(has and not self.session.read_only)
        tip = HELP["main.restore"][0]
        self.restore_action.setToolTip(tip if has else f"{tip}\nNot available: open a project first")

    def show_history(self) -> None:
        if self._history is None:
            self._history = HistoryDialog(self.session.undo_stack, self.window)
        self._history.show()
        self._history.raise_()

    def show_restore(self) -> None:
        if self.session.project is None:
            self.session.status.emit("open a project first")
            return
        from aurora.app.import_dialog import RestoreDialog
        d = RestoreDialog(self.session, self.window)
        try:
            path = d.chosen() if d.exec() == QDialog.Accepted else None
        finally:
            d.deleteLater()
        if path is not None:
            self.session.restore_backup(path)

    # ------------------------------------------------------------------ dialogs asked for by the Session
    def ask_import(self, path, rows):
        from aurora.app import import_dialog
        return import_dialog.ask_policies(self.window, path, rows)

    def warn(self, title: str, text: str) -> None:
        QMessageBox.warning(self.window, title, text)

    def _on_recovered(self, text: str) -> None:
        self.last_message = text
        box = QMessageBox(QMessageBox.Warning, "Session restored", text, QMessageBox.Ok, self.window)
        box.setWindowModality(Qt.NonModal)
        box.setAttribute(Qt.WA_DeleteOnClose)
        for b in box.buttons():
            b.setToolTip("Close this message")
        box.show()

    def _on_reloaded(self, sid: str) -> None:
        """After a Reload the Reduce scan list shows the stack that stayed selected (without loading another)."""
        lst = getattr(getattr(self.window, "reduce", None), "scan_list", None)
        if lst is None or not sid:
            return
        for i in range(lst.count()):
            if lst.item(i).data(Qt.UserRole) == sid:
                if lst.currentRow() != i:
                    lst.blockSignals(True)
                    lst.setCurrentRow(i)
                    lst.blockSignals(False)
                break

    # ------------------------------------------------------------------ the save indicator
    def eventFilter(self, obj, ev) -> bool:     # noqa: N802 (Qt's name)
        if obj is self.window and self.indicator is None and ev.type() in (QEvent.ChildAdded, QEvent.Show):
            if self._install_indicator() or ev.type() == QEvent.Show:
                self.window.removeEventFilter(self)
        return False

    def _install_indicator(self) -> bool:
        bar = self.window.findChild(QWidget, "topbar")
        lay = bar.layout() if bar is not None else None
        btn = bar.findChild(QToolButton, "modebtn") if bar is not None else None
        idx = lay.indexOf(btn) if lay is not None and btn is not None else -1
        if idx < 0:
            return False
        lab = QLabel(bar)
        lab.setObjectName("saveind")
        explain(lab, "main.save_state")
        lab.setTextInteractionFlags(Qt.NoTextInteraction)
        lay.insertWidget(idx, lab)
        self.indicator = lab
        self._paint()
        return True

    def _on_state(self, state: str) -> None:
        self.state = state or ""
        self._paint()

    def _paint(self) -> None:
        lab = self.indicator
        if lab is None:
            return
        state = self.state
        lab.setText(state)
        lab.setVisible(bool(state))
        word = "saved" if state.startswith("saved") else state
        path = self.session.session_file if self.session.project is not None else "session.json"
        detail = STATE_DETAIL.get(word, "").format(path=path)
        tip = HELP["main.save_state"][0]
        lab.setToolTip(f"{tip}\n{detail}" if detail else tip)
        lab.setAccessibleDescription(state)
