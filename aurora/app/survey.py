"""Survey perspective: the plate map of a holder with scan footprints and the zones, the Zone preview, the sketch,
the sample sheet.

Zones are drawn and erased on the plate map with the tool bar Reduce has (zone_tools.ZoneToolBar: Draw / Erase x Move /
Rectangle / Brush / Lasso, + Polygon in Advanced; right-drag or Shift-drag erases; one stroke is one undo step). The
page opens in Move, so a click never changes a zone: in Move a click inside a zone selects it, a click on a complete
footprint outside every zone opens that stack in Reduce, a double-click inside a zone over a complete footprint opens
that stack with the zone selected. The Zone preview (zone_preview.ZonePreviewCard) shows the zone's XRF emission from
its survey points and its XAS on a stack it covers, live while a stroke is drawn (throttled, zone_tools.PreviewThrottle).

The first view of a holder holds the survey map and every footprint and zone (a note counts what lies outside the
map). The sketch is optional: its card is shown only when the holder has one (aurora.core.sketches: .aurora/sketches/,
sketches/, zones_html/sketches/); 'Load a sketch…' (next to the holder, and in the map's right-click menu) copies an
image into .aurora/sketches/, 'Remove the sketch' removes that copy. The sample card collapses to one line when the
sheet has no row for the holder, or when there is no sheet (then with New / Choose sample sheet…). The right column
(zones, Zone preview, sketch, samples) scrolls up and down rather than squeeze the preview.

Advanced: 'Grid of zones…' arms the rect+ tool (the tool bar is off meanwhile); the rectangle dragged over a block of
pads opens the GridDialog, whose zones show dashed on the map until Accept adds them in one session edit."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSplitter, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from aurora.app import nav, shortcuts
from aurora.app.help_text import explain, explain_all, labelled, say
from aurora.app.mapview import MapView, scale_label
from aurora.app.mode import is_advanced, mark_advanced, on_mode_changed
from aurora.app.session import Session
from aurora.app.theme import FOOTPRINTS, MAP_OVERLAY, curve, on_theme_changed, swatch_icon
from aurora.app.vocab import channel_words, det_items, plural, unit_suffix
from aurora.app.widgets import (Card, ElidedLabel, EmptyState, FlowLayout, default_dir, default_path, guarded_write,
                                notify_saved, overlay_host, pair, remember_dir, set_enabled)
from aurora.app.zone_preview import ZonePreviewCard
from aurora.app.zone_tools import (FEEDBACK_MS, PreviewThrottle, ZoneEditor, ZoneToolBar, clear_zone, register_shortcuts,
                                   zone_drawn, zone_menu, zone_name)
from aurora.core import sketches
from aurora.core.model import BEFORE_SURVEY, ScanInfo
from aurora.core.zones import zone_bbox

SHEET_ANCHOR = "24-the-sample-sheet"             # MANUAL.md: what a sample sheet holds and where it goes

FOOTPRINT_LABELS = {"complete": "complete", "aborted": "stopped early (aborted)", "in_progress": "in progress",
                    "test": "beamline test"}
NO_SURVEY_MAP = ("This holder has no survey map: no survey was recorded before these stacks. Reduce works on the "
                 "stacks directly.")
NO_SHEET = "No sample sheet: names are typed by hand."


def holder_title(h) -> str:
    """The holder as Survey names it: its name, or 'stacks before the first survey' for the stacks recorded before
    any survey (the holder 'unknown')."""
    if h is None:
        return ""
    return BEFORE_SURVEY if h.display == BEFORE_SURVEY else f"holder {h.name}"


class ColumnScroll(QScrollArea):
    """The Survey's right column: it scrolls up and down, never sideways, so it is as wide as its widest card needs
    (and at least `min_width`): a card wider than the column (a sketch image, a long header) widens the column instead
    of being cut off at the right edge."""

    def __init__(self, min_width: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._min_width = int(min_width)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def setWidget(self, w: QWidget) -> None:
        super().setWidget(w)
        w.installEventFilter(self)

    def eventFilter(self, obj, ev) -> bool:
        from PySide6.QtCore import QEvent
        if obj is self.widget() and ev.type() == QEvent.LayoutRequest:
            self.updateGeometry()                       # a scroll area does not pass its content's new size up
        return False

    def minimumSizeHint(self):
        s = super().minimumSizeHint()
        w = self.widget()
        need = self._min_width
        if w is not None:
            need = max(need, w.minimumSizeHint().width() + self.verticalScrollBar().sizeHint().width()
                       + 2 * self.frameWidth())
        s.setWidth(need)
        return s


class FootprintLegend(QWidget):
    """The small legend of the scan footprints: colour and dash per status (never colour alone)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = FlowLayout(self, spacing=6)
        cap = QLabel("measured areas (stacks):"); cap.setObjectName("muted")
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


