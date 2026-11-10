"""Reduce perspective: the zone tool. Stack map with the zone tool bar (Draw / Erase x Move / Rectangle / Brush / Lasso /
Polygon, zone_tools.ZoneToolBar, the Survey's too), the XRF windows of step 2, the zone card, per-scan settings with
copy and rules, preview. The preview follows a stroke while it is drawn (preview_draft, throttled by
zone_tools.PreviewThrottle) and keeps a usable height at the smallest window (the page scrolls rather than squeeze
it).

Easy mode shows John Bussey's four numbered steps: '1 ·' the scans of the holder, '2 ·' the XRF window, '3 ·' the
region (navigate / rectangle / lasso, Clear, New sample, Delete, the sample label), '4 ·' a read-only line of the
settings used and 'Add to Check'. Advanced mode shows every control. The same widgets serve both modes (containers are
marked with mode.mark_advanced / mark_easy), so a switch never changes a value.
"""
from __future__ import annotations

import json

import numpy as np
from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QCursor, QKeySequence
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from aurora.app import jobs, nav, shortcuts
from aurora.app.help_text import explain, explain_all, labelled
from aurora.app.mapview import TOOLS, MapView, scale_label, split_tool
from aurora.app.mode import is_advanced, mark_advanced, mark_advanced_action, mark_easy, on_mode_changed
from aurora.app.preview import PreviewPlot
from aurora.app.reduce_window import XrfWindowCard
from aurora.app.session import DEFAULT_LABEL, CheckItem, Session
from aurora.app.theme import curve, min_height, on_theme_changed, refill_tones, set_item_tone
from aurora.app.trace_cache import zone_traces_cached
from aurora.app.zone_tools import PreviewThrottle, ZoneEditor, ZoneToolBar, fast_settings, register_shortcuts, zone_name
from aurora.app.vocab import DET_LABELS, NORM_LABELS, det_items, plural, unit_suffix
from aurora.app.widgets import (Card, Chip, ElidedLabel, EmptyState, FlowLayout, TaskBar, fit_height, overlay_host,
                                page_scroll, pair, set_enabled)
from aurora.core import rules as rl
from aurora.core import xrf_windows as xw
from aurora.core.io.config import ScanSettings, Zone
from aurora.core.model import ScanInfo
from aurora.core.names import pretty_edge, short_time, split_stack_id
from aurora.core.spectra import NORM_MODES
from aurora.core.zones import Stroke, geometry, members, set_geometry, shape_text, zone_bbox

NO_PROJECT = "open a project first"
GEOMETRY_MARK = "aurora_zone_geometry"           # the clipboard JSON of 'Copy geometry'
# fields the 'advanced settings in use' chip compares: line and windows are shown in step 2, also in Easy; the de-spike
# rule and the IPFY form have no control in either mode (a session without them keeps the earlier de-spike rule, which
# the step-4 line names)
CHIP_FIELDS = tuple(f for f in ScanSettings.SETTINGS_FIELDS if f not in ("line", "roi", "despike_mode", "ipfy_mode"))
COPY_FIELDS_SETTING = "reduce/copy_fields"
PREVIEW_NO_SCAN = "Pick a scan in step 1"
PREVIEW_NO_ZONE = "Draw a zone on the map (drag) to see its spectrum here"


def popup(menu: QMenu, pos) -> QMenu:
    """Show a menu without blocking (deleted when it closes). PySide6 6.12 ignores a monkeypatched QMenu.exec, so a
    blocking menu would also hang the click-everything test."""
    menu.setAttribute(Qt.WA_DeleteOnClose)
    menu.popup(pos)
    return menu


class _UndoKeysPassThrough(QObject):
    """Ctrl+Z / Ctrl+Y in a zone text box with nothing of its own to undo or redo reach Edit > Undo / Redo. A QLineEdit
    takes those keys even with an empty history, so a label picked from the list and Ctrl+Z did nothing."""

    def eventFilter(self, obj, ev) -> bool:
        edit = obj.lineEdit() if isinstance(obj, QComboBox) else obj      # a combo hands the keys to its line edit
        if ev.type() == QEvent.ShortcutOverride and isinstance(edit, QLineEdit):
            undo = ev.matches(QKeySequence.Undo) and not edit.isUndoAvailable()
            redo = ev.matches(QKeySequence.Redo) and not edit.isRedoAvailable()
            if undo or redo:
                ev.ignore()
                return True
        return False


class _VirtualScan:
    """Minimal ScanInfo stand-in for a stitched stack."""

    def __init__(self, sd):
        self.time, self.label, self.edge_text, self.summary, self.name = "stitch", sd.name, sd.edge_text, {}, sd.name
        self.edge = sd.edge
        self.day = sd.day


def _field_words(attr: str, value) -> str:
    """'fluorescence normalisation: / I0, maximum = 1', 'SDD3 off', 'de-spike: off' for the chip's tooltip."""
    key = rl.ATTRS.get(attr, attr)
    if key == "sdd":
        return rl.describe_value("sdd", value)
    return f"{rl.FIELD_LABELS.get(key, attr)}: {rl.describe_value(key, value)}"


def easy_differences(st: ScanSettings, easy: ScanSettings) -> list[str]:
    """The fields (ScanSettings attribute names) where st differs from the Easy defaults, in CHIP_FIELDS order."""
    return st.differs_from(easy, fields=CHIP_FIELDS)


def _sdd_words(st: ScanSettings, easy: ScanSettings) -> str:
    """'SDD3 off', 'SDD1 on (flagged saturated)': the detectors that differ from the Easy defaults (which leave out
    only the SDDs flagged saturated)."""
    parts = [f"SDD{i + 1} off" if not on else f"SDD{i + 1} on (flagged saturated)"
             for i, (on, dflt) in enumerate(zip(st.sdd, easy.sdd)) if bool(on) != bool(dflt)]
    return ", ".join(parts) or rl.describe_value("sdd", st.sdd)


def differences_text(st: ScanSettings, diffs: list[str], easy: ScanSettings | None = None) -> str:
    parts = []
    for f in diffs:
        if f == "sdd":
            parts.append(_sdd_words(st, easy) if easy is not None else rl.describe_value("sdd", st.sdd))
        else:
            parts.append(_field_words(f, getattr(st, f)))
    return "; ".join(parts)


