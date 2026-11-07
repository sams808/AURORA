"""Survey perspective: the plate map of a holder with scan footprints and the zones, the sketch, the sample sheet."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from aurora.app.mapview import MapView
from aurora.app.preview import PAL
from aurora.app.session import Session
from aurora.app.theme import TOKENS
from aurora.app.widgets import Card
from aurora.core.model import ScanInfo

STATUS_COLOUR = {"complete": "#F2A65A", "aborted": "#ff5555", "in_progress": "#A78BFA", "test": "#5B6B8C"}


class SurveyPerspective(QWidget):
    open_requested = Signal(object)       # a complete stack's footprint was clicked: open it in Reduce

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._survey = None
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        h1 = QLabel("Survey"); h1.setObjectName("h1")
        sub = QLabel("plate map, scan footprints, zones"); sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub)
        head.addSpacing(16)
        head.addWidget(QLabel("Holder"))
        self.holder_combo = QComboBox()
        self.holder_combo.currentIndexChanged.connect(self._holder_picked)
        head.addWidget(self.holder_combo)
        head.addStretch(1)
        self.import_btn = QPushButton("Import zones…")
        self.import_btn.clicked.connect(self._import)
        self.export_btn = QPushButton("Export zones…")
        self.export_btn.clicked.connect(self._export)
        head.addWidget(self.import_btn); head.addWidget(self.export_btn)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        left = Card("Survey map")
        opts = QHBoxLayout()
        opts.addWidget(QLabel("channel"))
        self.channel = QComboBox(); opts.addWidget(self.channel)
        opts.addWidget(QLabel("SDD"))
        self.sdd = QComboBox(); self.sdd.addItems(["sum", "1", "2", "3", "4"]); opts.addWidget(self.sdd)
        self.per_i0 = QCheckBox("/ I0"); self.per_i0.setChecked(True); opts.addWidget(self.per_i0)
        self.log = QCheckBox("log"); self.log.setChecked(True); opts.addWidget(self.log)
        self.invert = QCheckBox("invert"); opts.addWidget(self.invert)
        opts.addWidget(QLabel("clip %"))
        self.clip = QDoubleSpinBox(); self.clip.setRange(0, 20); self.clip.setValue(0.5); self.clip.setSingleStep(0.5); opts.addWidget(self.clip)
        self.show_footprints = QCheckBox("footprints"); self.show_footprints.setChecked(True); opts.addWidget(self.show_footprints)
        self.show_zones = QCheckBox("zones"); self.show_zones.setChecked(True); opts.addWidget(self.show_zones)
        opts.addStretch(1)
        left.body.addLayout(opts)
        self.map = MapView()
        self.map.hovered.connect(self._hover)
        self.map.clicked.connect(self._clicked)
        left.body.addWidget(self.map, 1)
        self.hint = QLabel(""); self.hint.setObjectName("muted")
        left.body.addWidget(self.hint)
        split.addWidget(left)

        right = QSplitter(Qt.Vertical)
        zc = Card("Zones of this holder")
        self.zone_table = QTableWidget(0, 4)
        self.zone_table.setHorizontalHeaderLabels(["#", "Label", "Group", "Scans"])
        self.zone_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.zone_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.zone_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.zone_table.itemSelectionChanged.connect(self._zone_row_selected)
        zc.body.addWidget(self.zone_table, 1)
        right.addWidget(zc)
        sc = Card("Sketch")
        self.sketch = QLabel("no sketch for this holder (zones_html/sketches/<holder>.png)")
        self.sketch.setObjectName("muted")
        self.sketch.setAlignment(Qt.AlignCenter)
        self.sketch.setMinimumHeight(160)
        sc.body.addWidget(self.sketch, 1)
        right.addWidget(sc)
        tc = Card("Samples on this holder (sample sheet)")
        self.sheet_table = QTableWidget(0, 4)
        self.sheet_table.setHorizontalHeaderLabels(["Sample", "Group", "Composition", "Edges"])
        self.sheet_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.sheet_table.setEditTriggers(QTableWidget.NoEditTriggers)
        tc.body.addWidget(self.sheet_table, 1)
        right.addWidget(tc)
        split.addWidget(right)
        split.setStretchFactor(0, 3); split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        for w in (self.channel, self.sdd):
            w.currentIndexChanged.connect(self.redraw_map)
        for w in (self.per_i0, self.log, self.invert, self.show_footprints, self.show_zones):
            w.toggled.connect(self.redraw_map)
        self.clip.valueChanged.connect(self.redraw_map)

        session.project_changed.connect(self._project_changed)
        session.holder_changed.connect(self._holder_changed)
        session.zones_changed.connect(self._zones_changed)
        session.zone_selected.connect(lambda z: self._highlight_zone())

    # ------------------------------------------------------------------ wiring
    def _project_changed(self, project) -> None:
        self.holder_combo.blockSignals(True)
        self.holder_combo.clear()
        for key, (day, h) in self.session.holder_keys.items():
            self.holder_combo.addItem(f"{day} · {h.name}  ({len(h.stacks)} stacks)", key)
        self.holder_combo.blockSignals(False)

    def _holder_picked(self, i: int) -> None:
        key = self.holder_combo.itemData(i)
        if key:
            self.session.set_holder(key)

    def _holder_changed(self, key: str) -> None:
        i = self.holder_combo.findData(key)
        if i >= 0 and self.holder_combo.currentIndex() != i:
            self.holder_combo.blockSignals(True); self.holder_combo.setCurrentIndex(i); self.holder_combo.blockSignals(False)
        self.load_holder()

    def load_holder(self) -> None:
        self._survey = None
        try:
            self._survey = self.session.survey()
        except Exception as err:
            self.hint.setText(f"survey could not be read: {err}")
        self.channel.blockSignals(True)
        self.channel.clear()
        if self._survey is not None:
            self.channel.addItems(self._survey.channels)
            main = self._main_line_guess()
            if main in self._survey.channels:
                self.channel.setCurrentText(main)
        self.channel.blockSignals(False)
        self.redraw_map(); self.map.autorange()
        self._fill_zone_table(); self._fill_sheet(); self._load_sketch()

    def _main_line_guess(self) -> str:
        for s in self.session.stacks_of():
            if s.summary and s.summary.get("mode", "").startswith("PFY"):
                return s.summary["mode"][4:]
        return "F Kα"

    # ------------------------------------------------------------------ drawing
    def redraw_map(self) -> None:
        sv = self._survey
        self.map.clear_overlays()
        if sv is None:
            self.map.set_grid(None)
            return
        ch = self.channel.currentText() or (sv.channels[0] if sv.channels else "total")
        det = None if self.sdd.currentIndex() == 0 else self.sdd.currentIndex() - 1
        c = self.clip.value()
        vals = sv.values(ch, det=det, per_i0=self.per_i0.isChecked(), log10=self.log.isChecked(),
                         invert=self.invert.isChecked(), clip=(c, 100 - c))
        self.map.set_grid(sv.grid(vals))
        if self.show_footprints.isChecked():
            for s in self._footprints():
                box = self._bbox(s)
                if box is None:
                    continue
                col = STATUS_COLOUR.get(s.status, "#F2A65A")
                sel = self.session.stack_id(s) == self.session.current_stack
                self.map.add_rect(*box, col, 2.5 if sel else 1.2, dashed=s.status != "complete")
                self.map.add_label(box[0], box[3], f"{s.time} {s.label}", col)
        if self.show_zones.isChecked():
            hz = self.session.holder_zones(create=False)
            for i, z in enumerate(hz.zones if hz else []):
                self.map.draw_zone(z, PAL[i % len(PAL)], selected=(z.id == self.session.current_zone))
        note = "; ".join(sv.notes)
        self.hint.setText(f"{sv.n} points at {sv.energy:.1f} eV, pitch {sv.pitch[0]:.2f} x {sv.pitch[1]:.2f} mm" + (f" · {note}" if note else ""))

    def _footprints(self) -> list[ScanInfo]:
        h = self.session.holder()
        return (h.stacks + h.dropped) if h else []

    def _bbox(self, s: ScanInfo):
        md = s.md
        try:
            x0, x1, y0, y1 = (float(md[k]) for k in ("xstart", "xstop", "ystart", "ystop"))
        except (KeyError, ValueError):
            sd = self.session.stack_loaded(self.session.stack_id(s))
            if sd is None:
                return None
            x0, x1, y0, y1 = sd.bbox
        return (min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1))

    def _highlight_zone(self) -> None:
        self.redraw_map()
        zid = self.session.current_zone
        for r in range(self.zone_table.rowCount()):
            if self.zone_table.item(r, 0).data(Qt.UserRole) == zid:
                self.zone_table.blockSignals(True); self.zone_table.selectRow(r); self.zone_table.blockSignals(False)

    def _zones_changed(self) -> None:
        self._fill_zone_table(); self.redraw_map()

    def _fill_zone_table(self) -> None:
        hz = self.session.holder_zones(create=False)
        zones = hz.zones if hz else []
        self.zone_table.blockSignals(True)
        self.zone_table.setRowCount(len(zones))
        for r, z in enumerate(zones):
            it = QTableWidgetItem(str(z.id)); it.setData(Qt.UserRole, z.id)
            self.zone_table.setItem(r, 0, it)
            self.zone_table.setItem(r, 1, QTableWidgetItem(z.label))
            self.zone_table.setItem(r, 2, QTableWidgetItem(z.group))
            self.zone_table.setItem(r, 3, QTableWidgetItem(str(sum(1 for s in z.settings.values() if s.include))))
        self.zone_table.blockSignals(False)

    def _zone_row_selected(self) -> None:
        rows = self.zone_table.selectionModel().selectedRows()
        if rows:
            self.session.select_zone(self.zone_table.item(rows[0].row(), 0).data(Qt.UserRole))

    def _fill_sheet(self) -> None:
        h = self.session.holder()
        rows = [r for r in self.session.sheet_rows if h and r.plate.strip().lower() == h.name.lower()]
        self.sheet_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate((row.sample_id, row.group, row.composition, " ".join(x for x in (row.f_k_edge, row.other_edges) if x))):
                self.sheet_table.setItem(r, c, QTableWidgetItem(v))

    def _load_sketch(self) -> None:
        h = self.session.holder()
        p = self.session.project.root / "zones_html" / "sketches" / f"{h.name}.png" if h else None
        if p and p.exists():
            pm = QPixmap(str(p))
            self.sketch.setPixmap(pm.scaledToWidth(min(420, pm.width()), Qt.SmoothTransformation))
            self.sketch.setText("")
        else:
            self.sketch.setPixmap(QPixmap())
            self.sketch.setText("no sketch for this holder (zones_html/sketches/<holder>.png)")

    # ------------------------------------------------------------------ interaction
    def _hover(self, x: float, y: float) -> None:
        sv = self._survey
        if sv is None:
            return
        txt = f"x {x:.2f} mm, y {y:.2f} mm"
        for s in self._footprints():
            b = self._bbox(s)
            if b and b[0] <= x <= b[1] and b[2] <= y <= b[3]:
                txt += f" · {s.name}"
                break
        self.hint.setText(txt)

    def _clicked(self, x: float, y: float) -> None:
        for s in self._footprints():
            b = self._bbox(s)
            if b and b[0] <= x <= b[1] and b[2] <= y <= b[3] and s.status == "complete":
                self.open_requested.emit(s)
                self.redraw_map()
                return

    def _import(self) -> None:
        start = str(self.session.project.root / "zones_html") if self.session.project else ""
        p, _ = QFileDialog.getOpenFileName(self, "Import zones (zones_all.json)", start, "JSON (*.json)")
        if p:
            self.session.import_zones(Path(p))

    def _export(self) -> None:
        start = str(self.session.project.root / "zones_html" / "zones_all.json") if self.session.project else "zones_all.json"
        p, _ = QFileDialog.getSaveFileName(self, "Export zones", start, "JSON (*.json)")
        if p:
            self.session.export_zones(Path(p))
