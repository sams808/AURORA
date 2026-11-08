"""Maps perspective (CLS dashboard parity): the hypercube of one stack. Map of any emission window at any energy,
ROI emission spectrum with a draggable window, XANES of the ROI, elemental maps per fixed line, RGB composite,
pixel PCA + k-means clusters with a cluster merger, PyMca exports.

Every sum uses each detector's own channels for the window (the SDDs have their own calibration) over the SDDs
ticked in the control row; SDDs flagged saturated start unticked. The view is split in three parts, each behind a
small LRU cache keyed by its inputs, so a slider step computes only what changed:
- map: energy, window, detectors, / I0, deconvolution
- spectrum: energy, ROI, detectors
- XANES: window, ROI, / I0 (a background job when it has to read the cube)
While the energy slider is dragged only the map follows (60 ms debounce); the rest follows on release. Clusters,
elemental maps, a costly deconvolution and both PyMca exports run as jobs with progress and Cancel.
"""
from __future__ import annotations

import csv
import hashlib
import io
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QLabel, QListView, QListWidget, QListWidgetItem, QPushButton, QSlider, QSpinBox,
                               QSplitter, QTabWidget, QVBoxLayout, QWidget)

from aurora.app import jobs, nav
from aurora.app.help_text import explain, explain_all, labelled
from aurora.app.mapview import MapView
from aurora.app.session import Session
from aurora.app.theme import (MAP_OVERLAY, TOKENS, categorical, composite, curve, dash, make_plot, on_theme_changed,
                              pc, restyle_plot, role_pen, set_tone, style_plot, swatch_icon, with_alpha)
from aurora.app.vocab import DET_LABELS, plural, unit_suffix
from aurora.app.widgets import (Card, ElidedLabel, EmptyState, FlowLayout, TaskBar, default_path, guarded_write,
                                notify_saved, overlay_host, pair, remember_dir, set_enabled, set_plot_hint)
from aurora.core import hypercube as hcmod
from aurora.core.compare import Spectrum
from aurora.core.constants import DETS
from aurora.core.deconv import deconvolve_cost, deconvolve_grid
from aurora.core.flags import saturated_dets
from aurora.core.grid import Grid, to_grid
from aurora.core.hypercube import (CacheSpaceError, Hypercube, bins_spec, describe_windows, export_pymca_4d,
                                   export_pymca_xanes, merged_spectra, paint_squares, windows_key)
from aurora.core.names import short_time
from aurora.core.zones import members

CHANNELS = 256                     # SDD channels per spectrum (the raw .bin layout)
CUBE_BYTES = 4                     # at most uint32 counts in the cached cube (uint16 when the counts allow)
NO_PROJECT = "open a project first"
NO_CUBE = "load a hypercube first"
NO_SDD = "tick at least one SDD"
NO_BINS = "elemental maps need fixed ROI bins (config/roi_bins.json)"
NO_CLUSTERS = "run the clustering first"
NO_TICKED = "tick at least one cluster"
RUNNING = "running"
MAP_DEBOUNCE_MS = 60               # while the energy slider is dragged, the map follows 60 ms after the last step
DECONV_SYNC_OPS = 2e6              # a deconvolution cheaper than this (multiply-adds) runs at once, a costlier one as a job
CACHE_ENTRIES = 48                 # entries of each view cache (maps, spectra); XANES keeps half as many
DIM_ALPHA = 0.3                    # unticked clusters: their colour at 30 % over the map background


class _LRU:
    """A small least-recently-used cache: get(key) is the value or None, put(key, value) keeps `size` entries."""

    def __init__(self, size: int):
        self.size = int(size)
        self._d: OrderedDict = OrderedDict()

    def get(self, key):
        if key in self._d:
            self._d.move_to_end(key)
            return self._d[key]
        return None

    def put(self, key, value) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.size:
            self._d.popitem(last=False)

    def clear(self) -> None:
        self._d.clear()

    def __len__(self) -> int:
        return len(self._d)


def _roi_key(roi) -> tuple | None:
    if roi is None:
        return None
    a = np.ascontiguousarray(np.asarray(roi, np.int64))
    return int(a.size), hashlib.sha1(a.tobytes()).hexdigest()


def _xanes_per_det(hc: Hypercube, windows: dict, roi, per_i0: bool, progress=None) -> dict[int, np.ndarray]:
    """The XANES of every detector of `windows` over `roi` (runs on a worker thread when it reads the cube)."""
    out = {}
    n = len(windows) * hc.n_energies
    for j, (d, w) in enumerate(sorted(windows.items())):
        cb = None
        if progress is not None:
            def cb(i, _n, _m="", j=j):
                progress(j * hc.n_energies + i, n, "computing the XANES of the ROI")
        out[d] = hc.xanes({d: w}, pix=roi, per_i0=per_i0, progress=cb)
    return out


