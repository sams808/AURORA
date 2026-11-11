"""The window side of the Session, installed when a Session is created inside the main window (no main.py hook):

- Edit > Undo (Ctrl+Z) and Redo (Ctrl+Y, Ctrl+Shift+Z) naming the edit, Edit > History… (Advanced, a QUndoView);
- File > Restore zones from a backup… (Advanced);
- the save indicator in the top bar ('unsaved', 'saved 12:03', 'read-only'), driven by Session.save_state;
- the per-holder import dialog and the import error message (Session.import_zones);
- the 'session restored' message after a damaged session.json (Session.recovered), and the non-modal messages of
  Session.warned (opened read-only because another AURORA holds the project, its lock taken over, a foreign change);
- the question before closing or opening another project with edits that could not be saved (Session.confirm_leave):
  Export zones… / Discard / Cancel;
- putting the Reduce scan list back on the kept stack after a Reload (Session.reloaded).

The menu entries go through aurora.app.shortcuts (their owner is this object, a child of the Session, itself a child
of the window, so shortcuts.adopt places them). The indicator is inserted before the Easy / Advanced switch as soon as
the top bar is inside the window (an event filter on the window watches for it during MainWindow.__init__).
"""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QLabel, QMessageBox, QToolButton, QUndoView,
                               QVBoxLayout, QWidget)

from aurora.app import shortcuts
from aurora.app.help_text import HELP, explain, explain_buttons

log = logging.getLogger("aurora.session")

STATE_DETAIL = {
    "unsaved": "A change waits for the autosave: it is written to {path} within a second.",
    "saved": "Your outlines and settings are saved in {path}.",
    "not saved": "The last save failed: {why}. AURORA tries again at the next change and when the window closes.",
    "read-only": "This session is read-only (written by a newer AURORA, open in another AURORA, or in a folder that "
                 "cannot be written): edits here are not saved. Closing the window offers to export them to a file.",
}
# the read-only cases in words (Easy and Advanced; Advanced adds EXPORT_HINT, the action Easy mode hides)
READ_ONLY_DETAIL = {
    "folder": "AURORA cannot write in this beamtime folder: outlines drawn now are lost when AURORA closes. Choose a "
              "working folder (the note at the top of the window) to keep your work there.",
    "newer": "This session was saved by a newer AURORA: edits here are not saved. Install the newer AURORA to keep "
             "working on it.",
    "other": "This session is open in another AURORA (or its lock was taken over): edits here are not saved.",
}
EXPORT_HINT = " Survey > Export zones… keeps them in a file."
UNSAVED_TITLE = "Unsaved zone edits"
UNSAVED_TEXT = ("The zone edits of {project} are not saved: {why}.\n\nExport zones… writes them to a zones_all.json "
                "of your choice (Survey > Import zones… reads it back); Discard {then} without them.")
UNSAVED_THEN = {"close the window": "closes the window", "open another project": "opens the other project"}


def _menu_text(text: str) -> str:
    return text.replace("&", "&&")


