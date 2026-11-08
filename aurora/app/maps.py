"""Maps perspective (CLS dashboard parity): the hypercube of one stack. Map of any emission window at any energy,
ROI emission spectrum with a draggable window, XANES of the ROI, elemental maps per fixed line, RGB composite,
pixel PCA + k-means clusters, PyMca exports."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QProgressBar,
                               QPushButton, QSlider, QSpinBox, QSplitter, QTabWidget, QVBoxLayout, QWidget)

from aurora.app.mapview import MapView
from aurora.app.session import Session
from aurora.app.theme import (MAP_OVERLAY, categorical, curve, dash, make_plot, on_theme_changed, pc, restyle_plot,
                              role_pen, style_plot, with_alpha)
from aurora.app.widgets import Card, Worker
from aurora.core.constants import DETS
from aurora.core.grid import to_grid
from aurora.core.hypercube import Hypercube, export_pymca_4d, export_pymca_xanes, pca_kmeans
from aurora.core.zones import members


class MapsPerspective(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.hc: Hypercube | None = None
        self._worker: Worker | None = None
        self.library = None                          # the Compare library (MainWindow sets it)
        self._roi: np.ndarray | None = None          # pixel indices of the ROI (None = all)
        self._cluster = None
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Maps"); h1.setObjectName("h1")
        sub = QLabel("the full hypercube of a stack: any emission window, any energy, any region"); sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub); head.addSpacing(16)
        head.addWidget(QLabel("stack"))
        self.scan_combo = QComboBox(); self.scan_combo.setMinimumWidth(260); head.addWidget(self.scan_combo)
        self.load_btn = QPushButton("Load hypercube"); self.load_btn.setObjectName("primary"); self.load_btn.clicked.connect(self.load)
        head.addWidget(self.load_btn)
        head.addStretch(1)
        self.status = QLabel(""); self.status.setObjectName("muted"); head.addWidget(self.status)
        root.addLayout(head)
        self.progress = QProgressBar(); self.progress.setVisible(False); root.addWidget(self.progress)

        ctl = QHBoxLayout()
        ctl.addWidget(QLabel("detector"))
        self.det = QComboBox(); self.det.addItems(["sum", "SDD1", "SDD2", "SDD3", "SDD4"]); ctl.addWidget(self.det)
        ctl.addWidget(QLabel("window"))
        self.line = QComboBox(); ctl.addWidget(self.line)
        ctl.addWidget(QLabel("eV"))
        self.w_lo = QDoubleSpinBox(); self.w_lo.setRange(0, 5000); self.w_lo.setValue(600); ctl.addWidget(self.w_lo)
        self.w_hi = QDoubleSpinBox(); self.w_hi.setRange(0, 5000); self.w_hi.setValue(720); ctl.addWidget(self.w_hi)
        self.per_i0 = QCheckBox("/ I0"); self.per_i0.setChecked(True); ctl.addWidget(self.per_i0)
        self.log = QCheckBox("log"); ctl.addWidget(self.log)
        self.deconv = QCheckBox("deconvolve"); ctl.addWidget(self.deconv)
        self.deconv_method = QComboBox(); self.deconv_method.addItems(["Richardson-Lucy", "Wiener"]); ctl.addWidget(self.deconv_method)
        self.psf_um = QDoubleSpinBox(); self.psf_um.setRange(0.5, 2000); self.psf_um.setValue(100); self.psf_um.setSuffix(" µm PSF"); ctl.addWidget(self.psf_um)
        self.deconv_iter = QSpinBox(); self.deconv_iter.setRange(1, 500); self.deconv_iter.setValue(20); self.deconv_iter.setSuffix(" it"); ctl.addWidget(self.deconv_iter)
        ctl.addWidget(QLabel("ROI"))
        self.roi_mode = QComboBox(); self.roi_mode.addItems(["whole map", "current zone", "rectangle (draw)"]); ctl.addWidget(self.roi_mode)
        ctl.addStretch(1)
        root.addLayout(ctl)
        erow = QHBoxLayout()
        erow.addWidget(QLabel("energy"))
        self.energy = QSlider(Qt.Horizontal); self.energy.setRange(0, 0); erow.addWidget(self.energy, 1)
        self.energy_label = QLabel("-"); self.energy_label.setMinimumWidth(90); erow.addWidget(self.energy_label)
        self.avg = QCheckBox("average over energies"); erow.addWidget(self.avg)
        root.addLayout(erow)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._explore_tab(), "Explore")
        self.tabs.addTab(self._elements_tab(), "Elemental maps")
        self.tabs.addTab(self._rgb_tab(), "RGB")
        self.tabs.addTab(self._cluster_tab(), "Clusters")
        root.addWidget(self.tabs, 1)
        self.det.currentIndexChanged.connect(self.redraw)
        self.line.currentIndexChanged.connect(self._line_picked)
        self.w_lo.valueChanged.connect(self.redraw); self.w_hi.valueChanged.connect(self.redraw)
        self.per_i0.toggled.connect(self.redraw); self.log.toggled.connect(self.redraw); self.avg.toggled.connect(self.redraw)
        self.energy.valueChanged.connect(self.redraw)
        self.roi_mode.currentIndexChanged.connect(self._roi_changed)
        self.deconv.toggled.connect(self.redraw); self.deconv_method.currentIndexChanged.connect(self.redraw)
        self.psf_um.valueChanged.connect(self.redraw); self.deconv_iter.valueChanged.connect(self.redraw)
        session.holder_changed.connect(self._holder_changed)
        session.zone_selected.connect(lambda z: self._roi_changed())
        session.zones_changed.connect(self._roi_changed)
        on_theme_changed(self, lambda _s: self._theme_changed())

    def _theme_changed(self) -> None:
        self._style_band()
        self.redraw()
        self._draw_clusters()

    def _style_band(self) -> None:
        """The emission window band: the action colour at 16 % alpha with an action pen."""
        self.band.setBrush(pg.mkBrush(with_alpha(pc("band"), 0.16)))
        for ln in self.band.lines:
            ln.setPen(role_pen("band", 1.4))

    # ------------------------------------------------------------------ tabs
    def _explore_tab(self) -> QWidget:
        w = QSplitter(Qt.Horizontal)
        left = Card("Map at the energy")
        self.map = MapView(); self.map.set_tool("navigate")
        self.map.rect_drawn.connect(self._rect)
        left.body.addWidget(self.map, 1)
        self.map_hint = QLabel(""); self.map_hint.setObjectName("muted"); left.body.addWidget(self.map_hint)
        w.addWidget(left)
        right = QSplitter(Qt.Vertical)
        sc = Card("Emission spectrum of the ROI (drag the band to set the window)")
        self.spec = make_plot(x="emission energy (eV)", y="counts")
        self.spec.getViewBox().disableAutoRange()
        self.band = pg.LinearRegionItem(values=[600, 720])
        self._style_band()
        self.band.sigRegionChangeFinished.connect(self._band_moved)
        self.spec.addItem(self.band, ignoreBounds=True)
        sc.body.addWidget(self.spec, 1)
        right.addWidget(sc)
        xc = Card("XANES of the ROI in the window")
        self.xanes = make_plot(x="incident energy (eV)", y="window counts / I0", legend="inside")
        self.xanes.getViewBox().disableAutoRange()
        xc.body.addWidget(self.xanes, 1)
        right.addWidget(xc)
        w.addWidget(right)
        w.setSizes([700, 760])
        return w

    def _elements_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        row = QHBoxLayout()
        b = QPushButton("Draw the elemental maps (every fixed line)"); b.clicked.connect(self.draw_elements); row.addWidget(b)
        self.el_export = QPushButton("Export PyMca 4D (_Elemental_PyMca.h5)…"); self.el_export.clicked.connect(self._export_4d); row.addWidget(self.el_export)
        row.addStretch(1)
        lay.addLayout(row)
        self.el_layout = pg.GraphicsLayoutWidget()
        style_plot(self.el_layout, "map")
        lay.addWidget(self.el_layout, 1)
        return w

    def _rgb_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.rgb_lines = []
        for k, name in enumerate(("R", "G", "B")):
            row.addWidget(QLabel(name))
            c = QComboBox(); row.addWidget(c); self.rgb_lines.append(c)
        b = QPushButton("Draw the RGB composite"); b.clicked.connect(self.draw_rgb); row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self.rgb_view = MapView(); self.rgb_view.set_tool("navigate"); self.rgb_view.set_lookup(None)   # RGB, no colour map
        lay.addWidget(self.rgb_view, 1)
        return w

    def _cluster_tab(self) -> QWidget:
        w = QSplitter(Qt.Horizontal)
        left = Card("PCA + k-means on the pixels (window XANES / I0)")
        row = QHBoxLayout()
        row.addWidget(QLabel("PCA components")); self.n_pc = QSpinBox(); self.n_pc.setRange(1, 20); self.n_pc.setValue(5); row.addWidget(self.n_pc)
        row.addWidget(QLabel("clusters")); self.n_k = QSpinBox(); self.n_k.setRange(2, 20); self.n_k.setValue(4); row.addWidget(self.n_k)
        b = QPushButton("Run"); b.setObjectName("primary"); b.clicked.connect(self.run_clusters); row.addWidget(b)
        b2 = QPushButton("Export PyMca XANES stack (_PCA-CA.h5)…"); b2.clicked.connect(self._export_xanes); row.addWidget(b2)
        row.addStretch(1)
        left.body.addLayout(row)
        self.cluster_map = MapView(); self.cluster_map.set_tool("navigate")
        self.cluster_map.set_lookup(self._cluster_lut(self.n_k.value()))       # categorical colours, one per cluster
        left.body.addWidget(self.cluster_map, 1)
        self.cluster_info = QLabel(""); self.cluster_info.setObjectName("muted"); self.cluster_info.setWordWrap(True); left.body.addWidget(self.cluster_info)
        w.addWidget(left)
        right = Card("Cluster mean spectra")
        self.cluster_plot = make_plot(x="incident energy (eV)", legend="inside"); self.cluster_plot.getViewBox().disableAutoRange()
        right.body.addWidget(self.cluster_plot, 1)
        w.addWidget(right)
        return w

    # ------------------------------------------------------------------ loading
    def _holder_changed(self, key: str) -> None:
        self.scan_combo.clear()
        for s in self.session.stacks_of(key):
            self.scan_combo.addItem(f"{s.time} {s.label} · {s.edge_text} {s.energy_text}", self.session.stack_id(s))
        self.hc = None; self._roi = None
        self.map.clear_overlays(); self.map.set_grid(None); self.spec.clear(); self.spec.addItem(self.band, ignoreBounds=True); self.xanes.clear()

    def load(self) -> None:
        sid = self.scan_combo.currentData()
        if not sid or self.session.project is None or (self._worker is not None and self._worker.isRunning()):
            return
        scan = self.session.scan_of(sid); project = self.session.project
        self.progress.setVisible(True); self.progress.setRange(0, 0); self.load_btn.setEnabled(False)
        self._worker = Worker(lambda cb: Hypercube.load(project, scan, progress=cb), self)
        self._worker.progress.connect(lambda i, n, m: (self.progress.setRange(0, max(n, 1)), self.progress.setValue(i), self.status.setText(m)), Qt.QueuedConnection)
        self._worker.done.connect(self._loaded, Qt.QueuedConnection)
        self._worker.failed.connect(lambda t: (self.progress.setVisible(False), self.load_btn.setEnabled(True), self.status.setText(t.strip().splitlines()[-1])), Qt.QueuedConnection)
        self._worker.start()

    def _loaded(self, hc: Hypercube) -> None:
        if self._worker is not None:
            self._worker.wait(30000)
        self.progress.setVisible(False); self.load_btn.setEnabled(True)
        self.set_hypercube(hc)

    def set_hypercube(self, hc: Hypercube) -> None:
        self.hc = hc
        self.energy.blockSignals(True); self.energy.setRange(0, hc.n_energies - 1); self.energy.setValue(hc.n_energies // 2); self.energy.blockSignals(False)
        bins = self.session.project.roi_bins if self.session.project else None
        self.line.blockSignals(True); self.line.clear(); self.line.addItem("custom (eV below)", None)
        for lab in (bins.labels if bins else []):
            ev = bins.line_ev(lab)
            self.line.addItem(f"{lab}" + (f" {ev:.0f} eV" if ev else ""), lab)
        self.line.blockSignals(False)
        for c in self.rgb_lines:
            c.clear(); c.addItems([self.line.itemText(i) for i in range(1, self.line.count())])
        sat = self.session.scan_of(hc.name and self.scan_combo.currentData()).summary.get("sdd_saturated", []) if self.scan_combo.currentData() else []
        self.status.setText(f"{hc.name}: {hc.n_energies} energies x {hc.n_pix} px x 256 ch x 4" + (f" · saturated {', '.join(sat).upper()}" if sat else "")
                            + (f" · {len(hc.skipped)} skipped" if hc.skipped else ""))
        self._roi_changed()
        self.redraw(); self.map.autorange()

    # ------------------------------------------------------------------ ROI and window
    def _roi_changed(self, *_) -> None:
        if self.hc is None:
            return
        mode = self.roi_mode.currentIndex()
        if mode == 0:
            self._roi = None
        elif mode == 1:
            z = self.session.zone()
            self._roi = members(z, self.hc.x, self.hc.y, self.hc.pitch) if z is not None else None
        self.map.set_tool("rect+" if mode == 2 else "navigate")
        self.redraw()

    def _rect(self, x0, x1, y0, y1) -> None:
        if self.hc is None:
            return
        px, py = self.hc.pitch
        hx, hy = px * 0.45, py * 0.45
        self._roi = np.where((self.hc.x >= x0 - hx) & (self.hc.x <= x1 + hx) & (self.hc.y >= y0 - hy) & (self.hc.y <= y1 + hy))[0]
        self.redraw()

    def _line_picked(self) -> None:
        lab = self.line.currentData()
        bins = self.session.project.roi_bins if self.session.project else None
        if lab and bins and self.hc is not None:
            b = bins.bounds(lab)
            d = 0 if self.det.currentIndex() == 0 else self.det.currentIndex() - 1
            lo, hi = b[DETS[d]]
            ev = self.hc.channel_axis(d)
            self.w_lo.blockSignals(True); self.w_hi.blockSignals(True)
            self.w_lo.setValue(float(ev[lo])); self.w_hi.setValue(float(ev[min(hi, 255)]))
            self.w_lo.blockSignals(False); self.w_hi.blockSignals(False)
        self.redraw()

    def _band_moved(self) -> None:
        lo, hi = self.band.getRegion()
        self.w_lo.blockSignals(True); self.w_hi.blockSignals(True)
        self.w_lo.setValue(lo); self.w_hi.setValue(hi)
        self.w_lo.blockSignals(False); self.w_hi.blockSignals(False)
        self.line.blockSignals(True); self.line.setCurrentIndex(0); self.line.blockSignals(False)
        self.redraw()

    def window(self, det: int) -> tuple[int, int]:
        return self.hc.window_channels(det, self.w_lo.value(), self.w_hi.value())

    # ------------------------------------------------------------------ drawing
    def redraw(self) -> None:
        hc = self.hc
        if hc is None:
            return
        d = None if self.det.currentIndex() == 0 else self.det.currentIndex() - 1
        d0 = 0 if d is None else d
        lo, hi = self.window(d0)
        ei = None if self.avg.isChecked() else self.energy.value()
        self.energy_label.setText(f"{hc.E[self.energy.value()]:.2f} eV" + ("" if hc.valid[self.energy.value()] else " (skipped)"))
        m = hc.window_map(d, lo, hi, ei, self.per_i0.isChecked())
        if self.log.isChecked():
            with np.errstate(all="ignore"):
                m = np.log10(np.maximum(m, 0) + 1.0)
        self.map.clear_overlays()
        g = to_grid(hc.x, hc.y, m)
        if self.deconv.isChecked():
            from aurora.core.deconv import deconvolve_grid
            g.img = deconvolve_grid(g.img, self.psf_um.value(), float(np.mean(hc.pitch)),
                                    "lucy" if self.deconv_method.currentIndex() == 0 else "wiener", self.deconv_iter.value())
        self.map.set_grid(g)
        if self._roi is not None and self._roi.size:
            self.map.draw_pixels(hc.x[self._roi], hc.y[self._roi], hc.pitch, MAP_OVERLAY["roi"])
        self.map_hint.setText(f"window ch {lo}-{hi - 1} ({self.w_lo.value():.0f}-{self.w_hi.value():.0f} eV) · "
                              + ("averaged over the valid energies" if ei is None else f"E index {ei}") + f" · ROI {len(self._roi) if self._roi is not None else hc.n_pix} px")
        # emission spectrum of the ROI
        self.spec.clear(); self.spec.addItem(self.band, ignoreBounds=True)
        axis = hc.channel_axis(d0)
        ys = []
        for k in (range(4) if d is None else [d]):
            sp = hc.spectrum(k, self._roi, None if ei is None else [ei])
            self.spec.plot(hc.channel_axis(k), sp, pen=pg.mkPen(curve(k), width=1.2, style=dash(k)))
            ys.append(sp)
        v = np.concatenate(ys)
        self.spec.getViewBox().setRange(xRange=(float(axis[0]), float(axis[-1])), yRange=(0, float(v.max()) if v.max() > 0 else 1), padding=0.05, disableAutoRange=True)
        self.band.blockSignals(True); self.band.setRegion([self.w_lo.value(), self.w_hi.value()]); self.band.blockSignals(False)
        # XANES of the ROI
        self.xanes.clear(); self.xanes.plotItem.legend.clear()
        xs = []
        for k in range(4):
            lo_k, hi_k = self.window(k)
            xa = hc.xanes(k, lo_k, hi_k, self._roi, self.per_i0.isChecked())
            self.xanes.plot(hc.E, xa, pen=pg.mkPen(curve(k), width=1, style=dash(k)), connect="finite", name=f"SDD{k + 1}")
            xs.append(xa)
        tot = np.nansum(xs, axis=0); tot[np.all(np.isnan(xs), axis=0)] = np.nan
        self.xanes.plot(hc.E, tot, pen=role_pen("sum", 2), connect="finite", name="sum")
        v = np.concatenate(xs + [tot]); v = v[np.isfinite(v)]
        if v.size:
            self.xanes.getViewBox().setRange(xRange=(float(hc.E[0]), float(hc.E[-1])), yRange=(float(v.min()), float(v.max()) if v.max() > v.min() else float(v.min()) + 1), padding=0.05, disableAutoRange=True)

    def draw_elements(self) -> None:
        hc = self.hc
        bins = self.session.project.roi_bins if self.session.project else None
        if hc is None or bins is None or not bins.labels:
            self.status.setText("load a hypercube; elemental maps need fixed ROI bins (config/roi_bins.json)"); return
        self.el_layout.clear()
        d = None if self.det.currentIndex() == 0 else self.det.currentIndex() - 1
        ei = None if self.avg.isChecked() else self.energy.value()
        from aurora.app.settings_dialog import get
        try:
            cmap = pg.colormap.get(get("map_colormap"))
        except Exception:
            cmap = pg.colormap.get("viridis")
        for i, lab in enumerate(bins.labels):
            b = bins.bounds(lab)
            if d is None:
                m = sum(hc.window_map(k, *b[DETS[k]], ei, False) for k in range(4))
                if self.per_i0.isChecked():
                    i0 = hc.i0[ei] if ei is not None else np.nanmean(hc.i0[hc.valid], axis=0)
                    with np.errstate(all="ignore"):
                        m = np.where(i0 > 0, m / i0, np.nan)
            else:
                m = hc.window_map(d, *b[DETS[d]], ei, self.per_i0.isChecked())
            g = to_grid(hc.x, hc.y, m)
            p = self.el_layout.addPlot(row=i // 4, col=i % 4, title=lab)
            p.setAspectLocked(True); p.hideAxis("left"); p.hideAxis("bottom")
            img = pg.ImageItem(g.img, axisOrder="row-major"); img.setLookupTable(cmap.getLookupTable(nPts=256))
            fin = np.isfinite(g.img)
            if fin.any():
                img.setLevels((float(np.nanmin(g.img)), float(np.nanmax(g.img)) or 1.0))
            img.setRect(pg.QtCore.QRectF(float(g.xs[0]) - g.dx / 2, float(g.ys[0]) - g.dy / 2, len(g.xs) * g.dx, len(g.ys) * g.dy))
            p.addItem(img)
            p.getViewBox().setRange(xRange=(float(g.xs[0]) - g.dx, float(g.xs[-1]) + g.dx), yRange=(float(g.ys[0]) - g.dy, float(g.ys[-1]) + g.dy), padding=0, disableAutoRange=True)
        restyle_plot(self.el_layout, "map")                       # titles and axes of the new plots

    def draw_rgb(self) -> None:
        hc = self.hc
        bins = self.session.project.roi_bins if self.session.project else None
        if hc is None or bins is None:
            return
        d = None if self.det.currentIndex() == 0 else self.det.currentIndex() - 1
        d0 = 0 if d is None else d
        wins = []
        for c in self.rgb_lines:
            lab = self.line.itemData(c.currentIndex() + 1)
            wins.append(bins.bounds(lab)[DETS[d0]] if lab else (0, 1))
        rgb = hc.rgb(d, wins, None if self.avg.isChecked() else self.energy.value())
        # one grid per colour, then stacked as an RGB image
        g = [to_grid(hc.x, hc.y, rgb[:, k]) for k in range(3)]
        img = np.stack([np.nan_to_num(gg.img) for gg in g], axis=-1)
        self.rgb_view.image.setImage(img, levels=[(0, 1), (0, 1), (0, 1)])
        self.rgb_view.set_lookup(None)
        self.rgb_view._data_rect = pg.QtCore.QRectF(float(g[0].xs[0]) - g[0].dx / 2, float(g[0].ys[0]) - g[0].dy / 2, len(g[0].xs) * g[0].dx, len(g[0].ys) * g[0].dy)
        self.rgb_view.image.setRect(self.rgb_view._data_rect)
        self.rgb_view.autorange()

    def run_clusters(self) -> None:
        hc = self.hc
        if hc is None:
            return
        d = None if self.det.currentIndex() == 0 else self.det.currentIndex() - 1
        lo, hi = self.window(0 if d is None else d)
        M = hc.pixel_matrix(d, lo, hi, self.per_i0.isChecked())
        cr = pca_kmeans(M, hc.E[hc.valid], self.n_pc.value(), self.n_k.value())
        self._cluster = cr
        n_k = cr.centres.shape[0]
        lab = cr.labels.astype(float); lab[lab < 0] = np.nan
        self.cluster_map.clear_overlays(); self.cluster_map.set_grid(to_grid(hc.x, hc.y, lab))
        self.cluster_map.set_lookup(self._cluster_lut(n_k))
        self.cluster_map.image.setLevels((0, max(n_k - 1, 1)))      # one categorical colour per cluster index
        self.cluster_map.autorange()
        self._draw_clusters()
        self.cluster_info.setText(f"{cr.centres.shape[0]} clusters on {int((cr.labels >= 0).sum())} px · PCA explained " + ", ".join(f"{100 * e:.1f} %" for e in cr.explained))

    @staticmethod
    def _cluster_lut(n: int) -> np.ndarray:
        from PySide6.QtGui import QColor
        return np.array([QColor(c).getRgb()[:3] for c in categorical(max(int(n), 2))], dtype=np.ubyte)

    def _draw_clusters(self) -> None:
        """Cluster mean spectra in the cluster map's categorical colours (legend colour = map colour)."""
        self.cluster_plot.clear(); self.cluster_plot.plotItem.legend.clear()
        cr = self._cluster
        if cr is None:
            return
        cols = categorical(max(cr.centres.shape[0], 2))
        for k in range(cr.centres.shape[0]):
            n = int((cr.labels == k).sum())
            self.cluster_plot.plot(cr.E, cr.centres[k], pen=pg.mkPen(cols[k], width=1.6, style=dash(k)), name=f"cluster {k + 1} ({n} px)")
        v = cr.centres[np.isfinite(cr.centres)]
        if v.size:
            self.cluster_plot.getViewBox().setRange(xRange=(float(cr.E[0]), float(cr.E[-1])), yRange=(float(v.min()), float(v.max()) if v.max() > v.min() else float(v.min()) + 1), padding=0.05, disableAutoRange=True)

    # ------------------------------------------------------------------ exports
    def _export_xanes(self) -> None:
        if self.hc is None:
            return
        p, _ = QFileDialog.getSaveFileName(self, "PyMca XANES stack", f"{self.hc.name}_PCA-CA.h5", "HDF5 (*.h5)")
        if p:
            d = None if self.det.currentIndex() == 0 else self.det.currentIndex() - 1
            lo, hi = self.window(0 if d is None else d)
            export_pymca_xanes(self.hc, Path(p), d, lo, hi, self.per_i0.isChecked())
            self.status.setText(f"written {p}")

    def _export_4d(self) -> None:
        if self.hc is None:
            return
        p, _ = QFileDialog.getSaveFileName(self, "PyMca 4D stack", f"{self.hc.name}_Elemental_PyMca.h5", "HDF5 (*.h5)")
        if p:
            export_pymca_4d(self.hc, Path(p), self.per_i0.isChecked())
            self.status.setText(f"written {p}")
