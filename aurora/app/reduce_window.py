"""Reduce step 2, the XRF window (John Bussey's step 2): the emission spectrum of the stack, the survey or the region
(log y), one draggable window per SDD in the series colours with their dashes, lo / hi spin boxes in channels with
the eV equivalent, and the presets (every fixed line of roi_bins.json, the line of the edge when a calibration
exists, custom).

The card shows a choice {'line': label, 'roi': windows or None}: a fixed line keeps roi None (the reduced stack's own
bins); the line of the edge and custom windows set ScanSettings.roi. `committed` carries a choice made by the user;
the Reduce perspective writes it into the zone's settings through session.edit. Reading the spectra is a job of the
window (Cancel, progress); the result is cached by aurora.core.xrf_windows.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QPushButton, QSpinBox, QWidget

from aurora.app import jobs
from aurora.app.help_text import explain_all, labelled
from aurora.app.mode import mark_advanced
from aurora.app.theme import curve, dash, make_plot, mk_pen, on_theme_changed, with_alpha
from aurora.app.vocab import DET_LABELS, unit_suffix
from aurora.app.widgets import Card, FlowLayout, pair, set_plot_hint
from aurora.core import xrf_windows as xw
from aurora.core.constants import DETS, N_CH

SOURCES = (("stack", "stack, all energies"), ("survey", "survey"), ("region", "inside the region"))
CUSTOM = "custom"


class XrfWindowCard(Card):
    committed = Signal(object)              # {'line': str, 'roi': dict | None}
    remember_requested = Signal(object)     # the same, for 'Remember these windows for <edge>'

    def __init__(self, session, taskbar=None, parent: QWidget | None = None):
        super().__init__("XRF window", parent, help_anchor="4-reduce")
        self.session = session
        self.taskbar = taskbar
        self._sd = None
        self._scan = None
        self._pix = None
        self._labels: list[str] = []
        self._windows: dict[str, list[int]] = {d: list(xw.TOTAL_BOUNDS) for d in DETS}
        self._line = xw.TOTAL_LABEL
        self._custom = False
        self._updating = False
        self._spectrum = None                # (4, 256) shown, or None
        self._edge_line = None               # xw.EdgeLine when offered
        # presets row
        self.preset = QComboBox()
        self.preset.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset.setMinimumContentsLength(10)
        self.body.addWidget(pair(labelled("preset", self.preset, "reduce.window.preset"), self.preset))
        # the spectrum
        self.plot = make_plot(x="channel", y="counts")
        self.plot.setMinimumHeight(100)
        self.plot.getViewBox().disableAutoRange()
        self.plot.setLogMode(x=False, y=True)
        self.plot.setAccessibleName("emission spectrum with the four detector windows")
        self.regions: list[pg.LinearRegionItem] = []
        for i in range(len(DETS)):
            r = pg.LinearRegionItem(values=list(xw.TOTAL_BOUNDS), bounds=(0, N_CH))
            r.setZValue(10 + i)
            r.sigRegionChanged.connect(lambda _r, k=i: self._region_moved(k))
            r.sigRegionChangeFinished.connect(lambda _r, k=i: self._region_done(k))
            self.plot.addItem(r, ignoreBounds=True)
            self.regions.append(r)
        self._curves = [self.plot.plot([], [], pen=mk_pen(curve(i), 1.2, dash(i)), name=DET_LABELS[i], connect="finite")
                        for i in range(len(DETS))]
        self._style_regions()
        self.body.addWidget(self.plot, 1)
        # lo / hi in channels, the eV equivalent
        self.lo = QSpinBox(); self.lo.setRange(0, N_CH - 1); self.lo.setSuffix(unit_suffix("ch"))
        self.hi = QSpinBox(); self.hi.setRange(1, N_CH); self.hi.setSuffix(unit_suffix("ch"))
        for s in (self.lo, self.hi):
            s.setMaximumWidth(86)
        row = QWidget()
        rl = FlowLayout(row, spacing=6)
        rl.addWidget(pair(labelled("lo", self.lo, "reduce.window.lo"), self.lo))
        rl.addWidget(pair(labelled("hi", self.hi, "reduce.window.hi"), self.hi))
        self.det = QComboBox(); self.det.addItems(list(DET_LABELS))
        self.det_box = pair(labelled("for", self.det, "reduce.window.det"), self.det)     # Advanced
        rl.addWidget(self.det_box)
        self.body.addWidget(row)
        self.ev_label = QLabel("")
        self.ev_label.setWordWrap(True)
        self.ev_label.setObjectName("muted")
        self.body.addWidget(self.ev_label)
        # Advanced: source, unlinked windows, the detector the spins edit, remember as a default
        self.adv_box = QWidget()
        al = FlowLayout(self.adv_box, spacing=6)
        self.source = QComboBox()
        for k, text in SOURCES:
            self.source.addItem(text, k)
        al.addWidget(pair(labelled("spectrum of", self.source, "reduce.window.source"), self.source))
        self.same_range = QCheckBox("same range for all detectors")
        self.same_range.setChecked(True)
        al.addWidget(self.same_range)
        self.remember_btn = QPushButton("Remember windows for the edge")
        al.addWidget(self.remember_btn)
        self.body.addWidget(self.adv_box)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setObjectName("muted")
        self.body.addWidget(self.status)
        explain_all([(self.same_range, "reduce.window.same"), (self.remember_btn, "reduce.window.remember")])
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(350)
        self._commit_timer.timeout.connect(self.commit_now)
        self.preset.currentIndexChanged.connect(self._preset_picked)
        self.lo.valueChanged.connect(lambda _v: self._spin_changed())
        self.hi.valueChanged.connect(lambda _v: self._spin_changed())
        self.same_range.toggled.connect(self._same_toggled)
        self.det.currentIndexChanged.connect(lambda _i: self._fill_spins())
        self.source.currentIndexChanged.connect(lambda _i: self.refresh_spectrum())
        self.remember_btn.clicked.connect(lambda: self.remember_requested.emit(self.choice()))
        on_theme_changed(self, lambda _s: (self._style_regions(), self._draw()))
        mark_advanced(self.adv_box, self.det_box)
        self.set_stack(None, None)

    # ------------------------------------------------------------------ the stack and its presets
    def set_stack(self, sd, scan, choice: dict | None = None) -> None:
        """Show a stack (None: nothing loaded) with a choice {'line', 'roi'} (None: the stack's main line)."""
        self._sd, self._scan = sd, scan
        self._labels = list(sd.labels) if sd is not None else []
        proj = self.session.project
        self._edge_line = xw.edge_line_windows(getattr(sd, "edge", None), getattr(proj, "calibration", None),
                                               getattr(proj, "roi_bins", None)) if sd is not None else None
        self._updating = True
        try:
            self.preset.clear()
            bins = getattr(proj, "roi_bins", None)
            labs = [lab for lab in self._labels if xw.bins_windows(bins, lab) is not None]
            labs += [lab for lab in (bins.labels if bins is not None else []) if bins.has(lab) and lab not in labs]
            for lab in labs:                            # every line of roi_bins.json, and the stack's own lines
                self.preset.addItem(f"{lab} (fixed bins)", ("line", lab))
            if self._edge_line is not None:
                self.preset.addItem(f"line of the edge: {self._edge_line.label} {self._edge_line.line_ev:.1f} eV",
                                    ("edge", self._edge_line.label))
            self.preset.addItem(CUSTOM, ("custom", ""))
        finally:
            self._updating = False
        for w in (self.preset, self.lo, self.hi, self.same_range, self.remember_btn):
            w.setEnabled(sd is not None)
        self.det.setEnabled(sd is not None and not self.same_range.isChecked())
        edge = getattr(sd, "edge_text", "") if sd is not None else ""
        self.remember_btn.setText(f"Remember windows for {edge}" if edge else "Remember windows for the edge")
        self.show_choice(choice)
        self.refresh_spectrum()

    def show_choice(self, choice: dict | None) -> None:
        """Display a choice without committing it."""
        sd = self._sd
        bins = getattr(self.session.project, "roi_bins", None)
        line = (choice or {}).get("line") or (sd.main if sd is not None else xw.TOTAL_LABEL)
        roi = (choice or {}).get("roi")
        self._line = line
        if roi:
            self._windows = xw.normalise_windows(roi)
            self._custom = True
        else:
            self._windows = xw.bins_windows(bins, line) or {d: list(xw.TOTAL_BOUNDS) for d in DETS}
            self._custom = False
        self._updating = True
        try:
            self.preset.setCurrentIndex(self._preset_index())
            if roi and not xw.same_for_all(self._windows):
                self.same_range.setChecked(False)
            self._place_regions()
            self._fill_spins()
        finally:
            self._updating = False
        self._describe()

    def _preset_index(self) -> int:
        bins = getattr(self.session.project, "roi_bins", None)
        for i in range(self.preset.count()):
            kind, lab = self.preset.itemData(i)
            if not self._custom and kind == "line" and lab == self._line:
                return i
            if self._custom and kind == "line" and lab not in self._labels and xw.same(self._windows,
                                                                                      xw.bins_windows(bins, lab)):
                return i                                # a roi_bins line the stack was not reduced with
            if self._custom and kind == "edge" and self._edge_line is not None and xw.same(self._windows, self._edge_line.windows):
                return i
        return max(0, self.preset.count() - 1)          # custom

    def choice(self) -> dict:
        """{'line': label, 'roi': windows or None}: a fixed line when the windows are exactly its bins."""
        bins = getattr(self.session.project, "roi_bins", None)
        if self._custom:
            lab = xw.label_for_windows(self._windows, bins, self._labels)
            if lab is not None:
                return {"line": lab, "roi": None}
            return {"line": self._line, "roi": xw.normalise_windows(self._windows)}
        return {"line": self._line, "roi": None}

    def windows(self) -> dict[str, list[int]]:
        return xw.normalise_windows(self._windows)

    # ------------------------------------------------------------------ user changes
    def _preset_picked(self, i: int) -> None:
        if self._updating or i < 0:
            return
        kind, lab = self.preset.itemData(i)
        bins = getattr(self.session.project, "roi_bins", None)
        if kind == "line":
            # a line the stack was reduced with keeps its bins; another roi_bins line is read from the raw spectra
            self._line, self._custom = lab, lab not in self._labels
            self._windows = xw.bins_windows(bins, lab)
        elif kind == "edge" and self._edge_line is not None:
            self._line, self._custom = self._edge_line.label, True
            self._windows = dict(self._edge_line.windows)
        else:
            self._custom = True
        self._updating = True
        try:
            self._place_regions()
            self._fill_spins()
        finally:
            self._updating = False
        self._describe()
        self.commit_now()

    def set_window(self, det: int | None, lo: int, hi: int, commit: bool = True) -> None:
        """Set one detector's window (or all four with det None or 'same range' ticked)."""
        lo, hi = int(lo), int(hi)
        w = xw.normalise_windows({DETS[0]: [lo, hi]})[DETS[0]]
        targets = range(len(DETS)) if det is None or self.same_range.isChecked() else [det]
        for k in targets:
            self._windows[DETS[k]] = list(w)
        self._custom = True
        self._updating = True
        try:
            self._place_regions()
            self._fill_spins()
            self.preset.setCurrentIndex(self._preset_index())
        finally:
            self._updating = False
        self._describe()
        if commit:
            self._commit_timer.start()

    def _region_moved(self, k: int) -> None:
        if self._updating:
            return
        lo, hi = self.regions[k].getRegion()
        if self.same_range.isChecked():                 # dragging one moves all four
            self._updating = True
            try:
                for j, r in enumerate(self.regions):
                    if j != k:
                        r.setRegion((lo, hi))
            finally:
                self._updating = False

    def _region_done(self, k: int) -> None:
        if self._updating:
            return
        lo, hi = self.regions[k].getRegion()
        self.set_window(k, int(round(lo)), int(round(hi)))

    def _spin_changed(self) -> None:
        if self._updating:
            return
        k = 0 if self.same_range.isChecked() else self.det.currentIndex()
        lo, hi = self.lo.value(), self.hi.value()
        if hi <= lo:
            hi = lo + 1
        self.set_window(k, lo, hi)

    def _same_toggled(self, on: bool) -> None:
        self.det.setEnabled(not on and self._sd is not None)
        if self._updating or not on:
            return
        k = self.det.currentIndex()
        lo, hi = self._windows[DETS[k]]
        self.set_window(None, lo, hi)

    def commit_now(self) -> None:
        self._commit_timer.stop()
        if self._sd is not None:
            self.committed.emit(self.choice())

    # ------------------------------------------------------------------ display
    def _style_regions(self) -> None:
        for i, r in enumerate(self.regions):
            r.setBrush(pg.mkBrush(with_alpha(curve(i), 0.10)))
            r.setHoverBrush(pg.mkBrush(with_alpha(curve(i), 0.22)))
            for ln in r.lines:
                ln.setPen(mk_pen(curve(i), 1.6, dash(i)))
                ln.setHoverPen(mk_pen(curve(i), 2.6, dash(i)))

    def _place_regions(self) -> None:
        for k, r in enumerate(self.regions):
            lo, hi = self._windows[DETS[k]]
            r.setRegion((lo, hi))
            r.setVisible(self._sd is not None)

    def _fill_spins(self) -> None:
        k = 0 if self.same_range.isChecked() else self.det.currentIndex()
        lo, hi = self._windows[DETS[k]]
        old = self._updating
        self._updating = True
        try:
            self.lo.setValue(lo)
            self.hi.setValue(hi)
        finally:
            self._updating = old
        self._describe()

    def _describe(self) -> None:
        if self._sd is None:
            self.ev_label.setText("pick a scan in step 1")
            return
        proj = self.session.project
        cal = getattr(proj, "calibration", None)
        k = 0 if self.same_range.isChecked() else self.det.currentIndex()
        lo, hi = self._windows[DETS[k]]
        a, b = xw.channel_ev(cal, DETS[k], lo), xw.channel_ev(cal, DETS[k], hi)
        nominal = cal is None or not getattr(cal, "calibrated", False)
        who = "all detectors" if self.same_range.isChecked() and xw.same_for_all(self._windows) else DET_LABELS[k]
        self.ev_label.setText(f"{who}: channels {lo}:{hi} = {a:.0f}–{b:.0f} eV"
                              + (" (10 eV/ch nominal)" if nominal else " (calibrated)"))

    def refresh_spectrum(self) -> None:
        """Draw the spectrum of the chosen source; the stack's is read in a job the first time (cached)."""
        sd = self._sd
        self._spectrum = None
        src = self.source.currentData()
        if sd is None:
            self.status.setText("")
            self._draw()
            return
        if src == "survey":
            try:
                sv = self.session.survey()
            except Exception as err:                   # an unreadable survey: say so, keep the card usable
                sv = None
                self.status.setText(f"the survey could not be read ({err})")
            if sv is not None:
                self._spectrum = np.asarray(sv.spec, float)
                self.status.setText(f"survey {sv.name} at {sv.energy:.1f} eV")
            self._draw()
            return
        em = xw.cached_stack_emission(sd)
        if em is not None:
            self._show_emission(em, sd)
            return
        if xw.source_of(sd) is None:
            self.status.setText("no raw spectra for this stack (stitched or not found): the fixed bins only")
            self._draw()
            return
        sid = sd.id
        self.status.setText("reading the emission spectrum …")
        self._draw()
        jobs.of(self).submit(f"emission:{sid}", lambda cb, s=sd: xw.stack_emission_data(s, cb),
                             on_done=lambda em, s=sd: self._emission_read(s, em),
                             on_failed=lambda t: self.status.setText("could not read the spectra: " + t.strip().splitlines()[-1]),
                             label=f"reading the emission spectrum of {sd.name}", taskbar=self.taskbar)

    def _emission_read(self, sd, em) -> None:
        if sd is self._sd:
            self._show_emission(em, sd)

    def _show_emission(self, em, sd) -> None:
        src = self.source.currentData()
        if src == "region":
            if self._pix is None or not len(self._pix):
                self.status.setText("nothing selected yet: the spectrum of the whole stack")
                self._spectrum = em.spectrum(DETS)
            else:
                self._spectrum = em.spectrum(DETS, self._pix)
                self.status.setText(f"inside the region ({len(self._pix)} points), all energies")
        else:
            self._spectrum = em.spectrum(DETS)
            self.status.setText(f"{sd.name}: all {sd.n_energies} energies, {sd.n_pix} points"
                                + (f" · {em.notes[0]}" if em.notes else ""))
        self._draw()

    def set_region(self, pix) -> None:
        """The current region's members on the stack (for 'inside the region')."""
        self._pix = None if pix is None else np.asarray(pix, int)
        if self.source.currentData() == "region" and self._sd is not None:
            em = xw.cached_stack_emission(self._sd)
            if em is not None:
                self._show_emission(em, self._sd)

    def _draw(self) -> None:
        spec = self._spectrum
        if spec is None:
            for c in self._curves:
                c.setData([], [])
            set_plot_hint(self.plot, "the emission spectrum appears here" if self._sd is not None else "pick a scan in step 1")
            return
        set_plot_hint(self.plot, None)
        x = np.arange(N_CH) + 0.5                        # channel c spans [c, c + 1): windows sit on the channel edges
        pos = []
        for k, c in enumerate(self._curves):
            y = np.asarray(spec[k], float)
            y = np.where(y > 0, y, np.nan)              # log axis: empty channels are gaps
            c.setData(x, y)
            pos.append(y[np.isfinite(y)])
        vals = np.concatenate(pos) if pos else np.array([])
        lo, hi = (float(vals.min()), float(vals.max())) if vals.size else (1.0, 10.0)
        self.plot.getViewBox().setRange(xRange=(0, N_CH), yRange=(np.log10(max(lo, 0.5)), np.log10(max(hi, lo * 10, 1.0))),
                                        padding=0.03, disableAutoRange=True)
