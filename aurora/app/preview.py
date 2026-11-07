"""Preview of a zone's traces: fluorescence (four SDDs + sum) above, TEY and I0 below, spikes marked."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from aurora.core.zones import ZoneTraces

PAL = ["#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#F0E442"]
DASH = [Qt.SolidLine, Qt.DashLine, Qt.DotLine, Qt.DashDotLine]


class PreviewPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        opts = QHBoxLayout()
        self.show_sdd = QCheckBox("SDDs"); self.show_sdd.setChecked(True)
        self.show_sum = QCheckBox("sum"); self.show_sum.setChecked(True)
        self.show_tey = QCheckBox("TEY"); self.show_tey.setChecked(True)
        self.show_i0 = QCheckBox("I0"); self.show_i0.setChecked(True)
        self.show_spikes = QCheckBox("spikes"); self.show_spikes.setChecked(True)
        self.all_scans = QCheckBox("all scans of the zone")
        for c in (self.show_sdd, self.show_sum, self.show_tey, self.show_i0, self.show_spikes, self.all_scans):
            opts.addWidget(c)
        opts.addStretch(1)
        self.readout = QLabel("")
        self.readout.setObjectName("muted")
        opts.addWidget(self.readout)
        lay.addLayout(opts)
        self.gl = pg.GraphicsLayoutWidget()
        self.gl.setBackground("w")
        self.top = self.gl.addPlot(row=0, col=0)
        self.bottom = self.gl.addPlot(row=1, col=0)
        self.bottom.setXLink(self.top)
        self.top.setLabel("left", "fluorescence")
        self.bottom.setLabel("left", "TEY")
        self.bottom.setLabel("bottom", "energy (eV)")
        self.right = pg.ViewBox()
        self.bottom.showAxis("right")
        self.bottom.scene().addItem(self.right)
        self.bottom.getAxis("right").linkToView(self.right)
        self.right.setXLink(self.bottom)
        self.bottom.getAxis("right").setLabel("I0", color="#C8501E")
        self.bottom.vb.sigResized.connect(self._sync_right)
        for p in (self.top, self.bottom):
            p.showGrid(x=True, y=True, alpha=0.15)
            p.addLegend(offset=(10, 10))
        self.gl.ci.layout.setRowStretchFactor(0, 3)
        self.gl.ci.layout.setRowStretchFactor(1, 2)
        lay.addWidget(self.gl, 1)
        self._traces: list[tuple[str, ZoneTraces, dict]] = []
        for c in (self.show_sdd, self.show_sum, self.show_tey, self.show_i0, self.show_spikes):
            c.toggled.connect(self.redraw)
        self.top.scene().sigMouseMoved.connect(self._moved)

    def _sync_right(self) -> None:
        self.right.setGeometry(self.bottom.vb.sceneBoundingRect())
        self.right.linkedViewChanged(self.bottom.vb, self.right.XAxis)

    def set_traces(self, traces: list[tuple[str, ZoneTraces, dict]]) -> None:
        """[(name, traces, meta)] where meta may hold 'edge_ev' and 'edge'."""
        self._traces = traces
        self.redraw()

    def redraw(self) -> None:
        self.top.clear(); self.bottom.clear(); self.right.clear()
        if self.top.legend is not None:
            self.top.legend.clear()
        if self.bottom.legend is not None:
            self.bottom.legend.clear()
        if not self._traces:
            self.top.setTitle("select a zone")
            return
        multi = len(self._traces) > 1
        t0 = self._traces[0][1]
        title = "fluorescence" + (" (raw counts)" if t0.settings and t0.settings.fluo_norm == "raw" else "")
        self.top.setTitle(title)
        if t0.pre:
            for rng, col in ((t0.pre, (31, 95, 191, 18)), (t0.post, (31, 95, 191, 18))):
                reg = pg.LinearRegionItem(values=list(rng), movable=False, brush=pg.mkBrush(*col), pen=pg.mkPen(None))
                reg.setZValue(-20)
                self.top.addItem(reg)
        for ti, (name, t, meta) in enumerate(self._traces):
            col = PAL[ti % len(PAL)] if multi else None
            if meta.get("edge_ev") and t.E[0] <= meta["edge_ev"] <= t.E[-1]:
                line = pg.InfiniteLine(meta["edge_ev"], angle=90, pen=pg.mkPen("#999999", style=Qt.DashLine))
                self.top.addItem(line)
            if self.show_sdd.isChecked():
                for i, y in enumerate(t.det):
                    if y is None:
                        continue
                    on = t.settings.sdd[i] if t.settings else True
                    pen = pg.mkPen(col or PAL[i], width=1 if on else 0.8, style=DASH[i])
                    item = self.top.plot(t.E, y, pen=pen, connect="finite", name=(f"SDD{i + 1}" + ("" if on else " (off)")) if ti == 0 else None)
                    item.setOpacity(1.0 if on else 0.35)
                    self._spikes(self.top, t.E, y, t.spikes.get(f"sdd{i + 1}"))
            if self.show_sum.isChecked() and t.sum is not None:
                self.top.plot(t.E, t.sum, pen=pg.mkPen(col or "#111111", width=2.2), connect="finite",
                              name=(name + " " if multi else "") + "sum of ticked SDDs")
                self._spikes(self.top, t.E, t.sum, t.spikes.get("sum"))
            if self.show_tey.isChecked():
                self.bottom.plot(t.E, t.tey, pen=pg.mkPen(col or "#444444", width=1.6), connect="finite", name=(name + " " if multi else "") + "TEY")
                self._spikes(self.bottom, t.E, t.tey, t.spikes.get("tey"))
            if self.show_i0.isChecked():
                c = pg.PlotCurveItem(t.E, t.i0, pen=pg.mkPen("#C8501E", width=1, style=Qt.DotLine), connect="finite")
                self.right.addItem(c)
                if t.settings and t.settings.i0_source == "good":
                    self.right.addItem(pg.PlotCurveItem(t.E, t.i0_used, pen=pg.mkPen("#2a8a4a", width=1.4, style=Qt.DashLine), connect="finite"))
                sp = t.spikes.get("i0")
                if sp and self.show_spikes.isChecked():
                    self.right.addItem(pg.ScatterPlotItem(t.E[sp], t.i0_raw[sp], size=6, brush=pg.mkBrush("#e02020"), pen=None))
        self.right.enableAutoRange()
        self._sync_right()

    def _spikes(self, plot, E, y, idx) -> None:
        if idx and self.show_spikes.isChecked():
            idx = [i for i in idx if np.isfinite(y[i])]
            if idx:
                plot.addItem(pg.ScatterPlotItem(E[idx], y[idx], size=6, brush=pg.mkBrush("#e02020"), pen=None))

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
