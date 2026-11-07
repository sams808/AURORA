"""Reduce perspective: the zone tool. Stack map with rect / brush zone editing, zone card, per-scan settings, preview."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QSplitter, QToolButton,
                               QVBoxLayout, QWidget)

from aurora.app.mapview import MapView, TOOLS
from aurora.app.preview import PAL, PreviewPlot
from aurora.app.session import Session
from aurora.app.theme import TOKENS
from aurora.app.widgets import Card, Worker
from aurora.core.constants import DETS
from aurora.core.io.config import ScanSettings, Zone
from aurora.core.model import ScanInfo
from aurora.core.zones import NORM_MODES, i0_score, members, zone_traces

NORM_LABELS = {"raw": "raw counts", "i0": "/ I0", "i0pre": "/ I0 − pre-edge line", "i0jump": "/ I0, edge jump = 1",
               "area": "/ I0, area = 1", "max": "/ I0, max = 1"}


class ScanSettingsCard(Card):
    """Per-zone, per-scan settings (the zone tool's scan card)."""

    def __init__(self, session: Session, sid: str, scan: ScanInfo, settings: ScanSettings, lines: list[str], parent=None):
        super().__init__(f"{scan.time}  {scan.label}  ·  {scan.edge_text}", parent)
        self.session, self.sid, self.scan, self.s = session, sid, scan, settings
        self.setObjectName("card")
        flags = []
        if scan.summary.get("sdd_saturated"):
            flags.append("saturated " + ", ".join(scan.summary["sdd_saturated"]).upper())
        if scan.summary.get("i0_bad"):
            flags.append("I0 invalid")
        fl = QLabel(" · ".join(flags) if flags else f"{settings.n} px")
        fl.setStyleSheet(f"color:{TOKENS['warn_fg']}" if flags else f"color:{TOKENS['muted']}")
        self.header.addWidget(fl)
        row1 = QHBoxLayout()
        self.inc = QCheckBox("include"); self.inc.setChecked(settings.include); row1.addWidget(self.inc)
        self.sdd = []
        for i, d in enumerate(DETS):
            c = QCheckBox(d.upper()); c.setChecked(settings.sdd[i]); row1.addWidget(c); self.sdd.append(c)
        row1.addWidget(QLabel("line"))
        self.line = QComboBox(); self.line.addItems(lines)
        self.line.setCurrentText(settings.line if settings.line in lines else (lines[0] if lines else ""))
        row1.addWidget(self.line)
        row1.addStretch(1)
        self.body.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("fluo"))
        self.fn = QComboBox()
        for m in NORM_MODES:
            self.fn.addItem(NORM_LABELS[m], m)
        self.fn.setCurrentIndex(NORM_MODES.index(settings.fluo_norm) if settings.fluo_norm in NORM_MODES else 3)
        row2.addWidget(self.fn)
        self.tey = QCheckBox("TEY"); self.tey.setChecked(settings.tey); row2.addWidget(self.tey)
        self.tn = QComboBox()
        for m in NORM_MODES:
            self.tn.addItem(NORM_LABELS[m], m)
        self.tn.setCurrentIndex(NORM_MODES.index(settings.tey_norm) if settings.tey_norm in NORM_MODES else 3)
        row2.addWidget(self.tn)
        row2.addStretch(1)
        self.body.addLayout(row2)
        row3 = QHBoxLayout()
        self.ds = QCheckBox("de-spike k"); self.ds.setChecked(settings.despike); row3.addWidget(self.ds)
        self.k = QDoubleSpinBox(); self.k.setRange(1, 50); self.k.setValue(settings.k); self.k.setSingleStep(1); row3.addWidget(self.k)
        row3.addWidget(QLabel("I0"))
        self.i0src = QComboBox(); self.i0src.addItem("own", "own"); self.i0src.addItem("good-I0 mean", "good")
        self.i0src.setCurrentIndex(1 if settings.i0_source == "good" else 0); row3.addWidget(self.i0src)
        row3.addWidget(QLabel("pre"))
        self.pre = QLineEdit(self._rng(settings.pre)); self.pre.setPlaceholderText("auto"); self.pre.setMaximumWidth(110); row3.addWidget(self.pre)
        row3.addWidget(QLabel("post"))
        self.post = QLineEdit(self._rng(settings.post)); self.post.setPlaceholderText("auto"); self.post.setMaximumWidth(110); row3.addWidget(self.post)
        row3.addStretch(1)
        self.body.addLayout(row3)
        for w in (self.inc, self.tey, self.ds, *self.sdd):
            w.toggled.connect(self._changed)
        for w in (self.line, self.fn, self.tn, self.i0src):
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

    def _changed(self, *_) -> None:
        s = self.s
        s.include = self.inc.isChecked()
        s.sdd = [c.isChecked() for c in self.sdd]
        s.line = self.line.currentText()
        s.fluo_norm = self.fn.currentData()
        s.tey = self.tey.isChecked()
        s.tey_norm = self.tn.currentData()
        s.despike = self.ds.isChecked()
        s.k = float(self.k.value())
        s.i0_source = self.i0src.currentData()
        s.pre = self._parse(self.pre.text())
        s.post = self._parse(self.post.text())
        self.session.touch()


