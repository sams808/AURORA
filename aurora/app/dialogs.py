"""Small dialogs: pick reference spectra, stitch tiles."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from aurora.core.names import split_stack_id
from aurora.core.references import list_references
from aurora.core.stitch import auto_trims, seams, stitch


class ReferencesDialog(QDialog):
    """Tick reference spectra from the reference folder (Settings), filtered by element."""

    def __init__(self, folder: str, edge: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Reference spectra")
        self.folder = folder
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("folder"))
        self.folder_edit = QLineEdit(folder); row.addWidget(self.folder_edit, 1)
        b = QPushButton("…"); b.setMaximumWidth(36); b.clicked.connect(self._choose); row.addWidget(b)
        row.addWidget(QLabel("element"))
        self.edge = QLineEdit(edge); self.edge.setPlaceholderText("all"); self.edge.setMaximumWidth(60); row.addWidget(self.edge)
        lay.addLayout(row)
        self.list = QListWidget(); lay.addWidget(self.list, 1)
        self.info = QLabel(""); self.info.setObjectName("muted"); lay.addWidget(self.info)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.folder_edit.editingFinished.connect(self.refresh); self.edge.editingFinished.connect(self.refresh)
        self.setMinimumSize(560, 420)
        self.refresh()

    def _choose(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Reference spectra folder", self.folder_edit.text())
        if d:
            self.folder_edit.setText(d); self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        refs = list_references(Path(self.folder_edit.text()), self.edge.text().strip() or None)
        for r in refs:
            it = QListWidgetItem(f"{r.label}" + (f"   ({r.edge})" if r.edge else "") + f"   {r.path.name}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable); it.setCheckState(Qt.Unchecked); it.setData(Qt.UserRole, (str(r.path), r.label))
            self.list.addItem(it)
        self.info.setText(f"{len(refs)} files" if refs else "no spectra found (csv / txt / dat / nor / xmu, one level of subfolders)")

    def chosen(self) -> list[tuple[Path, str]]:
        """(path, label) of the ticked references."""
        return [(Path(self.list.item(i).data(Qt.UserRole)[0]), self.list.item(i).data(Qt.UserRole)[1])
                for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked]


class StitchDialog(QDialog):
    """Pick two or more loaded stacks of the holder, halve the overlaps, see the seams, bake a stitched stack."""

    def __init__(self, stacks: dict[str, object], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Stitch tiles")
        self.stacks = stacks
        self.result = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("tick the tiles (loaded stacks of the holder):"))
        self.list = QListWidget()
        for sid, sd in stacks.items():
            it = QListWidgetItem(f"{split_stack_id(sid)[1]}   x {sd.x.min():.2f}..{sd.x.max():.2f}  y {sd.y.min():.2f}..{sd.y.max():.2f} mm   {sd.n_pix} px")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable); it.setCheckState(Qt.Unchecked); it.setData(Qt.UserRole, sid)
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("trim"))
        self.mode = QComboBox(); self.mode.addItems(["halve the overlaps", "no trim"]); row.addWidget(self.mode)
        b = QPushButton("Check the seams"); b.clicked.connect(self.check); row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.info = QLabel(""); self.info.setObjectName("muted"); self.info.setWordWrap(True); lay.addWidget(self.info)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Bake")
        buttons.accepted.connect(self.bake); buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.setMinimumSize(600, 380)

    def chosen(self):
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.Checked]

    def _trims(self, tiles):
        return auto_trims(tiles) if self.mode.currentIndex() == 0 else None

    def check(self) -> None:
        tiles = [self.stacks[s] for s in self.chosen()]
        if len(tiles) < 2:
            self.info.setText("tick at least two tiles"); return
        trims = self._trims(tiles)
        from aurora.core.stitch import Trim
        s = seams(tiles, trims or [Trim() for _ in tiles])
        self.info.setText("; ".join(f"tiles {a + 1}-{b + 1} ({ax}): {v} {g:+.3f} mm" for (a, b), ax, g, v in [(x.tiles, x.axis, x.gap_mm, x.verdict) for x in s])
                          or "no adjacent tiles found")

    def bake(self) -> None:
        tiles = [self.stacks[s] for s in self.chosen()]
        if len(tiles) < 2:
            self.info.setText("tick at least two tiles"); return
        try:
            self.result = stitch(tiles, self._trims(tiles))
        except ValueError as err:
            self.info.setText(str(err)); return
        self.accept()