class HistoryDialog(QDialog):
    """Edit > History…: the undo stack as a list; clicking a line goes back (or forward) to that point."""

    def __init__(self, stack, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit history")
        lay = QVBoxLayout(self)
        intro = QLabel(shortcuts.native_text("Every zone, settings and good-I0 edit since the project was opened, "
                                             "oldest first (the last 100). Click a line to go back to that point; "
                                             "Ctrl+Z and Ctrl+Y step one edit."))
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
        self.last_message = ""                  # the last 'session restored' or warning text shown (tests read it)
        self.unsaved_box: QMessageBox | None = None     # the unsaved-edits question while it is open
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
        session.warned.connect(self._on_warned)
        session.reloaded.connect(self._on_reloaded)
        session.project_changed.connect(self._sync)
        window.installEventFilter(self)
        from aurora.app.mode import on_mode_changed
        on_mode_changed(self, lambda _a: self._paint(), call_now=False)     # the read-only detail reads by mode
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
        self._on_warned("Session restored", text)

    def _on_warned(self, title: str, text: str) -> None:
        """A non-modal message box (the window stays usable): a restored or read-only session, a foreign change."""
        self.last_message = text
        box = QMessageBox(QMessageBox.Warning, title, text, QMessageBox.Ok, self.window)
        box.setWindowModality(Qt.NonModal)
        box.setAttribute(Qt.WA_DeleteOnClose)
        for b in box.buttons():
            b.setToolTip("Close this message")
        box.show()

    def ask_unsaved(self, action: str, why: str) -> bool:
        """Session.confirm_leave: edits that could not be saved. Export zones… (a file outside the project, then go
        on), Discard (go on without them) or Cancel (stay). True when the window may go on with `action`."""
        ses = self.session
        name = ses.project.root.name if ses.project is not None else "this project"
        box = QMessageBox(QMessageBox.Warning, UNSAVED_TITLE,
                          UNSAVED_TEXT.format(project=name, why=why.rstrip("."),
                                              then=UNSAVED_THEN.get(action, "goes on")), QMessageBox.NoButton,
                          self.window)
        export = box.addButton("Export zones…", QMessageBox.AcceptRole)
        discard = box.addButton("Discard", QMessageBox.DestructiveRole)
        cancel = box.addButton(QMessageBox.Cancel)
        export.setToolTip("Write the zones of this session to a zones_all.json you choose, then go on")
        discard.setToolTip("Go on without the edits that are not saved")
        cancel.setToolTip("Stay: nothing is closed or opened")
        box.setDefaultButton(export)
        box.setEscapeButton(cancel)
        self.unsaved_box = box                  # tests answer it
        try:
            box.exec()
            clicked = box.clickedButton()
        finally:
            self.unsaved_box = None
            box.deleteLater()
        if clicked is discard:
            log.warning("unsaved zone edits of %s discarded (%s)", name, action)
            ses.discard_unsaved()
            return True
        if clicked is not export:
            return False
        return self.export_unsaved(name)

    def export_unsaved(self, name: str) -> bool:
        """Export zones… of the unsaved-edits question: a file dialog that starts outside the project (which may not
        be writable), then Session.export_zones. True when the file was written."""
        docs = Path.home() / "Documents"
        start = (docs if docs.is_dir() else Path.home()) / f"{name}_zones_all.json"
        p, _ = QFileDialog.getSaveFileName(self.window, "Export zones", str(start), "JSON (*.json)")
        if not p:
            return False
        try:
            self.session.export_zones(Path(p))
        except (OSError, ValueError, TypeError) as err:
            reason = (getattr(err, "strerror", None) or str(err)).strip().rstrip(".")
            QMessageBox.warning(self.window, "Could not write", f"Could not write {Path(p).name}: {reason}. "
                                                                "The edits are still in this window.")
            return False
        self.session.discard_unsaved()
        return True

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

    def state_detail(self, word: str, path) -> str:
        """The second line of the indicator's tooltip: what the state means and, read-only, why and what to do."""
        from aurora.app.mode import is_advanced
        ses = self.session
        if word == "read-only":
            kind = ("newer" if getattr(ses, "_newer", False) else "folder" if getattr(ses, "folder_read_only", False)
                    else "other")
            return READ_ONLY_DETAIL[kind] + (EXPORT_HINT if is_advanced() else "")
        if word == "not saved":
            from aurora.app.widgets import plain_error
            why = getattr(ses, "_save_error", "") or "the reason is in the status line"
            why = why.replace("the last save failed (", "").rstrip(")") if why.startswith("the last save failed (") else why
            return STATE_DETAIL["not saved"].format(why=plain_error(why))
        return STATE_DETAIL.get(word, "").format(path=path)

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
        lab.setMinimumWidth(lab.fontMetrics().horizontalAdvance(state) + 4 if state else 0)   # never 'saved 0'
        win = self.window
        if hasattr(win, "_paint_next") and getattr(win, "next_step", None) is not None and win.next_label.isVisible():
            win._paint_next(win.next_step, True)            # the Next link gives way to the indicator
        word = "saved" if state.startswith("saved") else state
        from aurora.app.theme import set_tone
        set_tone(lab, "warn" if word in ("not saved", "read-only") else None)      # edits at risk stand out
        path = self.session.session_file if self.session.project is not None else "session.json"
        detail = self.state_detail(word, path)
        from aurora.app.help_text import tooltip
        tip = HELP["main.save_state"][0]
        more = tooltip("main.save_state")[len(tip):]
        lab.setToolTip((f"{tip}\n{detail}" if detail else tip) + more)
        lab.setAccessibleDescription(state)