class MapsPerspective(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.hc: Hypercube | None = None
        self.library = None                          # the Compare library (MainWindow sets it)
        self._roi: np.ndarray | None = None          # pixel indices of the ROI (None = all)
        self._roi_k = None
        self._cluster = None                         # ClusterResult of the last Run
        self._cluster_M: np.ndarray | None = None    # the (n_pix, nE_valid) matrix it was computed from
        self._cluster_meta: dict = {}                # provenance: scan, windows, SDDs, PCA components, k, seed
        self._gen = 0                                # bumped per hypercube: late job results of an older one are dropped
        self._dirty = False                          # a zone changed while Maps was hidden
        self._flagged: dict[int, str] = {}           # SDD index -> 'flagged saturated (source)'
        self._load_note = ""
        self.stats = {"redraws": 0, "maps": 0, "spectra": 0, "xanes": 0}
        self._map_cache = _LRU(CACHE_ENTRIES)
        self._spec_cache = _LRU(CACHE_ENTRIES)
        self._xanes_cache = _LRU(CACHE_ENTRIES // 2)
        self._xanes_want = None
        self._xanes_shown = None
        self._deconv_want = None
        self._roi_drawn = ("none",)
        self._grid_tpl = None
        self.element_grids: list[tuple[str, Grid]] = []
        self._map_timer = QTimer(self)
        self._map_timer.setSingleShot(True)
        self._map_timer.setInterval(MAP_DEBOUNCE_MS)
        self._map_timer.timeout.connect(self._draw_map)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Maps"); h1.setObjectName("h1")
        sub = ElidedLabel("the full hypercube of a stack: any emission window, any energy, any region", Qt.ElideRight)
        sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub, 1)
        self.scan_combo = QComboBox()
        self.scan_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.scan_combo.setMinimumContentsLength(26)
        self.scan_combo.currentIndexChanged.connect(self._scan_picked)
        head.addWidget(pair(labelled("stack", self.scan_combo, "maps.stack"), self.scan_combo))
        self.load_btn = QPushButton("Load hypercube"); self.load_btn.setObjectName("primary"); self.load_btn.clicked.connect(self.load)
        head.addWidget(self.load_btn)
        self.status = ElidedLabel("", Qt.ElideRight); self.status.setObjectName("muted"); head.addWidget(self.status, 1)
        root.addLayout(head)
        self.taskbar = TaskBar(self)
        root.addWidget(self.taskbar)

        ctl_box = QWidget()
        ctl = FlowLayout(ctl_box, spacing=6)
        sdd_box = QWidget()
        sl = QHBoxLayout(sdd_box); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(6)
        sl.addWidget(QLabel("sum of"))
        self.sdd_boxes: list[QCheckBox] = []
        for k, name in enumerate(DET_LABELS):
            cb = QCheckBox(name); cb.setChecked(True)
            explain(cb, "maps.sdd"); cb.setAccessibleName(f"include {name} in the sum")
            cb.toggled.connect(self._sdds_changed)
            sl.addWidget(cb); self.sdd_boxes.append(cb)
        ctl.addWidget(sdd_box)
        self.sdd_note = QLabel(""); set_tone(self.sdd_note, "warn"); self.sdd_note.hide()
        ctl.addWidget(self.sdd_note)
        self.line = QComboBox()
        self.line.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.line.setMinimumContentsLength(10)
        ctl.addWidget(pair(labelled("window", self.line, "maps.line"), self.line))
        self.w_lo = QDoubleSpinBox(); self.w_lo.setRange(0, 5000); self.w_lo.setValue(600)
        self.w_hi = QDoubleSpinBox(); self.w_hi.setRange(0, 5000); self.w_hi.setValue(720)
        for w in (self.w_lo, self.w_hi):
            w.setSuffix(unit_suffix("eV"))
        ctl.addWidget(pair(self.w_lo, self.w_hi))
        self.per_i0 = QCheckBox("/ I0"); self.per_i0.setChecked(True); ctl.addWidget(self.per_i0)
        self.log = QCheckBox("log"); ctl.addWidget(self.log)
        self.deconv = QCheckBox("deconvolve"); ctl.addWidget(self.deconv)
        self.deconv_method = QComboBox(); self.deconv_method.addItems(["Richardson-Lucy", "Wiener"]); ctl.addWidget(self.deconv_method)
        self.psf_um = QDoubleSpinBox(); self.psf_um.setRange(0.5, 2000); self.psf_um.setValue(100); self.psf_um.setSuffix(unit_suffix("um"))
        ctl.addWidget(pair(labelled("PSF", self.psf_um, "maps.psf"), self.psf_um))
        self.deconv_iter = QSpinBox(); self.deconv_iter.setRange(1, 500); self.deconv_iter.setValue(20)
        self.deconv_iter.setSuffix(unit_suffix("iterations")); ctl.addWidget(self.deconv_iter)
        self.roi_mode = QComboBox(); self.roi_mode.addItems(["whole map", "current zone", "rectangle (draw)"])
        ctl.addWidget(pair(labelled("ROI", self.roi_mode, "maps.roi"), self.roi_mode))
        root.addWidget(ctl_box)
        erow = QHBoxLayout()
        self.energy = QSlider(Qt.Horizontal); self.energy.setRange(0, 0)
        erow.addWidget(labelled("energy", self.energy, "maps.energy")); erow.addWidget(self.energy, 1)
        self.energy_label = QLabel("-"); self.energy_label.setMinimumWidth(110); erow.addWidget(self.energy_label)
        self.avg = QCheckBox("average over energies"); erow.addWidget(self.avg)
        root.addLayout(erow)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._explore_tab(), "Explore")
        self.tabs.addTab(self._elements_tab(), "Elemental maps")
        self.tabs.addTab(self._rgb_tab(), "RGB")
        self.tabs.addTab(self._cluster_tab(), "Clusters")
        self.body = overlay_host(self.tabs)
        root.addWidget(self.body, 1)
        self.empty_state = EmptyState("No project open: the hypercube of any stack opens here.",
                                      [("Open project…", lambda: nav.open_project_dialog(self)),
                                       ("Open the demo beamtime", lambda: nav.open_demo(self))], icon="maps",
                                      parent=self.body)
        self.cube_state = EmptyState("No hypercube loaded.", [("Load hypercube", self.load)], icon="maps", parent=self.body)
        explain_all([(self.load_btn, "maps.load"), (self.w_lo, "maps.w_lo"), (self.w_hi, "maps.w_hi"),
                     (self.per_i0, "maps.per_i0"), (self.log, "maps.log"), (self.deconv, "maps.deconv"),
                     (self.deconv_method, "maps.deconv_method"), (self.deconv_iter, "maps.iter"), (self.avg, "maps.avg")])
        self.line.currentIndexChanged.connect(self._line_picked)
        self.w_lo.valueChanged.connect(self._custom_window); self.w_hi.valueChanged.connect(self._custom_window)
        self.per_i0.toggled.connect(self.redraw); self.log.toggled.connect(self.redraw); self.avg.toggled.connect(self.redraw)
        self.energy.valueChanged.connect(self._energy_changed)
        self.energy.sliderReleased.connect(self._energy_released)
        self.roi_mode.currentIndexChanged.connect(self._roi_changed)
        self.deconv.toggled.connect(self.redraw); self.deconv_method.currentIndexChanged.connect(self.redraw)
        self.psf_um.valueChanged.connect(self.redraw); self.deconv_iter.valueChanged.connect(self.redraw)
        session.project_changed.connect(lambda _p: self.update_actions())
        session.holder_changed.connect(self._holder_changed)
        session.zone_selected.connect(lambda _z: self._zones_changed())
        session.zones_changed.connect(self._zones_changed)
        on_theme_changed(self, lambda _s: self._theme_changed())
        self.update_actions()
        self._draw_empty()

    def window(self, det: int | None = None):
        """QWidget.window() without an argument; with a detector, its emission window in channels (window_channels)."""
        if det is None:
            return QWidget.window(self)
        return self.window_channels(det)

    def cube_estimate(self, sid: str | None = None) -> str:
        """'up to 120 MB' for the stack's cached cube (energies x pixels x 256 channels x 4 detectors, at most 4 bytes)."""
        sid = sid or self.scan_combo.currentData()
        scan = self.session.scan_of(sid) if sid else None
        if scan is None or not scan.n_energies or not scan.n_points:
            return ""
        if Hypercube.cache_exists(self.session.project, scan):
            return "cached, opens at once"
        mb = scan.n_energies * scan.n_points * CHANNELS * len(DETS) * CUBE_BYTES / 1e6
        return f"up to {mb:.0f} MB, cached under .aurora/cache"

    # ------------------------------------------------------------------ state of the actions
    def _running(self, key: str) -> bool:
        job = jobs.of(self).job(key)
        return job is not None and job.is_running()

    def ticked(self) -> tuple[int, ...]:
        """The SDDs in the sum (indices 0..3)."""
        return tuple(k for k, cb in enumerate(self.sdd_boxes) if cb.isChecked())

    def ticked_clusters(self) -> list[int]:
        out = []
        for i in range(self.cluster_list.count()):
            it = self.cluster_list.item(i)
            if it.checkState() == Qt.Checked:
                out.append(int(it.data(Qt.UserRole)))
        return out

    def update_actions(self) -> None:
        has = self.session.project is not None
        sid = self.scan_combo.currentData()
        busy = sid is not None and self._running(f"cube:{sid}")
        set_enabled(self.load_btn, has and bool(sid) and not busy,
                    NO_PROJECT if not has else ("loading" if busy else "the holder has no complete stack"))
        cube = self.hc is not None
        sdds = bool(self.ticked())
        bins = bool(bins_spec(self.session.project.roi_bins)) if has else False
        base = NO_PROJECT if not has else NO_CUBE

        def why(key: str | None, need_sdd: bool = True, need_bins: bool = False) -> tuple[bool, str]:
            if not cube:
                return False, base
            if key and self._running(key):
                return False, RUNNING
            if need_sdd and not sdds:
                return False, NO_SDD
            if need_bins and not bins:
                return False, NO_BINS
            return True, ""

        for b, args in ((self.draw_el_btn, ("maps:elements", True, True)), (self.el_export, ("maps:export_4d", False)),
                        (self.draw_rgb_btn, (None, True, True)), (self.cluster_btn, ("maps:clusters",)),
                        (self.xanes_export, ("maps:export_xanes",))):
            set_enabled(b, *why(*args))
        has_cl = self._cluster is not None
        ticked = bool(self.ticked_clusters()) if has_cl else False
        reason = base if not cube else (NO_CLUSTERS if not has_cl else NO_TICKED)
        for b in (self.zone_btn, self.send_btn):
            set_enabled(b, cube and has_cl and ticked, reason)
        set_enabled(self.cluster_export, cube and has_cl, base if not cube else NO_CLUSTERS)
        self.empty_state.set_shown(not has)
        show_cube = has and not cube and bool(sid) and not busy
        if show_cube:
            if self._load_note:
                text = self._load_note
            else:
                est = self.cube_estimate(sid)
                text = (f"No hypercube loaded. Load the one of {self.scan_combo.currentText()}"
                        + (f" ({est})." if est else "."))
            self.cube_state.set_content(text, [("Load hypercube", self.load)])
        self.cube_state.set_shown(show_cube)

    def _draw_empty(self) -> None:
        for plot, text in ((self.spec, "the emission spectrum of the ROI appears here"),
                           (self.xanes, "the XANES of the ROI appears here"),
                           (self.cluster_plot, "Run the clustering to see the cluster spectra"),
                           (self.merged_plot, "the merged spectra of the ticked clusters appear here")):
            set_plot_hint(plot, text)

    def _theme_changed(self) -> None:
        self._style_band()
        for k, c in enumerate(self._spec_curves):
            c.setPen(pg.mkPen(curve(k), width=1.2, style=dash(k)))
        self._e_marker.setPen(role_pen("edge", 1.0))
        self._xanes_shown = None
        self.redraw()
        if self._cluster is not None:
            self._fill_cluster_list(keep=True)
            self._show_cluster_map()
        self._draw_clusters()

    def _style_band(self) -> None:
        """The emission window band: the action colour at 16 % alpha with an action pen."""
        self.band.setBrush(pg.mkBrush(with_alpha(pc("band"), 0.16)))
        for ln in self.band.lines:
            ln.setPen(role_pen("band", 1.4))

    # ------------------------------------------------------------------ tabs
    def _explore_tab(self) -> QWidget:
        w = QSplitter(Qt.Horizontal)
        left = Card("Map at the energy", help_anchor="8-maps")
        self.map = MapView(); self.map.set_tool("navigate")
        self.map.rect_drawn.connect(self._rect)
        left.body.addWidget(self.map, 1)
        self.map_hint = ElidedLabel("", Qt.ElideMiddle); self.map_hint.setObjectName("muted"); left.body.addWidget(self.map_hint)
        w.addWidget(left)
        right = QSplitter(Qt.Vertical)
        sc = Card("Emission spectrum of the ROI (drag the band to set the window)", help_anchor="8-maps")
        self.spec = make_plot(x="emission energy (eV)", y="counts")
        self.spec.getViewBox().disableAutoRange()
        self._spec_curves = [self.spec.plot([], [], pen=pg.mkPen(curve(k), width=1.2, style=dash(k))) for k in range(len(DETS))]
        self.band = pg.LinearRegionItem(values=[600, 720])
        self._style_band()
        self.band.sigRegionChangeFinished.connect(self._band_moved)
        self.spec.addItem(self.band, ignoreBounds=True)
        sc.body.addWidget(self.spec, 1)
        right.addWidget(sc)
        xc = Card("XANES of the ROI in the window", help_anchor="8-maps")
        self.xanes = make_plot(x="incident energy (eV)", y="window counts / I0", legend="inside")
        self.xanes.plotItem.legend.setColumnCount(2)      # five entries in three rows: the curves stay visible
        self.xanes.getViewBox().disableAutoRange()
        self._e_marker = pg.InfiniteLine(angle=90, movable=False, pen=role_pen("edge", 1.0))
        self.xanes.addItem(self._e_marker, ignoreBounds=True)
        xc.body.addWidget(self.xanes, 1)
        right.addWidget(xc)
        w.addWidget(right)
        w.setSizes([700, 760])
        return w

    def _elements_tab(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w)
        row = QHBoxLayout()
        b = QPushButton("Draw the elemental maps (every fixed line)"); b.clicked.connect(self.draw_elements); row.addWidget(b)
        self.draw_el_btn = b
        self.el_export = QPushButton("Export PyMca 4D (_Elemental_PyMca.h5)…"); self.el_export.clicked.connect(self._export_4d); row.addWidget(self.el_export)
        explain_all([(b, "maps.draw_elements"), (self.el_export, "maps.export_4d")])
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
            c = QComboBox()
            row.addWidget(labelled(name, c, "maps.rgb")); row.addWidget(c); self.rgb_lines.append(c)
            c.setAccessibleName(f"{name} window")
        b = QPushButton("Draw the RGB composite"); b.clicked.connect(self.draw_rgb); row.addWidget(b)
        self.draw_rgb_btn = b
        explain(b, "maps.draw_rgb")
        row.addStretch(1)
        lay.addLayout(row)
        self.rgb_view = MapView(); self.rgb_view.set_tool("navigate"); self.rgb_view.set_lookup(None)   # RGB, no colour map
        lay.addWidget(self.rgb_view, 1)
        return w

    def _cluster_tab(self) -> QWidget:
        w = QSplitter(Qt.Horizontal)
        left = Card("PCA + k-means on the pixels (window XANES / I0)", help_anchor="8-maps")
        row_box = QWidget(); row = FlowLayout(row_box, spacing=6)
        self.n_pc = QSpinBox(); self.n_pc.setRange(1, 20); self.n_pc.setValue(5); self.n_pc.setSuffix(unit_suffix("components"))
        row.addWidget(pair(labelled("PCA", self.n_pc, "maps.n_pc"), self.n_pc))
        self.n_k = QSpinBox(); self.n_k.setRange(2, 20); self.n_k.setValue(4); self.n_k.setSuffix(unit_suffix("clusters"))
        row.addWidget(pair(labelled("k-means", self.n_k, "maps.n_k"), self.n_k))
        self.seed = QSpinBox(); self.seed.setRange(0, 2_147_483_647); self.seed.setValue(0)
        row.addWidget(pair(labelled("seed (k-means)", self.seed, "maps.seed"), self.seed))
        b = QPushButton("Run"); b.setObjectName("primary"); b.clicked.connect(self.run_clusters); row.addWidget(b)
        b2 = QPushButton("Export PyMca XANES stack (_PCA-CA.h5)…"); b2.clicked.connect(self._export_xanes); row.addWidget(b2)
        self.cluster_btn, self.xanes_export = b, b2
        explain_all([(b, "maps.run_clusters"), (b2, "maps.export_xanes")])
        left.body.addWidget(row_box)
        self.cluster_map = MapView(); self.cluster_map.set_tool("navigate")
        self.cluster_map.set_lookup(self._cluster_lut(self.n_k.value()))       # categorical colours, one per cluster
        left.body.addWidget(self.cluster_map, 1)
        self.cluster_info = QLabel(""); self.cluster_info.setObjectName("muted"); self.cluster_info.setWordWrap(True); left.body.addWidget(self.cluster_info)
        w.addWidget(left)
        right = Card("Clusters: tick to merge", help_anchor="8-maps")
        self.cluster_list = QListWidget()               # the legend of the map and the spectra: same swatches
        self.cluster_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cluster_list.setFlow(QListView.LeftToRight)
        self.cluster_list.setWrapping(True)
        self.cluster_list.setResizeMode(QListView.Adjust)
        self.cluster_list.setSpacing(2)
        row_h = max(self.fontMetrics().height(), 16) + 10
        self.cluster_list.setFixedHeight(2 * row_h + 2 * self.cluster_list.frameWidth() + 4)    # two rows, then scroll
        explain(self.cluster_list, "maps.clusters")
        self.cluster_list.itemChanged.connect(lambda _it: self._cluster_ticks_changed())
        right.body.addWidget(self.cluster_list)
        act_box = QWidget(); act = FlowLayout(act_box, spacing=6)
        self.zone_btn = QPushButton("Make zone from ticked clusters"); self.zone_btn.clicked.connect(self.make_zone_from_clusters)
        self.send_btn = QPushButton("Send to Compare"); self.send_btn.clicked.connect(self.send_clusters_to_compare)
        self.cluster_export = QPushButton("Export…"); self.cluster_export.clicked.connect(self._export_clusters)
        for x in (self.zone_btn, self.send_btn, self.cluster_export):
            act.addWidget(x)
        explain_all([(self.zone_btn, "maps.make_zone"), (self.send_btn, "maps.send"),
                     (self.cluster_export, "maps.export_clusters")])
        right.body.addWidget(act_box)
        plots = QSplitter(Qt.Vertical)
        self.cluster_plot = make_plot(x="incident energy (eV)", y="cluster mean", legend="inside")
        self.cluster_plot.getViewBox().disableAutoRange()
        plots.addWidget(self.cluster_plot)
        self.merged_plot = make_plot(x="incident energy (eV)", y="ticked sum", legend="inside")
        self.merged_plot.getViewBox().disableAutoRange()
        plots.addWidget(self.merged_plot)
        plots.setSizes([320, 240])
        right.body.addWidget(plots, 1)
        w.addWidget(right)
        w.setSizes([760, 700])
        return w

    # ------------------------------------------------------------------ loading
    def _scan_picked(self, _i: int) -> None:
        self._load_note = ""
        self.update_actions()

    def _holder_changed(self, key: str) -> None:
        self.scan_combo.blockSignals(True)
        self.scan_combo.clear()
        for s in self.session.stacks_of(key):
            self.scan_combo.addItem(f"{s.time} {s.label} · {s.edge_text} {s.energy_text}", self.session.stack_id(s))
        self.scan_combo.blockSignals(False)
        self._load_note = ""
        self._reset_cube(None)
        self._draw_empty()
        self.update_actions()

    def _reset_cube(self, hc: Hypercube | None) -> None:
        self.hc = hc
        self._gen += 1
        self._roi = None; self._roi_k = None
        for c in (self._map_cache, self._spec_cache, self._xanes_cache):
            c.clear()
        self._xanes_want = self._xanes_shown = self._deconv_want = None
        self._grid_tpl = None
        self._roi_drawn = ("none",)
        self._cluster = None; self._cluster_M = None; self._cluster_meta = {}
        self.cluster_list.blockSignals(True); self.cluster_list.clear(); self.cluster_list.blockSignals(False)
        self.cluster_map.clear_overlays(); self.cluster_map.set_grid(None)
        self.cluster_info.setText("")
        self._draw_clusters()
        self.element_grids = []
        self.map.clear_overlays(); self.map.set_grid(None)
        for c in self._spec_curves:
            c.setData([], [])
        self.xanes.clear(); self.xanes.plotItem.legend.clear(); self.xanes.addItem(self._e_marker, ignoreBounds=True)

    def load(self) -> None:
        sid = self.scan_combo.currentData()
        if not sid or self.session.project is None:
            return
        scan = self.session.scan_of(sid); project = self.session.project
        self._load_note = ""

        def work(cb):
            try:
                return Hypercube.load(project, scan, progress=cb)
            except CacheSpaceError as err:          # a calm refusal: nothing was written
                return err
        jobs.of(self).submit(f"cube:{sid}", work, on_done=self._loaded,
                             on_failed=lambda t: self.status.setText(t.strip().splitlines()[-1]),
                             on_end=lambda _s: self.update_actions(), label=f"loading the hypercube of {scan.name}",
                             taskbar=self.taskbar)
        self.update_actions()

    def _loaded(self, hc) -> None:
        if isinstance(hc, CacheSpaceError):
            self._load_note = f"Not loaded: the hypercube {hc}. Free some disk space, then load it again."
            self.status.setText(f"hypercube not loaded: {hc}")
            self.update_actions()
            return
        self.set_hypercube(hc)

    def set_hypercube(self, hc: Hypercube) -> None:
        self._reset_cube(hc)
        self.energy.blockSignals(True); self.energy.setRange(0, hc.n_energies - 1); self.energy.setValue(hc.n_energies // 2); self.energy.blockSignals(False)
        bins = self.session.project.roi_bins if self.session.project else None
        self.line.blockSignals(True); self.line.clear(); self.line.addItem("custom (eV below)", None)
        for lab in (bins.labels if bins else []):
            ev = bins.line_ev(lab)
            self.line.addItem(f"{lab}" + (f" {ev:.0f} eV" if ev else ""), lab)
        self.line.blockSignals(False)
        for c in self.rgb_lines:
            c.clear(); c.addItems([self.line.itemText(i) for i in range(1, self.line.count())])
        self._apply_flags(hc)
        sat = ", ".join(DET_LABELS[k] for k in sorted(self._flagged))
        self.status.setText(f"{hc.name}: {hc.n_energies} energies x {hc.n_pix} px x 256 ch x 4"
                            + (f" ({hc.dtype})" if hc.dtype else "") + (f" · saturated {sat}" if sat else "")
                            + (f" · {len(hc.skipped)} skipped" if hc.skipped else ""))
        self._roi_changed()
        self.map.autorange()
        self.update_actions()

    def _apply_flags(self, hc: Hypercube) -> None:
        """SDDs flagged saturated (merged flags: sgm_report summary and AURORA's checks) start unticked: flagged and
        off by default, the scan itself is never dropped."""
        scan = self.session.project.find(hc.name) if self.session.project is not None else None
        flags = self.session.flags_of(scan) if scan is not None else []
        sat = saturated_dets(flags)
        self._flagged = {}
        for f in flags:
            for d in f.dets:
                if f.kind == "sdd_saturated" and d in DETS:
                    self._flagged[DETS.index(d)] = f.source
        for k, cb in enumerate(self.sdd_boxes):
            cb.blockSignals(True)
            cb.setChecked(DETS[k] not in sat)
            cb.blockSignals(False)
            base = cb.property("aurora_tip") or cb.toolTip()
            cb.setToolTip(base + (f"\n{DET_LABELS[k]} is flagged saturated ({self._flagged[k]}): off by default."
                                  if k in self._flagged else ""))
        if self._flagged:
            names = ", ".join(DET_LABELS[k] for k in sorted(self._flagged))
            srcs = ", ".join(sorted(set(self._flagged.values())))
            self.sdd_note.setText(f"{names} flagged saturated ({srcs}): not in the sum by default")
            self.sdd_note.show()
        else:
            self.sdd_note.setText("")
            self.sdd_note.hide()

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
        self._roi_k = _roi_key(self._roi)
        self.map.set_tool("rect+" if mode == 2 else "navigate")
        self.redraw()

    def _zones_changed(self, *_) -> None:
        """Zones matter only for the 'current zone' ROI; while Maps is hidden they mark it dirty (one redraw when shown)."""
        if self.hc is None or self.roi_mode.currentIndex() != 1:
            return
        if not self.isVisible():
            self._dirty = True
            return
        self._roi_changed()

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if self._dirty:
            self._dirty = False
            self._roi_changed()

    def _rect(self, x0, x1, y0, y1) -> None:
        if self.hc is None:
            return
        px, py = self.hc.pitch
        hx, hy = px * 0.45, py * 0.45
        self._roi = np.where((self.hc.x >= x0 - hx) & (self.hc.x <= x1 + hx) & (self.hc.y >= y0 - hy) & (self.hc.y <= y1 + hy))[0]
        self._roi_k = _roi_key(self._roi)
        self.redraw()

    def _line_picked(self) -> None:
        lab = self.line.currentData()
        bins = self.session.project.roi_bins if self.session.project else None
        if lab and bins and bins.has(lab) and self.hc is not None:
            b = bins.bounds(lab)
            d = (self.ticked() or (0,))[0]
            lo, hi = b[DETS[d]]
            ev = self.hc.channel_axis(d)
            self.w_lo.blockSignals(True); self.w_hi.blockSignals(True)
            self.w_lo.setValue(float(ev[lo])); self.w_hi.setValue(float(ev[min(hi, 255)]))
            self.w_lo.blockSignals(False); self.w_hi.blockSignals(False)
        self.redraw()

    def _custom_window(self) -> None:
        """The eV bounds were edited: the window is custom (each SDD's own calibration) from now on."""
        if self.line.currentIndex() != 0:
            self.line.blockSignals(True); self.line.setCurrentIndex(0); self.line.blockSignals(False)
        self.redraw()

    def _band_moved(self) -> None:
        lo, hi = self.band.getRegion()
        self.w_lo.blockSignals(True); self.w_hi.blockSignals(True)
        self.w_lo.setValue(lo); self.w_hi.setValue(hi)
        self.w_lo.blockSignals(False); self.w_hi.blockSignals(False)
        self.line.blockSignals(True); self.line.setCurrentIndex(0); self.line.blockSignals(False)
        self.redraw()

    def current_windows(self) -> dict[int, tuple[int, int]]:
        """Each detector's own channels: the fixed bins of the chosen line, else the eV window through each SDD's own
        calibration. Always all four detectors; the ticks choose which ones are summed."""
        lab = self.line.currentData()
        bins = self.session.project.roi_bins if self.session.project else None
        if lab and bins is not None and bins.has(lab):
            b = bins.bounds(lab)
            return {d: (int(b[DETS[d]][0]), int(b[DETS[d]][1])) for d in range(len(DETS))}
        return self.hc.ev_windows(self.w_lo.value(), self.w_hi.value())

    def window_channels(self, det: int) -> tuple[int, int]:
        """The emission window of detector `det` in its own channels (the line's bins, or w_lo..w_hi eV)."""
        return self.current_windows()[det]

    def _sdds_changed(self, _on: bool = False) -> None:
        self.redraw()
        self.update_actions()

    def _energy_changed(self, _v: int) -> None:
        if self.energy.isSliderDown():
            self._map_timer.start()                  # dragging: only the map, 60 ms after the last step
        else:
            self.redraw()

    def _energy_released(self) -> None:
        self._map_timer.stop()
        self.redraw()

    # ------------------------------------------------------------------ drawing
    def redraw(self) -> None:
        """Map, spectrum and XANES of the current inputs, each from its cache when nothing it depends on changed."""
        if self.hc is None:
            return
        self.stats["redraws"] += 1
        self._map_timer.stop()
        self._draw_map()
        self._draw_spectrum()
        self._draw_xanes()

    def _ei(self) -> int | None:
        return None if self.avg.isChecked() else int(self.energy.value())

    def _deconv_key(self):
        if not self.deconv.isChecked():
            return None
        return (self.deconv_method.currentIndex(), round(float(self.psf_um.value()), 4), int(self.deconv_iter.value()))

    def _grid(self, m: np.ndarray) -> Grid:
        """to_grid of a per-pixel array, with the grid geometry of this cube computed once."""
        if self._grid_tpl is None:
            g = to_grid(self.hc.x, self.hc.y, np.zeros(self.hc.n_pix))
            ok = g.ix >= 0
            cnt = np.zeros(g.img.shape)
            np.add.at(cnt, (g.iy[ok], g.ix[ok]), 1)
            self._grid_tpl = (g, ok, cnt)
        g, ok, cnt = self._grid_tpl
        acc = np.zeros(cnt.shape)
        np.add.at(acc, (g.iy[ok], g.ix[ok]), np.nan_to_num(np.asarray(m, float)[ok]))
        img = np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan)
        return Grid(img=img, xs=g.xs, ys=g.ys, ix=g.ix, iy=g.iy)

    def _label_grid(self, labels: np.ndarray) -> Grid:
        """Categorical grid: each cell takes the label of its point (never an average of labels); NaN when none."""
        self._grid(np.zeros(self.hc.n_pix))
        g, ok, _cnt = self._grid_tpl
        lab = np.asarray(labels)
        use = ok & (lab >= 0)
        img = np.full(g.img.shape, np.nan)
        img[g.iy[use], g.ix[use]] = lab[use]
        return Grid(img=img, xs=g.xs, ys=g.ys, ix=g.ix, iy=g.iy)

    def _map_key(self) -> tuple:
        dets = self.ticked()
        w = self.current_windows()
        return (self._gen, self._ei(), windows_key({d: w[d] for d in dets}), dets, self.per_i0.isChecked(),
                self._deconv_key())

    def _map_grid(self) -> tuple[Grid, str]:
        """The grid of the current map, from the cache when its inputs did not change; (grid, note)."""
        key = self._map_key()
        g = self._map_cache.get(key)
        if g is not None:
            return g, ""
        raw_key = key[:-1] + (None,)
        raw = self._map_cache.get(raw_key)
        gen, ei, _wk, dets, per_i0, dk = key
        if raw is None:
            self.stats["maps"] += 1
            w = self.current_windows()
            m = self.hc.window_map({d: w[d] for d in dets}, energy_index=ei, per_i0=per_i0) if dets \
                else np.full(self.hc.n_pix, np.nan)
            raw = self._grid(m)
            self._map_cache.put(raw_key, raw)
        if dk is None or not dets:
            return raw, ""
        method, psf, n_iter = dk
        meth = "lucy" if method == 0 else "wiener"
        pitch = float(np.mean(self.hc.pitch))
        if deconvolve_cost(raw.img.shape, psf, pitch, meth, n_iter) <= DECONV_SYNC_OPS:
            g = replace(raw, img=deconvolve_grid(raw.img, psf, pitch, meth, n_iter))
            self._map_cache.put(key, g)
            return g, ""
        self._request_deconv(key, raw, psf, pitch, meth, n_iter)
        return raw, "deconvolving …"

    def _request_deconv(self, key, raw: Grid, psf: float, pitch: float, meth: str, n_iter: int) -> None:
        self._deconv_want = key
        if self._running("maps:deconv"):
            return                                   # its end draws again, asking for the key wanted then
        img = raw.img.copy()

        def work(cb):
            return key, deconvolve_grid(img, psf, pitch, meth, n_iter, progress=cb)
        jobs.of(self).submit("maps:deconv", work, on_done=lambda r: self._deconv_done(r, raw),
                             on_end=self._deconv_end, label="deconvolving the map", taskbar=self.taskbar)

    def _deconv_done(self, result, raw: Grid) -> None:
        key, img = result
        if key[0] != self._gen:
            return
        self._map_cache.put(key, replace(raw, img=img))

    def _deconv_end(self, state: str) -> None:
        if self.hc is None:
            return
        if state == "done" or self._deconv_want != self._map_key():
            self._draw_map()
        elif state == "cancelled":
            self.map_hint.setText(self.map_hint.full_text().replace("deconvolving …", "deconvolution cancelled"))

    def _draw_map(self) -> None:
        hc = self.hc
        if hc is None:
            return
        e = int(self.energy.value())
        self.energy_label.setText(f"{hc.E[e]:.2f} eV" + ("" if hc.valid[e] else " (skipped)"))
        g, note = self._map_grid()
        if self.log.isChecked():
            with np.errstate(all="ignore"):
                g = replace(g, img=np.log10(np.maximum(g.img, 0) + 1.0))
        self.map.set_grid(g)
        rk = (self._gen, self._roi_k)
        if rk != self._roi_drawn:                    # the ROI outline changes only with the ROI
            self.map.clear_overlays()
            if self._roi is not None and self._roi.size:
                self.map.draw_pixels(hc.x[self._roi], hc.y[self._roi], hc.pitch, MAP_OVERLAY["roi"])
            self._roi_drawn = rk
        dets = self.ticked()
        w = self.current_windows()
        lab = self.line.currentData()
        what = (lab if lab else f"{self.w_lo.value():.0f}-{self.w_hi.value():.0f} eV")
        ei = self._ei()
        parts = [f"{what}: {describe_windows({d: w[d] for d in dets})}" if dets else NO_SDD,
                 "averaged over the valid energies" if ei is None else f"at {hc.E[ei]:.2f} eV",
                 f"ROI {len(self._roi) if self._roi is not None else hc.n_pix} px"]
        if note:
            parts.append(note)
        self.map_hint.setText(" · ".join(parts))

    def _draw_spectrum(self) -> None:
        hc = self.hc
        ei = self._ei()
        dets = self.ticked()
        key = (self._gen, ei, self._roi_k, dets)
        sp = self._spec_cache.get(key)
        if sp is None:
            self.stats["spectra"] += 1
            sp = {d: hc.spectrum(d, self._roi, None if ei is None else [ei]) for d in dets}
            self._spec_cache.put(key, sp)
        for k, c in enumerate(self._spec_curves):
            if k in sp:
                c.setData(hc.channel_axis(k), sp[k])
            else:
                c.setData([], [])
        set_plot_hint(self.spec, None if dets else NO_SDD)
        axis = hc.channel_axis(dets[0] if dets else 0)
        top = max((float(np.max(v)) for v in sp.values() if v.size), default=0.0)
        self.spec.getViewBox().setRange(xRange=(float(axis[0]), float(axis[-1])), yRange=(0, top if top > 0 else 1),
                                        padding=0.05, disableAutoRange=True)
        self.band.blockSignals(True); self.band.setRegion([self.w_lo.value(), self.w_hi.value()]); self.band.blockSignals(False)

    def _xanes_key(self) -> tuple:
        return (self._gen, windows_key(self.current_windows()), self._roi_k, self.per_i0.isChecked())

    def _draw_xanes(self) -> None:
        hc = self.hc
        self._e_marker.setValue(float(hc.E[int(self.energy.value())]))
        key = self._xanes_key()
        dets = self.ticked()
        if (key, dets) == self._xanes_shown:
            return
        arrays = self._xanes_cache.get(key)
        if arrays is None:
            w = self.current_windows()
            if hc.served_without_cube(w, self._roi):
                self.stats["xanes"] += 1
                arrays = _xanes_per_det(hc, w, self._roi, self.per_i0.isChecked())
                self._xanes_cache.put(key, arrays)
            else:
                self._request_xanes(key, w)
                return
        self._plot_xanes(arrays, dets)
        self._xanes_shown = (key, dets)

    def _request_xanes(self, key, w: dict) -> None:
        """The XANES reads the cube: a job with a 'computing' note; a request made while one runs waits for it."""
        self._xanes_want = key
        self.xanes.clear(); self.xanes.plotItem.legend.clear(); self.xanes.addItem(self._e_marker, ignoreBounds=True)
        set_plot_hint(self.xanes, "computing the XANES of the ROI …")
        self._xanes_shown = None
        if self._running("maps:xanes"):
            return
        self.stats["xanes"] += 1
        hc, per_i0 = self.hc, self.per_i0.isChecked()
        roi = None if self._roi is None else np.array(self._roi, copy=True)

        def work(cb):
            return key, _xanes_per_det(hc, w, roi, per_i0, cb)
        jobs.of(self).submit("maps:xanes", work, on_done=self._xanes_done, on_end=self._xanes_end,
                             label="computing the XANES of the ROI")

    def _xanes_done(self, result) -> None:
        key, arrays = result
        if key[0] != self._gen:
            return
        self._xanes_cache.put(key, arrays)

    def _xanes_end(self, state: str) -> None:
        if self.hc is None:
            return
        if state == "done" or self._xanes_want != self._xanes_key():
            self._draw_xanes()                       # the wanted key: drawn now, or the next job starts
        else:
            set_plot_hint(self.xanes, f"XANES not computed ({state})")

    def _plot_xanes(self, arrays: dict[int, np.ndarray], dets: tuple[int, ...]) -> None:
        hc = self.hc
        set_plot_hint(self.xanes, None)
        self.xanes.clear(); self.xanes.plotItem.legend.clear(); self.xanes.addItem(self._e_marker, ignoreBounds=True)
        for k in range(len(DETS)):
            on = k in dets
            colour = curve(k) if on else with_alpha(curve(k), 0.35)
            self.xanes.plot(hc.E, arrays[k], pen=pg.mkPen(colour, width=1.0 if on else 0.8, style=dash(k)),
                            connect="finite", name=DET_LABELS[k] + ("" if on else " (not summed)"))
        xs = [arrays[k] for k in dets]
        if xs:
            tot = np.nansum(xs, axis=0)
            tot[np.all(np.isnan(xs), axis=0)] = np.nan
        else:
            tot = np.full(hc.n_energies, np.nan)
        name = "sum" if len(dets) == len(DETS) else ("sum of " + ", ".join(DET_LABELS[k] for k in dets) if dets else "sum")
        self.xanes.plot(hc.E, tot, pen=role_pen("sum", 2), connect="finite", name=name)
        v = np.concatenate([arrays[k] for k in range(len(DETS))] + [tot]); v = v[np.isfinite(v)]
        if v.size:
            self.xanes.getViewBox().setRange(xRange=(float(hc.E[0]), float(hc.E[-1])), yRange=(float(v.min()), float(v.max()) if v.max() > v.min() else float(v.min()) + 1), padding=0.05, disableAutoRange=True)

    # ------------------------------------------------------------------ elemental maps and RGB
    def draw_elements(self) -> None:
        hc = self.hc
        bins = self.session.project.roi_bins if self.session.project else None
        spec = bins_spec(bins)
        if hc is None or not spec:
            self.status.setText("load a hypercube; " + NO_BINS); return
        dets = self.ticked()
        if not dets:
            self.status.setText(NO_SDD); return
        ei, per_i0, gen = self._ei(), self.per_i0.isChecked(), self._gen

        def work(cb):
            out = []
            for i, (lab, w) in enumerate(spec):
                cb(i, len(spec), f"elemental map {lab}")
                m = hc.window_map({d: w[d] for d in dets}, energy_index=ei, per_i0=per_i0)
                out.append((lab, to_grid(hc.x, hc.y, m)))
            cb(len(spec), len(spec), "elemental maps ready")
            return gen, out
        jobs.of(self).submit("maps:elements", work, on_done=self._elements_done, on_end=lambda _s: self.update_actions(),
                             label="drawing the elemental maps", taskbar=self.taskbar)
        self.update_actions()

    def _elements_done(self, result) -> None:
        gen, out = result
        if gen != self._gen:
            return
        self.element_grids = out
        self.el_layout.clear()
        from aurora.app.settings_dialog import get
        try:
            cmap = pg.colormap.get(get("map_colormap"))
        except Exception:
            cmap = pg.colormap.get("viridis")
        for i, (lab, g) in enumerate(out):
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
        dets = self.ticked()
        if not dets:
            self.status.setText(NO_SDD); return
        spec = dict(bins_spec(bins))
        wins = []
        for c in self.rgb_lines:
            lab = self.line.itemData(c.currentIndex() + 1)
            wins.append({d: spec[lab][d] for d in dets} if lab in spec else {})
        rgb = hc.rgb(wins, energy_index=self._ei())
        # one grid per colour, then stacked as an RGB image
        g = [self._grid(rgb[:, k]) for k in range(3)]
        img = np.stack([np.nan_to_num(gg.img) for gg in g], axis=-1)
        self.rgb_view.image.setImage(img, levels=[(0, 1), (0, 1), (0, 1)])
        self.rgb_view.set_lookup(None)
        self.rgb_view._data_rect = pg.QtCore.QRectF(float(g[0].xs[0]) - g[0].dx / 2, float(g[0].ys[0]) - g[0].dy / 2, len(g[0].xs) * g[0].dx, len(g[0].ys) * g[0].dy)
        self.rgb_view.image.setRect(self.rgb_view._data_rect)
        self.rgb_view.autorange()

    # ------------------------------------------------------------------ clusters
    def run_clusters(self) -> None:
        hc = self.hc
        if hc is None:
            return
        dets = self.ticked()
        if not dets:
            self.status.setText(NO_SDD); return
        all_w = self.current_windows()
        w = {d: all_w[d] for d in dets}
        per_i0, n_pc, n_k, seed, gen = self.per_i0.isChecked(), self.n_pc.value(), self.n_k.value(), self.seed.value(), self._gen
        meta = {"scan": hc.name, "windows": {DETS[d]: list(w[d]) for d in dets}, "sdds": [DETS[d] for d in dets],
                "pca_components": int(n_pc), "k": int(n_k), "seed": int(seed), "per_i0": bool(per_i0),
                "window": self.line.currentData() or f"{self.w_lo.value():.1f}-{self.w_hi.value():.1f} eV"}

        def work(cb):
            nv = int(np.count_nonzero(hc.valid))
            M = hc.pixel_matrix(w, per_i0=per_i0, progress=lambda i, _n, msg="": cb(i, nv + 1, msg))
            cr = hcmod.pca_kmeans(M, hc.E[hc.valid], n_pc, n_k, seed)
            cb(nv + 1, nv + 1, "clusters ready")           # a cancel during k-means ends here: nothing is replaced
            return gen, M, cr, meta
        jobs.of(self).submit("maps:clusters", work, on_done=self._clusters_done, on_end=lambda _s: self.update_actions(),
                             label="clustering the pixels", taskbar=self.taskbar)
        self.update_actions()

    def _clusters_done(self, result) -> None:
        gen, M, cr, meta = result
        if gen != self._gen:
            return
        self.show_clusters(cr, M, meta)

    def show_clusters(self, cr, M: np.ndarray | None = None, meta: dict | None = None) -> None:
        """Show a ClusterResult: the list (all ticked), the map in categorical colours and the spectra."""
        self._cluster, self._cluster_M, self._cluster_meta = cr, M, dict(meta or {})
        self._fill_cluster_list(keep=False)
        self._show_cluster_map()
        self.cluster_map.autorange()
        self._draw_clusters()
        n_k = cr.centres.shape[0]
        self.cluster_info.setText(f"{plural(n_k, 'cluster')} on {int((cr.labels >= 0).sum())} px · seed {cr.seed} · PCA explained "
                                  + ", ".join(f"{100 * e:.1f} %" for e in cr.explained))
        self.update_actions()

    def cluster_colours(self) -> list[str]:
        """The categorical colour of every cluster: map, list swatch and legend use the same list."""
        n = self._cluster.centres.shape[0] if self._cluster is not None else self.n_k.value()
        return categorical(max(int(n), 2))

    def _fill_cluster_list(self, keep: bool) -> None:
        cr = self._cluster
        old = set(self.ticked_clusters()) if keep else None
        cols = self.cluster_colours()
        self.cluster_list.blockSignals(True)
        self.cluster_list.clear()
        for k in range(cr.centres.shape[0]):
            n = int((cr.labels == k).sum())
            it = QListWidgetItem(swatch_icon(cols[k]), f"cluster {k + 1} · {n} px")
            it.setData(Qt.UserRole + 1, cols[k])                # the swatch colour: the legend colour of cluster k
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if old is None or k in old else Qt.Unchecked)
            it.setData(Qt.UserRole, k)
            self.cluster_list.addItem(it)
        self.cluster_list.blockSignals(False)

    def _cluster_lut(self, n: int, ticked=None) -> np.ndarray:
        """One entry per cluster (levels 0 .. n-1): its categorical colour, dimmed over the map background when unticked."""
        from PySide6.QtGui import QColor
        cols = categorical(max(int(n), 2))
        out = []
        for k, c in enumerate(cols):
            if ticked is not None and k not in ticked:
                c = composite(with_alpha(c, DIM_ALPHA), TOKENS["map_bg"])
            out.append(QColor(c).getRgb()[:3])
        return np.array(out, dtype=np.ubyte)

    def _show_cluster_map(self) -> None:
        cr = self._cluster
        if cr is None or self.hc is None:
            return
        n_k = cr.centres.shape[0]
        self.cluster_map.clear_overlays()
        self.cluster_map.set_grid(self._label_grid(cr.labels))
        self.cluster_map.set_lookup(self._cluster_lut(n_k, set(self.ticked_clusters())))
        self.cluster_map.image.setLevels((0, max(n_k - 1, 1)))      # label k -> LUT entry k

    def _cluster_ticks_changed(self) -> None:
        self._show_cluster_map()
        self._draw_clusters()
        self.update_actions()

    def merged(self) -> tuple[np.ndarray, np.ndarray, int]:
        """(mean, sum, pixels) of the ticked clusters' rows of the pixel matrix."""
        cr, M = self._cluster, self._cluster_M
        if cr is None or M is None:
            return np.array([]), np.array([]), 0
        return merged_spectra(M, cr.labels, self.ticked_clusters())

    def _draw_clusters(self) -> None:
        """Cluster mean spectra in the cluster map's categorical colours (legend colour = map colour), the merged mean
        and, below, the merged sum of the ticked clusters."""
        for p in (self.cluster_plot, self.merged_plot):
            p.clear(); p.plotItem.legend.clear()
        cr = self._cluster
        if cr is None:
            set_plot_hint(self.cluster_plot, "Run the clustering to see the cluster spectra")
            set_plot_hint(self.merged_plot, "the merged spectra of the ticked clusters appear here")
            return
        set_plot_hint(self.cluster_plot, None)
        cols = self.cluster_colours()
        ticked = set(self.ticked_clusters())
        for k in range(cr.centres.shape[0]):
            n = int((cr.labels == k).sum())
            on = k in ticked
            colour = cols[k] if on else with_alpha(cols[k], 0.35)
            item = self.cluster_plot.plot(cr.E, cr.centres[k], pen=pg.mkPen(colour, width=1.6 if on else 1.0, style=dash(k)))
            item.setToolTip(f"cluster {k + 1} ({n} px)")      # the cluster list above is the legend (same colours)
        mean, total, n = self.merged()
        if n:
            names = "+".join(str(k + 1) for k in sorted(ticked))
            self.cluster_plot.plot(cr.E, mean, pen=role_pen("sum", 2.2), name=f"merged {names} ({n} px)")
            set_plot_hint(self.merged_plot, None)
            self.merged_plot.plot(cr.E, total, pen=role_pen("sum", 2.0), name=f"sum of clusters {names} ({n} px)")
            vs = total[np.isfinite(total)]
            if vs.size:
                self.merged_plot.getViewBox().setRange(xRange=(float(cr.E[0]), float(cr.E[-1])), yRange=(float(vs.min()), float(vs.max()) if vs.max() > vs.min() else float(vs.min()) + 1), padding=0.05, disableAutoRange=True)
        else:
            set_plot_hint(self.merged_plot, NO_TICKED)
        v = cr.centres[np.isfinite(cr.centres)]
        if v.size:
            self.cluster_plot.getViewBox().setRange(xRange=(float(cr.E[0]), float(cr.E[-1])), yRange=(float(v.min()), float(v.max()) if v.max() > v.min() else float(v.min()) + 1), padding=0.05, disableAutoRange=True)

    def _cluster_names(self) -> str:
        return "+".join(str(k + 1) for k in self.ticked_clusters())

    def _cluster_sid(self) -> str:
        scan = self.session.project.find(self.hc.name) if self.session.project is not None and self.hc else None
        return self.session.stack_id(scan) if scan is not None else (self.hc.name if self.hc else "")

    def _cluster_label(self) -> str:
        holder = self.session.holder()
        sid = self._cluster_sid()
        return f"clusters {self._cluster_names()} · {holder.name + ' ' if holder else ''}{short_time(sid) or self.hc.name}"

    def make_zone_from_clusters(self):
        """A new zone of the holder whose painted squares are exactly the ticked clusters' pixels (one square of the
        pixel pitch on each pixel), so members() returns those pixels and zones_all.json keeps its format."""
        cr, hc = self._cluster, self.hc
        ticked = self.ticked_clusters()
        if cr is None or hc is None or not ticked:
            return None
        pix = np.where(np.isin(cr.labels, ticked))[0]
        squares = paint_squares(hc.x, hc.y, hc.pitch, pix)
        label = self._cluster_label()
        with self.session.edit(f"make a zone from clusters {self._cluster_names()} of {hc.name}"):
            z = self.session.new_zone(label)
            if z is None:
                return None
            z.paint = squares
            z.comment = (f"Maps clusters {self._cluster_names()} of {hc.name} (k = {cr.centres.shape[0]}, "
                         f"seed {cr.seed}, {describe_windows({DETS.index(d): tuple(w) for d, w in self._cluster_meta.get('windows', {}).items()})})")
        self.status.setText(f"zone {z.id} '{z.label}' made from {plural(len(pix), 'pixel')}")
        return z

    def send_clusters_to_compare(self) -> int:
        """Add the merged mean of the ticked clusters to the Compare library, with its provenance."""
        cr = self._cluster
        if cr is None or self.library is None or not self.ticked_clusters():
            if self.library is None:
                self.status.setText("no Compare library to send to")
            return 0
        mean, _total, n = self.merged()
        prov = {**self._cluster_meta, "clusters": [k + 1 for k in self.ticked_clusters()], "n_pix": int(n),
                "stack": self._cluster_sid(), "made_by": "AURORA Maps clusters"}
        label = self._cluster_label()
        spec = Spectrum(label, "fluo", np.asarray(cr.E, float), np.asarray(mean, float),
                        source=f"maps clusters {self._cluster_names()} of {self._cluster_sid()}", meta={"provenance": prov})
        added = self.library.add_spectra([spec], show_signals=("fluo",))
        self.status.setText(f"'{label}' sent to Compare" if added else f"'{label}' updated in Compare")
        return len(added)

    def clusters_csv(self) -> str:
        """E, the mean of each cluster and the merged mean of the ticked ones, with a provenance header."""
        cr = self._cluster
        mean, _total, n = self.merged()
        buf = io.StringIO()
        meta = self._cluster_meta
        buf.write(f"# AURORA Maps clusters of {meta.get('scan', '')}\n")
        buf.write(f"# window {meta.get('window', '')}: {_windows_text(meta.get('windows', {}))}; SDDs {', '.join(meta.get('sdds', []))}\n")
        buf.write(f"# PCA components {meta.get('pca_components', '')}, k-means k = {meta.get('k', '')}, seed {cr.seed}, "
                  f"/ I0 {meta.get('per_i0', '')}; merged = clusters {self._cluster_names() or 'none'} ({n} px)\n")
        wr = csv.writer(buf, lineterminator="\n")
        k = cr.centres.shape[0]
        wr.writerow(["energy_eV"] + [f"cluster_{i + 1}_mean" for i in range(k)] + ["merged_mean"])
        for j, e in enumerate(cr.E):
            wr.writerow([f"{e:.4f}"] + [f"{cr.centres[i, j]:.8g}" for i in range(k)]
                        + [f"{mean[j]:.8g}" if n else ""])
        return buf.getvalue()

    def export_clusters(self, csv_path: Path) -> list[Path]:
        """Write the cluster CSV and the cluster map PNG next to it (<name>_map.png)."""
        from aurora.core.io.safety import assert_writable
        from aurora.core.io.textio import write_text_atomic
        csv_path = Path(csv_path)
        write_text_atomic(csv_path, self.clusters_csv())
        png = csv_path.with_name(csv_path.stem + "_map.png")
        assert_writable(png)
        if not self.cluster_map.grab().save(str(png)):
            raise OSError(f"{png.name}: the image could not be written")
        return [csv_path, png]

    def _export_clusters(self) -> None:
        if self._cluster is None or self.hc is None:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export the clusters", default_path("maps", f"{self.hc.name}_clusters.csv", self.session),
                                           "CSV (*.csv)")
        if not p:
            return
        remember_dir("maps", p)
        with guarded_write(self, Path(p).name) as g:
            paths = self.export_clusters(Path(p))
        if g.ok:
            notify_saved(self.status, paths)

    # ------------------------------------------------------------------ exports (jobs)
    def _export_job(self, key: str, path: Path, fn) -> None:
        jobs.of(self).submit(key, fn, on_done=lambda p: notify_saved(self.status, [p]),
                             on_end=lambda _s: self.update_actions(), label=f"writing {path.name}", taskbar=self.taskbar)
        self.update_actions()

    def _export_xanes(self) -> None:
        if self.hc is None:
            return
        dets = self.ticked()
        if not dets:
            self.status.setText(NO_SDD); return
        p, _ = QFileDialog.getSaveFileName(self, "PyMca XANES stack", default_path("maps", f"{self.hc.name}_PCA-CA.h5", self.session),
                                           "HDF5 (*.h5)")
        if not p:
            return
        remember_dir("maps", p)
        hc, path, per_i0 = self.hc, Path(p), self.per_i0.isChecked()
        all_w = self.current_windows()
        w = {d: all_w[d] for d in dets}
        self._export_job("maps:export_xanes", path, lambda cb: export_pymca_xanes(hc, path, w, per_i0=per_i0, progress=cb))

    def _export_4d(self) -> None:
        if self.hc is None:
            return
        p, _ = QFileDialog.getSaveFileName(self, "PyMca 4D stack", default_path("maps", f"{self.hc.name}_Elemental_PyMca.h5", self.session),
                                           "HDF5 (*.h5)")
        if not p:
            return
        remember_dir("maps", p)
        hc, path, per_i0, dets = self.hc, Path(p), self.per_i0.isChecked(), self.ticked() or None
        self._export_job("maps:export_4d", path, lambda cb: export_pymca_4d(hc, path, per_i0, progress=cb, dets=dets))


def _windows_text(windows: dict) -> str:
    """'sdd1 63-70, sdd2 68-75' (inclusive channels) for the CSV header."""
    return ", ".join(f"{d} {lo}-{hi - 1}" for d, (lo, hi) in windows.items())