class SketchLabel(QLabel):
    """The holder's sketch, scaled to the card (aspect kept) whenever the card is resized; it never widens the column."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._image = QPixmap()
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

    def set_image(self, pm: QPixmap) -> None:
        self._image = QPixmap(pm)
        self._fit()

    def image(self) -> QPixmap:
        return self._image

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._fit()

    def _fit(self) -> None:
        if self._image.isNull() or self.width() < 2 or self.height() < 2:
            self.setPixmap(QPixmap())
            return
        self.setPixmap(self._image.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))


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
        self.subtitle = sub = ElidedLabel(say("survey.subtitle"), Qt.ElideRight); sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub, 1)
        self.holder_combo = QComboBox()
        self.holder_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.holder_combo.setMinimumContentsLength(18)
        self.holder_combo.currentIndexChanged.connect(self._holder_picked)
        head.addWidget(labelled("Holder", self.holder_combo, "survey.holder"))
        head.addWidget(self.holder_combo)
        self.sketch_btn = QPushButton("Load a sketch…")        # optional: a drawing or photo of the holder
        self.sketch_btn.setFlat(True)
        self.sketch_btn.clicked.connect(lambda: self.load_sketch())
        head.addWidget(self.sketch_btn)
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
        self.toolbar = ZoneToolBar(session, default="navigate")     # Move first: opening the page changes nothing
        self.toolbar.tool_changed.connect(self.set_tool)
        self.toolbar.clear_btn.clicked.connect(self.clear_zone)
        self.clear_btn = self.toolbar.clear_btn
        left.body.addWidget(self.toolbar)
        self.map = MapView()
        self.map.setAccessibleName("plate map of the holder")
        self.map.set_zone_tools(True)                   # zone-aware clicks, right-drag erases, right-click menus
        self.map.hovered.connect(self._hover)
        self.map.clicked.connect(self._clicked)
        self.map.double_clicked.connect(self._double_clicked)
        self.map.zone_clicked.connect(lambda zid: self.session.select_zone(zid))
        self.map.context_requested.connect(self._context)
        self.map.rect_drawn.connect(self._rect_drawn)
        self.last_menu = None
        # every drag or click with a drawing tool is a stroke: one edit, one undo step (zone_tools.ZoneEditor)
        self.editor = ZoneEditor(session, self.map, self._map_points, "Survey", self)
        self.editor.feedback_ms = FEEDBACK_MS           # thousands of survey points: the feedback every 50 ms at most
        self.editor.enabled = lambda: not self.grid_btn.isChecked()
        self.editor.new_each = lambda: self.toolbar.new_each.isChecked() and is_advanced()
        self.editor.message.connect(lambda t: self.hint.setText(t))
        self.throttle = PreviewThrottle(self.preview_draft, self)
        self.editor.throttle = self.throttle
        self.throttle.state_changed.connect(self.editor.refresh_hint)
        self.editor.draft_changed.connect(self._draft_changed)
        self._drafted = False
        self.toolbar.brush_changed.connect(self.editor.set_brush)
        self.editor.set_tool(self.toolbar.tool(), self.toolbar.brush_n())
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
        self.zone_table.verticalHeader().hide()         # the # column numbers the zones: no second row number
        hh = self.zone_table.horizontalHeader()
        for c in (0, 2, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)  # the label takes the width: no sideways scroll bar
        self.zone_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.zone_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.zone_table.itemSelectionChanged.connect(self._zone_row_selected)
        self.zone_table.verticalHeader().setVisible(False)          # the '#' column numbers the zones already
        self.zone_table.horizontalHeader().setStretchLastSection(True)
        self.zone_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._fit_zone_table()                          # its header and two zones, at every Text size
        on_theme_changed(self.zone_table, lambda _s: self._fit_zone_table())
        zc.body.addWidget(self.zone_table, 1)
        right.addWidget(zc)
        self.zone_preview = ZonePreviewCard(session)    # the zone's XRF emission and its XAS on a covered stack
        self.zone_preview.open_requested.connect(self._open_zone_scan)
        right.addWidget(self.zone_preview)
        # the sketch is optional: the card exists only for a holder that has one (_load_sketch shows or hides it)
        self.sketch_card = sc = Card("Sketch", help_anchor="3-survey")
        self.sketch_path: Path | None = None
        self.remove_sketch_btn = QPushButton("Remove the sketch")
        self.remove_sketch_btn.setFlat(True)
        self.remove_sketch_btn.clicked.connect(lambda: self.remove_sketch())
        sc.header.addWidget(self.remove_sketch_btn)
        self.sketch = SketchLabel()
        self.sketch.setAccessibleName("sketch of the holder")
        self.sketch.setMinimumHeight(120)
        sc.body.addWidget(self.sketch, 1)
        sc.setVisible(False)
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
        self.sheet_note.setTextFormat(Qt.RichText)
        self.sheet_note.linkActivated.connect(self._sheet_link)
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
        right.setCollapsible(1, False)
        for i, f in enumerate((1, 4, 1, 1)):            # the Zone preview takes the height
            right.setStretchFactor(i, f)
        right.setSizes([130, 440, 150, 150])
        self.right = self.right_split = right
        # at the smallest window the column scrolls rather than squeezing the preview's plots below their minimum
        self.right_scroll = ColumnScroll(400)           # the preview's plots stay readable at 1200 px
        self.right_scroll.setWidget(right)
        split.addWidget(self.right_scroll)
        split.setStretchFactor(0, 3); split.setStretchFactor(1, 2)
        self.body = overlay_host(split)
        root.addWidget(self.body, 1)

        for w in (self.channel, self.sdd):
            w.currentIndexChanged.connect(self.redraw_map)
        for w in (self.per_i0, self.log, self.invert, self.show_footprints, self.show_zones):
            w.toggled.connect(self.redraw_map)
        self.clip.valueChanged.connect(self.redraw_map)

        self.no_map_state = EmptyState(NO_SURVEY_MAP, [], icon="survey", parent=self.map)
        self.zones_state = EmptyState(say("survey.zones_empty"), [], icon="survey", parent=self.zone_table)
        self.empty_state = EmptyState("No project open: the plate map of each holder appears here.",
                                      [("Open project…", lambda: nav.open_project_dialog(self)),
                                       ("Open the demo beamtime", lambda: nav.open_demo(self))], icon="survey",
                                      parent=self.body)
        explain_all([(self.per_i0, "survey.per_i0"), (self.log, "survey.log"), (self.invert, "survey.invert"),
                     (self.show_footprints, "survey.footprints"), (self.show_zones, "survey.zones"),
                     (self.import_btn, "survey.import"), (self.export_btn, "survey.export")])
        explain(self.grid_btn, "survey.grid")
        explain_all([(self.new_sheet_btn, "survey.new_sheet"), (self.choose_sheet_btn, "survey.choose_sheet")])
        explain(self.sketch_btn, "survey.sketch_load")
        explain(self.remove_sketch_btn, "survey.sketch_remove")
        session.project_changed.connect(self._project_changed)
        session.holder_changed.connect(self._holder_changed)
        session.zones_changed.connect(self._zones_changed)
        session.zone_selected.connect(lambda z: self._highlight_zone())
        session.sheet_changed.connect(self._fill_sheet)
        self.channel.currentIndexChanged.connect(lambda _i: (self.zone_preview.set_channel(self.channel.currentText()),
                                                             self._preview_when_shown()))
        self._map_dirty = False                         # a zone changed while Survey was hidden: redraw when shown
        self._preview_dirty = False
        self._shortcuts()
        on_theme_changed(self, lambda _s: self.redraw_map())
        # Easy mode: the plate map with fixed display settings, footprints, hover readout, samples, sketch
        mark_advanced(self.options_row, self.import_btn, self.export_btn, self.grid_btn)
        on_mode_changed(self, lambda _a: (self.subtitle.setText(say("survey.subtitle")), self.update_actions(),
                                          self.redraw_map(), self._preview_when_shown()), call_now=False)
        self.update_actions()

    def update_actions(self) -> None:
        has = self.session.project is not None
        for b in (self.import_btn, self.export_btn):
            set_enabled(b, has, "open a project first")
        set_enabled(self.grid_btn, has and self._survey is not None,
                    "open a project first" if not has else "this holder has no survey map")
        z = self.session.zone() if has else None
        set_enabled(self.clear_btn, zone_drawn(z), "open a project first" if not has else (
            "select a zone first" if z is None else "the zone is empty"))
        self.toolbar.setEnabled(has and self._survey is not None and not self.grid_btn.isChecked())
        if not self.grid_btn.isEnabled() and self.grid_btn.isChecked():
            self.grid_btn.setChecked(False)
        self.empty_state.set_shown(not has)
        h = self.session.holder()
        self.no_map_state.set_shown(has and h is not None and h.survey is None)
        if not has:
            self.sketch_path = None
            self.sketch_card.setVisible(False)
        self.sketch_btn.setVisible(has and h is not None)
        why = self._sketch_refusal()
        set_enabled(self.sketch_btn, has and h is not None and not why, why or "open a project first")
        own = has and h is not None and sketches.is_own(self.session.project.root, self.sketch_path)
        set_enabled(self.remove_sketch_btn, own and not why,
                    why or "this sketch is a file of the beamtime folder: AURORA removes only the copy it made")
        title = holder_title(h)
        if is_advanced():
            self.zones_card.set_title(f"Zones of {'the ' if title == BEFORE_SURVEY else ''}{title}" if h else
                                      "Zones of the holder")
        else:
            self.zones_card.set_title(say("survey.zones_title"))
        hz = self.session.holder_zones(create=False) if has else None
        self.zones_state.set_content(say("survey.zones_empty"))
        self.zones_state.set_shown(has and h is not None and not (hz and hz.zones))
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
        error = ""
        try:
            self._survey = self.session.survey()
        except Exception as err:
            error = f"survey could not be read: {err}"
            self.hint.setText(error)
        sv = self._survey
        self.map.set_points(sv.x, sv.y, sv.pitch) if sv is not None else self.map.set_points(None, None, None)
        self.channel.blockSignals(True)
        self.channel.clear()
        if self._survey is not None:
            self.channel.addItems(self._survey.channels)
            main = self._main_line_guess()
            if main in self._survey.channels:
                self.channel.setCurrentText(main)
        self.channel.blockSignals(False)
        self.zone_preview.set_survey(self._survey, self.channel.currentText(), error)
        self.editor.idle()
        self.redraw_map(); self.fit_view()
        self._fill_zone_table(); self._fill_sheet(); self._load_sketch()
        self._preview_when_shown()

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
        self._map_dirty = False
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
        notes = list(sv.notes)
        outside = self._outside_text()
        if outside:
            notes.append(outside)
        note = "; ".join(notes)
        what = (channel_words(self.channel.currentText()) + f" at {sv.energy:.1f} eV"
                + (", divided by the beam intensity" if self.per_i0.isChecked() else "")
                + (", log scale" if self.log.isChecked() else ""))
        bright = "darker = more signal" if self.invert.isChecked() else "brighter = more signal"
        if is_advanced():
            text = (f"Colour: {what} · {plural(sv.n, 'point')}, pitch {sv.pitch[0]:.2f} x {sv.pitch[1]:.2f} mm")
        else:
            text = f"Colour: {what} ({bright}) · {plural(sv.n, 'pixel')}, {sv.pitch[0]:.2f} mm apart"
        self.hint.setText(text + (f" · {note}" if note else "") + (f" · {self._grid_note}" if self._grid_note else ""))

    # ------------------------------------------------------------------ the first view (PM-02)
    def _survey_rect(self):
        """(x0, x1, y0, y1) mm of the survey map's pixels (centres +- half a pitch), or None."""
        sv = self._survey
        if sv is None or not len(sv.x):
            return None
        px, py = sv.pitch
        return (float(np.min(sv.x)) - px / 2, float(np.max(sv.x)) + px / 2,
                float(np.min(sv.y)) - py / 2, float(np.max(sv.y)) + py / 2)

    def _overlay_boxes(self) -> list[tuple[str, tuple[float, float, float, float]]]:
        """('footprint' | 'zone', box) of every footprint and zone of the holder."""
        out = [("footprint", b) for b in (self._bbox(s) for s in self._footprints()) if b is not None]
        hz = self.session.holder_zones(create=False)
        for z in (hz.zones if hz else []):
            b = zone_bbox(z)
            if b is not None:
                out.append(("zone", b))
        return out

    def outside_counts(self) -> tuple[int, int]:
        """(footprints, zones) of the holder that are not entirely on the survey map."""
        r = self._survey_rect()
        if r is None:
            return 0, 0
        tol = 1e-6
        out = [k for k, (x0, x1, y0, y1) in self._overlay_boxes()
               if x0 < r[0] - tol or x1 > r[1] + tol or y0 < r[2] - tol or y1 > r[3] + tol]
        return out.count("footprint"), out.count("zone")

    def _outside_text(self) -> str:
        nf, nz = self.outside_counts()
        parts = ([plural(nf, "measured area")] if nf else []) + ([plural(nz, "zone")] if nz else [])
        if not parts:
            return ""
        verb = "lies" if nf + nz == 1 else "lie"
        return f"{' and '.join(parts)} {verb} outside the survey map"

    def fit_view(self) -> None:
        """Show the survey map and every footprint and zone of the holder (the map alone when they all lie on it)."""
        r = self._survey_rect()
        boxes = [b for _k, b in self._overlay_boxes()]
        if r is None and not boxes:
            self.map.autorange()
            return
        allb = ([r] if r is not None else []) + boxes
        x0, x1 = min(b[0] for b in allb), max(b[1] for b in allb)
        y0, y1 = min(b[2] for b in allb), max(b[3] for b in allb)
        if r is not None and (x0, x1, y0, y1) == r:
            self.map.autorange()
            return
        self.map.zoom_to(x0, x1, y0, y1, margin=0.04)

    def view_rect(self) -> tuple[float, float, float, float]:
        """The map's visible range (x0, x1, y0, y1) in mm."""
        (x0, x1), (y0, y1) = self.map.vb.viewRange()
        return float(x0), float(x1), float(y0), float(y1)

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

    def _redraw_when_shown(self) -> None:
        """Redraw the plate map now when the page is shown, else when it is shown next: a zone edit in Reduce (every
        stroke is one) must not redraw a hidden survey of thousands of points."""
        if self.isVisible():
            self.redraw_map()
        else:
            self._map_dirty = True

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if self._map_dirty:
            self.redraw_map()
        if self._preview_dirty:
            self._preview_dirty = False
            self.zone_preview.refresh()

    def _preview_when_shown(self) -> None:
        """The Zone preview of the selected zone, now when the page is shown, else when it is shown next."""
        if self.isVisible():
            self._preview_dirty = False
            self.zone_preview.refresh()
        else:
            self._preview_dirty = True

    def _highlight_zone(self) -> None:
        self._redraw_when_shown()
        self._preview_when_shown()
        self.update_actions()
        self._select_row()

    def _select_row(self) -> None:
        """The selected zone's row in the zones table (none selected: no row)."""
        zid = self.session.current_zone
        self.zone_table.blockSignals(True)
        self.zone_table.clearSelection()
        for r in range(self.zone_table.rowCount()):
            if self.zone_table.item(r, 0).data(Qt.UserRole) == zid:
                self.zone_table.selectRow(r)
        self.zone_table.blockSignals(False)

    def _zones_changed(self) -> None:
        self._fill_zone_table(); self._redraw_when_shown(); self._preview_when_shown()
        self.update_actions()
        self._select_row()                              # a stroke selects its new zone before the row exists

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

    def _fit_zone_table(self) -> None:
        """The zones table never shows less than its header and two rows (the column scrolls below the preview)."""
        t = self.zone_table
        t.setMinimumHeight(t.horizontalHeader().sizeHint().height() + 2 * t.verticalHeader().defaultSectionSize()
                           + 2 * t.frameWidth())

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
        """No sheet: one line with a link to the manual's sample-sheet section, and New / Choose sample sheet… (never a
        path); a sheet without a row for this holder: why, with the holders it names; otherwise the table."""
        ses = self.session
        has_sheet = ses.project is not None and bool(ses.sheet_rows)
        note = ""
        if ses.project is not None and h is not None and not has_sheet:
            note = f'{NO_SHEET} <a href="help">How to add one</a>'
        elif has_sheet and h is not None and not rows:
            theirs = ses.sheet_choice.holders
            shown = ", ".join(theirs[:5]) + (" …" if len(theirs) > 5 else "")
            where = BEFORE_SURVEY if holder_title(h) == BEFORE_SURVEY else f"holder {h.name}"
            note = (f"The sample sheet lists no sample on {where} (its holders: {shown})." if theirs else
                    f"The sample sheet lists no sample on {where}.")
        self.sheet_note.setText(note)
        self.sheet_note.setVisible(bool(note))
        self.sheet_actions.setVisible(note.startswith(NO_SHEET))
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

    def _sheet_link(self, _href: str) -> None:
        from aurora.app.help import open_help
        open_help(self, "MANUAL.md", SHEET_ANCHOR)

    def _load_sketch(self) -> None:
        """Show the holder's sketch in its card, or hide the card: a holder without one shows no sketch at all."""
        h = self.session.holder()
        root = self.session.project.root if self.session.project is not None else None
        p = sketches.find_sketch(root, h.name) if h is not None and root is not None else None
        pm = QPixmap(str(p)) if p is not None else QPixmap()
        if p is not None and pm.isNull():
            self.hint.setText(f"the sketch {p.name} could not be read as an image")
            p = None
        self.sketch_path = p
        if p is None:
            self.sketch.set_image(QPixmap())
            self.sketch.setToolTip("")
            self.sketch_card.setVisible(False)
        else:
            self.sketch.set_image(pm)
            try:
                shown = p.relative_to(root).as_posix()
            except ValueError:
                shown = str(p)
            self.sketch.setToolTip(f"{shown} ({pm.width()} × {pm.height()} px)")
            self.sketch_card.setVisible(True)
        self.update_actions()

    def _sketch_refusal(self) -> str:
        """Why no sketch can be added or removed here ('' when it can): a protected (read-only) beamtime."""
        from aurora.core.io.safety import is_protected
        proj = self.session.project
        if proj is None:
            return ""
        return ("this beamtime folder is protected (read-only): AURORA keeps no copy in it"
                if is_protected(proj.root / sketches.OWN_DIR[0] / sketches.OWN_DIR[1] / "x.png") else "")

    def load_sketch(self, path: str | Path | None = None) -> Path | None:
        """Load a sketch…: copy an image (PNG or JPEG) of the holder to .aurora/sketches/<holder>.<ext> and show it.
        `path` None asks for the file. Returns the copy, or None when nothing was loaded."""
        h = self.session.holder()
        if self.session.project is None or h is None:
            return None
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, f"Load a sketch of holder {h.name}",
                                                  default_dir("sketch", self.session), sketches.IMAGE_FILTER)
            if not path:
                return None
            remember_dir("sketch", path)
        path = Path(path)
        if QPixmap(str(path)).isNull():
            QMessageBox.warning(self, "Load a sketch", f"{path.name} could not be read as an image (PNG or JPEG).")
            return None
        out = None
        with guarded_write(self, f"the sketch of holder {h.name}") as g:
            out = sketches.add_sketch(self.session.project.root, h.name, path)
        if not g.ok:
            return None
        self._load_sketch()
        self.session.status.emit(f"sketch of holder {h.name}: {path.name} copied to "
                                 f"{out.relative_to(self.session.project.root).as_posix()}")
        return out

    def remove_sketch(self) -> list[Path]:
        """Remove the sketch: delete AURORA's copy of the holder's sketch (never a file of the user's own folders)."""
        h = self.session.holder()
        if self.session.project is None or h is None:
            return []
        gone: list[Path] = []
        with guarded_write(self, f"the sketch of holder {h.name}") as g:
            gone = sketches.remove_sketch(self.session.project.root, h.name)
        if g.ok and gone:
            self.session.status.emit(f"sketch of holder {h.name} removed (AURORA's copy; the original image is kept)")
        self._load_sketch()
        return gone

    def sketch_actions(self, menu: QMenu) -> None:
        """Add 'Load a sketch…' and 'Remove the sketch' to a menu of the Survey map."""
        h = self.session.holder()
        why = self._sketch_refusal()
        a = menu.addAction("Load a sketch…", lambda: self.load_sketch())
        a.setEnabled(self.session.project is not None and h is not None and not why)
        r = menu.addAction("Remove the sketch", lambda: self.remove_sketch())
        r.setEnabled(self.remove_sketch_btn.isEnabled())

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
        zid = self.map.zone_at(x, y)
        z = self.session.zone(zid) if zid is not None else None
        if z is not None:
            txt += f" · {zone_name(z)}"
        self.hint.setText(txt)

    def _footprint_at(self, x: float, y: float) -> ScanInfo | None:
        """The complete stack whose footprint holds (x, y), None when none."""
        for s in self._footprints():
            b = self._bbox(s)
            if b and b[0] <= x <= b[1] and b[2] <= y <= b[3] and s.status == "complete":
                return s
        return None

    def _clicked(self, x: float, y: float) -> None:
        """A click in Move outside the other zones: on a complete footprint outside every zone, open it in Reduce."""
        if self.map.zone_at(x, y) is not None:
            return                                      # inside a zone a click selects it (never opens a stack)
        s = self._footprint_at(x, y)
        if s is not None:
            self.open_requested.emit(s)
            self.redraw_map()

    def _double_clicked(self, x: float, y: float) -> None:
        """A double-click in Move inside a zone over a complete footprint: that stack in Reduce, the zone selected."""
        zid = self.map.zone_at(x, y)
        s = self._footprint_at(x, y)
        if zid is not None and s is not None:
            self._open_zone_scan(s, zid)

    def _open_zone_scan(self, scan, zid: int) -> None:
        self.session.select_zone(zid)
        self.open_requested.emit(scan)

    # ------------------------------------------------------------------ zones on the plate map
    def _map_points(self):
        """(x, y, pitch, name) of the survey points, for the zone editor; None without a survey."""
        sv = self._survey
        return (sv.x, sv.y, sv.pitch, sv.name) if sv is not None and sv.n else None

    def set_tool(self, tool: str) -> None:
        """The map's tool ('rect+', 'brush-', 'navigate', ...) and the tool bar (shortcuts, the bar, tests)."""
        from aurora.app.mapview import TOOLS
        if tool not in TOOLS:
            return
        if self.grid_btn.isChecked():
            self.toolbar.set_tool(tool)                 # remembered for after the grid
            return
        self.editor.set_tool(tool, self.toolbar.brush_n())
        self.toolbar.set_tool(tool)

    def preview_draft(self, draft, fast: bool = False) -> None:
        """The Zone preview of a stroke in progress (the throttle calls it)."""
        if draft is not None:
            self._drafted = True
            self.zone_preview.show_draft(draft, fast)

    def _draft_changed(self, draft) -> None:
        if draft is not None:
            self.throttle.request(draft)
            return
        self.throttle.cancel()
        if self._drafted:
            self._drafted = False
            if not self.editor.last_committed:          # nothing written: the zone as it is
                self.zone_preview.refresh()

    def clear_zone(self) -> None:
        zid = clear_zone(self.session)
        if zid is not None:
            self.hint.setText(f"zone {zid} cleared: draw its region again")

    def step_zone(self, d: int) -> None:
        hz = self.session.holder_zones(create=False)
        ids = [z.id for z in (hz.zones if hz else [])]
        if ids:
            cur = self.session.current_zone
            i = ids.index(cur) if cur in ids else -1
            self.session.select_zone(ids[(i + d) % len(ids)] if i >= 0 else ids[0])

    def _new_zone(self) -> None:
        self.session.new_zone()

    def _delete_zone(self) -> None:
        if self.session.current_zone is not None:
            self.session.delete_zone(self.session.current_zone)

    def _shortcuts(self) -> None:
        """The zone keys of the Survey (Help > Keyboard shortcuts lists them under 'Survey'); the same as Reduce's."""
        add = lambda text, key, fn: shortcuts.add(self, text, key, fn, section="Survey")   # noqa: E731
        a = self.shortcut_actions = register_shortcuts(self, self.toolbar, "Survey")
        a.update({
            "new": add("New sample", "N", self._new_zone),
            "delete": add("Delete the sample", "Delete", self._delete_zone),
            "prev_zone": add("Previous sample", "Alt+Up", lambda: self.step_zone(-1)),
            "next_zone": add("Next sample", "Alt+Down", lambda: self.step_zone(1)),
            "fit": add("Fit the view", "F", self.fit_view),
        })
        a["delete"].setShortcuts(shortcuts.delete_keys())

    def zone_menu(self, zid: int):
        """The right-click menu of a zone on the plate map: the shared zone entries, then 'Open <scan> in Reduce' for
        every complete stack the zone covers."""
        from aurora.core.zones import members
        extra = []
        z = self.session.zone(zid)
        for s in (self.session.stacks_of() if z is not None else []):
            sid = self.session.stack_id(s)
            c = self.session.coordinates(sid)
            if c is not None and members(z, c[0], c[1], c[2]).size:
                extra.append((f"Open {s.time} {s.label} in Reduce".replace("  ", " "),
                              lambda _c=False, ss=s: self._open_zone_scan(ss, zid)))
        return zone_menu(self, self.session, zid, self.map, extra)

    def map_menu(self, x: float, y: float) -> QMenu:
        """The right-click menu of the plate map outside the zones: Fit view, Load a sketch…, Remove the sketch."""
        m = QMenu(self)
        m.addAction("Fit view", self.fit_view)
        m.addSeparator()
        self.sketch_actions(m)
        return m

    def _context(self, x: float, y: float, zid) -> None:
        from aurora.app.reduce import popup
        self.last_menu = popup(self.zone_menu(zid) if zid is not None else self.map_menu(x, y), QCursor.pos())

    # ------------------------------------------------------------------ grid of zones (Advanced)
    def _grid_armed(self, on: bool) -> None:
        self.toolbar.setEnabled(not on and self._survey is not None)
        if on:
            self.map.set_tool("rect+")                  # the zone editor is off (editor.enabled)
            self.editor.idle()
        else:
            self.editor.set_tool(self.toolbar.tool(), self.toolbar.brush_n())
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
