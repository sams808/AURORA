"""Dialogs that bring zones into the session: the per-holder Replace / Merge / Skip choice of an import
(Session.import_zones(path, policy='ask')) and File > Restore zones from a backup….

ask_policies(parent, path, rows) is the one function Session calls (through SessionUI); tests replace it.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLabel,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from aurora.app.help_text import HELP, explain, explain_buttons
from aurora.app.shortcuts import native_text
from aurora.app.widgets import fit_screen

POLICY_LABELS = (("merge", "Merge"), ("replace", "Replace"), ("skip", "Skip"))


def _help(widget: QWidget, key: str) -> None:
    from aurora.app.help import open_help
    open_help(widget, "MANUAL.md", HELP[key][2])


def _item(text: str, tip: str = "") -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if tip:
        it.setToolTip(tip)
    return it


def _header(table: QTableWidget, titles_and_tips) -> None:
    table.setColumnCount(len(titles_and_tips))
    for i, (title, tip) in enumerate(titles_and_tips):
        it = QTableWidgetItem(title)
        it.setToolTip(tip)
        table.setHorizontalHeaderItem(i, it)


class ImportDialog(QDialog):
    """One row per holder of the file that already has other zones here: Merge (default), Replace or Skip."""

    def __init__(self, path, rows, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Import zones")
        self.rows = list(rows)
        lay = QVBoxLayout(self)
        intro = QLabel(f"{Path(path).name} has zones for holders that already have zones here. Choose, per holder:")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.table = QTableWidget(len(self.rows), 5)
        _header(self.table, [("Holder", "The holder key"), ("Zones here", "Zones of this holder in the session"),
                             ("In the file", "Zones of this holder in the file"),
                             ("Same area", "Zones of the file with the same rectangles, painted pixels and polygons "
                                           "as a zone here (Merge skips them)"),
                             ("What to do", HELP["import.policy"][0])])
        explain(self.table, "import.table")
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.combos: dict[str, QComboBox] = {}
        for r, row in enumerate(self.rows):
            self.table.setItem(r, 0, _item(row.holder if row.holder == row.key else f"{row.holder} ({row.key})"))
            self.table.setItem(r, 1, _item(str(row.existing)))
            self.table.setItem(r, 2, _item(str(row.incoming)))
            self.table.setItem(r, 3, _item(str(row.duplicates)))
            combo = QComboBox()
            for data, label in POLICY_LABELS:
                combo.addItem(label, data)
            combo.setCurrentIndex(0)                    # Merge is the default
            explain(combo, "import.policy")
            combo.setAccessibleName(f"what to do with holder {row.key}")
            self.table.setCellWidget(r, 4, combo)
            self.combos[row.key] = combo
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        # rows as tall as the combo boxes in them (a default row cut their bottom border), and up to six rows in view
        for c in self.combos.values():
            c.ensurePolished()                          # the style sheet's height, not Fusion's
        row_h = max((c.sizeHint().height() for c in self.combos.values()), default=0) + 4
        for r in range(self.table.rowCount()):
            self.table.setRowHeight(r, max(self.table.rowHeight(r), row_h))
        shown = sum(self.table.rowHeight(r) for r in range(min(6, self.table.rowCount())))
        self.table.setMinimumHeight(hh.sizeHint().height() + shown + 2 * self.table.frameWidth() + 2)
        lay.addWidget(self.table, 1)
        note = QLabel(native_text("Merge adds the file's zones with new numbers and skips the ones with the same "
                                  "area. Replace puts the file's zones in place of this holder's. Skip leaves the "
                                  "holder as it is. A backup of the session is saved first, and Ctrl+Z undoes the "
                                  "whole import."))
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Help)
        buttons.button(QDialogButtonBox.Ok).setText("Import")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.helpRequested.connect(lambda: _help(self, "import.policy"))
        explain_buttons(buttons)
        buttons.button(QDialogButtonBox.Ok).setToolTip("Import the zones with the choices above")
        lay.addWidget(buttons)
        fit_screen(self, 620, max(300, self.sizeHint().height()))

    def set_policy(self, key: str, policy: str) -> None:
        c = self.combos[key]
        c.setCurrentIndex(c.findData(policy))

    def policies(self) -> dict[str, str]:
        return {key: c.currentData() for key, c in self.combos.items()}


def ask_policies(parent: QWidget | None, path, rows) -> dict[str, str] | None:
    """The per-holder choice of an import; None when the dialog is cancelled."""
    d = ImportDialog(path, rows, parent)
    try:
        return d.policies() if d.exec() == QDialog.Accepted else None
    finally:
        d.deleteLater()


class RestoreDialog(QDialog):
    """File > Restore zones from a backup…: the snapshots and the previous save, newest first."""

    def __init__(self, session, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Restore zones from a backup")
        self.backups = session.backups()
        lay = QVBoxLayout(self)
        intro = QLabel(native_text("AURORA keeps a backup every 10 minutes while you edit, one before every import, "
                                   "proposal, rule and restore (the last 30), and the previous save. Restoring "
                                   "replaces the zones of every holder; the current zones are backed up first and "
                                   "Ctrl+Z undoes the restore."))
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.table = QTableWidget(len(self.backups), 5)
        _header(self.table, [("Saved", "When the backup was written"), ("Kind", "snapshot or previous save, and why"),
                             ("Holders", "Holders in the backup"), ("Zones", "Zones in the backup"),
                             ("File", "The file in .aurora (or .aurora/backups)")])
        explain(self.table, "restore.table")
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        for r, b in enumerate(self.backups):
            kind = b.kind + (f" ({b.reason})" if b.reason else "")
            self.table.setItem(r, 0, _item(f"{b.time:%Y-%m-%d %H:%M:%S}"))
            self.table.setItem(r, 1, _item(kind if b.ok else f"{kind}: unreadable", b.error))
            self.table.setItem(r, 2, _item(str(b.holders) if b.ok else "–"))
            self.table.setItem(r, 3, _item(str(b.zones) if b.ok else "–"))
            self.table.setItem(r, 4, _item(b.path.name, str(b.path)))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setStretchLastSection(True)
        lay.addWidget(self.table, 1)
        self.empty = QLabel("No backup yet: the first one is written with the first saved change.")
        self.empty.setObjectName("muted")
        self.empty.setVisible(not self.backups)
        lay.addWidget(self.empty)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Help)
        self.ok = self.buttons.button(QDialogButtonBox.Ok)
        self.ok.setText("Restore")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.helpRequested.connect(lambda: _help(self, "main.restore"))
        explain_buttons(self.buttons)
        self.ok.setToolTip("Replace the zones by the selected backup")
        lay.addWidget(self.buttons)
        self.table.itemSelectionChanged.connect(self._selection)
        self.table.doubleClicked.connect(lambda _i: self.ok.isEnabled() and self.accept())
        if self.backups and self.backups[0].ok:
            self.table.selectRow(0)
        self._selection()
        self.resize(700, 360)

    def _selection(self) -> None:
        from aurora.app.widgets import set_enabled
        b = self._current()
        set_enabled(self.ok, b is not None and b.ok, "select a readable backup first")

    def _current(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        return self.backups[min(rows)] if rows else None

    def chosen(self) -> Path | None:
        b = self._current()
        return b.path if b is not None and b.ok else None
