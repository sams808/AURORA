"""Preview of a zone's traces: fluorescence (four SDDs + sum), TEY, I0 in three linked panels, spikes marked."""
from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontMetricsF
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

from aurora.app.theme import (DASH, PAL, curve, dash, min_height, on_theme_changed, pc, role_pen,  # noqa: F401
                              style_plot, with_alpha)                     # PAL / DASH re-exported
from aurora.core.spectra import ZoneTraces


class SparseAxis(pg.AxisItem):
    """A y axis whose tick labels never print over each other on a short panel, and show at least two values where
    they can. pyqtgraph keeps at least 2.25 major intervals whatever the height (a 56 px panel printed five labels into
    56 px), and its step can leave a single label once the labels at the ends are dropped. Here the step moves along
    1, 2, 2.5, 5, 10 ...: up while the labels are closer than 1.25 lines (never down to fewer than two labels), down
    while one label only is shown (never closer than a line)."""

    @staticmethod
    def _mantissa(step: float) -> float:
        return round(step / 10 ** math.floor(math.log10(step)), 2)

    def tickSpacing(self, minVal, maxVal, size):
        levels = super().tickSpacing(minVal, maxVal, size)
        lo, hi = min(minVal, maxVal), max(minVal, maxVal)
        if not levels or hi <= lo or size <= 0:
            return levels
        line = QFontMetricsF(self.style.get("tickFont") or QFont()).height()
        edge = 0.5 * line * (hi - lo) / size                        # pyqtgraph drops a label this close to an end
        count = lambda s: math.floor((hi - edge) / s) - math.ceil((lo + edge) / s) + 1      # noqa: E731  labels shown
        pitch = lambda s: size * s / (hi - lo)                                              # noqa: E731  px apart
        step = first = levels[0][0]
        while pitch(step) < 1.25 * line:                            # too close: a larger step
            nxt = step * (1.25 if self._mantissa(step) == 2 else 2.0)
            if count(nxt) < 2:
                break
            step = nxt
        while count(step) < 2:                                      # a single label: a smaller step
            nxt = step / (1.25 if self._mantissa(step) == 2.5 else 2.0)
            if pitch(nxt) < line:
                break
            step = nxt
        if step == first:
            return levels
        return [(step, 0), (step / (4 if self._mantissa(step) == 2 else 5), 0)]

    def tickStrings(self, values, scale, spacing):
        # pyqtgraph gives a step of 0.25 one decimal ('0.2', '0.5', '0.8' at 0.25, 0.5, 0.75): one more for 2.5 steps
        if spacing and self._mantissa(spacing * scale) == 2.5:
            spacing = spacing / 10
        return super().tickStrings(values, scale, spacing)


class PreviewPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        opts = QHBoxLayout()
        self.toggles = QWidget()                       # display toggles: Advanced mode
        from aurora.app.widgets import ElidedLabel, FlowLayout
        tl = FlowLayout(self.toggles, spacing=6)
        self.show_sdd = QCheckBox("SDDs"); self.show_sdd.setChecked(True)
        self.show_sum = QCheckBox("sum"); self.show_sum.setChecked(True)
        self.show_tey = QCheckBox("TEY"); self.show_tey.setChecked(True)
        self.show_i0 = QCheckBox("I0"); self.show_i0.setChecked(True)
        self.show_spikes = QCheckBox("spikes"); self.show_spikes.setChecked(True)
        for c in (self.show_sdd, self.show_sum, self.show_tey, self.show_i0, self.show_spikes):
            tl.addWidget(c)
        opts.addWidget(self.toggles)
        self.all_scans = QCheckBox("all scans of the zone")
        opts.addWidget(self.all_scans)
        opts.addStretch(1)
        self.readout = ElidedLabel("", Qt.ElideRight)
        self.readout.setObjectName("muted")
        opts.addWidget(self.readout, 1)
        lay.addLayout(opts)
        self.gl = pg.GraphicsLayoutWidget()
        self.top = self.gl.addPlot(row=0, col=0, axisItems={"left": SparseAxis("left")})
        self.mid = self.gl.addPlot(row=1, col=0, axisItems={"left": SparseAxis("left")})
        self.low = self.gl.addPlot(row=2, col=0, axisItems={"left": SparseAxis("left")})
        self.mid.setXLink(self.top)
        self.low.setXLink(self.top)
        self.top.setLabel("left", "fluorescence")
        self.mid.setLabel("left", "TEY")
        self.low.setLabel("left", "I0 (zone sum)")
        self.low.setLabel("bottom", "energy (eV)")
        for p in (self.top, self.mid, self.low):
            p.showGrid(x=True, y=True, alpha=0.15)
            p.vb.disableAutoRange()
        for p in (self.top, self.mid):                  # linked x: one row of energies, under the lowest panel
            p.getAxis("bottom").setStyle(showValues=False)
        self.top.addLegend(offset=(10, 10))
        self.gl.ci.layout.setRowStretchFactor(0, 4)
        self.gl.ci.layout.setRowStretchFactor(1, 2)
        self.gl.ci.layout.setRowStretchFactor(2, 2)
        lay.addWidget(self.gl, 1)
        min_height(self.gl, 300)                        # three panels with readable ticks (the page scrolls if needed)
        style_plot(self.gl)
        on_theme_changed(self, lambda _s: self.redraw())
        self._traces: list[tuple[str, ZoneTraces, dict]] = []
        self._waiting = ""                              # the hint of an empty preview whose data is still being read
        for c in (self.show_sdd, self.show_sum, self.show_tey, self.show_i0, self.show_spikes):
            c.toggled.connect(self.redraw)
        self.top.scene().sigMouseMoved.connect(self._moved)
        from aurora.app.help_text import explain_all
        from aurora.app.mode import mark_advanced
        explain_all([(self.show_sdd, "preview.sdd"), (self.show_sum, "preview.sum"), (self.show_tey, "preview.tey"),
                     (self.show_i0, "preview.i0"), (self.show_spikes, "preview.spikes"),
                     (self.all_scans, "preview.all_scans")])
        self.gl.setAccessibleName("preview of the zone's traces")
        mark_advanced(self.toggles)

    def set_traces(self, traces: list[tuple[str, ZoneTraces, dict]], waiting: str = "") -> None:
        """[(name, traces, meta)] where meta may hold 'edge_ev' and 'edge'. waiting: what an empty preview is still
        reading ('reading the good-I0 scans …')."""
        self._traces = traces
        self._waiting = waiting
        self.redraw()

    def redraw(self) -> None:
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
                    self._spikes(self.top, t.E, y, t.spikes.get(f"sdd{i + 1}"))
            if self.show_sum.isChecked() and t.sum is not None:
                self.top.plot(t.E, t.sum, pen=pg.mkPen(col or pc("sum"), width=2.2), connect="finite",
                              name=(name + " " if multi else "") + "sum of ticked SDDs")
                self._spikes(self.top, t.E, t.sum, t.spikes.get("sum"))
            if self.show_tey.isChecked():
                self.mid.plot(t.E, t.tey, pen=pg.mkPen(col or pc("tey"), width=1.6), connect="finite")
                self._spikes(self.mid, t.E, t.tey, t.spikes.get("tey"))
            if self.show_i0.isChecked():
                self.low.plot(t.E, t.i0, pen=pg.mkPen(col or pc("i0"), width=1.2), connect="finite")
                if t.settings and t.settings.i0_source == "good":
                    self.low.plot(t.E, t.i0_used, pen=role_pen("good", 1.4), connect="finite")
                sp = t.spikes.get("i0")
                if sp and self.show_spikes.isChecked():
                    self.low.addItem(pg.ScatterPlotItem(t.E[sp], t.i0_raw[sp], size=6, brush=pg.mkBrush(pc("spike")), pen=None), ignoreBounds=True)
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
            vals = np.concatenate([np.asarray(a, float) for a in arrays]) if arrays else np.array([])
            vals = vals[np.isfinite(vals)]
            if vals.size:
                lo, hi = float(vals.min()), float(vals.max())
                if hi <= lo:
                    hi = lo + 1.0
            else:
                lo, hi = 0.0, 1.0
            plot.vb.setRange(xRange=(x0, x1), yRange=(lo, hi), padding=0.05, disableAutoRange=True)

    def _spikes(self, plot, E, y, idx) -> None:
        if idx and self.show_spikes.isChecked():
            idx = [i for i in idx if np.isfinite(y[i])]
            if idx:
                plot.addItem(pg.ScatterPlotItem(E[idx], y[idx], size=6, brush=pg.mkBrush(pc("spike")), pen=None), ignoreBounds=True)

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
