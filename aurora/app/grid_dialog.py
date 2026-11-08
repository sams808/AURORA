"""Survey, Advanced: the 'Grid of zones…' dialog. The rectangle dragged with the rect+ tool over a block of pads is cut
into rows x cols zones; rows, cols and labels come from the config/plate_layout.json group whose match words fit the
holder's scans, or from the holder's sample-sheet rows. The Survey map shows the zones dashed while the dialog is
open; Accept adds them to the holder in one session edit ('grid of N zones')."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QSpinBox,
                               QVBoxLayout, QWidget)

from aurora.app.help_text import explain, explain_buttons, labelled
from aurora.core.propose import grid_cells, layout_group, layout_groups, norm_text, pads_from_survey, snap_to_pads, \
    zones_from_cells

ROTATIONS = (0, 90, 180, 270)
DEFAULT_SHAPE = (2, 2)                   # rows x cols offered when no layout group fits (a start value, edit freely)
DEFAULT_FILL_PCT = 70                    # zones_from_grid's default fill of 0.7


def _overlap(a, b) -> float:
    if a is None or b is None:
        return 0.0
    ox = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    oy = max(0.0, min(a[3], b[3]) - max(a[2], b[2]))
    return ox * oy


def best_layout_group(layout: dict, holder: str, scans, bbox) -> dict | None:
    """The layout group of the holder that fits the scans under the rectangle best: the scans are tried by how much
    of their footprint lies in the rectangle (most first), then the other scans of the holder; the first scan whose
    label matches a group (sgm_report's group_for rule) gives it. scans: [(ScanInfo, footprint bbox or None)]."""
    ordered = sorted(scans, key=lambda sb: -_overlap(sb[1], bbox))
    for scan, _box in ordered:
        g = layout_group(layout, holder, scan.label or scan.name)
        if g is not None:
            return g
    return None


class GridDialog(QDialog):
    """Rows, columns, fill, rotation and labels of a grid of zones over `bbox` (x0, x1, y0, y1 mm)."""
    preview_changed = Signal(object)          # (list[Zone], [(GridCell, why skipped)])

    def __init__(self, session, bbox, survey=None, channel: str | None = None, scans=(), parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("griddialog")
        self.setWindowTitle("Grid of zones")
        self.setMinimumWidth(460)
        self.session = session
        x0, x1 = sorted((float(bbox[0]), float(bbox[1])))
        y0, y1 = sorted((float(bbox[2]), float(bbox[3])))
        self.bbox = (x0, x1, y0, y1)
        self.survey = survey
        self.channel = channel
        self._pads = None
        h = session.holder()
        self.holder = h.name if h else ""
        layout = session.project.plate_layout if session.project is not None else {}
        self.groups = layout_groups(layout, self.holder)
        self.sheet = [r for r in session.sheet_rows if h and r.plate.strip().lower() == h.name.lower()]
        self.matched = best_layout_group(layout, self.holder, list(scans), self.bbox)

        lay = QVBoxLayout(self)
        self.area = QLabel(f"Holder {self.holder or '?'}: rectangle x {x0:.2f} … {x1:.2f} mm, y {y0:.2f} … {y1:.2f} mm")
        self.area.setObjectName("muted")
        self.area.setWordWrap(True)
        lay.addWidget(self.area)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.layout_combo = QComboBox()
        self.layout_combo.addItem("none", None)
        for i, g in enumerate(self.groups):
            shape = (f"{g['rows']} × {g['cols']}" if g.get("rows") and g.get("cols") else
                     f"{len(g.get('labels') or [])} samples, arrangement unknown")
            self.layout_combo.addItem(f"{g.get('id', f'group {i + 1}')} ({shape})", i)
        self.rows = QSpinBox(); self.rows.setRange(1, 40); self.rows.setSuffix(" rows")
        self.cols = QSpinBox(); self.cols.setRange(1, 40); self.cols.setSuffix(" columns")
        self.rows.setValue(DEFAULT_SHAPE[0]); self.cols.setValue(DEFAULT_SHAPE[1])
        self.fill = QSpinBox(); self.fill.setRange(10, 100); self.fill.setSingleStep(5); self.fill.setSuffix(" %")
        self.fill.setValue(DEFAULT_FILL_PCT)
        self.rotation = QComboBox()
        for r in ROTATIONS:
            self.rotation.addItem(f"{r}°", r)
        self.labels = QComboBox()
        self.labels.addItem("layout cells", "layout")
        self.labels.addItem("sample sheet rows", "sheet")
        self.labels.addItem("zone numbers", "numbers")
        self.first_row = QSpinBox(); self.first_row.setRange(1, max(1, len(self.sheet)))
        self.first_row.setSuffix(f" of {len(self.sheet)}" if self.sheet else " (no sheet rows for this holder)")
        self.snap = QCheckBox("snap to pads")
        rows_ = [("layout group", self.layout_combo, "survey.grid.layout"), ("rows", self.rows, "survey.grid.rows"),
                 ("columns", self.cols, "survey.grid.cols"), ("cell fill", self.fill, "survey.grid.fill"),
                 ("rotation", self.rotation, "survey.grid.rotation"), ("labels from", self.labels, "survey.grid.labels"),
                 ("first sheet row", self.first_row, "survey.grid.first_row")]
        for r, (text, w, key) in enumerate(rows_):
            grid.addWidget(labelled(text, w, key), r, 0)
            grid.addWidget(w, r, 1)
        grid.addWidget(self.snap, len(rows_), 0, 1, 2)
        grid.setColumnStretch(1, 1)
        explain(self.snap, "survey.grid.snap")
        lay.addLayout(grid)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Help | QDialogButtonBox.Cancel | QDialogButtonBox.Ok)
        self.accept_btn = self.buttons.button(QDialogButtonBox.Ok)
        self.accept_btn.setText("Accept")
        self.accept_btn.setToolTip("Add the zones of the dashed preview to the holder (one edit, undone in one step)")
        explain_buttons(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.helpRequested.connect(self._help)
        lay.addWidget(self.buttons)

        if self.survey is None:
            self.snap.setEnabled(False)
            self.snap.setToolTip(self.snap.toolTip() + "\nNot available: this holder has no survey map")
        self._set_label_items()
        self.layout_combo.currentIndexChanged.connect(self._group_picked)
        for w in (self.rows, self.cols, self.fill, self.first_row):
            w.valueChanged.connect(self._update)
        for w in (self.rotation, self.labels):
            w.currentIndexChanged.connect(self._update)
        self.snap.toggled.connect(self._update)
        start = 0
        if self.matched is not None:
            start = next((i + 1 for i, g in enumerate(self.groups) if g.get("id") == self.matched.get("id")), 0)
        self.layout_combo.setCurrentIndex(start)
        self._group_picked()

    # ------------------------------------------------------------------ state
    def group(self) -> dict | None:
        i = self.layout_combo.currentData()
        return self.groups[i] if i is not None and 0 <= i < len(self.groups) else None

    def _set_label_items(self) -> None:
        """Offer only the label sources that exist: a layout group with names, a sample sheet for the holder."""
        model = self.labels.model()
        g = self.group()
        for i in range(self.labels.count()):
            src = self.labels.itemData(i)
            ok = (src == "layout" and g is not None and bool(g.get("labels"))) or (src == "sheet" and bool(self.sheet)) \
                or src == "numbers"
            model.item(i).setEnabled(ok)
        if not model.item(self.labels.currentIndex()).isEnabled():
            self.labels.setCurrentIndex(self.labels.findData("numbers"))

    def _group_picked(self, *_a) -> None:
        g = self.group()
        for w in (self.rows, self.cols):
            w.blockSignals(True)
        if g is not None and g.get("rows") and g.get("cols"):
            self.rows.setValue(int(g["rows"]))
            self.cols.setValue(int(g["cols"]))
        for w in (self.rows, self.cols):
            w.blockSignals(False)
        self.labels.blockSignals(True)
        self._set_label_items()
        if g is not None and g.get("labels"):
            self.labels.setCurrentIndex(self.labels.findData("layout"))
        elif self.sheet:
            self.labels.setCurrentIndex(self.labels.findData("sheet"))
        self.labels.blockSignals(False)
        self._update()

    def source(self) -> str:
        return self.labels.currentData() or "numbers"

    def _sheet_group(self, label) -> str:
        key = norm_text(label)
        row = next((r for r in self.sheet if key and norm_text(r.sample_id) == key), None)
        return row.group if row is not None else ""

    def label_lists(self):
        """(labels, groups) in the layout's reading order for the chosen source (None, None for numbers)."""
        n = self.rows.value() * self.cols.value()
        src = self.source()
        g = self.group()
        if src == "layout" and g is not None and g.get("labels"):
            labels = g["labels"]
            gid = str(g.get("id", ""))
            if labels and isinstance(labels[0], (list, tuple)):
                groups = [[(self._sheet_group(v) or gid) if v is not None else "" for v in row] for row in labels]
            else:
                groups = [self._sheet_group(v) or gid for v in labels]
            return labels, groups
        if src == "sheet" and self.sheet:
            rows = self.sheet[self.first_row.value() - 1:][:n]
            return [r.sample_id for r in rows], [r.group for r in rows]
        return None, None

    def pads(self):
        if self._pads is None and self.survey is not None:
            ch = self.channel if self.channel in (self.survey.lines or []) else None
            self._pads = pads_from_survey(self.survey, channel=ch)
        return self._pads or []

    def cells(self):
        """(kept cells, [(cell, why skipped)]) in reading order."""
        labels, groups = self.label_lists()
        cells = grid_cells(self.bbox, self.rows.value(), self.cols.value(), fill=self.fill.value() / 100.0,
                           rotation=int(self.rotation.currentData() or 0), labels=labels, groups=groups)
        skipped = [(c, "empty on the layout") for c in cells if c.empty]
        cells = [c for c in cells if not c.empty]
        if self.snap.isChecked() and self.survey is not None:
            pitch = tuple(getattr(self.survey, "pitch", (0.0, 0.0)))
            cells, nopad = snap_to_pads(cells, self.pads(), pitch)
            skipped += [(c, "no pad") for c in nopad]
        skipped.sort(key=lambda cw: (cw[0].row, cw[0].col))
        return cells, skipped

    def start_id(self) -> int:
        hz = self.session.holder_zones(create=False)
        return max((z.id for z in hz.zones), default=0) + 1 if hz else 1

    def zones(self):
        cells, _skipped = self.cells()
        return zones_from_cells(cells, start_id=self.start_id(), rows=self.rows.value(), cols=self.cols.value())

    def skipped_text(self) -> str:
        _cells, skipped = self.cells()
        return ", ".join(f"{c.name}{f' {c.label}' if c.label else ''} ({why})" for c, why in skipped)

    def _update(self, *_a) -> None:
        self.first_row.setEnabled(self.source() == "sheet")
        cells, skipped = self.cells()
        zones = zones_from_cells(cells, start_id=self.start_id(), rows=self.rows.value(), cols=self.cols.value())
        text = f"{len(zones)} zone{'s' if len(zones) != 1 else ''} in the dashed preview"
        g = self.group()
        if self.matched is not None and g is not None and g.get("id") == self.matched.get("id"):
            text += f" · layout group '{self.matched.get('id', '')}' matches the holder's scans"
        if skipped:
            text += " · skipped: " + ", ".join(f"{c.name}{f' {c.label}' if c.label else ''} ({why})" for c, why in skipped)
        self.summary.setText(text)
        self.accept_btn.setEnabled(bool(zones))
        self.preview_changed.emit((zones, skipped))

    def _help(self) -> None:
        from aurora.app.help import open_help
        open_help(self, "MANUAL.md", "3-survey")
