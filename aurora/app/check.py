"""Check perspective: per-scan quality (count rate, saturation, I0 validity), the good-I0 selection for the holder,
and the agreement of the four detectors on the selected zone."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QListWidget, QListWidgetItem, QProgressBar,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from aurora.app.mode import mark_advanced
from aurora.app.session import Session
from aurora.app.theme import (curve, dash, make_plot, on_theme_changed, pc, refill_tones, role_pen, set_item_tone,
                              status_text)
from aurora.app.widgets import Card, Worker
from aurora.core.constants import DETS
from aurora.core.qc import (CountRate, count_rate_probe, day_reference, i0_candidates, i0_check, saturation_check,
                            sdd_consistency, spike_summary)
from aurora.core.zones import members, zone_traces

COLS = ("Time", "Scan", "Edge", "Status", "I0 level", "I0", "SDD1 kcps", "SDD2 kcps", "SDD3 kcps", "SDD4 kcps", "Saturated", "High rate", "Notes")


class CheckPerspective(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._rates: dict[str, CountRate] = {}
        self._results: dict[str, dict] = {}
        self._x_curves = None                 # last I0-explorer read (scan name, curves), redrawn on a theme change
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Check"); h1.setObjectName("h1")
        sub = QLabel("count rate, saturation, I0 validity per scan · good I0 for the holder · the four detectors on a zone")
        sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub); head.addStretch(1)
        self.run_btn = QPushButton("Run the checks on this holder"); self.run_btn.setObjectName("primary")
        self.run_btn.clicked.connect(self.run_checks)
        head.addWidget(self.run_btn)
        root.addLayout(head)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        root.addWidget(self.progress)

        split = QSplitter(Qt.Vertical)
        top = Card("Scans of the holder")
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(len(COLS) - 1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.itemSelectionChanged.connect(self._row_selected)
        top.body.addWidget(self.table, 1)
        self.day_label = QLabel(""); self.day_label.setObjectName("muted")
        top.body.addWidget(self.day_label)
        split.addWidget(top)

        bottom = QSplitter(Qt.Horizontal)
        i0c = Card("I0 of the holder's scans (per-pixel mean)")
        opts = QHBoxLayout()
        self.i0_norm = QCheckBox("normalise to the level"); self.i0_norm.setChecked(True); self.i0_norm.toggled.connect(self._draw_i0)
        self.auto_btn = QPushButton("Auto-select good I0"); self.auto_btn.clicked.connect(self._auto_select)
        opts.addWidget(self.i0_norm); opts.addStretch(1); opts.addWidget(self.auto_btn)
        i0c.body.addLayout(opts)
        self.i0_list = QListWidget()
        self.i0_list.itemChanged.connect(self._good_toggled)
        i0c.body.addWidget(self.i0_list, 1)
        self.i0_plot = make_plot(x="energy (eV)", y="I0")
        self.i0_plot.getViewBox().disableAutoRange()
        i0c.body.addWidget(self.i0_plot, 2)
        bottom.addWidget(i0c)
        sddc = Card("The four detectors on the selected zone")
        self.sdd_note = QLabel("select a zone in Reduce and a scan above"); self.sdd_note.setObjectName("muted"); self.sdd_note.setWordWrap(True)
        sddc.body.addWidget(self.sdd_note)
        self.sdd_plot = make_plot(x="energy (eV)", y="normalised (own range)", legend="inside")
        self.sdd_plot.getViewBox().disableAutoRange()
        sddc.body.addWidget(self.sdd_plot, 1)
        self.spike_note = QLabel(""); self.spike_note.setObjectName("muted"); self.spike_note.setWordWrap(True)
        sddc.body.addWidget(self.spike_note)
        bottom.addWidget(sddc)
        i0x = Card("I0 explorer: mesh vs photodiode (any stack, incl. the photodiode tests)")
        xrow = QHBoxLayout()
        self.x_scan = QComboBox(); xrow.addWidget(self.x_scan, 1)
        xb = QPushButton("Read"); xb.clicked.connect(self.explore_i0); xrow.addWidget(xb)
        i0x.body.addLayout(xrow)
        self.x_plot = make_plot(x="energy (eV)", y="normalised to the median", legend="inside")
        self.x_plot.getViewBox().disableAutoRange()
        i0x.body.addWidget(self.x_plot, 1)
        self.x_note = QLabel(""); self.x_note.setObjectName("muted"); self.x_note.setWordWrap(True); i0x.body.addWidget(self.x_note)
        bottom.addWidget(i0x)
        split.addWidget(bottom)
        split.setSizes([330, 520])
        root.addWidget(split, 1)
        session.holder_changed.connect(self._holder_changed)
        session.zone_selected.connect(lambda z: self._update_sdd())
        session.stack_selected.connect(lambda sd: self._update_sdd())
        session.zones_changed.connect(self._update_sdd)
        on_theme_changed(self, lambda _s: self._theme_changed())
        # Easy mode (until the 'Spectra to check' queue lands): the checks of the holder and the four-detector card;
        # the good-I0 picker and the I0 explorer are Advanced
        self.i0_card, self.sdd_card, self.explorer_card = i0c, sddc, i0x
        mark_advanced(self.i0_card, self.explorer_card)

    def _theme_changed(self) -> None:
        refill_tones(self.table)
        refill_tones(self.i0_list)
        self._draw_i0()
        self._update_sdd()
        self._draw_x()

    # ------------------------------------------------------------------ run
    def _holder_changed(self, key: str) -> None:
        self._results.clear()
        self._fill_table()
        self._fill_i0()
        self.x_scan.clear()
        h = self.session.holder(key)
        for s in (h.stacks + h.dropped) if h else []:
            if s.kind == "stack" and s.n_energies > 1:
                self.x_scan.addItem(f"{s.time} {s.label} · {s.status}", s.name)

    def explore_i0(self) -> None:
        name = self.x_scan.currentData()
        scan = self.session.project.find(name) if (name and self.session.project) else None
        if scan is None:
            return
        from aurora.core.qc import mcc_curves
        c = mcc_curves(self.session.project, scan)
        self._x_curves = c
        self._draw_x()
        if not c.E.size:
            self.x_note.setText("no mcc data read"); return
        r = c.ratio[np.isfinite(c.ratio)]
        if r.size:
            self.x_note.setText(f"{scan.name}: mesh level {np.nanmedian(c.mesh):.3g}, diode level {np.nanmedian(c.diode):.3g}; diode/mesh spans "
                                f"{r.min():.3f}-{r.max():.3f} over the scan"
                                + (" (the mesh has its own spectral response: consider the diode or a good-I0 mean)" if r.max() - r.min() > 0.05 else ""))
        else:
            self.x_note.setText(f"{scan.name}: no finite ratio")

    def _draw_x(self) -> None:
        """The I0 explorer curves, with their colours read now (they follow a theme change)."""
        self.x_plot.clear(); self.x_plot.plotItem.legend.clear()
        c = self._x_curves
        if c is None or not c.E.size:
            return

        def med(a):
            m = np.nanmedian(a) if np.isfinite(a).any() else 1.0
            return m if m else 1.0

        curves = [("mesh mcc1 (I0)", c.mesh / med(c.mesh), pc("i0"), Qt.SolidLine),
                  ("photodiode mcc2", c.diode / med(c.diode), pc("diode"), Qt.DashLine),
                  ("diode / mesh", c.ratio, pc("ratio"), Qt.DotLine), ("TEY mcc4", c.tey / med(c.tey), pc("tey"), Qt.DashDotLine)]
        ys = []
        for label, y, col, st in curves:
            self.x_plot.plot(c.E, y, pen=pg.mkPen(col, width=1.4, style=st), connect="finite", name=label); ys.append(y)
        v = np.concatenate(ys); v = v[np.isfinite(v)]
        if v.size:
            self.x_plot.getViewBox().setRange(xRange=(float(c.E[0]), float(c.E[-1])), yRange=(float(v.min()), float(v.max()) if v.max() > v.min() else float(v.min()) + 1), padding=0.05, disableAutoRange=True)

    def run_checks(self) -> None:
        if self.session.project is None or (self._worker is not None and self._worker.isRunning()):
            return
        scans = list(self.session.stacks_of())
        if not scans:
            return
        project, session = self.session.project, self.session

        def job(cb):
            rates = {}
            for i, s in enumerate(scans):
                cb(i, len(scans) + len(scans), f"count rate {s.name}")
                rates[session.stack_id(s)] = count_rate_probe(project, s)
            for i, s in enumerate(scans):
                cb(len(scans) + i, 2 * len(scans), f"loading {s.name}")
                try:
                    session.stack(session.stack_id(s))
                except Exception as err:
                    rates.setdefault(session.stack_id(s), None)
                    continue
            return rates

        self.progress.setVisible(True); self.progress.setRange(0, 0)
        self.run_btn.setEnabled(False)
        self._worker = Worker(job, self)
        self._worker.progress.connect(lambda i, n, m: (self.progress.setRange(0, max(n, 1)), self.progress.setValue(i), self.day_label.setText(m)), Qt.QueuedConnection)
        self._worker.done.connect(self._done, Qt.QueuedConnection)
        self._worker.failed.connect(lambda t: (self.progress.setVisible(False), self.run_btn.setEnabled(True), self.day_label.setText(t.strip().splitlines()[-1])), Qt.QueuedConnection)
        self._worker.start()

    def _done(self, rates: dict) -> None:
        self._worker.wait(30000)
        self.progress.setVisible(False); self.run_btn.setEnabled(True)
        self._rates = {k: v for k, v in rates.items() if v is not None}
        self.compute()
        self.session.touch()        # memberships for the stacks loaded here

    def compute(self) -> None:
        """Per scan: saturation against the day's other scans, I0 validity against the day's other scans."""
        self._results.clear()
        scans = list(self.session.stacks_of())
        for s in scans:
            sid = self.session.stack_id(s)
            cr = self._rates.get(sid)
            if cr is None:
                continue
            others = [r for k, r in self._rates.items() if k != sid]
            day_kcps, day_i0 = day_reference(others)
            sat = saturation_check(cr, day_kcps, s.n_points)
            i0r = i0_check(cr.i0_level, day_i0)
            self._results[sid] = dict(rate=cr, sat=sat, i0=i0r, day_kcps=day_kcps, day_i0=day_i0)
        day_kcps, day_i0 = day_reference(list(self._rates.values()))
        self.day_label.setText("median over the holder's scans: " + ", ".join(f"{d.upper()} {k:.0f} kcps" for d, k in zip(DETS, day_kcps) if np.isfinite(k))
                               + (f" · I0 level {day_i0:.3g}" if np.isfinite(day_i0) else ""))
        self._fill_table(); self._fill_i0(); self._update_sdd()

    # ------------------------------------------------------------------ table
    def _fill_table(self) -> None:
        scans = list(self.session.stacks_of())
        self.table.setRowCount(len(scans))
        for r, s in enumerate(scans):
            sid = self.session.stack_id(s)
            res = self._results.get(sid)
            summ = s.summary or {}
            vals = [s.time, s.label, s.edge_text, s.status, "", "", "", "", "", "", "", "", ""]
            if res:
                cr, sat, i0r = res["rate"], res["sat"], res["i0"]
                k = cr.top_kcps
                vals[4] = f"{i0r.level:.3g}"
                vals[5] = "invalid" if i0r.bad else "ok"
                for j in range(4):
                    vals[6 + j] = f"{k[j]:.0f}" if np.isfinite(k[j]) else "?"
                vals[10] = ", ".join(d.upper() for d in sat.saturated) or ""
                vals[11] = ", ".join(d.upper() for d in sat.high_rate) or ""
                vals[12] = " ".join(sat.notes + ([i0r.note] if i0r.note else []))[:300]
            elif summ:
                vals[5] = "invalid" if summ.get("i0_bad") else ""
                vals[10] = ", ".join(summ.get("sdd_saturated") or []).upper()
                vals[12] = "from sgm_report: " + " ".join(summ.get("notes", []))[:280]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, sid)
                if c == 5 and v:                      # I0 verdict: icon plus word, never colour alone
                    set_item_tone(it, "bad" if v == "invalid" else "ok")
                elif c in (10, 11) and v:
                    set_item_tone(it, "bad" if c == 10 else "warn")
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()

    def _row_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows:
            sid = self.table.item(rows[0].row(), 0).data(Qt.UserRole)
            if self.session.stack_loaded(sid) is not None:
                self.session.select_stack(sid)

    # ------------------------------------------------------------------ I0
    def _fill_i0(self) -> None:
        stacks = {self.session.stack_id(s): self.session.stack_loaded(self.session.stack_id(s)) for s in self.session.stacks_of()}
        stacks = {k: v for k, v in stacks.items() if v is not None}
        labels = {self.session.stack_id(s): f"{s.time} {s.label} · {s.edge_text}" for s in self.session.stacks_of()}
        self._cands = i0_candidates(stacks, labels)
        self.i0_list.blockSignals(True)
        self.i0_list.clear()
        for c in self._cands:
            tone = "ok" if c.looks_good else "warn"
            it = QListWidgetItem(f"{c.label}   level {c.level:.3g} · step {100 * c.max_step:.1f} % · rough {100 * c.rough:.1f} % · "
                                 f"≤0: {c.nonpos}   {status_text(tone)[0]} {'looks good' if c.looks_good else 'check'}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if self.session.zones.good_i0.get(c.sid) else Qt.Unchecked)
            it.setData(Qt.UserRole, c.sid)
            set_item_tone(it, tone, icon=False)       # the check box sits where an icon would
            self.i0_list.addItem(it)
        self.i0_list.blockSignals(False)
        self._draw_i0()

    def _good_toggled(self, item: QListWidgetItem) -> None:
        self.session.zones.good_i0[item.data(Qt.UserRole)] = item.checkState() == Qt.Checked
        self.session.touch()
        self._draw_i0()

    def _auto_select(self) -> None:
        for c in getattr(self, "_cands", []):
            self.session.zones.good_i0[c.sid] = c.looks_good
        self.session.touch()
        self._fill_i0()

    def _draw_i0(self) -> None:
        self.i0_plot.clear()
        cands = getattr(self, "_cands", [])
        if not cands:
            return
        xs, ys = [], []
        curves = []
        for i, c in enumerate(cands):
            y = c.curve / c.level if (self.i0_norm.isChecked() and c.level) else c.curve
            good = bool(self.session.zones.good_i0.get(c.sid))
            pen = pg.mkPen(pc("good") if good else curve(i), width=2 if good else 1, style=Qt.SolidLine if good else Qt.DashLine)
            self.i0_plot.plot(c.E, y, pen=pen, connect="finite")
            xs += [float(c.E[0]), float(c.E[-1])]; ys.append(y)
            if good:
                curves.append((c.E, y))
        if curves:
            E = curves[0][0]
            from aurora.core.zones import good_i0_curve
            m = good_i0_curve(E, curves)
            if m is not None:
                self.i0_plot.plot(E, m, pen=role_pen("ink", 2.5), connect="finite")
        vals = np.concatenate(ys); vals = vals[np.isfinite(vals)]
        if vals.size and xs:
            if self.i0_norm.isChecked():
                lo, hi = max(float(vals.min()), -0.2), min(float(vals.max()), 2.5)
            else:
                lo, hi = float(vals.min()), float(vals.max())
            self.i0_plot.getViewBox().setRange(xRange=(min(xs), max(xs)), yRange=(lo, hi if hi > lo else lo + 1), padding=0.05, disableAutoRange=True)

    # ------------------------------------------------------------------ SDD agreement
    def _update_sdd(self) -> None:
        self.sdd_plot.clear()
        if self.sdd_plot.plotItem.legend is not None:
            self.sdd_plot.plotItem.legend.clear()
        z = self.session.zone()
        sid = self.session.current_stack
        sd = self.session.stack_loaded(sid) if sid else None
        if z is None or sd is None or sid not in z.settings:
            self.sdd_note.setText("select a zone (Reduce) and a scan that covers it")
            self.spike_note.setText("")
            return
        pix = members(z, sd.x, sd.y, sd.pitch)
        st = z.settings[sid]
        good = self.session.good_curve_for(sd.E) if st.i0_source == "good" else None
        t = zone_traces(sd, pix, st, good)
        if t is None:
            self.sdd_note.setText("no pixel of this scan inside the zone")
            return
        res = self._results.get(sid)
        scan = self.session.scan_of(sid)                  # None for a stitched stack
        sat = res["sat"].saturated if res else (((scan.summary or {}) if scan is not None else {}).get("sdd_saturated") or [])
        c = sdd_consistency(t, sat)
        self.sdd_note.setText(f"{z.label} · {sid[11:]} · {t.n_pix} px, line {t.line}: {c.note}")
        if c.normalised is not None:
            xs = (float(c.energies[0]), float(c.energies[-1]))
            for k in range(4):
                flagged = any(d == DETS[k] for d, _ in c.flags)
                pen = pg.mkPen(curve(k), width=2 if flagged else 1, style=dash(k))
                self.sdd_plot.plot(c.energies, c.normalised[:, k], pen=pen, name=f"SDD{k + 1}" + (" !" if flagged else ""))
            vals = c.normalised[np.isfinite(c.normalised)]
            self.sdd_plot.getViewBox().setRange(xRange=xs, yRange=(float(vals.min()), float(vals.max())), padding=0.05, disableAutoRange=True)
        sp = spike_summary(t)
        self.spike_note.setText(("spikes interpolated (k = %g): " % st.k + ", ".join(f"{k} {v}" for k, v in sp.items())) if sp else
                                ("de-spiking off" if not st.despike else "no spikes found"))