class ReducePerspective(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._cards: list[ScanSettingsCard] = []
        self._placeholder: QLabel | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)

        head = QHBoxLayout()
        h1 = QLabel("Reduce"); h1.setObjectName("h1")
        sub = QLabel("draw the zones on the stack map, choose per scan what goes into the report"); sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub); head.addStretch(1)
        self.status_label = QLabel(""); self.status_label.setObjectName("muted"); head.addWidget(self.status_label)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)

        # ---- left: scans of the holder + zones
        left = QSplitter(Qt.Vertical)
        sc = Card("Scans of the holder")
        self.scan_list = QListWidget()
        self.scan_list.currentItemChanged.connect(self._scan_picked)
        sc.body.addWidget(self.scan_list, 1)
        left.addWidget(sc)
        zc = Card("Zones")
        zb = QHBoxLayout()
        self.new_zone_btn = QPushButton("New zone"); self.new_zone_btn.clicked.connect(lambda: self.session.new_zone())
        self.del_zone_btn = QPushButton("Delete"); self.del_zone_btn.clicked.connect(self._delete_zone)
        zb.addWidget(self.new_zone_btn); zb.addWidget(self.del_zone_btn); zb.addStretch(1)
        zc.body.addLayout(zb)
        self.zone_list = QListWidget()
        self.zone_list.currentItemChanged.connect(self._zone_picked)
        zc.body.addWidget(self.zone_list, 1)
        left.addWidget(zc)
        split.addWidget(left)

        # ---- centre: stack map with tools
        centre = Card("Stack map")
        tools = QHBoxLayout()
        self.tool_group = QButtonGroup(self)
        for i, t in enumerate(TOOLS):
            b = QToolButton(); b.setText(t); b.setCheckable(True); b.setChecked(i == 1)
            b.setToolTip({"navigate": "zoom / pan", "rect+": "drag a rectangle to add to the zone (a new zone if none is selected)",
                          "rect-": "drag a rectangle to remove from the zone", "brush+": "click / drag to paint pixels",
                          "brush-": "click / drag to erase pixels"}[t])
            self.tool_group.addButton(b, i); tools.addWidget(b)
        self.tool_group.idClicked.connect(lambda i: self.map.set_tool(TOOLS[i]))
        tools.addWidget(QLabel("brush"))
        self.brush_n = QSpinBox(); self.brush_n.setRange(1, 9); self.brush_n.setValue(1); tools.addWidget(self.brush_n)
        self.target_new = QCheckBox("rect+ starts a new zone"); tools.addWidget(self.target_new)
        tools.addStretch(1)
        centre.body.addLayout(tools)
        show = QHBoxLayout()
        show.addWidget(QLabel("show"))
        self.map_line = QComboBox(); self.map_line.setMinimumWidth(90); show.addWidget(self.map_line)
        self.map_sdd = QComboBox(); self.map_sdd.addItems(["sum", "SDD1", "SDD2", "SDD3", "SDD4"]); show.addWidget(self.map_sdd)
        self.map_mode = QComboBox()
        for k, v in (("above", "integrated above edge"), ("all", "all energies"), ("jump", "edge jump"), ("at", "at energy")):
            self.map_mode.addItem(v, k)
        show.addWidget(self.map_mode)
        self.map_energy = QSpinBox(); self.map_energy.setRange(0, 0); self.map_energy.setPrefix("E#"); show.addWidget(self.map_energy)
        self.map_i0 = QCheckBox("/ I0"); self.map_i0.setChecked(True); show.addWidget(self.map_i0)
        self.map_log = QCheckBox("log"); show.addWidget(self.map_log)
        show.addStretch(1)
        centre.body.addLayout(show)
        self.map = MapView()
        self.map.set_tool("rect+")
        self.map.rect_drawn.connect(self._rect)
        self.map.brush_at.connect(self._brush)
        self.map.hovered.connect(self._hover)
        centre.body.addWidget(self.map, 1)
        self.map_hint = QLabel(""); self.map_hint.setObjectName("muted")
        centre.body.addWidget(self.map_hint)
        split.addWidget(centre)

        # ---- right: zone card, per-scan settings, preview
        right = QSplitter(Qt.Vertical)
        self.zone_card = Card("Zone")
        zr = QHBoxLayout()
        zr.addWidget(QLabel("label"))
        self.z_label = QComboBox(); self.z_label.setEditable(True); self.z_label.setMinimumWidth(160)
        zr.addWidget(self.z_label, 1)
        zr.addWidget(QLabel("group"))
        self.z_group = QComboBox(); self.z_group.setEditable(True); zr.addWidget(self.z_group, 1)
        self.zone_card.body.addLayout(zr)
        zr2 = QHBoxLayout()
        zr2.addWidget(QLabel("comment"))
        self.z_comment = QLineEdit(); zr2.addWidget(self.z_comment, 1)
        self.zone_card.body.addLayout(zr2)
        self.z_info = QLabel(""); self.z_info.setObjectName("muted"); self.z_info.setWordWrap(True)
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
        self.cards_box = QWidget(); self.cards_lay = QVBoxLayout(self.cards_box); self.cards_lay.setContentsMargins(0, 0, 0, 0)
        self.cards_lay.addStretch(1)
        self.cards_area.setWidget(self.cards_box)
        right.addWidget(self.cards_area)
        pc = Card("Preview")
        self.preview = PreviewPlot()
        self.preview.all_scans.toggled.connect(self.update_preview)
        pc.body.addWidget(self.preview, 1)
        right.addWidget(pc)
        self.cards_area.setMinimumHeight(170)
        pc.setMinimumHeight(260)
        right.setStretchFactor(0, 0); right.setStretchFactor(1, 2); right.setStretchFactor(2, 3)
        right.setSizes([150, 230, 420])
        split.addWidget(right)
        left.setMinimumWidth(250)
        split.setStretchFactor(0, 0); split.setStretchFactor(1, 3); split.setStretchFactor(2, 3)
        split.setSizes([270, 660, 560])
        root.addWidget(split, 1)

        for w in (self.map_line, self.map_sdd, self.map_mode):
            w.currentIndexChanged.connect(self.redraw_map)
        self.map_energy.valueChanged.connect(self.redraw_map)
        self.map_i0.toggled.connect(self.redraw_map)
        self.map_log.toggled.connect(self.redraw_map)

        session.holder_changed.connect(self._holder_changed)
        session.zones_changed.connect(self._zones_changed)
        session.zone_selected.connect(self._zone_selected)
        session.stack_selected.connect(self._stack_selected)

    # ------------------------------------------------------------------ holder / scans
    def _holder_changed(self, key: str) -> None:
        self.scan_list.blockSignals(True)
        self.scan_list.clear()
        for s in self.session.stacks_of(key):
            flags = ", ".join(s.flags)
            it = QListWidgetItem(f"{s.time}  {s.label}  ·  {s.edge_text}  {s.energy_text}" + (f"   [{flags}]" if flags else ""))
            it.setData(Qt.UserRole, self.session.stack_id(s))
            if flags:
                it.setForeground(Qt.darkYellow)
            self.scan_list.addItem(it)
        self.scan_list.blockSignals(False)
        self._fill_zone_list()
        self.map.clear_overlays(); self.map.set_grid(None)
        self._clear_cards(); self.preview.set_traces([])
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
        if self._worker is not None and self._worker.isRunning():
            return
        self.status_label.setText(f"reading {sid[11:]} …")
        self._worker = Worker(lambda cb: self.session.stack(sid, progress=cb), self)
        self._worker.progress.connect(lambda i, n, m: self.status_label.setText(f"{m}  ({i}/{n})"), Qt.QueuedConnection)
        self._worker.done.connect(lambda sd, sid=sid: self._loaded(sid), Qt.QueuedConnection)
        self._worker.failed.connect(lambda t: self.status_label.setText("could not read the stack: " + t.strip().splitlines()[-1]), Qt.QueuedConnection)
        self._worker.start()

    def _loaded(self, sid: str) -> None:
        self._worker.wait(30000)
        self.status_label.setText("")
        self.session.touch()                      # memberships for the newly loaded stack
        cur = self.scan_list.currentItem()
        if cur is not None and cur.data(Qt.UserRole) == sid:        # still the scan the user wants to see
            self.session.select_stack(sid)

    def _stack_selected(self, sd) -> None:
        self.map_line.blockSignals(True)
        self.map_line.clear()
        if sd is not None:
            self.map_line.addItems(sd.labels)
            self.map_line.setCurrentText(sd.main)
            self.map_energy.setRange(0, max(sd.n_energies - 1, 0))
        self.map_line.blockSignals(False)
        self.redraw_map()
        if sd is not None:
            self.map.autorange()
        self._rebuild_cards()
        self.update_preview()

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
            self.map.draw_zone(z, PAL[i % len(PAL)], selected=sel)
            if sel:
                pix = members(z, sd.x, sd.y, sd.pitch)
                self.map.draw_pixels(sd.x[pix], sd.y[pix], sd.pitch, PAL[i % len(PAL)])
        self.map_hint.setText(f"{sd.name}: {sd.n_energies} energies x {sd.n_pix} px, pitch {sd.pitch[0]:.3f} x {sd.pitch[1]:.3f} mm, "
                              f"main line {sd.main}" + (f", edge {sd.edge_text} {sd.edge_ev:.1f} eV" if sd.edge_ev else "") + f" · {sd.source}")

    def _hover(self, x: float, y: float) -> None:
        sd = self.session.stack_loaded(self.session.current_stack) if self.session.current_stack else None
        txt = f"x {x:.2f} mm, y {y:.2f} mm"
        if sd is not None:
            px, py = sd.pitch
            hit = np.where((np.abs(sd.x - x) <= px / 2) & (np.abs(sd.y - y) <= py / 2))[0]
            if hit.size:
                txt += f" · px {hit[0]}"
        self.status_label.setText(txt)

    def _target_zone(self, create: bool) -> Zone | None:
        z = self.session.zone()
        if z is None or (create and self.target_new.isChecked()):
            z = self.session.new_zone() if create else None
        return z

    def _rect(self, x0: float, x1: float, y0: float, y1: float) -> None:
        tool = self.map.vb.tool
        if tool == "rect+":
            z = self._target_zone(True)
            z.rects.append([x0, x1, y0, y1])
        elif tool == "rect-":
            z = self._target_zone(False)
            if z is None:
                self.status_label.setText("select a zone first")
                return
            z.erects.append([x0, x1, y0, y1])
        else:
            return
        self.session.touch()

    def _brush(self, x: float, y: float, add: bool) -> None:
        sd = self.session.stack_loaded(self.session.current_stack) if self.session.current_stack else None
        if sd is None:
            return
        z = self._target_zone(add)
        if z is None:
            self.status_label.setText("select a zone first")
            return
        best = int(np.argmin((sd.x - x) ** 2 + (sd.y - y) ** 2))
        cx, cy = float(sd.x[best]), float(sd.y[best])
        px, py = sd.pitch
        n = self.brush_n.value()
        key = z.paint if add else z.erase
        for i in range(-((n - 1) // 2), n // 2 + 1):
            for j in range(-((n - 1) // 2), n // 2 + 1):
                sx, sy = round(cx + i * px, 3), round(cy + j * py, 3)
                if not any(abs(s[0] - sx) < 1e-6 and abs(s[1] - sy) < 1e-6 for s in key):
                    key.append([sx, sy, px, py])
                if add:
                    z.erase = [s for s in z.erase if not (abs(s[0] - sx) < 1e-6 and abs(s[1] - sy) < 1e-6)]
        self.session.touch()

    # ------------------------------------------------------------------ zones
    def _fill_zone_list(self) -> None:
        hz = self.session.holder_zones(create=False)
        self.zone_list.blockSignals(True)
        self.zone_list.clear()
        for z in (hz.zones if hz else []):
            n = sum(1 for s in z.settings.values() if s.include)
            it = QListWidgetItem(f"{z.id}  {z.label}" + (f"  ({z.group})" if z.group else "") + f"  · {n} scans")
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

    def _zones_changed(self) -> None:
        self._fill_zone_list(); self.redraw_map()
        z = self.session.zone()
        have = [c.sid for c in self._cards]
        want = [self.session.stack_id(s) for s in self.session.stacks_of()
                if z is not None and self.session.stack_id(s) in z.settings and self.session.stack_loaded(self.session.stack_id(s))]
        if have != want:
            self._rebuild_cards()
        self.update_preview()

    def _zone_selected(self, z: Zone | None) -> None:
        self._fill_zone_list()
        self._fill_zone_card(z)
        self.redraw_map(); self._rebuild_cards(); self.update_preview()

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
            self.z_info.setText(f"zone {z.id}: {len(z.rects)} rect, {len(z.erects)} erase rect, {len(z.paint)} painted px, "
                                f"{len(z.erase)} erased px · covered by {len(z.members)} loaded scan(s)")
        for w in (self.z_label, self.z_group, self.z_comment):
            w.blockSignals(False)

    def _zone_edited(self) -> None:
        z = self.session.zone()
        if z is None:
            return
        z.label = self.z_label.currentText().strip() or z.label
        z.group = self.z_group.currentText().strip()
        z.comment = self.z_comment.text().strip()
        if not self.z_group.currentText().strip() and z.label:
            m = next((r for r in self.session.sheet_rows if r.sample_id and r.sample_id.lower() in z.label.lower()), None)
            if m:
                z.group = m.group
                self.z_group.setEditText(z.group)
        self.session.touch()

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
        for scan in self.session.stacks_of():
            sid = self.session.stack_id(scan)
            st = z.settings.get(sid)
            sd = self.session.stack_loaded(sid)
            if st is None or sd is None:
                continue
            card = ScanSettingsCard(self.session, sid, scan, st, sd.labels)
            self.cards_lay.insertWidget(self.cards_lay.count() - 1, card)
            self._cards.append(card)
        if not self._cards:
            lab = QLabel("no loaded scan covers this zone (pick the scans in the list to load them)")
            lab.setObjectName("muted")
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
            label = f"{z.label} · {self.session.holder().name if self.session.holder() else ''} {sid[11:16]}"
            n += len(self.library.add_traces(label, t.E, t.sum, t.tey if st.tey else None, source=f"zone {z.id} of {sid}"))
        self.status_label.setText(f"{n} traces sent to Compare" if n else "nothing to send: load the zone's scans first")
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
                traces.append((sid[11:16], t, {"edge_ev": sd.edge_ev, "edge": sd.edge_text}))
        self.preview.set_traces(traces)
