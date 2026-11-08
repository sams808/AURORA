"""Reduce perspective: the zone tool. Stack map with rect / brush zone editing, zone card, per-scan settings, preview."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QSplitter,
                               QToolButton, QVBoxLayout, QWidget)

from aurora.app import jobs, nav
from aurora.app.help_text import explain, explain_all, labelled
from aurora.app.mapview import MapView, TOOLS
from aurora.app.mode import mark_advanced
from aurora.app.preview import PreviewPlot
from aurora.app.session import Session
from aurora.app.theme import curve, on_theme_changed, refill_tones, set_item_tone
from aurora.app.vocab import DET_LABELS, NORM_LABELS, det_items, plural, unit_suffix
from aurora.app.widgets import Card, ElidedLabel, EmptyState, FlowLayout, TaskBar, overlay_host, pair, set_enabled
from aurora.core.io.config import ScanSettings, Zone
from aurora.core.model import ScanInfo
from aurora.core.names import short_time, split_stack_id
from aurora.core.spectra import NORM_MODES
from aurora.core.zones import members, zone_traces

NO_PROJECT = "open a project first"


class _VirtualScan:
    """Minimal ScanInfo stand-in for a stitched stack."""

    def __init__(self, sd):
        self.time, self.label, self.edge_text, self.summary, self.name = "stitch", sd.name, sd.edge_text, {}, sd.name


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
    """Per-zone, per-scan settings (the zone tool's scan card), in a two-column grid that fits 420 px."""

    SIGNALS = (("pfy", "PFY"), ("ipfy", "IPFY"))

    def __init__(self, session: Session, sid: str, scan: ScanInfo, settings: ScanSettings, lines: list[str], parent=None,
                 zone_id: int | None = None):
        super().__init__(f"{scan.time}  {scan.label}  ·  {scan.edge_text}", parent, help_anchor="45-one-card-per-attached-scan")
        self.session, self.sid, self.scan, self.s = session, sid, scan, settings
        self.zone_id = zone_id
        self.setObjectName("card")
        flags = []
        if scan.summary.get("sdd_saturated"):
            flags.append("saturated " + ", ".join(scan.summary["sdd_saturated"]).upper())
        if scan.summary.get("i0_bad"):
            flags.append("I0 invalid")
        fl = ElidedLabel(" · ".join(flags) if flags else plural(settings.n, "px"), Qt.ElideRight)
        fl.setProperty("tone", "warn" if flags else "muted")
        self.header.addWidget(fl)
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
        self.line.setCurrentText(settings.line if settings.line in lines else (lines[0] if lines else ""))
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
        explain_all([(self.inc, "reduce.include"), (self.tey, "reduce.tey"), (self.tn, "reduce.tey_norm"),
                     (self.ds, "reduce.despike"), (self.k, "reduce.k")] + [(c, "reduce.sdd") for c in self.sdd])
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
        if all(getattr(self.s, k) == v for k, v in new.items()):
            return                              # a focus-out with nothing changed (e.g. hidden by a mode switch) writes nothing
        zid = self.zone_id if self.zone_id is not None else self.session.current_zone
        what = short_time(self.sid) or self.scan.name
        with self.session.edit(f"change the settings of {what} in zone {zid}"):
            for k, v in new.items():
                setattr(self.s, k, v)


class ReducePerspective(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._cards: list[ScanSettingsCard] = []
        self._placeholder: QLabel | None = None
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

        # ---- left: scans of the holder + zones
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
        zc = Card("Zones", help_anchor="43-zones")
        zb = QGridLayout()                               # 2 x 2: never clipped in the narrow left column
        zb.setSpacing(6)
        self.new_zone_btn = QPushButton("New sample"); self.new_zone_btn.clicked.connect(self._new_zone)
        self.del_zone_btn = QPushButton("Delete"); self.del_zone_btn.clicked.connect(self._delete_zone)
        self.propose_btn = QPushButton("Propose zones")         # no dialog: no ellipsis
        self.propose_btn.clicked.connect(lambda: self.propose_zones())
        self.stitch_btn = QPushButton("Stitch…"); self.stitch_btn.clicked.connect(self.stitch_tiles)
        for i, b in enumerate((self.new_zone_btn, self.del_zone_btn, self.propose_btn, self.stitch_btn)):
            zb.addWidget(b, i // 2, i % 2)
        zc.body.addLayout(zb)
        self.zone_list = QListWidget()
        self.zone_list.setTextElideMode(Qt.ElideRight)
        self.zone_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.zone_list.setAccessibleName("zones of the holder")
        self.zone_list.currentItemChanged.connect(self._zone_picked)
        zc.body.addWidget(self.zone_list, 1)
        left.addWidget(zc)
        split.addWidget(left)

        # ---- centre: stack map with tools
        centre = Card("Stack map", help_anchor="42-stack-map")
        tools_box = QWidget()
        tools = FlowLayout(tools_box, spacing=6)
        self.tool_group = QButtonGroup(self)
        self.tool_buttons: dict[str, QToolButton] = {}
        for i, t in enumerate(TOOLS):
            b = QToolButton(); b.setText(t); b.setCheckable(True); b.setChecked(i == 1)
            explain(b, f"reduce.tool.{t}")
            self.tool_group.addButton(b, i); tools.addWidget(b)
            self.tool_buttons[t] = b
        self.tool_group.idClicked.connect(lambda i: self.map.set_tool(TOOLS[i]))
        self.brush_box = QWidget()                      # brush size and 'rect+ starts a new zone': Advanced
        bl = QHBoxLayout(self.brush_box); bl.setContentsMargins(0, 0, 0, 0)
        self.brush_n = QSpinBox(); self.brush_n.setRange(1, 9); self.brush_n.setValue(1)
        self.brush_n.setSuffix(unit_suffix("pixels"))
        bl.addWidget(pair(labelled("brush", self.brush_n, "reduce.brush"), self.brush_n))
        self.target_new = QCheckBox("rect+ starts a new zone"); bl.addWidget(self.target_new)
        tools.addWidget(self.brush_box)
        centre.body.addWidget(tools_box)
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
        self.map = MapView()
        self.map.setAccessibleName("stack map")
        self.map.set_tool("rect+")
        self.map.rect_drawn.connect(self._rect)
        self.map.brush_at.connect(self._brush)
        self.map.hovered.connect(self._hover)
        centre.body.addWidget(self.map, 1)
        self.map_hint = ElidedLabel("", Qt.ElideMiddle); self.map_hint.setObjectName("muted")
        centre.body.addWidget(self.map_hint)
        split.addWidget(centre)

        # ---- right: zone card, per-scan settings, preview
        right = QSplitter(Qt.Vertical)
        self.zone_card = Card("Zone", help_anchor="44-the-zone-card")
        zr = QHBoxLayout()
        self.z_label = QComboBox(); self.z_label.setEditable(True); self.z_label.setMinimumWidth(140)
        zr.addWidget(labelled("label", self.z_label, "reduce.label")); zr.addWidget(self.z_label, 1)
        self.zone_card.body.addLayout(zr)
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
        zb2 = QHBoxLayout()
        self.send_btn = QPushButton("Send the zone's traces to Compare")
        self.send_btn.clicked.connect(self.send_to_compare)
        zb2.addWidget(self.send_btn); zb2.addStretch(1)
        self.zone_card.body.addLayout(zb2)
        self.library = None
        self.z_label.lineEdit().editingFinished.connect(self._zone_edited)
        self.z_group.lineEdit().editingFinished.connect(self._zone_edited)
        self.z_comment.editingFinished.connect(self._zone_edited)
        right.addWidget(self.zone_card)
        self.cards_area = QScrollArea(); self.cards_area.setWidgetResizable(True)
        self.cards_area.setAccessibleName("settings of each scan of the zone")
        self.cards_box = QWidget(); self.cards_lay = QVBoxLayout(self.cards_box); self.cards_lay.setContentsMargins(0, 0, 0, 0)
        self.cards_lay.addStretch(1)
        self.cards_area.setWidget(self.cards_box)
        right.addWidget(self.cards_area)
        self.preview_card = pc = Card("Preview", help_anchor="47-preview")
        self.preview = PreviewPlot()
        self.preview.all_scans.toggled.connect(self.update_preview)
        pc.body.addWidget(self.preview, 1)
        right.addWidget(pc)
        self.cards_area.setMinimumHeight(120)
        pc.setMinimumHeight(200)
        right.setStretchFactor(0, 0); right.setStretchFactor(1, 2); right.setStretchFactor(2, 3)
        right.setSizes([190, 230, 420])
        split.addWidget(right)
        left.setMinimumWidth(250)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 2); split.setStretchFactor(2, 3)
        split.setSizes([260, 560, 640])                 # the scan cards need about 420 px; the map is square
        self.body = overlay_host(split)
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
        self.zone_state = EmptyState("No sample yet: drag a rectangle over the stack with rect+.",
                                     [("Propose zones", lambda: self.propose_zones(), True)], icon="reduce", parent=pc)
        explain_all([(self.load_all_btn, "reduce.load_all"), (self.scan_list, "reduce.scans"),
                     (self.new_zone_btn, "reduce.new_zone"), (self.del_zone_btn, "reduce.delete_zone"),
                     (self.propose_btn, "reduce.propose"), (self.stitch_btn, "reduce.stitch"),
                     (self.target_new, "reduce.target_new"), (self.map_sdd, "reduce.map_sdd"),
                     (self.map_mode, "reduce.map_mode"), (self.map_energy, "reduce.map_energy"),
                     (self.map_i0, "reduce.map_i0"), (self.map_log, "reduce.map_log"), (self.send_btn, "reduce.send")])
        session.project_changed.connect(lambda _p: self.update_actions())
        session.holder_changed.connect(self._holder_changed)
        session.zones_changed.connect(self._zones_changed)
        session.zone_selected.connect(self._zone_selected)
        session.stack_selected.connect(self._stack_selected)
        on_theme_changed(self, lambda _s: (self.redraw_map(), refill_tones(self.scan_list)))
        # Easy mode: John Bussey's basic path (scan, region, preview); everything else is Advanced
        mark_advanced(self.load_all_btn, self.propose_btn, self.stitch_btn, self.tool_buttons["rect-"],
                      self.tool_buttons["brush+"], self.tool_buttons["brush-"], self.brush_box, self.show_row,
                      self.group_box, self.comment_box, self.send_btn, self.cards_area)
        self.update_actions()

    # ------------------------------------------------------------------ actions and states
    def update_actions(self) -> None:
        ses = self.session
        has = ses.project is not None
        h = ses.holder()
        z = ses.zone() if has else None
        loaded = [s for s in ses.stacks_of() if ses.stack_loaded(ses.stack_id(s)) is not None] if has else []
        set_enabled(self.new_zone_btn, has and h is not None, NO_PROJECT if not has else "this project has no holder")
        set_enabled(self.del_zone_btn, z is not None, NO_PROJECT if not has else "select a zone first")
        set_enabled(self.send_btn, z is not None and self.library is not None and bool(z.settings),
                    NO_PROJECT if not has else ("select a zone first" if z is None else "load a scan that covers the zone"))
        set_enabled(self.load_all_btn, has and bool(ses.stacks_of()), NO_PROJECT if not has else "the holder has no complete stack")
        set_enabled(self.propose_btn, has and h is not None, NO_PROJECT)
        set_enabled(self.stitch_btn, len(loaded) >= 2,
                    NO_PROJECT if not has else "load at least two stacks of the holder first (Load all)")
        self.scans_card.set_title(f"Scans of holder {h.name}" if h else "Scans of the holder")
        self.empty_state.set_shown(not has)
        hz = ses.holder_zones(create=False) if has else None
        self.zone_state.set_shown(has and h is not None and not (hz and hz.zones))

    def _new_zone(self) -> None:
        if self.session.new_zone() is None:
            self.status_label.setText(NO_PROJECT)

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
            for i, sid in enumerate(sids):
                cb(i, len(sids), f"reading {split_stack_id(sid)[1]}")
                try:
                    session.stack(sid)
                except Exception as err:
                    cb(i, len(sids), f"{split_stack_id(sid)[1]}: {err}")
            return sids

        self.status_label.setText(f"reading {plural(len(sids), 'scan')} …")
        jobs.of(self).submit(f"stacks:{self.session.current_holder}", job, on_done=self._all_loaded,
                             on_failed=lambda t: self.status_label.setText("could not read: " + t.strip().splitlines()[-1]),
                             on_end=lambda _s: self.update_actions(), label="reading the scans of the holder",
                             taskbar=self.taskbar)

    def _all_loaded(self, sids) -> None:
        n = sum(1 for s in sids if self.session.stack_loaded(s) is not None)
        self.status_label.setText(f"{n} of {plural(len(sids), 'scan')} read")
        self.session.touch()
        cur = self.scan_list.currentItem()
        if cur is not None and self.session.current_stack is None:
            self.session.select_stack(cur.data(Qt.UserRole))
        else:
            self._rebuild_cards(); self.update_preview()

    def _loaded(self, sid: str) -> None:
        self.status_label.setText("")
        self.session.touch()                      # memberships for the newly loaded stack
        cur = self.scan_list.currentItem()
        if cur is not None and cur.data(Qt.UserRole) == sid:        # still the scan the user wants to see
            self.session.select_stack(sid)

    def _stack_selected(self, sd) -> None:
        self.map_line.blockSignals(True)
        self.map_energy.blockSignals(True)
        self.map_line.clear()
        if sd is not None:
            self.map_line.addItems(sd.labels)
            self.map_line.setCurrentText(sd.main)
            self.map_energy.set_energies(sd.E)
        self.map_line.blockSignals(False)
        self.map_energy.blockSignals(False)
        self.redraw_map()
        if sd is not None:
            self.map.autorange()
        self._rebuild_cards()
        self.update_preview()
        self.update_actions()

    # ------------------------------------------------------------------ map
    def redraw_map(self) -> None:
        sd = self.session.stack_loaded(self.session.current_stack) if self.session.current_stack else None
        self.map.clear_overlays()
        if sd is None:
            self.map.set_grid(None)
            return
        label = self.map_line.currentText() or sd.main
        det = None if self.map_sdd.currentIndex() == 0 else self.map_sdd.currentIndex() - 1
        vals = sd.map_values(label, det=det, mode=self.map_mode.currentData(), energy_index=self.map_energy.value(),
                             per_i0=self.map_i0.isChecked())
        if self.map_log.isChecked():
            with np.errstate(all="ignore"):
                vals = np.log10(np.maximum(vals, 0) + 1.0)
        from aurora.core.grid import to_grid
        self.map.set_grid(to_grid(sd.x, sd.y, vals))
        hz = self.session.holder_zones(create=False)
        for i, z in enumerate(hz.zones if hz else []):
            sel = z.id == self.session.current_zone
            self.map.draw_zone(z, curve(i), selected=sel)
            if sel:
                pix = members(z, sd.x, sd.y, sd.pitch)
                self.map.draw_pixels(sd.x[pix], sd.y[pix], sd.pitch, curve(i))
        self.map_hint.setText(f"{sd.name}: {plural(sd.n_energies, 'energy')} x {sd.n_pix} px, pitch {sd.pitch[0]:.3f} x "
                              f"{sd.pitch[1]:.3f} mm, main line {sd.main}"
                              + (f", edge {sd.edge_text} {sd.edge_ev:.1f} eV" if sd.edge_ev else "") + f" · {sd.source}")

    def _hover(self, x: float, y: float) -> None:
        sd = self.session.stack_loaded(self.session.current_stack) if self.session.current_stack else None
        txt = f"x {x:.2f} mm, y {y:.2f} mm"
        if sd is not None:
            px, py = sd.pitch
            hit = np.where((np.abs(sd.x - x) <= px / 2) & (np.abs(sd.y - y) <= py / 2))[0]
            if hit.size:
                txt += f" · px {hit[0]}"
        self.status_label.setText(txt)

    def _makes_new_zone(self, add: bool) -> bool:
        """Whether an adding tool starts a new zone: no zone selected, or 'rect+ starts a new zone' ticked."""
        return add and (self.session.zone() is None or self.target_new.isChecked())

    def _rect(self, x0: float, x1: float, y0: float, y1: float) -> None:
        tool = self.map.vb.tool
        if self.session.holder() is None:
            self.status_label.setText(NO_PROJECT)
            return
        if tool == "rect+":
            new = self._makes_new_zone(True)
            z = self.session.zone()
            with self.session.edit("add a rectangle to a new zone" if new else f"add a rectangle to zone {z.id}"):
                if new:
                    z = self.session.new_zone()
                if z is not None:
                    z.rects.append([x0, x1, y0, y1])
        elif tool == "rect-":
            z = self.session.zone()
            if z is None:
                self.status_label.setText("select a zone first")
                return
            with self.session.edit(f"remove a rectangle from zone {z.id}"):
                z.erects.append([x0, x1, y0, y1])

    def _brush(self, x: float, y: float, add: bool) -> None:
        sd = self.session.stack_loaded(self.session.current_stack) if self.session.current_stack else None
        if sd is None:
            return
        new = self._makes_new_zone(add)
        z = self.session.zone()
        if z is None and not new:
            self.status_label.setText("select a zone first")
            return
        best = int(np.argmin((sd.x - x) ** 2 + (sd.y - y) ** 2))
        cx, cy = float(sd.x[best]), float(sd.y[best])
        px, py = sd.pitch
        n = self.brush_n.value()
        verb = "paint" if add else "erase"
        with self.session.edit(f"{verb} pixels in a new zone" if new else f"{verb} pixels in zone {z.id}"):
            if new:
                z = self.session.new_zone()
                if z is None:
                    return
            key = z.paint if add else z.erase
            for i in range(-((n - 1) // 2), n // 2 + 1):
                for j in range(-((n - 1) // 2), n // 2 + 1):
                    sx, sy = round(cx + i * px, 3), round(cy + j * py, 3)
                    if not any(abs(s[0] - sx) < 1e-6 and abs(s[1] - sy) < 1e-6 for s in key):
                        key.append([sx, sy, px, py])
                    if add:
                        z.erase = [s for s in z.erase if not (abs(s[0] - sx) < 1e-6 and abs(s[1] - sy) < 1e-6)]

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
        self._fill_zone_list(); self.redraw_map()
        z = self.session.zone()
        have = [c.sid for c in self._cards]
        want = [sid for sid in (z.settings if z is not None else {}) if self.session.stack_loaded(sid) is not None]
        if have != want:
            self._rebuild_cards()
        self.update_preview()
        self.update_actions()

    def _zone_selected(self, z: Zone | None) -> None:
        self._fill_zone_list()
        self._fill_zone_card(z)
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
            self.z_info.setText("no zone selected: draw a rectangle with rect+ to create one")
        else:
            self.z_label.setEditText(z.label); self.z_group.setEditText(z.group); self.z_comment.setText(z.comment)
            self.z_info.setText(f"zone {z.id}: {plural(len(z.rects), 'rect')}, {plural(len(z.erects), 'erase rect')}, "
                                f"{len(z.paint)} painted px, {len(z.erase)} erased px · covered by "
                                f"{plural(len(z.members), 'loaded scan')}")
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
            self.cards_lay.insertWidget(self.cards_lay.count() - 1, card)
            self._cards.append(card)
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
        for sid, st in z.settings.items():
            sd = self.session.stack_loaded(sid)
            if sd is None or not st.include:
                continue
            pix = members(z, sd.x, sd.y, sd.pitch)
            good = self.session.good_curve_for(sd.E) if st.i0_source == "good" else None
            t = zone_traces(sd, pix, st, good)
            if t is None:
                continue
            label = f"{z.label} · {self.session.holder().name if self.session.holder() else ''} {short_time(sid) or sd.name}"
            n += len(self.library.add_traces(label, t.E, t.sum, t.tey if st.tey else None, source=f"zone {z.id} of {sid}"))
        self.status_label.setText(f"{plural(n, 'trace')} sent to Compare" if n else "nothing to send: load the zone's scans first")
        return n

    # ------------------------------------------------------------------ preview
    def update_preview(self) -> None:
        z = self.session.zone()
        if z is None:
            self.preview.set_traces([])
            return
        sids = []
        if self.preview.all_scans.isChecked():
            sids = [s for s, st in z.settings.items() if st.include and self.session.stack_loaded(s) is not None]
        elif self.session.current_stack and self.session.current_stack in z.settings:
            sids = [self.session.current_stack]
        else:
            sids = [s for s in z.settings if self.session.stack_loaded(s) is not None][:1]
        traces = []
        for sid in sids:
            sd = self.session.stack_loaded(sid)
            if sd is None:
                continue
            st = z.settings[sid]
            pix = members(z, sd.x, sd.y, sd.pitch)
            good = self.session.good_curve_for(sd.E) if st.i0_source == "good" else None
            t = zone_traces(sd, pix, st, good)
            if t is not None:
                traces.append((short_time(sid) or sd.name, t, {"edge_ev": sd.edge_ev, "edge": sd.edge_text}))
        self.preview.set_traces(traces)