class EnergySpin(QSpinBox):
    """The index of a stack energy, shown as the energy itself ('686.00 eV')."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._E: list[float] = []
        self.setSuffix(unit_suffix("eV"))
        self.setRange(0, 0)

    def set_energies(self, E) -> None:
        self._E = [float(e) for e in E]
        self.setRange(0, max(len(self._E) - 1, 0))
        self.lineEdit().setText(self.textFromValue(self.value()) + self.suffix())

    def textFromValue(self, v: int) -> str:
        return f"{self._E[v]:.2f}" if 0 <= v < len(self._E) else "-"

    def valueFromText(self, text: str) -> int:
        try:
            e = float(text.replace(self.suffix().strip(), "").strip())
        except ValueError:
            return self.value()
        return int(np.argmin(np.abs(np.asarray(self._E) - e))) if self._E else 0

    def validate(self, text, pos):
        from PySide6.QtGui import QValidator
        return QValidator.Acceptable, text, pos


class ScanSettingsCard(Card):
    """Per-zone, per-scan settings (the zone tool's scan card), in a two-column grid that fits 420 px. A manual edit
    pins the fields it changes (rules leave them alone); 'Copy to…' and the right-click menu copy the card."""

    SIGNALS = (("pfy", "PFY"), ("ipfy", "IPFY"))
    copy_requested = Signal(object, str)              # card, target ('zone' | 'scan' | 'edge')

    def __init__(self, session: Session, sid: str, scan: ScanInfo, settings: ScanSettings, lines: list[str], parent=None,
                 zone_id: int | None = None):
        super().__init__(f"{scan.time}  {scan.label}  ·  {scan.edge_text}", parent, help_anchor="45-one-card-per-attached-scan")
        self.session, self.sid, self.scan, self.s = session, sid, scan, settings
        self.zone_id = zone_id
        self.owner = None                             # the ReducePerspective (copy, paste, the field checklist)
        self.setObjectName("card")
        flags = []
        if scan.summary.get("sdd_saturated"):
            flags.append("saturated " + ", ".join(scan.summary["sdd_saturated"]).upper())
        if scan.summary.get("i0_bad"):
            flags.append("I0 invalid")
        fl = ElidedLabel(" · ".join(flags) if flags else plural(settings.n, "px"), Qt.ElideRight)
        fl.setProperty("tone", "warn" if flags else "muted")
        self.header.addWidget(fl)
        self.chip = Chip("advanced settings in use", "warn")          # Easy only, when the card differs (I22)
        self.chip.hide()
        self.header.addWidget(self.chip)
        dets = QWidget()
        dl = FlowLayout(dets, spacing=8)
        self.inc = QCheckBox("include"); self.inc.setChecked(settings.include); dl.addWidget(self.inc)
        self.sdd = []
        for i, d in enumerate(DET_LABELS):
            c = QCheckBox(d); c.setChecked(settings.sdd[i]); dl.addWidget(c); self.sdd.append(c)
        self.body.addWidget(dets)
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        self.line = QComboBox(); self.line.addItems(lines)
        if settings.line and settings.line not in lines:       # a window line the stack has no fixed bins for
            self.line.addItem(settings.line)
        self.line.setCurrentText(settings.line if settings.line else (lines[0] if lines else ""))
        self.signal = QComboBox()
        for key, text in self.SIGNALS:
            self.signal.addItem(text, key)
        if self.signal.findData(settings.signal) < 0:          # a value this version does not offer is kept as is
            self.signal.addItem(settings.signal, settings.signal)
        self.signal.setCurrentIndex(self.signal.findData(settings.signal))
        self.fn = QComboBox()
        self.tn = QComboBox()
        for box, value in ((self.fn, settings.fluo_norm), (self.tn, settings.tey_norm)):
            for m in NORM_MODES:
                box.addItem(NORM_LABELS[m], m)
            box.setCurrentIndex(NORM_MODES.index(value) if value in NORM_MODES else 3)
        self.tey = QCheckBox("TEY"); self.tey.setChecked(settings.tey)
        self.ds = QCheckBox("de-spike"); self.ds.setChecked(settings.despike)
        self.k = QDoubleSpinBox(); self.k.setRange(1, 50); self.k.setValue(settings.k); self.k.setSingleStep(1)
        self.k.setSuffix(unit_suffix("MAD"))
        self.i0src = QComboBox(); self.i0src.addItem("own", "own"); self.i0src.addItem("good-I0 mean", "good")
        self.i0src.setCurrentIndex(1 if settings.i0_source == "good" else 0)
        self.pre = QLineEdit(self._rng(settings.pre)); self.pre.setPlaceholderText("auto")
        self.post = QLineEdit(self._rng(settings.post)); self.post.setPlaceholderText("auto")
        for box in (self.line, self.signal, self.i0src):
            box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            box.setMinimumContentsLength(5)
        for w in (self.k, self.pre, self.post):
            w.setMinimumWidth(76)
        # two columns of label + field; the normalisation combos span the card (their words need the width)
        rows = [((labelled("line", self.line, "reduce.line"), self.line), (labelled("signal", self.signal, "reduce.signal"), self.signal)),
                ((labelled("fluo", self.fn, "reduce.fluo"), self.fn), None),
                ((self.tey, self.tn), None),
                ((self.ds, self.k), (labelled("I0", self.i0src, "reduce.i0"), self.i0src)),
                ((labelled("pre (eV)", self.pre, "reduce.pre"), self.pre), (labelled("post (eV)", self.post, "reduce.post"), self.post))]
        for r, (a, b) in enumerate(rows):
            grid.addWidget(a[0], r, 0)
            grid.addWidget(a[1], r, 1, 1, 1 if b else 3)
            if b:
                grid.addWidget(b[0], r, 2)
                grid.addWidget(b[1], r, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.body.addLayout(grid)
        cr = QHBoxLayout()
        self.copy_btn = QPushButton("Copy to…")
        self.copy_btn.clicked.connect(self._copy_menu_at_button)
        cr.addWidget(self.copy_btn); cr.addStretch(1)
        self.body.addLayout(cr)
        explain_all([(self.inc, "reduce.include"), (self.tey, "reduce.tey"), (self.tn, "reduce.tey_norm"),
                     (self.ds, "reduce.despike"), (self.k, "reduce.k"), (self.copy_btn, "reduce.copy")]
                    + [(c, "reduce.sdd") for c in self.sdd])
        for c, d in zip(self.sdd, DET_LABELS):
            c.setAccessibleName(f"{d} in the sum")
        for w in (self.inc, self.tey, self.ds, *self.sdd):
            w.toggled.connect(self._changed)
        for w in (self.line, self.signal, self.fn, self.tn, self.i0src):
            w.currentIndexChanged.connect(self._changed)
        self.k.valueChanged.connect(self._changed)
        self.pre.editingFinished.connect(self._changed)
        self.post.editingFinished.connect(self._changed)

    @staticmethod
    def _rng(v) -> str:
        return f"{v[0]:g}-{v[1]:g}" if v else ""

    @staticmethod
    def _parse(text: str):
        t = text.strip().replace("to", "-").replace(":", "-")
        if not t:
            return None
        parts = [p for p in t.replace(",", " ").split("-") if p.strip()]
        try:
            a, b = float(parts[0]), float(parts[1])
        except (IndexError, ValueError):
            return None
        return [min(a, b), max(a, b)]

    def values(self) -> dict:
        """The card's widgets as ScanSettings field values."""
        return {"include": self.inc.isChecked(), "sdd": [c.isChecked() for c in self.sdd], "line": self.line.currentText(),
                "signal": self.signal.currentData(), "fluo_norm": self.fn.currentData(), "tey": self.tey.isChecked(),
                "tey_norm": self.tn.currentData(), "despike": self.ds.isChecked(), "k": float(self.k.value()),
                "i0_source": self.i0src.currentData(), "pre": self._parse(self.pre.text()), "post": self._parse(self.post.text())}

    def _changed(self, *_) -> None:
        new = self.values()
        changed = [k for k, v in new.items() if getattr(self.s, k) != v]
        if not changed:
            return                              # a focus-out with nothing changed (e.g. hidden by a mode switch) writes nothing
        zid = self.zone_id if self.zone_id is not None else self.session.current_zone
        what = short_time(self.sid) or self.scan.name
        with self.session.edit(f"change the settings of {what} in zone {zid}"):
            for k in changed:
                setattr(self.s, k, new[k])
            pins = [rl.ATTRS[k] for k in changed if k in rl.ATTRS]
            self.s.pinned = list(self.s.pinned) + [p for p in pins if p not in self.s.pinned]

    # ------------------------------------------------------------------ copy
    def copy_menu(self) -> QMenu:
        """The three copy targets, copy / paste, and the checklist of the fields that travel."""
        m = QMenu(self)
        for target, words in rl.COPY_TARGETS.items():
            a = m.addAction(f"Copy to {words}")
            a.setData(target)
            a.triggered.connect(lambda _c=False, t=target: self.copy_requested.emit(self, t))
        m.addSeparator()
        if self.owner is not None:
            m.addAction(shortcuts.native_text("Copy these settings (Ctrl+Shift+C)"), lambda: self.owner.copy_card(self))
            pa = m.addAction(shortcuts.native_text("Paste settings here (Ctrl+Shift+V)"), lambda: self.owner.paste_card(self))
            pa.setEnabled(self.owner.copied is not None)
            sub = m.addMenu("Fields that travel")
            on = set(self.owner.copy_fields())
            for group, words, _keys, _default in rl.COPY_GROUPS:
                a = sub.addAction(words)
                a.setCheckable(True)
                a.setChecked(group in on)
                a.toggled.connect(lambda checked, g=group: self.owner.set_copy_field(g, checked))
        return m

    def _copy_menu_at_button(self) -> None:
        self.last_menu = popup(self.copy_menu(), self.copy_btn.mapToGlobal(self.copy_btn.rect().bottomLeft()))

    def contextMenuEvent(self, ev) -> None:
        self.last_menu = popup(self.copy_menu(), ev.globalPos())
        ev.accept()


class ReducePerspective(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._cards: list[ScanSettingsCard] = []
        self._placeholder: QLabel | None = None
        self._choices: dict[tuple[str, str], dict] = {}        # (holder, edge) -> the step-2 window choice
        self._window_failed: set[str] = set()                   # window jobs that failed (not retried)
        self.copied: tuple[ScanSettings, list[str], str] | None = None      # Ctrl+Shift+C: settings, keys, from
        self._badges: list[QLabel] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        h1 = QLabel("Reduce"); h1.setObjectName("h1")
        sub = ElidedLabel("draw the zones on the stack map, choose per scan what goes into the report", Qt.ElideRight)
        sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub, 1)
        self.status_label = ElidedLabel("", Qt.ElideRight); self.status_label.setObjectName("muted")
        head.addWidget(self.status_label, 1)
        root.addLayout(head)
        self.taskbar = TaskBar(self)
        root.addWidget(self.taskbar)

        split = QSplitter(Qt.Horizontal)

        # ---- left: 1 · the scans of the holder, 2 · the XRF window
        left = QSplitter(Qt.Vertical)
        self.scans_card = sc = Card("Scans of the holder", help_anchor="41-scans-of-the-holder")
        self.load_all_btn = QPushButton("Load all")
        self.load_all_btn.clicked.connect(self.load_all)
        sc.header.addWidget(self.load_all_btn)
        self.scan_list = QListWidget()
        self.scan_list.setTextElideMode(Qt.ElideMiddle)
        self.scan_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)       # long names elide (full text in the tooltip)
        self.scan_list.currentItemChanged.connect(self._scan_picked)
        sc.body.addWidget(self.scan_list, 1)
        left.addWidget(sc)
        self.window_card = XrfWindowCard(session, self.taskbar)
        self.window_card.committed.connect(self._window_committed)
        self.window_card.remember_requested.connect(self.remember_windows)
        left.addWidget(fit_height(self.window_card))
        left.setStretchFactor(0, 1); left.setStretchFactor(1, 3)
        left.setSizes([170, 470])
        split.addWidget(left)

        # ---- centre: 3 · the region: tools, sample row, zone list | stack map, caption
        self.map_card = centre = Card("Stack map", help_anchor="42-stack-map")
        # the zone tools, the Survey's bar: [Draw | Erase] x Move / Rectangle / Brush / Lasso (+ Polygon in Advanced),
        # the brush size, Undo, Clear, and in Advanced 'every stroke starts a new zone'
        self.toolbar = ZoneToolBar(session, default="rect+")
        self.toolbar.tool_changed.connect(self.set_tool)
        self.brush_n = self.toolbar.brush_size
        self.target_new = self.toolbar.new_each
        self.clear_btn = self.toolbar.clear_btn
        self.clear_btn.clicked.connect(self.clear_zone)
        centre.body.addWidget(self.toolbar)
        zone_row = QWidget()
        zr = FlowLayout(zone_row, spacing=6)
        self.new_zone_btn = QPushButton("New sample"); self.new_zone_btn.clicked.connect(self._new_zone)
        self.del_zone_btn = QPushButton("Delete"); self.del_zone_btn.clicked.connect(self._delete_zone)
        self.z_label = QComboBox(); self.z_label.setEditable(True); self.z_label.setMinimumWidth(150)
        self.z_label.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.z_label.setMinimumContentsLength(12)
        zr.addWidget(self.new_zone_btn); zr.addWidget(self.del_zone_btn)
        zr.addWidget(pair(labelled("sample", self.z_label, "reduce.label"), self.z_label))
        centre.body.addWidget(zone_row)
        self.show_row = QWidget()                       # map line / SDD / mode / energy / I0 / log: Advanced
        show = FlowLayout(self.show_row, spacing=6)
        self.map_line = QComboBox()
        self.map_line.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.map_line.setMinimumContentsLength(7)
        show.addWidget(pair(labelled("show", self.map_line, "reduce.map_line"), self.map_line))
        self.map_sdd = QComboBox(); self.map_sdd.addItems(det_items()); show.addWidget(self.map_sdd)
        self.map_mode = QComboBox()
        for k, v in (("above", "integrated above edge"), ("all", "all energies"), ("jump", "edge jump"), ("at", "at energy")):
            self.map_mode.addItem(v, k)
        show.addWidget(self.map_mode)
        self.map_energy = EnergySpin(); show.addWidget(self.map_energy)
        self.map_i0 = QCheckBox("/ I0"); self.map_i0.setChecked(True); show.addWidget(self.map_i0)
        self.map_log = QCheckBox("log"); show.addWidget(self.map_log)
        centre.body.addWidget(self.show_row)
        self.zone_split = QSplitter(Qt.Horizontal)
        self.zone_list = QListWidget()
        self.zone_list.setTextElideMode(Qt.ElideRight)
        self.zone_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.zone_list.setAccessibleName("zones of the holder")
        self.zone_list.currentItemChanged.connect(self._zone_picked)
        self.zone_list.setMinimumWidth(110)
        self.zone_split.addWidget(self.zone_list)
        self.map = MapView()
        self.map.setAccessibleName("stack map")
        self.map.set_zone_tools(True)
        self.map.set_tool("rect+")
        # every drag or click on the map is a stroke, written by the zone editor in one edit (one undo step); the
        # legacy signals below edit the zone only when code emits them (scripts, the self-test), never twice
        self.editor = ZoneEditor(session, self.map, self._map_points, "Reduce", self)
        self.editor.new_each = lambda: self.target_new.isChecked() and is_advanced()
        self.editor.message.connect(self.status_label.setText)
        # the preview follows the stroke: at most every INTERVAL_MS, without de-spike on a slow scan, then at release
        self.throttle = PreviewThrottle(self.preview_draft, self)
        self.editor.throttle = self.throttle
        self.throttle.state_changed.connect(self.editor.refresh_hint)
        self.editor.draft_changed.connect(self._draft_changed)
        self._drafted = False                           # the preview shows a stroke in progress
        self._draft_st: dict = {}                       # (stroke serial, sid) -> the settings a new zone would get
        self.editor.set_brush(self.brush_n.value())
        self.brush_n.valueChanged.connect(self.editor.set_brush)
        self.map.rect_drawn.connect(self._rect)
        self.map.brush_at.connect(self._brush)
        self.map.polygon_drawn.connect(self._polygon)
        self.map.zone_clicked.connect(self._zone_clicked)
        self.map.context_requested.connect(self._context)
        self.map.hovered.connect(self._hover)
        self.zone_split.addWidget(self.map)
        min_height(self.map, 200)
        self.zone_split.setStretchFactor(0, 0); self.zone_split.setStretchFactor(1, 1)
        self.zone_split.setSizes([120, 480])
        centre.body.addWidget(self.zone_split, 1)
        self.region_caption = ElidedLabel("", Qt.ElideMiddle)
        centre.body.addWidget(self.region_caption)
        self.map_hint = ElidedLabel("", Qt.ElideMiddle); self.map_hint.setObjectName("muted")
        centre.body.addWidget(self.map_hint)
        split.addWidget(centre)

        # ---- right: 4 · reduce (the zone card), per-scan settings, preview
        self.right_split = right = QSplitter(Qt.Vertical)
        self.zone_card = Card("Zone", help_anchor="44-the-zone-card")
        self.step4_line = QLabel("")                    # Easy: the settings used, read-only
        self.step4_line.setWordWrap(True)
        self.step4_line.setObjectName("muted")
        self.step4_line.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.zone_card.body.addWidget(self.step4_line)
        self.chip_box = QWidget()                       # 'advanced settings in use' (Easy, when a field differs)
        cl = FlowLayout(self.chip_box, spacing=6)
        self.chip = Chip("advanced settings in use", "warn")
        self.reset_btn = QPushButton("Reset to Easy defaults")
        self.reset_btn.clicked.connect(self.reset_to_easy)
        cl.addWidget(self.chip); cl.addWidget(self.reset_btn)
        self.zone_card.body.addWidget(self.chip_box)
        self.group_box = QWidget()                      # group and comment fields: Advanced
        gl = QHBoxLayout(self.group_box); gl.setContentsMargins(0, 0, 0, 0)
        self.z_group = QComboBox(); self.z_group.setEditable(True); self.z_group.setMinimumWidth(140)
        gl.addWidget(labelled("group", self.z_group, "reduce.group")); gl.addWidget(self.z_group, 1)
        self.zone_card.body.addWidget(self.group_box)
        self.comment_box = QWidget()
        zr2 = QHBoxLayout(self.comment_box); zr2.setContentsMargins(0, 0, 0, 0)
        self.z_comment = QLineEdit()
        zr2.addWidget(labelled("comment", self.z_comment, "reduce.comment")); zr2.addWidget(self.z_comment, 1)
        self.zone_card.body.addWidget(self.comment_box)
        self.z_info = ElidedLabel("", Qt.ElideRight); self.z_info.setObjectName("muted")
        self.zone_card.body.addWidget(self.z_info)
        zb2 = QWidget()
        zl2 = FlowLayout(zb2, spacing=6)
        self.add_check_btn = QPushButton("Add to Check")
        self.add_check_btn.setObjectName("primary")
        self.add_check_btn.clicked.connect(lambda: self.add_to_check())
        self.send_btn = QPushButton("Send the zone's traces to Compare")
        self.send_btn.clicked.connect(self.send_to_compare)
        self.rules_btn = QPushButton("Rules…")
        self.rules_btn.clicked.connect(self.open_rules)
        self.propose_btn = QPushButton("Propose zones")         # no dialog: no ellipsis
        self.propose_btn.clicked.connect(lambda: self.propose_zones())
        self.stitch_btn = QPushButton("Stitch…"); self.stitch_btn.clicked.connect(self.stitch_tiles)
        self.load_poly_btn = QPushButton("Load polygon…"); self.load_poly_btn.clicked.connect(lambda: self.load_polygon())
        self.save_poly_btn = QPushButton("Save polygon…"); self.save_poly_btn.clicked.connect(lambda: self.save_polygon())
        for b in (self.add_check_btn, self.send_btn, self.rules_btn, self.propose_btn, self.stitch_btn,
                  self.load_poly_btn, self.save_poly_btn):
            zl2.addWidget(b)
        self.zone_card.body.addWidget(zb2)
        self.library = None
        self.z_label.lineEdit().editingFinished.connect(self._zone_edited)
        self.z_group.lineEdit().editingFinished.connect(self._zone_edited)
        for box in (self.z_label, self.z_group):            # an entry picked from the list renames at once
            box.textActivated.connect(lambda _t: self._zone_edited())
        self._undo_keys = _UndoKeysPassThrough(self)
        for edit in (self.z_label, self.z_label.lineEdit(), self.z_group, self.z_group.lineEdit(), self.z_comment):
            edit.installEventFilter(self._undo_keys)
        self.z_comment.editingFinished.connect(self._zone_edited)
        right.addWidget(fit_height(self.zone_card))
        self.cards_area = QScrollArea(); self.cards_area.setWidgetResizable(True)
        self.cards_area.setAccessibleName("settings of each scan of the zone")
        self.cards_box = QWidget(); self.cards_lay = QVBoxLayout(self.cards_box); self.cards_lay.setContentsMargins(0, 0, 0, 0)
        self.cards_lay.addStretch(1)
        self.cards_area.setWidget(self.cards_box)
        right.addWidget(self.cards_area)
        self.preview_card = pc = Card("Preview", help_anchor="47-preview")
        self.preview = PreviewPlot()
        self.preview.all_scans.setText("all scans of the sample")
        self.preview.all_scans.toggled.connect(self.update_preview)
        pc.body.addWidget(self.preview, 1)
        right.addWidget(pc)
        min_height(self.cards_area, 120)                # one scan card row and its line row at every Text size
        right.setCollapsible(2, False)                  # the preview keeps its minimum (preview.THREE_PANELS_PX)
        right.setStretchFactor(0, 0); right.setStretchFactor(1, 2); right.setStretchFactor(2, 3)
        right.setSizes([190, 230, 420])
        split.addWidget(right)
        # no fixed minimum width on the left column: its cards' own minimum (about 260 px, 310 px at Larger text)
        # keeps 'spectrum of' and its combo box whole; a fixed 250 let the splitter cut them at Larger text
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 3); split.setStretchFactor(2, 2)
        split.setSizes([280, 650, 530])                 # the scan cards need about 420 px; the map is square
        # at 1200 x 760 or a larger Text size the columns need more height than the window has: the page scrolls
        # vertically instead of squeezing the preview and the map below their minimum
        self.page_scroll = page_scroll(split)
        self.body = overlay_host(self.page_scroll)
        root.addWidget(self.body, 1)

        for w in (self.map_line, self.map_sdd, self.map_mode):
            w.currentIndexChanged.connect(self.redraw_map)
        self.map_energy.valueChanged.connect(self.redraw_map)
        self.map_i0.toggled.connect(self.redraw_map)
        self.map_log.toggled.connect(self.redraw_map)

        self.empty_state = EmptyState("No project open: the complete stacks of each holder appear here, ready for zones.",
                                      [("Open project…", lambda: nav.open_project_dialog(self)),
                                       ("Open the demo beamtime", lambda: nav.open_demo(self))], icon="reduce",
                                      parent=self.body)
        self.zone_state = EmptyState("No sample yet: choose Draw and drag over the stack.",
                                     [("Propose zones", lambda: self.propose_zones(), True)], icon="reduce", parent=pc)
        explain_all([(self.load_all_btn, "reduce.load_all"), (self.scan_list, "reduce.scans"),
                     (self.new_zone_btn, "reduce.new_zone"), (self.del_zone_btn, "reduce.delete_zone"),
                     (self.propose_btn, "reduce.propose"), (self.stitch_btn, "reduce.stitch"),
                     (self.map_sdd, "reduce.map_sdd"),
                     (self.map_mode, "reduce.map_mode"), (self.map_energy, "reduce.map_energy"),
                     (self.map_i0, "reduce.map_i0"), (self.map_log, "reduce.map_log"), (self.send_btn, "reduce.send"),
                     (self.clear_btn, "reduce.clear"), (self.load_poly_btn, "reduce.load_polygon"),
                     (self.save_poly_btn, "reduce.save_polygon"), (self.add_check_btn, "reduce.add_to_check"),
                     (self.reset_btn, "reduce.reset_easy"), (self.rules_btn, "reduce.rules"),
                     (self.zone_list, "reduce.zones")])
        self.chip.setAccessibleName("advanced settings in use")
        self._badge(self.scans_card, "1 ·"); self._badge(self.window_card, "2 ·")
        self._badge(self.map_card, "3 ·"); self._badge(self.zone_card, "4 ·")
        session.project_changed.connect(self._project_changed)
        session.holder_changed.connect(self._holder_changed)
        session.zones_changed.connect(self._zones_changed)
        session.zone_selected.connect(self._zone_selected)
        session.stack_selected.connect(self._stack_selected)
        session.flags_changed.connect(self._flags_changed)
        session.add_settings_default(self._settings_default)
        on_theme_changed(self, lambda _s: (self.redraw_map(), refill_tones(self.scan_list)))
        self._shortcuts()
        # Easy mode: John Bussey's four steps (scan, XRF window, region, reduce); everything else is Advanced
        mark_advanced(self.load_all_btn, self.show_row,
                      self.propose_btn, self.stitch_btn, self.save_poly_btn, self.group_box,
                      self.comment_box, self.z_info, self.send_btn, self.rules_btn, self.cards_area, self.map_hint)
        mark_easy(self.step4_line, self.add_check_btn, *self._badges)
        on_mode_changed(self, self._mode_changed)
        self.update_actions()

    # ------------------------------------------------------------------ steps and modes
    def _badge(self, card: Card, text: str) -> None:
        """The step number before a card's title, shown in Easy mode ('1 ·', '2 ·', ...)."""
        lab = QLabel(text)
        lab.setObjectName("cardtitle")
        card._top.insertWidget(0, lab)
        self._badges.append(lab)

    def fit_cards(self) -> None:
        """The splitters ignore wrapped rows: the zone card and the XRF window card get the height their rows need at
        their width, so no control is cut at the smallest window. widgets.fit_height keeps it so on every resize of a
        card and every change of its rows (a Text size change too); this call refits them now."""
        import shiboken6
        for card in (self.zone_card, self.window_card):
            if not shiboken6.isValid(self) or not shiboken6.isValid(card):    # a deferred call after the window died
                return
            fit_height(card)

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.fit_cards)

    def step_titles(self) -> list[str]:
        """The titles of the numbered step cards as shown ('1 · Scans of holder N5', ...), Easy mode."""
        out = []
        for lab, card in zip(self._badges, (self.scans_card, self.window_card, self.map_card, self.zone_card)):
            out.append(f"{lab.text()} {card.title.text()}" if not lab.isHidden() else card.title.text())
        return out

    def _mode_changed(self, adv: bool) -> None:
        self.map_card.set_title("Stack map" if adv else "Region")
        self.zone_card.set_title("Zone" if adv else "Reduce")
        h = max(self.right_split.height(), 600)          # Easy: step 4 is short, the preview takes the rest
        # the splitter keeps every pane at its minimum (the preview's plots: preview.THREE_PANELS_PX); the page scrolls
        self.right_split.setSizes([int(0.3 * h), int(0.27 * h), int(0.43 * h)] if adv else [170, 0, h - 170])
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.fit_cards)
        self._refresh_easy()

    def _shortcuts(self) -> None:
        """Reduce shortcuts (Help > Keyboard shortcuts lists them under 'Reduce')."""
        add = lambda text, key, fn: shortcuts.add(self, text, key, fn, section="Reduce")   # noqa: E731
        a = self.shortcut_actions = register_shortcuts(self, self.toolbar, "Reduce")     # V R B L P D E X Shift+B + -
        a.update({
            "new": add("New sample", "N", self._new_zone),
            "delete": add("Delete the sample", "Delete", self._delete_zone),
            "prev_zone": add("Previous sample", "Alt+Up", lambda: self.step_zone(-1)),
            "next_zone": add("Next sample", "Alt+Down", lambda: self.step_zone(1)),
            "prev_scan": add("Previous scan", "[", lambda: self.step_scan(-1)),
            "next_scan": add("Next scan", "]", lambda: self.step_scan(1)),
            "fit": add("Fit the view", "F", lambda: self.map.autorange()),
            "copy": add("Copy the scan settings", "Ctrl+Shift+C", lambda: self.copy_card(self.current_card())),
            "paste": add("Paste the scan settings", "Ctrl+Shift+V", lambda: self.paste_card(self.current_card())),
        })
        a["delete"].setShortcuts(shortcuts.delete_keys())          # Backspace too on macOS
        mark_advanced_action(self, a["copy"], a["paste"])

    def set_tool(self, tool: str) -> None:
        """Switch the map tool ('rect+', 'lasso-', 'navigate', ...) and the tool bar (shortcuts, the bar, tests)."""
        if tool not in TOOLS:
            return
        self.editor.set_tool(tool, self.brush_n.value())
        self.toolbar.set_tool(tool)

    def step_zone(self, d: int) -> None:
        n = self.zone_list.count()
        if n:
            r = self.zone_list.currentRow()
            self.zone_list.setCurrentRow((r + d) % n if r >= 0 else 0)

    def step_scan(self, d: int) -> None:
        n = self.scan_list.count()
        if n:
            r = self.scan_list.currentRow()
            self.scan_list.setCurrentRow((r + d) % n if r >= 0 else 0)

    # ------------------------------------------------------------------ actions and states
    def update_actions(self) -> None:
        ses = self.session
        has = ses.project is not None
        h = ses.holder()
        z = ses.zone() if has else None
        loaded = [s for s in ses.stacks_of() if ses.stack_loaded(ses.stack_id(s)) is not None] if has else []
        set_enabled(self.new_zone_btn, has and h is not None, NO_PROJECT if not has else "this project has no holder")
        set_enabled(self.del_zone_btn, z is not None, NO_PROJECT if not has else "select a zone first")
        set_enabled(self.clear_btn, z is not None and bool(z.rects or z.erects or z.paint or z.erase or z.polys or
                                                           z.epolys),
                    NO_PROJECT if not has else ("select a zone first" if z is None else "the zone is empty"))
        set_enabled(self.send_btn, z is not None and self.library is not None and bool(z.settings),
                    NO_PROJECT if not has else ("select a zone first" if z is None else "load a scan that covers the zone"))
        set_enabled(self.load_all_btn, has and bool(ses.stacks_of()), NO_PROJECT if not has else "the holder has no complete stack")
        set_enabled(self.propose_btn, has and h is not None, NO_PROJECT)
        set_enabled(self.stitch_btn, len(loaded) >= 2,
                    NO_PROJECT if not has else "load at least two stacks of the holder first (Load all)")
        set_enabled(self.load_poly_btn, has and h is not None, NO_PROJECT)
        one_poly = z is not None and len(z.polys) == 1 and not (z.rects or z.paint or z.erects or z.erase or z.epolys)
        set_enabled(self.save_poly_btn, one_poly,
                    NO_PROJECT if not has else ("select a zone first" if z is None else
                                                "John Bussey's file holds one polygon: the zone must be exactly one polygon"))
        set_enabled(self.rules_btn, has, NO_PROJECT)
        k, _m, _sd = self.region_count()
        set_enabled(self.add_check_btn, k > 0,
                    NO_PROJECT if not has else ("pick a scan in step 1" if _sd is None else
                                                "nothing selected: draw over the stack footprint"))
        self.scans_card.set_title(f"Scans of holder {h.name}" if h else "Scans of the holder")
        self.empty_state.set_shown(not has)
        hz = ses.holder_zones(create=False) if has else None
        self.zone_state.set_shown(has and h is not None and not (hz and hz.zones))
        self._caption()
        self._refresh_easy()

    def _new_zone(self) -> None:
        if self.session.new_zone() is None:
            self.status_label.setText(NO_PROJECT)

    def _project_changed(self, project) -> None:
        xw.register_project(project)
        self._choices.clear()
        self._window_failed.clear()
        self.update_actions()

    def current_stack(self):
        return self.session.stack_loaded(self.session.current_stack) if self.session.current_stack else None

    def _attach(self, sd) -> None:
        """Tie a loaded stack to its raw folders (the XRF windows read them)."""
        if sd is None or self.session.project is None or str(sd.id).startswith("stitched_"):
            return
        if getattr(sd, "_xrf_source", None) is None:
            scan = self.session.scan_of(sd.id)
            if scan is not None:
                xw.attach(sd, scan, self.session.project.root)

    # ------------------------------------------------------------------ holder / scans
    def _holder_changed(self, key: str) -> None:
        self.scan_list.blockSignals(True)
        self.scan_list.clear()
        for s in self.session.stacks_of(key):
            flags = ", ".join(s.flags)
            it = QListWidgetItem(f"{s.time}  {s.label}  ·  {s.edge_text}  {s.energy_text}" + (f"   [{flags}]" if flags else ""))
            it.setToolTip(it.text())
            it.setData(Qt.UserRole, self.session.stack_id(s))
            if flags:
                set_item_tone(it, "warn")
            self.scan_list.addItem(it)
        self.scan_list.blockSignals(False)
        self._fill_zone_list()
        self.map.clear_overlays(); self.map.set_grid(None)
        self._clear_cards(); self.preview.set_traces([])
        self.update_actions()
        if self.scan_list.count():
            self.scan_list.setCurrentRow(0)

    def _scan_picked(self, cur, _prev=None) -> None:
        if cur is None:
            return
        sid = cur.data(Qt.UserRole)
        self.load_stack(sid)

    def load_stack(self, sid: str) -> None:
        sd = self.session.stack_loaded(sid)
        if sd is not None:
            self.session.select_stack(sid)
            return
        session = self.session
        name = split_stack_id(sid)[1]
        jobs.of(self).submit(f"stack:{sid}", lambda cb: session.stack(sid, progress=cb),
                             on_done=lambda _sd, sid=sid: self._loaded(sid),
                             on_failed=lambda t: self.status_label.setText("could not read the stack: " + t.strip().splitlines()[-1]),
                             on_end=lambda _s: self.update_actions(), label=f"reading {name}", taskbar=self.taskbar)

    def load_all(self) -> None:
        """Read every complete stack of the holder that is not loaded yet (one job, cached stacks are instant)."""
        sids = [self.session.stack_id(s) for s in self.session.stacks_of() if self.session.stack_loaded(self.session.stack_id(s)) is None]
        if not sids:
            self.status_label.setText("every scan of the holder is loaded")
            return
        session = self.session

        def job(cb):
            n = len(sids)
            for i, sid in enumerate(sids):
                cb(i * 100, n * 100, f"reading {split_stack_id(sid)[1]}")
                try:                                    # each energy folder reports: Cancel stops within one
                    session.stack(sid, progress=lambda j, m, t="", i=i: cb(i * 100 + int(100 * j / max(m, 1)), n * 100, t))
                except Exception as err:
                    cb(i * 100 + 99, n * 100, f"{split_stack_id(sid)[1]}: {err}")
            return sids

        self.status_label.setText(f"reading {plural(len(sids), 'scan')} …")
        jobs.of(self).submit(f"stacks:{self.session.current_holder}", job, on_done=self._all_loaded,
                             on_failed=lambda t: self.status_label.setText("could not read: " + t.strip().splitlines()[-1]),
                             on_end=lambda _s: self.update_actions(), label="reading the scans of the holder",
                             taskbar=self.taskbar)

    def _all_loaded(self, sids) -> None:
        n = sum(1 for s in sids if self.session.stack_loaded(s) is not None)
        self.status_label.setText(f"{n} of {plural(len(sids), 'scan')} read")
        for s in sids:
            self._attach(self.session.stack_loaded(s))
        self.session.touch()
        cur = self.scan_list.currentItem()
        if cur is not None and self.session.current_stack is None:
            self.session.select_stack(cur.data(Qt.UserRole))
        else:
            self._rebuild_cards(); self.update_preview()

    def _loaded(self, sid: str) -> None:
        self.status_label.setText("")
        self._attach(self.session.stack_loaded(sid))
        self.session.touch()                      # memberships for the newly loaded stack
        cur = self.scan_list.currentItem()
        if cur is not None and cur.data(Qt.UserRole) == sid:        # still the scan the user wants to see
            self.session.select_stack(sid)

    def _stack_selected(self, sd) -> None:
        self._attach(sd)
        self.map_line.blockSignals(True)
        self.map_energy.blockSignals(True)
        self.map_line.clear()
        if sd is not None:
            self.map_line.addItems(sd.labels)
            self.map_line.setCurrentText(sd.main)
            self.map_energy.set_energies(sd.E)
            self.map.set_points(sd.x, sd.y, sd.pitch)
        else:
            self.map.set_points(None, None, None)
        self.editor.idle()
        self.map_line.blockSignals(False)
        self.map_energy.blockSignals(False)
        self._show_window_step()
        self.redraw_map()
        if sd is not None:
            self.map.autorange()
        self._rebuild_cards()
        self.update_preview()
        self.update_actions()

    # ------------------------------------------------------------------ step 2: the XRF window
    def _edge_key(self, scan_or_sd) -> str:
        return pretty_edge(getattr(scan_or_sd, "edge", None))

    def current_choice(self) -> dict | None:
        """The window shown in step 2: the current zone's settings for the current stack, else the choice remembered
        for the holder and edge, else None (the stack's main line)."""
        sd = self.current_stack()
        if sd is None:
            return None
        z = self.session.zone()
        st = z.settings.get(sd.id) if z is not None else None
        if st is not None:
            return {"line": st.line or sd.main, "roi": st.roi}
        return self._choices.get((self.session.current_holder or "", self._edge_key(sd)))

    def _show_window_step(self) -> None:
        sd = self.current_stack()
        scan = self.session.scan_of(sd.id) if sd is not None else None
        self.window_card.set_stack(sd, scan, self.current_choice())
        self._sync_map_line()

    def _show_choice(self) -> None:
        """The step-2 card follows the selected zone's window on the current stack (nothing is written)."""
        if self.current_stack() is not None:
            self.window_card.show_choice(self.current_choice())
            self._sync_map_line()

    def _sync_map_line(self) -> None:
        """The map follows the step-2 window: its fixed line, or the 'XRF window' entry for custom windows."""
        sd = self.current_stack()
        if sd is None:
            return
        ch = self.current_choice() or {"line": sd.main, "roi": None}
        self.map_line.blockSignals(True)
        i = self.map_line.findText(xw.WINDOW_LABEL)
        if ch.get("roi"):
            if i < 0:
                self.map_line.addItem(xw.WINDOW_LABEL)
            self.map_line.setCurrentText(xw.WINDOW_LABEL)
        else:
            if i >= 0:
                self.map_line.removeItem(i)
            if ch.get("line") in sd.labels:
                self.map_line.setCurrentText(ch["line"])
        self.map_line.blockSignals(False)

    def _window_committed(self, choice: dict) -> None:
        """A window chosen in step 2: remembered for the holder and edge (new zones take it), and written into the
        current zone's scans of that edge in one edit."""
        sd = self.current_stack()
        if sd is None:
            return
        edge = self._edge_key(sd)
        self._choices[(self.session.current_holder or "", edge)] = dict(choice)
        z = self.session.zone()
        if z is not None:
            targets = []
            for sid, st in z.settings.items():
                other = self.session.stack_loaded(sid) or self.session.scan_of(sid)
                if sid == sd.id or (other is not None and self._edge_key(other) == edge):
                    if st.line != choice["line"] or not xw.same(st.roi, choice["roi"]):
                        targets.append(st)
            if targets:
                with self.session.edit(f"set the XRF window of zone {z.id}"):
                    for st in targets:
                        st.line, st.roi = choice["line"], (dict(choice["roi"]) if choice["roi"] else None)
                        st.pinned = list(st.pinned) + [p for p in ("line", "roi") if p not in st.pinned]
                self.status_label.setText(f"XRF window {self._window_words(choice)} set for "
                                          f"{plural(len(targets), 'scan')} of zone {z.id}")
            else:
                self.status_label.setText(f"XRF window {self._window_words(choice)}")
        else:
            self.status_label.setText(f"XRF window {self._window_words(choice)}: new samples of this holder take it")
        self._sync_map_line()
        self.redraw_map()
        self.update_preview()
        self._refresh_easy()

    @staticmethod
    def _window_words(choice: dict) -> str:
        return f"{choice['line']} · {xw.describe(choice['roi'])}" if choice.get("roi") else f"{choice['line']} (fixed bins)"

    def _ensure_windows(self, sd, roi) -> bool:
        """True when the window counts of the stack are ready; otherwise a job reads them and redraws when done."""
        if not roi or xw.stack_window_ready(sd, roi):
            return True
        key = f"windows:{sd.id}:{xw.key(roi)}"
        if key in self._window_failed:
            return True                                 # read once and failed: zone_sums falls back with a note
        jobs.of(self).submit(key, lambda cb, s=sd, w=dict(roi): xw.stack_window_counts(s, w, cb),
                             on_done=lambda _wc: (self.redraw_map(), self.update_preview()),
                             on_failed=lambda t, k=key: self._windows_failed(k, t),
                             label=f"reading the XRF windows of {sd.name}", taskbar=self.taskbar)
        return False

    def _windows_failed(self, key: str, trace: str) -> None:
        self._window_failed.add(key)
        self.status_label.setText("could not read the windows: " + trace.strip().splitlines()[-1])
        self.redraw_map()
        self.update_preview()

    def remember_windows(self, choice: dict) -> None:
        """'Remember these windows for <edge>': a saved default rule {edge -> line, roi} (new scans inherit it)."""
        sd = self.current_stack()
        if sd is None or self.session.project is None:
            return
        edge = self._edge_key(sd)
        name = f"XRF window for {edge}"
        rules = rl.rules_from_json(self.session.zones.rules)
        rule = next((r for r in rules if r.name == name), None)
        if rule is None:
            rule = rl.Rule(scope={"kind": "project"}, scan_filter={"edge": edge}, name=name)
            rules.append(rule)
        rule.patch = {"line": choice["line"], "roi": choice["roi"]}
        rule.enabled = True
        with self.session.edit(f"remember the XRF window for {edge}"):
            self.session.zones.rules = rl.rules_to_json(rules)
        self.status_label.setText(f"new {edge} scans will use {self._window_words(choice)} (Rules…)")

    # ------------------------------------------------------------------ settings defaults (new zone x scan)
    def _zone_being_attached(self, sid: str):
        """The zone whose settings for sid are being created (members set, settings not yet): rules scoped to groups
        or zones need it. None when it cannot be told."""
        cands = []
        for hk, hz in self.session.zones.holders.items():
            for z in hz.zones:
                if sid in z.members and sid not in z.settings:
                    cands.append((hk, z))
        return cands[0] if len(cands) == 1 else (None, None)

    def _settings_default(self, scan, st: ScanSettings) -> None:
        """Every new zone x scan settings: the saved rules (ZonesFile.rules) in order, then the step-2 window chosen
        for the holder and edge in this session."""
        sid = f"{scan.day}_{scan.name}"
        holder = self.session.holder_of_stack(sid)
        saved = rl.rules_from_json(self.session.zones.rules)
        if saved:
            hk, z = self._zone_being_attached(sid)
            rl.apply_to_new(saved, hk or holder, z, scan, st, flags_of=self.session.flags_of)
        ch = self._choices.get((holder or "", self._edge_key(scan)))
        if ch is not None:
            st.line, st.roi = ch["line"], (dict(ch["roi"]) if ch["roi"] else None)

    # ------------------------------------------------------------------ map
    def redraw_map(self) -> None:
        sd = self.current_stack()
        self.map.clear_overlays()
        if sd is None:
            self.map.set_grid(None)
            self._caption()
            return
        label = self.map_line.currentText() or sd.main
        det = None if self.map_sdd.currentIndex() == 0 else self.map_sdd.currentIndex() - 1
        view, note = sd, ""
        if label == xw.WINDOW_LABEL:
            ch = self.current_choice() or {}
            roi = ch.get("roi")
            wc = None
            if roi and self._ensure_windows(sd, roi):
                try:
                    wc = xw.stack_window_counts(sd, roi)
                except (OSError, ValueError) as err:
                    note = f" · windows not read ({err})"
            if wc is not None:
                view = xw.window_view(sd, wc)
            else:
                label = sd.main
                note = note or " · reading the windows …"
        vals = view.map_values(label, det=det, mode=self.map_mode.currentData(), energy_index=self.map_energy.value(),
                               per_i0=self.map_i0.isChecked())
        if self.map_log.isChecked():
            with np.errstate(all="ignore"):
                vals = np.log10(np.maximum(vals, 0) + 1.0)
        from aurora.core.grid import to_grid
        self.map.set_grid(to_grid(sd.x, sd.y, vals))
        self.map.set_scale_label(scale_label("edge jump, counts" if self.map_mode.currentData() == "jump" else "counts",
                                             per_i0=self.map_i0.isChecked(), log=self.map_log.isChecked()))
        hz = self.session.holder_zones(create=False)
        for i, z in enumerate(hz.zones if hz else []):
            sel = z.id == self.session.current_zone
            self.map.draw_zone(z, curve(i), selected=sel)
            if sel:
                pix = members(z, sd.x, sd.y, sd.pitch)
                self.map.draw_pixels(sd.x[pix], sd.y[pix], sd.pitch, curve(i))
        self.map_hint.setText(f"{sd.name}: {plural(sd.n_energies, 'energy')} x {sd.n_pix} px, pitch {sd.pitch[0]:.3f} x "
                              f"{sd.pitch[1]:.3f} mm, main line {sd.main}"
                              + (f", edge {sd.edge_text} {sd.edge_ev:.1f} eV" if sd.edge_ev else "") + f" · {sd.source}" + note)
        self._caption()

    def region_count(self) -> tuple[int, int, object]:
        """(K members of the current zone on the current stack, M points of the stack, the stack)."""
        sd = self.current_stack()
        z = self.session.zone() if self.session.project is not None else None
        if sd is None:
            return 0, 0, None
        k = int(members(z, sd.x, sd.y, sd.pitch).size) if z is not None else 0
        return k, sd.n_pix, sd

    def _caption(self) -> None:
        k, m, sd = self.region_count()
        z = self.session.zone() if self.session.project is not None else None
        if sd is None:
            text = "Region: pick a scan in step 1" if self.session.project is not None else ""
        elif k == 0:
            text = "Region: nothing selected: draw over the stack footprint"
        else:
            text = f"Region: {shape_text(z)} · {k} of {m} points of {sd.name}"
        self.region_caption.setText(text)
        self.window_card.set_region(members(z, sd.x, sd.y, sd.pitch) if (sd is not None and z is not None) else None)
        self.window_card.set_zone(z)

    def _hover(self, x: float, y: float) -> None:
        sd = self.current_stack()
        txt = f"x {x:.2f} mm, y {y:.2f} mm"
        if sd is not None:
            px, py = sd.pitch
            hit = np.where((np.abs(sd.x - x) <= px / 2) & (np.abs(sd.y - y) <= py / 2))[0]
            if hit.size:
                txt += f" · px {hit[0]}"
        self.status_label.setText(txt)

    def _map_points(self):
        """(x, y, pitch, name) of the stack shown, for the zone editor; None without one. The name is the one of the
        scan list ('09:27 alpha_beta'), short enough for the map's hint."""
        sd = self.current_stack()
        if sd is None:
            return None
        scan = self.session.scan_of(sd.id)
        name = f"{scan.time} {scan.label}".strip() if scan is not None and (scan.time or scan.label) else sd.name
        return sd.x, sd.y, sd.pitch, name

    def _makes_new_zone(self, add: bool) -> bool:
        """Whether an adding tool starts a new zone: no zone selected, or 'every stroke starts a new zone' ticked (the rule
        of the legacy signals; strokes follow ZoneEditor.target_for)."""
        return add and (self.session.zone() is None or self.target_new.isChecked())

    def _legacy(self, stroke: Stroke) -> None:
        """A legacy map signal emitted by code: one edit through the zone editor, as before (a stroke of the mouse
        is written by the editor itself and emits these signals with map.stroke_emitting() True)."""
        if not self.map.stroke_emitting():
            self.editor.commit(stroke, legacy=True)

    def _rect(self, x0: float, x1: float, y0: float, y1: float) -> None:
        shape, erase = split_tool(self.map.vb.tool)
        if self.map.stroke_emitting() or shape in ("navigate", "brush"):
            return
        self._legacy(Stroke(shape="rect", erase=erase, start=(x0, y0), rect=(x0, x1, y0, y1)))

    def _polygon(self, pts) -> None:
        """A lasso or polygon closed on the map: added to the current zone (a new zone when none is selected)."""
        if len(pts) < 3:
            return
        self._legacy(Stroke(shape="polygon", path=[[float(p[0]), float(p[1])] for p in pts],
                            start=(float(pts[0][0]), float(pts[0][1]))))

    def _brush(self, x: float, y: float, add: bool) -> None:
        self._legacy(Stroke(shape="brush", erase=not add, start=(x, y), path=[[x, y]], brush_n=self.brush_n.value()))

    def _zone_clicked(self, zid: int) -> None:
        if zid != self.session.current_zone:
            self.session.select_zone(zid)

    def clear_zone(self) -> None:
        """Clear the current zone's drawing (rectangles, polygons, painted and erased pixels) in one edit."""
        z = self.session.zone()
        if z is None:
            return
        with self.session.edit(f"clear zone {z.id}"):
            z.rects, z.erects, z.paint, z.erase, z.polys, z.epolys = [], [], [], [], [], []
        self.status_label.setText(f"zone {z.id} cleared: draw its region again")

    # ------------------------------------------------------------------ right-click menus
    def zone_menu(self, zid: int) -> QMenu:
        """The menu of a zone on the map: Rename, Set group, Duplicate, Delete, Send to Compare, Copy geometry, Zoom."""
        m = QMenu(self)
        z = self.session.zone(zid)
        if z is None:
            return m
        m.addAction("Rename…", lambda: self.rename_zone(zid))
        if is_advanced():                                # group: an Advanced field (Easy never writes it)
            hz = self.session.holder_zones(create=False)
            groups = sorted({r.group for r in self.session.sheet_rows if r.group} |
                            {zz.group for zz in (hz.zones if hz else []) if zz.group})
            sub = m.addMenu("Set group")
            for g in groups + [""]:
                a = sub.addAction(g or "(none)")
                a.setCheckable(True)
                a.setChecked((z.group or "") == g)
                a.triggered.connect(lambda _c=False, gg=g: self.set_zone_group(zid, gg))
        m.addAction("Duplicate", lambda: self.duplicate_zone(zid))
        m.addAction("Delete", lambda: self.session.delete_zone(zid))
        if is_advanced():
            sa = m.addAction("Send to Compare", lambda: (self.session.select_zone(zid), self.send_to_compare()))
            sa.setEnabled(self.library is not None and bool(z.settings))
        m.addSeparator()
        m.addAction("Copy geometry", lambda: self.copy_geometry(zid))
        za = m.addAction("Zoom to zone", lambda: self.zoom_to_zone(zid))
        za.setEnabled(zone_bbox(z) is not None)
        return m

    def map_menu(self, x: float, y: float) -> QMenu:
        """The menu of the empty map: New zone here, Paste zone (centred here), Fit view."""
        m = QMenu(self)
        a = m.addAction("New zone here", lambda: self.new_zone_at(x, y))
        a.setEnabled(self.session.holder() is not None)
        p = m.addAction("Paste zone", lambda: self.paste_geometry(x, y))
        p.setEnabled(self.session.holder() is not None and self._clipboard_geometry() is not None)
        m.addAction("Fit view", self.map.autorange)
        return m

    def _context(self, x: float, y: float, zid) -> None:
        menu = self.zone_menu(zid) if zid is not None else self.map_menu(x, y)
        self.last_menu = popup(menu, QCursor.pos())

    def rename_zone(self, zid: int, label: str | None = None) -> None:
        z = self.session.zone(zid)
        if z is None:
            return
        if label is None:
            label, ok = QInputDialog.getText(self, "Rename the sample", "label", text=z.label)
            if not ok:
                return
        label = (label or "").strip()
        if not label or label == z.label:
            return
        with self.session.edit(f"rename zone {zid}"):
            z.label = label
        if zid == self.session.current_zone:
            self._fill_zone_card(z)

    def set_zone_group(self, zid: int, group: str) -> None:
        z = self.session.zone(zid)
        if z is None or (z.group or "") == group:
            return
        with self.session.edit(f"edit zone {zid}"):
            z.group = group
        if zid == self.session.current_zone:
            self._fill_zone_card(z)

    def duplicate_zone(self, zid: int):
        """A copy of the zone (drawing and settings) as a new zone, selected."""
        z = self.session.zone(zid)
        hz = self.session.holder_zones(create=False)
        if z is None or hz is None:
            return None
        import copy as _copy
        nid = max((zz.id for zz in hz.zones), default=0) + 1
        with self.session.edit(f"duplicate zone {zid}"):
            dup = Zone(id=nid, label=f"{z.label} (copy)", group=z.group, comment=z.comment)
            set_geometry(dup, geometry(z))
            dup.settings = {sid: _copy.deepcopy(st) for sid, st in z.settings.items()}
            hz.zones.append(dup)
            self.session.current_zone = nid
        self.session.select_zone(nid)
        return dup

    def copy_geometry(self, zid: int) -> str:
        z = self.session.zone(zid)
        if z is None:
            return ""
        text = json.dumps({GEOMETRY_MARK: 1, "label": z.label, **geometry(z)})
        QApplication.clipboard().setText(text)
        self.status_label.setText(f"geometry of zone {zid} copied (right-click the map: Paste zone)")
        return text

    def _clipboard_geometry(self) -> dict | None:
        try:
            d = json.loads(QApplication.clipboard().text() or "")
        except (ValueError, TypeError):
            return None
        return d if isinstance(d, dict) and d.get(GEOMETRY_MARK) else None

    def paste_geometry(self, x: float | None = None, y: float | None = None):
        """A new zone with the copied geometry, centred on (x, y) when given (its own place otherwise)."""
        geo = self._clipboard_geometry()
        if geo is None or self.session.holder() is None:
            return None
        tmp = Zone(id=0, label="")
        set_geometry(tmp, geo)
        bb = zone_bbox(tmp)
        dx = dy = 0.0
        if bb is not None and x is not None and y is not None:
            dx, dy = x - (bb[0] + bb[1]) / 2, y - (bb[2] + bb[3]) / 2
        with self.session.edit("paste a zone"):
            z = self.session.new_zone(f"{geo.get('label', 'zone')} (pasted)".strip())
            if z is not None:
                set_geometry(z, geo, dx, dy)
        return z

    def new_zone_at(self, x: float, y: float):
        """A new zone holding the pixel under (x, y)."""
        with self.session.edit("new zone here"):
            z = self.session.new_zone()
            if z is not None:
                z.rects.append([round(x, 3), round(x, 3), round(y, 3), round(y, 3)])
        return z

    def zoom_to_zone(self, zid: int) -> None:
        z = self.session.zone(zid)
        bb = zone_bbox(z) if z is not None else None
        if bb is not None:
            self.map.zoom_to(*bb)

    # ------------------------------------------------------------------ John Bussey's polygon files
    def load_polygon(self, path=None):
        """'Load polygon…': John Bussey's _polygon_roi.json (>= 3 [x, y] vertices, mm) into a new zone."""
        from pathlib import Path
        from aurora.app.widgets import default_dir, remember_dir
        if self.session.holder() is None:
            self.status_label.setText(NO_PROJECT)
            return None
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Load a polygon ROI (John Bussey's GUI)",
                                                  default_dir("zones", self.session),
                                                  "Polygon ROI (*_polygon_roi.json *.json);;All files (*)")
            if not path:
                return None
            remember_dir("zones", path)
        p = Path(path)
        try:
            pts = json.loads(p.read_text(encoding="utf-8"))
            poly = [[float(v[0]), float(v[1])] for v in pts]
            if len(poly) < 3:
                raise ValueError("fewer than 3 vertices")
        except (OSError, ValueError, TypeError, IndexError, KeyError) as err:
            self.status_label.setText(f"{p.name} is not a polygon ROI ({err})")
            return None
        label = p.stem[:-len("_polygon_roi")] if p.stem.endswith("_polygon_roi") else p.stem
        with self.session.edit(f"load the polygon {p.name}"):
            z = self.session.new_zone(label)
            if z is not None:
                z.polys.append(poly)
        self.status_label.setText(f"{p.name}: a polygon with {len(poly)} vertices as zone {z.id if z else ''}")
        return z

    def save_polygon(self, path=None):
        """'Save polygon…': the current zone (one polygon) as John Bussey's _polygon_roi.json."""
        from pathlib import Path
        from aurora.app.widgets import default_path, guarded_write, notify_saved, remember_dir
        from aurora.core.io.textio import write_json_atomic
        z = self.session.zone()
        if z is None or len(z.polys) != 1 or z.rects or z.paint or z.erects or z.erase:
            self.status_label.setText("John Bussey's file holds one polygon: the zone must be exactly one polygon")
            return None
        if path is None:
            name = "".join(c if c.isalnum() or c in "-_" else "_" for c in z.label) or f"zone{z.id}"
            path, _ = QFileDialog.getSaveFileName(self, "Save the polygon ROI (John Bussey's GUI)",
                                                  default_path("zones", f"{name}_polygon_roi.json", self.session),
                                                  "Polygon ROI (*_polygon_roi.json)")
            if not path:
                return None
            remember_dir("zones", path)
        p = Path(path)
        with guarded_write(self, p.name) as g:
            write_json_atomic(p, [[float(a), float(b)] for a, b in z.polys[0]], indent=2)
        if g.ok:
            notify_saved(self.status_label, [p])
            return p
        return None

    # ------------------------------------------------------------------ zones
    def _fill_zone_list(self) -> None:
        hz = self.session.holder_zones(create=False)
        self.zone_list.blockSignals(True)
        self.zone_list.clear()
        for z in (hz.zones if hz else []):
            n = sum(1 for s in z.settings.values() if s.include)
            it = QListWidgetItem(f"{z.id}  {z.label}" + (f"  ({z.group})" if z.group else "") + f"  · {plural(n, 'scan')}")
            it.setToolTip(it.text())
            it.setData(Qt.UserRole, z.id)
            self.zone_list.addItem(it)
            if z.id == self.session.current_zone:
                self.zone_list.setCurrentItem(it)
        self.zone_list.blockSignals(False)

    def _zone_picked(self, cur, _prev=None) -> None:
        if cur is not None:
            self.session.select_zone(cur.data(Qt.UserRole))

    def _delete_zone(self) -> None:
        if self.session.current_zone is not None:
            self.session.delete_zone(self.session.current_zone)

    def propose_zones(self, source: str | None = None) -> int:
        """Add proposed zones: from sgm_report's summary when it exists for the holder's day, else from the survey pads."""
        from aurora.core.propose import pads_from_survey, zones_from_pads, zones_from_summary
        hz = self.session.holder_zones()
        if hz is None:
            self.status_label.setText(NO_PROJECT)
            return 0
        start = max((z.id for z in hz.zones), default=0) + 1
        new = []
        day = next((d for d in self.session.project.days if d.date == self.session.holder_day()), None) if self.session.project else None
        h = self.session.holder()
        if source in (None, "summary") and day is not None and day.summary and h is not None:
            scans = [s for hh in day.summary.get("holders", []) if hh.get("holder") == h.name for s in hh.get("scans", [])]
            pitches = {}
            for s in h.stacks:
                sd = self.session.stack_loaded(self.session.stack_id(s))
                if sd is not None:
                    pitches[s.name] = sd.pitch
            new = zones_from_summary(scans, pitches, start)
        if not new and source in (None, "pads"):
            try:
                sv = self.session.survey()
            except Exception:
                sv = None
            if sv is not None:
                new = zones_from_pads(pads_from_survey(sv), start)
        existing = {(round(r[0], 2), round(r[2], 2)) for z in hz.zones for r in z.rects}
        new = [z for z in new if (round(z.rects[0][0], 2), round(z.rects[0][2], 2)) not in existing]
        if new:                                         # nothing proposed: nothing written
            with self.session.edit(f"propose {len(new)} zones"):
                hz.zones.extend(new)
        self.status_label.setText(f"{plural(len(new), 'zone')} proposed (edit or delete them)" if new else
                                  "nothing to propose (no summary, no pads)")
        return len(new)

    def stitch_tiles(self) -> None:
        from aurora.app.dialogs import StitchDialog
        loaded = {self.session.stack_id(s): self.session.stack_loaded(self.session.stack_id(s)) for s in self.session.stacks_of()}
        loaded = {k: v for k, v in loaded.items() if v is not None}
        if len(loaded) < 2:
            self.status_label.setText("load at least two stacks of the holder first (Load all)"); return
        d = StitchDialog(loaded, self)
        if d.exec() and d.result is not None:
            self.add_virtual_stack(d.result)

    def add_virtual_stack(self, sd) -> None:
        """Register a stitched stack with the session as if it were a scan of the holder."""
        self.session._stacks[sd.id] = sd
        it = QListWidgetItem(f"{sd.name}  ·  stitched {sd.n_pix} px")
        it.setToolTip(it.text())
        it.setData(Qt.UserRole, sd.id)
        self.scan_list.addItem(it)
        self.session.touch()
        self.scan_list.setCurrentItem(it)
        self.session.select_stack(sd.id)
        self.status_label.setText(f"{sd.name}: {sd.n_pix} px, {plural(sd.n_energies, 'energy')} ({sd.source})")

    def _zones_changed(self) -> None:
        self._fill_zone_list()
        self._show_choice()
        self.redraw_map()
        z = self.session.zone()
        have = [c.sid for c in self._cards]
        want = [sid for sid in (z.settings if z is not None else {}) if self.session.stack_loaded(sid) is not None]
        if have != want:
            self._rebuild_cards()
        else:
            for c in self._cards:
                self._card_chip(c)
        self.update_preview()
        self.update_actions()

    def _flags_changed(self, _sids) -> None:
        """New scan flags (Check's probe, a summary found by Reload): the cards show the settings that followed them,
        step 4 and the chip name the flags."""
        if any([b.isChecked() for b in c.sdd] != [bool(v) for v in c.s.sdd] or c.i0src.currentData() != c.s.i0_source
               for c in self._cards):
            self._rebuild_cards()
        self._refresh_easy()

    def _zone_selected(self, z: Zone | None) -> None:
        self._fill_zone_list()
        self._fill_zone_card(z)
        self._show_choice()
        self.redraw_map(); self._rebuild_cards(); self.update_preview()
        self.update_actions()

    def _fill_zone_card(self, z: Zone | None) -> None:
        for w in (self.z_label, self.z_group, self.z_comment):
            w.blockSignals(True)
        self.z_label.clear(); self.z_group.clear()
        h = self.session.holder()
        rows = [r for r in self.session.sheet_rows if h and r.plate.strip().lower() == h.name.lower()]
        self.z_label.addItems(sorted({r.sample_id for r in rows}))
        self.z_group.addItems(sorted({r.group for r in self.session.sheet_rows if r.group}))
        if z is None:
            self.z_label.setEditText(""); self.z_group.setEditText(""); self.z_comment.setText("")
            self.z_info.setText("no zone selected: choose Draw and drag on the map to create one")
        else:
            self.z_label.setEditText(z.label); self.z_group.setEditText(z.group); self.z_comment.setText(z.comment)
            self.z_info.setText(f"zone {z.id}: {plural(len(z.rects), 'rect')}, {plural(len(z.polys), 'polygon')}, "
                                f"{plural(len(z.erects), 'erase rect')}, {plural(len(z.epolys), 'erase outline')}, "
                                f"{len(z.paint)} painted px, {len(z.erase)} erased px "
                                f"· covered by {plural(len(z.members), 'loaded scan')}")
        for w in (self.z_label, self.z_group, self.z_comment):
            w.blockSignals(False)

    def _zone_edited(self) -> None:
        z = self.session.zone()
        if z is None:
            return
        label = self.z_label.currentText().strip() or z.label
        group = self.z_group.currentText().strip()
        comment = self.z_comment.text().strip()
        if not group and label:
            m = next((r for r in self.session.sheet_rows if r.sample_id and r.sample_id.lower() in label.lower()), None)
            if m:
                group = m.group
                self.z_group.blockSignals(True); self.z_group.setEditText(group); self.z_group.blockSignals(False)
        if (label, group, comment) == (z.label, z.group, z.comment):
            return                                      # nothing changed: nothing written
        what = "rename" if label != z.label else "edit"
        with self.session.edit(f"{what} zone {z.id}"):
            z.label, z.group, z.comment = label, group, comment

    # ------------------------------------------------------------------ step 4 and the Easy chip
    def easy_settings_for(self, sid: str) -> ScanSettings | None:
        """mode.easy_scan_settings for one zone x scan (the scan's flags; the stack's labels when loaded)."""
        from aurora.app.mode import easy_scan_settings
        scan = self.session.scan_of(sid)
        if scan is None:
            return None
        sd = self.session.stack_loaded(sid)
        return easy_scan_settings(scan, self.session.flags_of(scan), labels=list(sd.labels) if sd is not None else None)

    def step4_text(self, sid: str | None = None) -> str:
        """'I0 mcc ch1 (Au mesh) · TEY mcc ch4 · zone mean (John Bussey's reduction) · / I0, edge jump = 1 · de-spike
        k = 6 (John Bussey's default: off) · SDD1-4 (SDD3 saturated, left out)': the settings of the current sample on
        the current stack, read-only (the Easy defaults until it has some). A flagged SDD still in the sum or an I0
        flagged invalid still used is named. The zone mean is what the saved files hold (the / I0 traces of the zone
        sum are the same); the de-spike, on in Easy, was off by default in his GUI, so the line says so."""
        from aurora.core.constants import DETS
        from aurora.core.flags import i0_invalid, saturated_dets
        sd = self.current_stack()
        sid = sid or (sd.id if sd is not None else None)
        z = self.session.zone() if (sid and self.session.project is not None) else None
        st = (z.settings.get(sid) if z is not None else None) or (self.easy_settings_for(sid) if sid else None)
        if st is None:
            return "pick a scan in step 1: the settings used are listed here"
        flags = self.session.flags_of(self.session.scan_of(sid))
        sat = saturated_dets(flags)
        out = [d.upper() for d, on in zip(DETS, st.sdd) if d in sat and not on]
        kept = [d.upper() for d, on in zip(DETS, st.sdd) if d in sat and on]
        off = [d.upper() for d, on in zip(DETS, st.sdd) if d not in sat and not on]
        notes = []
        for names, text in ((out, "saturated, left out"), (kept, "flagged saturated, still in the sum"), (off, "off")):
            if names:
                notes.append(f"{', '.join(names)} {text}")
        dets = "SDD1-4" + (f" ({'; '.join(notes)})" if notes else "")
        bad_i0 = i0_invalid(flags)
        if st.i0_source == "good":
            i0 = "I0 mcc ch1 (Au mesh) · good-I0 mean" + (" (I0 invalid)" if bad_i0 else "")
        else:
            i0 = "I0 mcc ch1 (Au mesh)" + (" (flagged invalid, still used)" if bad_i0 else "")
        despike = "de-spike off"
        if st.despike:          # a session without a de-spike rule keeps the earlier one (no control sets it)
            despike = (f"de-spike k = {st.k:g} (the earlier rule), John Bussey's default: off"
                       if st.despike_mode == "classic" else f"de-spike k = {st.k:g} (John Bussey's default: off)")
        return " · ".join([i0, "TEY mcc ch4" if st.tey else "TEY off", "zone mean (John Bussey's reduction)",
                           f"fluorescence {NORM_LABELS.get(st.fluo_norm, st.fluo_norm)}", despike, dets])

    def zone_differences(self, z: Zone | None = None) -> dict[str, list[str]]:
        """sid -> the fields of the zone's settings that differ from the Easy defaults (empty lists left out)."""
        z = z if z is not None else self.session.zone()
        out = {}
        for sid, st in (z.settings.items() if z is not None else []):
            easy = self.easy_settings_for(sid)
            if easy is None:
                continue
            d = easy_differences(st, easy)
            if d:
                out[sid] = d
        return out

    def _refresh_easy(self) -> None:
        adv = is_advanced()
        self.step4_line.setText(self.step4_text())
        z = self.session.zone() if self.session.project is not None else None
        diffs = self.zone_differences(z) if (z is not None and not adv) else {}
        if diffs:
            lines = []
            for sid, fields in diffs.items():
                text = differences_text(z.settings[sid], fields, self.easy_settings_for(sid))
                lines.append(f"{short_time(sid) or split_stack_id(sid)[1]}: {text}" if len(z.settings) > 1 else text)
            tip = "\n".join(lines)
            self.chip.setToolTip(tip)
            self.reset_btn.setToolTip("Rewrite these fields with the Easy defaults (one undo step):\n" + tip)
        if self.chip_box.isVisibleTo(self.zone_card) != bool(diffs):
            self.chip_box.setVisible(bool(diffs))
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, self.fit_cards)
        for c in self._cards:
            self._card_chip(c)

    def _card_chip(self, card: ScanSettingsCard) -> None:
        if is_advanced():
            card.chip.hide()
            return
        easy = self.easy_settings_for(card.sid)
        d = easy_differences(card.s, easy) if easy is not None else []
        card.chip.setVisible(bool(d))
        card.chip.setToolTip(differences_text(card.s, d, easy) if d else "")

    def reset_to_easy(self) -> int:
        """'Reset to Easy defaults': the differing fields of the current zone's settings rewritten in one edit."""
        z = self.session.zone()
        diffs = self.zone_differences(z)
        if z is None or not diffs:
            return 0
        with self.session.edit(f"reset zone {z.id} to the Easy defaults"):
            for sid, fields in diffs.items():
                easy = self.easy_settings_for(sid)
                st = z.settings[sid]
                for f in fields:
                    setattr(st, f, getattr(easy, f))
                keys = {rl.ATTRS.get(f, f) for f in fields}
                st.pinned = [p for p in st.pinned if p not in keys]
        n = sum(len(v) for v in diffs.values())
        self.status_label.setText(f"zone {z.id}: {plural(n, 'field')} back to the Easy defaults")
        self._rebuild_cards()
        self._refresh_easy()
        return n

    def add_to_check(self) -> int:
        """'Add to Check': the zone x current stack queued for Easy Check. Settings that do not exist yet come from
        mode.easy_scan_settings (plus the step-2 window); existing settings are kept (the chip names what differs)."""
        from aurora.app.mode import easy_scan_settings
        self._zone_edited()                         # a label typed in the box and not committed yet (Ctrl+Return)
        k, _m, sd = self.region_count()
        z = self.session.zone()
        if z is None or sd is None or k == 0:
            self.status_label.setText("nothing selected: draw over the stack footprint")
            return 0
        scan = self.session.scan_of(sd.id)
        with self.session.edit(f"add zone {z.id} to Check"):
            if sd.id not in z.settings and scan is not None:
                st = easy_scan_settings(scan, self.session.flags_of(scan), labels=list(sd.labels))
                ch = self.current_choice() or self._choices.get((self.session.current_holder or "", self._edge_key(sd)))
                if ch:
                    st.line, st.roi = ch["line"], (dict(ch["roi"]) if ch.get("roi") else None)
                st.n = k
                z.settings[sd.id] = st
        n = self.session.queue_for_check([CheckItem(self.session.current_holder, z.id, sd.id)])
        # John Bussey's GUI asked for a label first: a default name is saved as it is, so say so
        unnamed = (" · it is saved under this name: name the sample in the sample box"
                   if DEFAULT_LABEL.match(z.label) else "")
        self.status_label.setText(f"{z.label} on {short_time(sd.id) or sd.name} added to Check{unnamed}" if n else
                                  f"{z.label} on {short_time(sd.id) or sd.name} is already in Check")
        return n

    # ------------------------------------------------------------------ batch settings: copy, paste, rules
    def copy_fields(self) -> list[str]:
        """The copy groups ticked under 'Fields that travel' (rules.COPY_GROUPS)."""
        from aurora.app.settings import settings
        v = settings().value(COPY_FIELDS_SETTING, None)
        if v is None:
            return [g for g, _w, _k, on in rl.COPY_GROUPS if on]
        if isinstance(v, str):
            v = [x for x in v.split(",") if x]
        return [g for g in v if g in {c[0] for c in rl.COPY_GROUPS}]

    def set_copy_field(self, group: str, on: bool) -> None:
        from aurora.app.settings import settings
        cur = set(self.copy_fields())
        cur = (cur | {group}) if on else (cur - {group})
        s = settings()
        s.setValue(COPY_FIELDS_SETTING, ",".join(g for g, _w, _k, _o in rl.COPY_GROUPS if g in cur))
        s.sync()

    def copy_keys(self) -> list[str]:
        on = set(self.copy_fields())
        return [k for g, _w, keys, _d in rl.COPY_GROUPS if g in on for k in keys]

    def current_card(self) -> ScanSettingsCard | None:
        """The card with the keyboard focus, else the card of the current stack, else the first card."""
        fw = QApplication.focusWidget()
        for c in self._cards:
            if fw is not None and (fw is c or c.isAncestorOf(fw)):
                return c
        return next((c for c in self._cards if c.sid == self.session.current_stack), self._cards[0] if self._cards else None)

    def _scan_map(self) -> dict:
        proj = self.session.project
        return {f"{s.day}_{s.name}": s for s in proj.scans} if proj is not None else {}

    def copy_card_to(self, card: ScanSettingsCard, target: str) -> list:
        """Copy one card to many: 'zone' (all scans of this zone), 'scan' (this scan in every zone of the holder),
        'edge' (every scan with the same edge in every zone of the holder). The ticked fields travel; include and n
        never do. One edit; the status line counts the cards and zones."""
        zid = card.zone_id if card.zone_id is not None else self.session.current_zone
        holder = self.session.current_holder
        targets = rl.copy_targets(self.session.zones, holder, zid, card.sid, target, self._scan_map())
        if not targets:
            self.status_label.setText(f"nothing to copy to: no other card for '{rl.COPY_TARGETS[target]}'")
            return []
        what = short_time(card.sid) or card.scan.name
        keys = self.copy_keys()
        smap = self._scan_map()
        edge = getattr(smap.get(card.sid) or card.scan, "edge", None)
        same = [t for t in targets if getattr(smap.get(t[2]), "edge", None) == edge]
        other = [t for t in targets if t not in same]
        with self.session.edit(f"copy the settings of {what} in zone {zid} to {rl.COPY_TARGETS[target]}"):
            changes = rl.copy_settings(card.s, same, keys)
            # the line and its windows belong to the edge: never copied to a scan of another edge
            changes += rl.copy_settings(card.s, other, [k for k in keys if k not in ("line", "roi")])
        msg = (f"settings copied to {rl.summary(changes)}" if changes else
               f"the settings were already the same in {plural(len(targets), 'card')}")
        if other and any(k in keys for k in ("line", "roi")):
            msg += f" (line and windows kept on {plural(len(other), 'card')} of another edge)"
        self.status_label.setText(msg)
        self._rebuild_cards()
        return changes

    def copy_card(self, card: ScanSettingsCard | None) -> None:
        """Ctrl+Shift+C: remember the card's ticked fields for a paste."""
        if card is None:
            return
        import copy as _copy
        self.copied = (_copy.deepcopy(card.s), self.copy_keys(), short_time(card.sid) or card.scan.name)
        self.status_label.setText(shortcuts.native_text(f"settings of {self.copied[2]} copied (Ctrl+Shift+V pastes them into a card)"))

    def paste_card(self, card: ScanSettingsCard | None) -> list:
        """Ctrl+Shift+V: the copied fields into this card, in one edit."""
        if card is None or self.copied is None:
            return []
        src, keys, name = self.copied
        zid = card.zone_id if card.zone_id is not None else self.session.current_zone
        with self.session.edit(f"paste settings into {short_time(card.sid) or card.scan.name} of zone {zid}"):
            changes = rl.copy_settings(src, [(self.session.current_holder, self.session.zone(zid), card.sid, card.s)], keys)
        self.status_label.setText(f"settings of {name} pasted ({plural(len(changes), 'field')} changed)")
        self._rebuild_cards()
        return changes

    def open_rules(self):
        """Rules…: the ordered rule list, an editor and the live table of what would change."""
        from aurora.app.rules_dialog import RulesDialog
        d = RulesDialog(self.session, self)
        d.exec()
        return d

    # ------------------------------------------------------------------ per-scan settings
    def _clear_cards(self) -> None:
        for c in self._cards + ([self._placeholder] if self._placeholder else []):
            self.cards_lay.removeWidget(c); c.deleteLater()
        self._cards.clear()
        self._placeholder = None

    def _rebuild_cards(self) -> None:
        self._clear_cards()
        z = self.session.zone()
        if z is None:
            return
        scans = {self.session.stack_id(s): s for s in self.session.stacks_of()}
        for sid, st in z.settings.items():
            sd = self.session.stack_loaded(sid)
            if sd is None:
                continue
            scan = scans.get(sid) or _VirtualScan(sd)
            card = ScanSettingsCard(self.session, sid, scan, st, sd.labels, zone_id=z.id)
            card.owner = self
            card.copy_requested.connect(self.copy_card_to)
            self.cards_lay.insertWidget(self.cards_lay.count() - 1, card)
            self._cards.append(card)
            self._card_chip(card)
        if not self._cards:
            lab = QLabel("no loaded scan covers this zone (pick the scans in the list to load them)")
            lab.setObjectName("muted")
            lab.setWordWrap(True)
            self.cards_lay.insertWidget(0, lab)
            self._placeholder = lab

    def send_to_compare(self) -> int:
        """Add the current zone's normalised sum and TEY (every included scan) to the Compare library."""
        z = self.session.zone()
        if z is None or self.library is None:
            return 0
        n = 0
        waiting = good_wait = 0
        for sid, st in z.settings.items():
            sd = self.session.stack_loaded(sid)
            if sd is None or not st.include:
                continue
            if st.roi and not self._ensure_windows(sd, st.roi):
                waiting += 1
                continue
            if st.i0_source == "good" and not self._ensure_good_i0(redraw=False):
                good_wait += 1
                continue
            pix = members(z, sd.x, sd.y, sd.pitch)
            good = self.session.good_curve_for(sd.E) if st.i0_source == "good" else None
            t = zone_traces_cached(sd, pix, st, good)
            if t is None:
                continue
            label = f"{z.label} · {self.session.holder().name if self.session.holder() else ''} {short_time(sid) or sd.name}"
            n += len(self.library.add_traces(label, t.E, t.sum, t.tey if st.tey else None, source=f"zone {z.id} of {sid}"))
        msg = f"{plural(n, 'trace')} sent to Compare" if n else "nothing to send: load the zone's scans first"
        if waiting:
            msg += f" ({plural(waiting, 'scan')} still reading its XRF windows: send again in a moment)"
        if good_wait:
            msg += f" ({plural(good_wait, 'scan')} waiting for the good-I0 scans being read: send again in a moment)"
        self.status_label.setText(msg)
        return n

    def _ensure_good_i0(self, redraw: bool = True) -> bool:
        """True when the stacks marked good I0 are loaded; otherwise a job reads them (progress and Cancel in the
        strip) and the preview is drawn again when they are in."""
        return self.session.ensure_good_i0(self, self.update_preview if redraw else (lambda: None), taskbar=self.taskbar)

    # ------------------------------------------------------------------ preview
    def _draft_changed(self, draft) -> None:
        """A stroke moved (draft) or ended (None): the throttled live preview, or back to the zone as written."""
        if draft is not None:
            self.throttle.request(draft)
            return
        self.throttle.cancel()
        if self._drafted:
            self._drafted = False
            if not self.editor.last_committed:          # nothing written: the zone as it was (a commit redrew it)
                self.update_preview()
                self._caption()

    def _draft_settings(self, draft, sd) -> ScanSettings | None:
        """The settings the stroke's zone has, or would get, on the stack (the Easy rules and the saved rules for a
        new zone), computed once per stroke."""
        owner = draft.target
        st = owner.settings.get(sd.id) if owner is not None else None
        if st is not None:
            return st
        key = (draft.stroke.serial, sd.id)
        if key not in self._draft_st:
            scan = self.session.scan_of(sd.id)
            self._draft_st = {key: self.session.default_settings(scan, sd) if scan is not None else None}
        return self._draft_st[key]

    def preview_draft(self, draft, fast: bool = False) -> None:
        """The live preview of a stroke in progress (the throttle calls it): the traces of its provisional points on the
        current stack only, through the shared trace cache, drawn in place (PreviewPlot.update_traces); fast: without
        the de-spike. The region caption counts what the stroke changes."""
        sd = self.current_stack()
        if sd is None or draft is None:
            return
        self._drafted = True
        pix = np.asarray(draft.members_after, int)
        name = short_time(sd.id) or sd.name
        st = self._draft_settings(draft, sd)
        note = ""
        if st is not None:
            if fast:
                st = fast_settings(st)
            if st.roi and not xw.stack_window_ready(sd, st.roi):
                import copy
                st = copy.deepcopy(st)
                st.roi = None                           # the fixed bins until the windows are read (at release)
                note = f"reading the XRF windows of {sd.name} …"
        if st is None or not pix.size:
            self.preview.set_traces([], waiting=PREVIEW_NO_ZONE)
        elif st.i0_source == "good" and self.session.good_i0_missing():
            pass                                        # the good-I0 scans are read at release (update_preview)
        else:
            good = self.session.good_curve_for(sd.E) if st.i0_source == "good" else None
            t = zone_traces_cached(sd, pix, st, good)
            if t is None:
                self.preview.set_traces([], waiting=PREVIEW_NO_ZONE)
            else:
                self.preview.update_traces([(name, t, {"edge_ev": sd.edge_ev, "edge": sd.edge_text})])
        k, dk = int(pix.size), int(pix.size) - int(draft.members_before.size)
        self.region_caption.setText(f"Region: {shape_text(draft.zone)} · {k} of {sd.n_pix} points of {sd.name}"
                                    + (f" ({dk:+d} while drawing)" if dk else "") + (f" · {note}" if note else ""))
        self.window_card.set_region(pix)
        self.window_card.set_zone(draft.zone)

    def preview_message(self, z, sd) -> str:
        """Why the preview shows no trace (the sentences of the spec): no scan, no zone, an empty zone, a zone outside
        this scan (naming the scans it covers)."""
        from aurora.app.zone_tools import zone_drawn
        if sd is None:
            return PREVIEW_NO_SCAN
        if z is None:
            return PREVIEW_NO_ZONE
        if not zone_drawn(z):
            return f"{zone_name(z)} is empty: drag on the map to draw it"
        if sd.id not in z.settings:
            name = short_time(sd.id) or sd.name
            others = [short_time(s) or split_stack_id(s)[1] for s in z.settings]
            if others:
                return (f"{zone_name(z)} holds no pixel of {name}: it lies outside this scan. Scans it covers: "
                        f"{', '.join(others)} (pick one in step 1)")
            return f"{zone_name(z)} holds no pixel of {name}: it lies outside this scan"
        return ""

    def update_preview(self) -> None:
        z = self.session.zone()
        if z is None:
            self.preview.set_traces([], waiting=PREVIEW_NO_SCAN if self.current_stack() is None and
                                    self.session.project is not None else PREVIEW_NO_ZONE)
            return
        sids = []
        if self.preview.all_scans.isChecked():
            sids = [s for s, st in z.settings.items() if st.include and self.session.stack_loaded(s) is not None]
        elif self.session.current_stack and self.session.current_stack in z.settings:
            sids = [self.session.current_stack]
        else:
            sids = [s for s in z.settings if self.session.stack_loaded(s) is not None][:1]
        traces = []
        waiting = ""
        for sid in sids:
            sd = self.session.stack_loaded(sid)
            if sd is None:
                continue
            st = z.settings[sid]
            if st.roi and not self._ensure_windows(sd, st.roi):
                continue                                # drawn when the windows are read
            if st.i0_source == "good" and not self._ensure_good_i0():
                waiting = "reading the good-I0 scans …"
                continue                                # drawn when they are read
            pix = members(z, sd.x, sd.y, sd.pitch)
            good = self.session.good_curve_for(sd.E) if st.i0_source == "good" else None
            t = zone_traces_cached(sd, pix, st, good)
            if t is not None:
                traces.append((short_time(sid) or sd.name, t, {"edge_ev": sd.edge_ev, "edge": sd.edge_text}))
        if not traces and not waiting:
            sd = self.current_stack()
            st = z.settings.get(sd.id) if sd is not None else None
            if st is not None and st.roi and not xw.stack_window_ready(sd, st.roi):
                waiting = f"reading the XRF windows of {sd.name} …"
            else:
                waiting = self.preview_message(z, sd)
        self.preview.set_traces(traces, waiting=waiting)
