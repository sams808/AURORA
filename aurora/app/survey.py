"""Survey perspective: the plate map of a holder with scan footprints and the zones, the sketch, the sample sheet.

Advanced: 'Grid of zones…' arms the rect+ tool; the rectangle dragged over a block of pads opens the GridDialog, whose
zones show dashed on the map until Accept adds them in one session edit."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from aurora.app import nav
from aurora.app.help_text import explain, explain_all, labelled
from aurora.app.mapview import MapView, scale_label
from aurora.app.mode import mark_advanced
from aurora.app.session import Session
from aurora.app.theme import FOOTPRINTS, MAP_OVERLAY, curve, on_theme_changed, swatch_icon
from aurora.app.vocab import det_items, plural, unit_suffix
from aurora.app.widgets import (Card, ElidedLabel, EmptyState, FlowLayout, default_path, guarded_write, notify_saved,
                                overlay_host, pair, remember_dir, set_enabled)
from aurora.core.model import BEFORE_SURVEY, ScanInfo

FOOTPRINT_LABELS = {"complete": "complete", "aborted": "aborted", "in_progress": "in progress", "test": "test"}
NO_SURVEY_MAP = ("This holder has no survey map: no survey was recorded before these stacks. Reduce works on the "
                 "stacks directly.")
NO_SHEET = "No sample sheet: names are typed by hand."


def holder_title(h) -> str:
    """The holder as Survey names it: its name, or 'stacks before the first survey' for the stacks recorded before
    any survey (the holder 'unknown')."""
    if h is None:
        return ""
    return BEFORE_SURVEY if h.display == BEFORE_SURVEY else f"holder {h.name}"


class FootprintLegend(QWidget):
    """The small legend of the scan footprints: colour and dash per status (never colour alone)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = FlowLayout(self, spacing=6)
        cap = QLabel("footprints:"); cap.setObjectName("muted")
        lay.addWidget(cap)
        self._icons: dict[str, QLabel] = {}
        for status, text in FOOTPRINT_LABELS.items():
            ic = QLabel()
            ic.setAccessibleName(f"{text} footprint")
            self._icons[status] = ic
            lay.addWidget(ic)
            t = QLabel(text); t.setObjectName("muted")
            lay.addWidget(t)
        self.refresh()
        on_theme_changed(self, lambda _s: self.refresh())

    def refresh(self) -> None:
        for status, ic in self._icons.items():
            colour, style = FOOTPRINTS[status]
            ic.setPixmap(swatch_icon(colour, style, 12).pixmap(12, 12))


