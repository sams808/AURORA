"""Preview of a zone's traces: fluorescence (four SDDs + sum), TEY, I0 in three linked panels, spikes marked.

A redraw whose traces have the structure of those drawn (one trace, the same detectors, settings, toggles and scheme)
moves the existing curves with setData instead of rebuilding the plots (update_traces, and set_traces, which goes the
same way): a zone being drawn redraws the preview at every stroke.

What is drawn is chosen in one 'Show ▾' menu (SDDs, sum, TEY, I0, spikes), so the controls stay one row in a narrow
column, and the hover readout is one elided line under the plots. The plots keep at least MIN_PLOT_PX (the
fluorescence panel MIN_TOP_PX); below COMPACT_BELOW_PX of plot height the TEY and I0 panels hide and the fluorescence
takes the whole height (compact), until 'TEY and I0 panels' in the menu brings them back. set_compact(True) keeps a
preview compact (the Survey's zone preview)."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QMenu, QToolButton, QVBoxLayout, QWidget

from aurora.app.theme import DASH, PAL, curve, dash, on_theme_changed, pc, role_pen, style_plot, with_alpha  # noqa: F401  PAL / DASH re-exported
from aurora.core.spectra import ZoneTraces

MIN_PLOT_PX = 180               # the three panels (or the compact fluorescence panel) never get less
MIN_TOP_PX = 110                # the fluorescence panel never gets less
COMPACT_BELOW_PX = 260          # below this plot height, the TEY and I0 panels hide (compact)
SHOW_TEXT = "Show ▾"


class PreviewPlot(QWidget):
    def __init__(self, parent=None, min_plot_px: int = MIN_PLOT_PX, overlay: bool = True):
        """overlay False: no 'all scans' check box (a preview of one scan only, the Survey's Zone preview)."""
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        opts = QHBoxLayout()
        from aurora.app.help_text import HELP, explain
        from aurora.app.widgets import ElidedLabel
        self.show_btn = QToolButton()                   # display toggles, one menu: Advanced mode
        self.show_btn.setText(SHOW_TEXT)
        self.show_btn.setPopupMode(QToolButton.InstantPopup)
        self.show_btn.setStyleSheet("QToolButton::menu-indicator { image: none; width: 0px; }")    # the ▾ says it
        explain(self.show_btn, "preview.show")
        self.show_menu = QMenu(self.show_btn)
        self.show_menu.setToolTipsVisible(True)
        self.show_btn.setMenu(self.show_menu)

        def toggle(text: str, key: str, on: bool = True):
            a = self.show_menu.addAction(text)
            a.setCheckable(True)
            a.setChecked(on)
            a.setToolTip(HELP[key][0])
            a.setStatusTip(HELP[key][0])
            return a
        self.show_sdd = toggle("SDDs", "preview.sdd")
        self.show_sum = toggle("sum", "preview.sum")
        self.show_tey = toggle("TEY", "preview.tey")
        self.show_i0 = toggle("I0", "preview.i0")
        self.show_spikes = toggle("spikes", "preview.spikes")
        self.show_menu.addSeparator()
        self.show_panels = toggle("TEY and I0 panels", "preview.panels", on=False)
        self.show_panels.setVisible(False)              # offered while the preview is compact
        self.toggles = self.show_btn
        opts.addWidget(self.show_btn)
        self.all_scans = QCheckBox("all scans of the zone")
        if overlay:
            opts.addWidget(self.all_scans)
        opts.addStretch(1)
        self.readout = ElidedLabel("", Qt.ElideRight)   # under the plots (added below): the row never wraps
        self.readout.setObjectName("muted")
        lay.addLayout(opts)
        self.gl = pg.GraphicsLayoutWidget()
        self.top = self.gl.addPlot(row=0, col=0)
        self.mid = self.gl.addPlot(row=1, col=0)
        self.low = self.gl.addPlot(row=2, col=0)
        self.mid.setXLink(self.top)
        self.low.setXLink(self.top)
        self.top.setLabel("left", "fluorescence")
        self.mid.setLabel("left", "TEY")
        self.low.setLabel("left", "I0 (zone sum)")
        self.low.setLabel("bottom", "energy (eV)")
        for p in (self.top, self.mid, self.low):
            p.showGrid(x=True, y=True, alpha=0.15)
            p.vb.disableAutoRange()
        self.top.addLegend(offset=(10, 10))
        self.gl.ci.layout.setRowStretchFactor(0, 4)
        self.gl.ci.layout.setRowStretchFactor(1, 2)
        self.gl.ci.layout.setRowStretchFactor(2, 2)
        lay.addWidget(self.gl, 1)
        lay.addWidget(self.readout)
        self.gl.setMinimumHeight(int(min_plot_px))
        self.top.setMinimumHeight(MIN_TOP_PX)
        self._forced_compact: bool | None = None        # None: automatic (by the plot height)
        self._compact = False
        self._bottom_values = self.top.getAxis("bottom").style.get("showValues", True)
        self.gl.installEventFilter(self)
        self.show_panels.toggled.connect(lambda _on: self._layout_rows())
        style_plot(self.gl)
        on_theme_changed(self, lambda _s: self.redraw())
        self._traces: list[tuple[str, ZoneTraces, dict]] = []
        self._waiting = ""                              # the hint of an empty preview whose data is still being read
        for c in (self.show_sdd, self.show_sum, self.show_tey, self.show_i0, self.show_spikes):
            c.toggled.connect(self.redraw)
        self.top.scene().sigMouseMoved.connect(self._moved)
        from aurora.app.help_text import explain_all
        from aurora.app.mode import mark_advanced
        explain_all([(self.all_scans, "preview.all_scans")])
        self.gl.setAccessibleName("preview of the zone's traces")
        mark_advanced(self.toggles)

    # ------------------------------------------------------------------ compact
    def set_compact(self, on: bool | None) -> None:
        """True: the fluorescence panel only, whatever the height (the Survey's zone preview); False: always the three
        panels; None: automatic (compact below COMPACT_BELOW_PX of plot height)."""
        self._forced_compact = on
        self._layout_rows()

    def is_compact(self) -> bool:
        return self._compact

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.gl and ev.type() == QEvent.Resize:
            self._layout_rows()
        return False

    def _layout_rows(self) -> None:
        if self._forced_compact is not None:
            want = self._forced_compact
        else:
            want = self.gl.height() < COMPACT_BELOW_PX and not self.show_panels.isChecked()
        self.show_panels.setVisible(self._forced_compact is None and (want or self.show_panels.isChecked()))
        if want == self._compact:
            return
        self._compact = want
        ci = self.gl.ci
        bottom = self.top.getAxis("bottom")
        if want:
            for p in (self.mid, self.low):
                p.setXLink(None)                        # a hidden panel without geometry would drag the x range
                ci.removeItem(p)
                p.hide()
            self.top.setLabel("bottom", "energy (eV)")
            bottom.setStyle(showValues=True)
        else:
            for row, p in ((1, self.mid), (2, self.low)):
                p.show()
                ci.addItem(p, row=row, col=0)
                p.setXLink(self.top)
            ci.layout.setRowStretchFactor(1, 2)
            ci.layout.setRowStretchFactor(2, 2)
            bottom.showLabel(False)
            bottom.setStyle(showValues=self._bottom_values)

    def set_traces(self, traces: list[tuple[str, ZoneTraces, dict]], waiting: str = "") -> None:
        """[(name, traces, meta)] where meta may hold 'edge_ev' and 'edge'. waiting: what an empty preview is still
        reading ('reading the good-I0 scans …')."""
        self._traces = traces
        self._waiting = waiting
        self.redraw()

    def redraw(self) -> None:
        if self._update_in_place():
            return
        self._items: dict = {}                          # role -> plot item (one trace), for update_traces
        self._sig = None
        for p in (self.top, self.mid, self.low):
            p.clear()
        if self.top.legend is not None:
            self.top.legend.clear()
        from aurora.app.widgets import set_plot_hint
        if not self._traces:
            self.top.setTitle("")
            set_plot_hint(self.top, self._waiting or "select a sample (zone) and a scan to see its traces")
            for p in (self.mid, self.low):
                set_plot_hint(p, " ")                 # no bare 0..1 axes on an empty preview
            return
        for p in (self.top, self.mid, self.low):
            set_plot_hint(p, None)
        multi = len(self._traces) > 1
        t0 = self._traces[0][1]
        self.top.setTitle("fluorescence" + (" (raw counts)" if t0.settings and t0.settings.fluo_norm == "raw" else ""))
        if t0.pre:
            for rng in (t0.pre, t0.post):
                reg = pg.LinearRegionItem(values=list(rng), movable=False, brush=pg.mkBrush(with_alpha(pc("region"), 0.10)),
                                          pen=pg.mkPen(None))
                reg.setZValue(-20)
                self.top.addItem(reg, ignoreBounds=True)
                self._items["pre" if rng is t0.pre else "post"] = reg
        for ti, (name, t, meta) in enumerate(self._traces):
            col = curve(ti) if multi else None
            if meta.get("edge_ev") and t.E[0] <= meta["edge_ev"] <= t.E[-1]:
                self.top.addItem(pg.InfiniteLine(meta["edge_ev"], angle=90, pen=role_pen("edge", 1.0),
                                                 label=meta.get("edge", ""), labelOpts={"position": 0.9, "color": pc("edge_label")}),
                                 ignoreBounds=True)
            if self.show_sdd.isChecked():
                for i, y in enumerate(t.det):
                    if y is None:
                        continue
                    on = t.settings.sdd[i] if t.settings else True
                    pen = pg.mkPen(col or curve(i), width=1 if on else 0.8, style=dash(i))
                    item = self.top.plot(t.E, y, pen=pen, connect="finite", name=(f"SDD{i + 1}" + ("" if on else " (off)")) if ti == 0 else None)
                    item.setOpacity(1.0 if on else 0.35)
                    self._items[f"sdd{i}"] = item
                    self._spikes(self.top, t.E, y, t.spikes.get(f"sdd{i + 1}"), None if multi else f"spk:sdd{i}")
            if self.show_sum.isChecked() and t.sum is not None:
                self._items["sum"] = self.top.plot(t.E, t.sum, pen=pg.mkPen(col or pc("sum"), width=2.2), connect="finite",
                                                   name=(name + " " if multi else "") + "sum of ticked SDDs")
                self._spikes(self.top, t.E, t.sum, t.spikes.get("sum"), None if multi else "spk:sum")
            if self.show_tey.isChecked():
                self._items["tey"] = self.mid.plot(t.E, t.tey, pen=pg.mkPen(col or pc("tey"), width=1.6), connect="finite")
                self._spikes(self.mid, t.E, t.tey, t.spikes.get("tey"), None if multi else "spk:tey")
            if self.show_i0.isChecked():
                self._items["i0"] = self.low.plot(t.E, t.i0, pen=pg.mkPen(col or pc("i0"), width=1.2), connect="finite")
                if t.settings and t.settings.i0_source == "good":
                    self._items["i0_used"] = self.low.plot(t.E, t.i0_used, pen=role_pen("good", 1.4), connect="finite")
                sp = t.spikes.get("i0")
                if self.show_spikes.isChecked() and (sp or not multi):
                    sp = list(sp or [])
                    it = pg.ScatterPlotItem(t.E[sp], t.i0_raw[sp], size=6, brush=pg.mkBrush(pc("spike")), pen=None)
                    self.low.addItem(it, ignoreBounds=True)
                    self._items["spk:i0"] = it
        if multi:
            self._items = {}
        self._sig = self._signature(self._traces)
        self._set_ranges()

    def _set_ranges(self) -> None:
        """Ranges from the data (pyqtgraph's auto-range misbehaves while a view has no geometry and then sticks)."""
        xs, top, mid, low = [], [], [], []
        for _, t, _ in self._traces:
            xs += [float(t.E[0]), float(t.E[-1])]
            if self.show_sum.isChecked() and t.sum is not None:
                top.append(t.sum)
            if self.show_sdd.isChecked():
                top += [y for y, on in zip(t.det, t.settings.sdd if t.settings else [True] * 4) if y is not None and on]
            if self.show_tey.isChecked():
                mid.append(t.tey)
            if self.show_i0.isChecked():
                low.append(t.i0)
                if t.settings and t.settings.i0_source == "good":
                    low.append(t.i0_used)
        if not xs:
            return
        x0, x1 = min(xs), max(xs)
        for plot, arrays in ((self.top, top), (self.mid, mid), (self.low, low)):
            if self._compact and plot is not self.top:
                continue
            vals = np.concatenate([np.asarray(a, float) for a in arrays]) if arrays else np.array([])
            vals = vals[np.isfinite(vals)]
            if vals.size:
                lo, hi = float(vals.min()), float(vals.max())
                if hi <= lo:
                    hi = lo + 1.0
            else:
                lo, hi = 0.0, 1.0
            plot.vb.setRange(xRange=(x0, x1), yRange=(lo, hi), padding=0.05, disableAutoRange=True)

    def _spikes(self, plot, E, y, idx, role: str | None = None) -> None:
        """The spikes of a curve as dots. With a role (one trace drawn), the dots exist even without a spike, so
        update_traces can move them."""
        if not self.show_spikes.isChecked() or not (idx or role):
            return
        idx = [i for i in (idx or []) if np.isfinite(y[i])]
        if idx or role:
            it = pg.ScatterPlotItem(E[idx], y[idx], size=6, brush=pg.mkBrush(pc("spike")), pen=None)
            plot.addItem(it, ignoreBounds=True)
            if role:
                self._items[role] = it

    # ------------------------------------------------------------------ updates in place
    def _signature(self, traces) -> tuple | None:
        """What the plot items of a redraw depend on: equal signatures draw the same items, only the data move."""
        if len(traces) != 1:
            return None
        from aurora.app.theme import scheme, text_px
        name, t, meta = traces[0]
        st = t.settings
        edge = meta.get("edge_ev")
        return (name, scheme(), text_px(), self.show_sdd.isChecked(), self.show_sum.isChecked(), self.show_tey.isChecked(),
                self.show_i0.isChecked(), self.show_spikes.isChecked(), tuple(y is None for y in t.det),
                t.sum is None, tuple(st.sdd) if st else None, st.i0_source if st else None,
                st.fluo_norm if st else None, bool(t.pre), edge, meta.get("edge", ""),
                bool(edge and t.E[0] <= edge <= t.E[-1]))

    def update_traces(self, traces: list[tuple[str, ZoneTraces, dict]]) -> None:
        """set_traces for a preview that changes quickly (a zone being drawn): when the traces have the structure of
        those drawn, the existing curves get the new data (setData, about ten times faster than a rebuild); otherwise
        a full redraw."""
        self.set_traces(traces)

    def _update_in_place(self) -> bool:
        """redraw() through setData when the structure is unchanged (see _signature). False: a rebuild is needed."""
        items = getattr(self, "_items", None)
        if not items or not self._traces or getattr(self, "_sig", None) is None or \
                self._signature(self._traces) != self._sig:
            return False
        import shiboken6
        if not all(shiboken6.isValid(it) for it in items.values()):
            return False
        _name, t, _meta = self._traces[0]
        for i, y in enumerate(t.det):
            if f"sdd{i}" in items:
                items[f"sdd{i}"].setData(t.E, y)
        series = {"sum": t.sum, "tey": t.tey, "i0": t.i0, "i0_used": t.i0_used}
        for role, y in series.items():
            if role in items and y is not None:
                items[role].setData(t.E, y)
        spikes = {**{f"spk:sdd{i}": (y, t.spikes.get(f"sdd{i + 1}")) for i, y in enumerate(t.det)},
                  "spk:sum": (t.sum, t.spikes.get("sum")), "spk:tey": (t.tey, t.spikes.get("tey"))}
        for role, (y, idx) in spikes.items():
            if role in items and y is not None:
                idx = [i for i in (idx or []) if np.isfinite(y[i])]
                items[role].setData(t.E[idx], y[idx])
        if "spk:i0" in items:
            sp = list(t.spikes.get("i0") or [])
            items["spk:i0"].setData(t.E[sp], t.i0_raw[sp])
        for role, rng in (("pre", t.pre), ("post", t.post)):
            if role in items and rng:
                items[role].setRegion(list(rng))
        self._set_ranges()
        return True

    def _moved(self, pos) -> None:
        if not self._traces or not self.top.sceneBoundingRect().contains(pos):
            return
        e = self.top.vb.mapSceneToView(pos).x()
        t = self._traces[0][1]
        i = int(np.argmin(np.abs(t.E - e)))
        parts = [f"E {t.E[i]:.2f} eV"]
        if t.sum is not None:
            parts.append(f"sum {t.sum[i]:.4g}")
        parts += [f"S{k + 1} {y[i]:.4g}" for k, y in enumerate(t.det) if y is not None]
        parts.append(f"TEY {t.tey[i]:.4g}")
        parts.append(f"I0 {t.i0[i]:.4g}")
        if i in (t.spikes.get("i0") or []):
            parts.append("I0 spike")
        self.readout.setText(" · ".join(parts))