class SurveyPerspective(QWidget):
    open_requested = Signal(object)       # a complete stack's footprint was clicked: open it in Reduce

    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._survey = None
        self.grid_dialog = None                         # the open 'Grid of zones' dialog
        self._grid_preview = None                       # (zones, skipped cells) drawn dashed until Accept
        self._grid_note = ""                            # what the last Accept added, kept in the hint line
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        h1 = QLabel("Survey"); h1.setObjectName("h1")
        sub = ElidedLabel("plate map, scan footprints, zones", Qt.ElideRight); sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub, 1)
        self.holder_combo = QComboBox()
        self.holder_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.holder_combo.setMinimumContentsLength(18)
        self.holder_combo.currentIndexChanged.connect(self._holder_picked)
        head.addWidget(labelled("Holder", self.holder_combo, "survey.holder"))
        head.addWidget(self.holder_combo)
        self.import_btn = QPushButton("Import zones…")
        self.import_btn.clicked.connect(self._import)
        self.export_btn = QPushButton("Export zones…")
        self.export_btn.clicked.connect(self._export)
        head.addWidget(self.import_btn); head.addWidget(self.export_btn)
        self.grid_btn = QPushButton("Grid of zones…")       # Advanced: rect+ over a block of pads, then the dialog
        self.grid_btn.setCheckable(True)
        self.grid_btn.toggled.connect(self._grid_armed)
        head.addWidget(self.grid_btn)
        root.addLayout(head)

        self.split = split = QSplitter(Qt.Horizontal)
        left = Card("Survey map", help_anchor="3-survey")
        self.options_row = QWidget()                   # channel, SDD, / I0, log, invert, clip, overlays: Advanced
        opts = FlowLayout(self.options_row, spacing=6)
        self.channel = QComboBox()
        self.channel.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.channel.setMinimumContentsLength(8)
        opts.addWidget(pair(labelled("channel", self.channel, "survey.channel"), self.channel))
        self.sdd = QComboBox(); self.sdd.addItems(det_items())
        opts.addWidget(pair(labelled("SDD", self.sdd, "survey.sdd"), self.sdd))
        self.per_i0 = QCheckBox("/ I0"); self.per_i0.setChecked(True); opts.addWidget(self.per_i0)
        self.log = QCheckBox("log"); self.log.setChecked(True); opts.addWidget(self.log)
        self.invert = QCheckBox("invert"); opts.addWidget(self.invert)
        self.clip = QDoubleSpinBox(); self.clip.setRange(0, 20); self.clip.setValue(0.5); self.clip.setSingleStep(0.5)
        self.clip.setSuffix(unit_suffix("percent"))
        opts.addWidget(pair(labelled("clip", self.clip, "survey.clip"), self.clip))
        self.show_footprints = QCheckBox("footprints"); self.show_footprints.setChecked(True); opts.addWidget(self.show_footprints)
        self.show_zones = QCheckBox("zones"); self.show_zones.setChecked(True); opts.addWidget(self.show_zones)
        left.body.addWidget(self.options_row)
        self.map = MapView()
        self.map.setAccessibleName("plate map of the holder")
        self.map.hovered.connect(self._hover)
        self.map.clicked.connect(self._clicked)
        self.map.rect_drawn.connect(self._rect_drawn)
        left.body.addWidget(self.map, 1)
        self.hint = ElidedLabel("", Qt.ElideRight); self.hint.setObjectName("muted")
        left.body.addWidget(self.hint)
        self.footprint_legend = FootprintLegend()
        left.body.addWidget(self.footprint_legend)
        split.addWidget(left)

        right = QSplitter(Qt.Vertical)
        self.zones_card = zc = Card("Zones of the holder", help_anchor="3-survey")
        self.zone_table = QTableWidget(0, 4)
        self.zone_table.setHorizontalHeaderLabels(["#", "Label", "Group", "Scans"])
        self.zone_table.setAccessibleName("zones of the holder")
        self.zone_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.zone_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.zone_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.zone_table.itemSelectionChanged.connect(self._zone_row_selected)
        zc.body.addWidget(self.zone_table, 1)
        right.addWidget(zc)
        sc = Card("Sketch", help_anchor="3-survey")
        self.sketch = QLabel("no sketch for this holder (zones_html/sketches/<holder>.png)")
        self.sketch.setObjectName("muted")
        self.sketch.setWordWrap(True)
        self.sketch.setAlignment(Qt.AlignCenter)
        self.sketch.setMinimumHeight(160)
        sc.body.addWidget(self.sketch, 1)
        right.addWidget(sc)
        self.samples_card = tc = Card("Samples on the holder (sample sheet)", help_anchor="3-survey")
        self.sheet_table = QTableWidget(0, 4)
        self.sheet_table.setHorizontalHeaderLabels(["Sample", "Group", "Composition", "Edges"])
        self.sheet_table.setAccessibleName("samples of the holder")
        self.sheet_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.sheet_table.setEditTriggers(QTableWidget.NoEditTriggers)
        tc.body.addWidget(self.sheet_table, 1)
        # without a sheet (or without a row for this holder): one line and the two ways to add one, no empty table
        self.sheet_note = QLabel("")
        self.sheet_note.setObjectName("muted")
        self.sheet_note.setWordWrap(True)
        tc.body.addWidget(self.sheet_note)
        self.sheet_actions = QWidget()
        sa = FlowLayout(self.sheet_actions, spacing=6)
        self.new_sheet_btn = QPushButton("New sample sheet…")
        self.new_sheet_btn.clicked.connect(lambda: self._project_files("new_sheet"))
        self.choose_sheet_btn = QPushButton("Choose sample sheet…")
        self.choose_sheet_btn.clicked.connect(lambda: self._project_files("choose_sheet"))
        sa.addWidget(self.new_sheet_btn); sa.addWidget(self.choose_sheet_btn)
        tc.body.addWidget(self.sheet_actions)
        self.sheet_note.hide(); self.sheet_actions.hide()
        tc.installEventFilter(self)
        right.addWidget(tc)
        split.addWidget(right)
        split.setStretchFactor(0, 3); split.setStretchFactor(1, 2)
        self.body = overlay_host(split)
        root.addWidget(self.body, 1)

        for w in (self.channel, self.sdd):
            w.currentIndexChanged.connect(self.redraw_map)
        for w in (self.per_i0, self.log, self.invert, self.show_footprints, self.show_zones):
            w.toggled.connect(self.redraw_map)
        self.clip.valueChanged.connect(self.redraw_map)

        self.no_map_state = EmptyState(NO_SURVEY_MAP, [], icon="survey", parent=self.map)
        self.empty_state = EmptyState("No project open: the plate map of each holder appears here.",
                                      [("Open project…", lambda: nav.open_project_dialog(self)),
                                       ("Open the demo beamtime", lambda: nav.open_demo(self))], icon="survey",
                                      parent=self.body)
        explain_all([(self.per_i0, "survey.per_i0"), (self.log, "survey.log"), (self.invert, "survey.invert"),
                     (self.show_footprints, "survey.footprints"), (self.show_zones, "survey.zones"),
                     (self.import_btn, "survey.import"), (self.export_btn, "survey.export")])
        explain(self.grid_btn, "survey.grid")
        explain_all([(self.new_sheet_btn, "survey.new_sheet"), (self.choose_sheet_btn, "survey.choose_sheet")])
        session.project_changed.connect(self._project_changed)
        session.holder_changed.connect(self._holder_changed)
        session.zones_changed.connect(self._zones_changed)
        session.zone_selected.connect(lambda z: self._highlight_zone())
        session.sheet_changed.connect(self._fill_sheet)
        on_theme_changed(self, lambda _s: self.redraw_map())
        # Easy mode: the plate map with fixed display settings, footprints, hover readout, samples, sketch
        mark_advanced(self.options_row, self.import_btn, self.export_btn, self.grid_btn)
        self.update_actions()

    def update_actions(self) -> None:
        has = self.session.project is not None
        for b in (self.import_btn, self.export_btn):
            set_enabled(b, has, "open a project first")
        set_enabled(self.grid_btn, has and self._survey is not None,
                    "open a project first" if not has else "this holder has no survey map")
        if not self.grid_btn.isEnabled() and self.grid_btn.isChecked():
            self.grid_btn.setChecked(False)
        self.empty_state.set_shown(not has)
        h = self.session.holder()
        self.no_map_state.set_shown(has and h is not None and h.survey is None)
        title = holder_title(h)
        self.zones_card.set_title(f"Zones of {'the ' if title == BEFORE_SURVEY else ''}{title}" if h else
                                  "Zones of the holder")
        self.samples_card.set_title(("Samples (sample sheet)" if title == BEFORE_SURVEY else
                                     f"Samples on {title} (sample sheet)") if h else "Samples on the holder (sample sheet)")

    # ------------------------------------------------------------------ wiring
    def _project_changed(self, project) -> None:
        self.holder_combo.blockSignals(True)
        self.holder_combo.clear()
        for key, (day, h) in self.session.holder_keys.items():
            name = BEFORE_SURVEY if holder_title(h) == BEFORE_SURVEY else h.name
            self.holder_combo.addItem(f"{name} · {day} · {plural(len(h.stacks), 'stack')}", key)
        self.holder_combo.blockSignals(False)
        self.update_actions()

    def _holder_picked(self, i: int) -> None:
        key = self.holder_combo.itemData(i)
        if key:
            self.session.set_holder(key)

    def _holder_changed(self, key: str) -> None:
        self._close_grid()
        i = self.holder_combo.findData(key)
        if i >= 0 and self.holder_combo.currentIndex() != i:
            self.holder_combo.blockSignals(True); self.holder_combo.setCurrentIndex(i); self.holder_combo.blockSignals(False)
        self.load_holder()
        self.update_actions()

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
        """The channel the map opens on: sgm_report's summary mode, else the resonant line of the most common edge of
        the holder's stacks when the survey has it, else the first fixed line of roi_bins.json, else the total."""
        from collections import Counter
        from aurora.core.lines import resonant_line
        stacks = self.session.stacks_of()
        for s in stacks:
            if s.summary and str(s.summary.get("mode", "")).startswith("PFY"):
                return s.summary["mode"][4:]
        channels = list(self._survey.channels) if self._survey is not None else []
        edges = Counter(s.edge for s in stacks if s.edge)
        for edge, _n in edges.most_common():
            line = resonant_line(*edge)
            if line in channels:
                return line
        bins = self.session.project.roi_bins if self.session.project is not None else None
        for line in (bins.labels if bins is not None else []):
            if line in channels:
                return line
        return "total" if "total" in channels or not channels else channels[0]

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
        self.map.set_scale_label(scale_label("counts" if ch not in ("I0", "TEY") else ch,
                                             per_i0=self.per_i0.isChecked() and sv.i0_usable and ch != "I0",
                                             log=self.log.isChecked() and ch != "I0", inverted=self.invert.isChecked()))
        if self.show_footprints.isChecked():
            for s in self._footprints():
                box = self._bbox(s)
                if box is None:
                    continue
                col, style = FOOTPRINTS.get(s.status, FOOTPRINTS["complete"])
                sel = self.session.stack_id(s) == self.session.current_stack
                self.map.add_rect(*box, col, 2.5 if sel else 1.2, style=style)
                self.map.add_label(box[0], box[3], f"{s.time} {s.label}", col)
        if self.show_zones.isChecked():
            hz = self.session.holder_zones(create=False)
            for i, z in enumerate(hz.zones if hz else []):
                self.map.draw_zone(z, curve(i), selected=(z.id == self.session.current_zone))
        if self._grid_preview is not None:              # the grid of zones before Accept: dashed, skipped cells dotted
            zones, skipped = self._grid_preview
            add, off = MAP_OVERLAY["rect_add"], MAP_OVERLAY["erase"]
            for z in zones:
                for r in z.rects:
                    self.map.add_rect(r[0], r[1], r[2], r[3], add, 1.4, dashed=True, z=12)
                    self.map.add_label(r[0], r[3], z.label, add)
            for cell, _why in skipped:
                x0, x1, y0, y1 = cell.cell
                self.map.add_rect(x0, x1, y0, y1, off, 1.0, z=12, style=Qt.DotLine)
        note = "; ".join(sv.notes)
        self.hint.setText(f"{plural(sv.n, 'point')} at {sv.energy:.1f} eV, pitch {sv.pitch[0]:.2f} x {sv.pitch[1]:.2f} mm"
                          + (f" · {note}" if note else "") + (f" · {self._grid_note}" if self._grid_note else ""))

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
        rows = self.session.sheet_for(h.name) if h else []
        self.sheet_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate((row.sample_id, row.group, row.composition, row.edges)):
                self.sheet_table.setItem(r, c, QTableWidgetItem(v))
        self._sheet_state(h, rows)

    def _sheet_state(self, h, rows) -> None:
        """No sheet: one line and New / Choose sample sheet…; a sheet without a row for this holder: why, with the
        holders it names; otherwise the table."""
        ses = self.session
        has_sheet = ses.project is not None and bool(ses.sheet_rows)
        note = ""
        if ses.project is not None and h is not None and not has_sheet:
            note = NO_SHEET
        elif has_sheet and h is not None and not rows:
            col = ses.sheet_choice.columns.get("holder", "holder")
            theirs = ses.sheet_choice.holders
            shown = ", ".join(theirs[:5]) + (" …" if len(theirs) > 5 else "")
            where = BEFORE_SURVEY if holder_title(h) == BEFORE_SURVEY else f"holder {h.name}"
            note = (f"The sample sheet lists no sample on {where} (its {col} column names {shown})." if theirs else
                    f"The sample sheet lists no sample on {where}.")
        self.sheet_note.setText(note)
        self.sheet_note.setVisible(bool(note))
        self.sheet_actions.setVisible(note == NO_SHEET)
        self.sheet_table.setVisible(not note)
        self._fit_samples_card()

    def _fit_samples_card(self) -> None:
        """A samples card with nothing to list takes the height of its line and buttons only (the other cards get the
        rest); with rows it takes its share again. Recomputed when the card's width changes (the buttons wrap)."""
        card = self.samples_card
        if not self.sheet_table.isHidden() or self.sheet_note.isHidden():
            self.sheet_note.setMinimumHeight(0)
            self.sheet_actions.setMinimumHeight(0)
            card.setMinimumHeight(0)
            card.setMaximumHeight(16777215)
            return
        m = card.layout().contentsMargins()
        w = max(card.width(), 200) - m.left() - m.right()
        self.sheet_note.setMinimumHeight(self.sheet_note.heightForWidth(w))
        self.sheet_actions.setMinimumHeight(self.sheet_actions.layout().heightForWidth(w)
                                            if not self.sheet_actions.isHidden() else 0)
        card.layout().activate()
        h = card.minimumSizeHint().height()
        card.setMinimumHeight(h)
        card.setMaximumHeight(h)

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent
        if obj is self.samples_card and event.type() == QEvent.Resize and event.oldSize().width() != event.size().width():
            self._fit_samples_card()
        return super().eventFilter(obj, event)

    def _project_files(self, what: str) -> None:
        """New / Choose sample sheet… of the window's File menu (aurora.app.project_files)."""
        from aurora.app.project_files import ProjectFiles
        win = nav.window_of(self)
        pf = win.findChild(ProjectFiles, "aurora_project_files") if win is not None else None
        if pf is not None:
            getattr(pf, what)()

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

    # ------------------------------------------------------------------ grid of zones (Advanced)
    def _grid_armed(self, on: bool) -> None:
        self.map.set_tool("rect+" if on else "navigate")
        if on:
            self._grid_note = ""
            self.hint.setText("Grid of zones: drag a rectangle over a block of pads (click the button again to cancel)")

    def _rect_drawn(self, x0: float, x1: float, y0: float, y1: float) -> None:
        if not self.grid_btn.isChecked():
            return
        px, py = self._survey.pitch if self._survey is not None else (0.0, 0.0)
        if abs(x1 - x0) < px or abs(y1 - y0) < py or x1 == x0 or y1 == y0:
            self.hint.setText("Grid of zones: drag a rectangle over the pads (a click selects nothing)")
            return
        self.grid_btn.setChecked(False)
        self.open_grid_dialog((x0, x1, y0, y1))

    def open_grid_dialog(self, bbox):
        """The Grid of zones dialog over bbox = (x0, x1, y0, y1) mm, its preview on the map (window-modal, not
        blocking: tests drive it and call accept())."""
        from aurora.app.grid_dialog import GridDialog
        self._close_grid()
        scans = [(s, self._bbox(s)) for s in self._footprints()]
        dlg = GridDialog(self.session, bbox, survey=self._survey, channel=self.channel.currentText() or None,
                         scans=scans, parent=self)
        self.grid_dialog = dlg
        dlg.preview_changed.connect(self._grid_preview_changed)
        dlg.finished.connect(lambda result, d=dlg: self._grid_finished(d, result))     # Qt: finished, then accepted
        dlg._update()
        dlg.open()
        return dlg

    def _grid_preview_changed(self, preview) -> None:
        self._grid_preview = preview
        self.redraw_map()

    def _accept_grid(self, dlg) -> str:
        """Add the dialog's zones to the holder in one edit; returns what was done ('' when nothing)."""
        zones = dlg.zones()
        skipped = dlg.skipped_text()
        if not zones:
            return ""
        with self.session.edit(f"grid of {len(zones)} zones"):
            hz = self.session.holder_zones()
            hz.zones.extend(zones)                  # numbered after the zones of the holder (GridDialog.start_id)
        note = f"added {plural(len(zones), 'zone')} to holder {hz.holder}" + (f"; skipped {skipped}" if skipped else "")
        self.session.status.emit(note)
        return note

    def _grid_finished(self, dlg, result: int) -> None:
        from PySide6.QtWidgets import QDialog
        if result == QDialog.Accepted:
            self._grid_note = self._accept_grid(dlg)        # kept in the hint line (redraw_map writes it)
        if self.grid_dialog is dlg:
            self.grid_dialog = None
        self._grid_preview = None
        self.redraw_map()

    def _close_grid(self) -> None:
        self._grid_note = ""
        if self.grid_btn.isChecked():
            self.grid_btn.setChecked(False)
        if self.grid_dialog is not None:
            d, self.grid_dialog = self.grid_dialog, None
            d.reject()
        self._grid_preview = None

    def _import(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Import zones (zones_all.json)", default_path("zones", "zones_all.json", self.session),
                                           "JSON (*.json)")
        if not p:
            return
        remember_dir("zones", p)
        from aurora.core.io.config import ZonesFile
        try:
            ZonesFile.load(Path(p))                      # read it first: a broken file leaves the zones unchanged
        except (OSError, ValueError) as err:
            QMessageBox.warning(self, "Import zones", f"Could not read {Path(p).name}: {err}. The zones are unchanged.")
            return
        with self.session.edit(f"import zones from {Path(p).name}"):
            self.session.import_zones(Path(p))

    def _export(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Export zones", default_path("zones", "zones_all.json", self.session),
                                           "JSON (*.json)")
        if not p:
            return
        remember_dir("zones", p)
        with guarded_write(self, Path(p).name) as g:
            self.session.export_zones(Path(p))
        if g.ok:
            notify_saved(self.hint, [Path(p)])
