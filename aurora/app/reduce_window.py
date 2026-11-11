"""Reduce step 2, the XRF window (John Bussey's step 2): the emission spectrum of the stack, the survey, the region,
or the survey points inside the region (John Bussey's 'XRF from survey, only inside region'; set_zone gives the zone)
(log y), one draggable window per SDD in the series colours with their dashes, lo / hi spin boxes in channels with
the eV equivalent, and the presets (every fixed line of roi_bins.json, the line of the edge, John Bussey's default
60:83 when no fixed line or calibration gives the edge's line, custom). Without a calibration the line of the edge
is offered on the nominal 10 eV per channel ('check the peak') when the beamtime has no fixed line of that name;
without any fixed line a note says that a new sample starts on the standard window of the edge's line
(Session.window_default) and asks to check that it covers the peak. The total XRF is said to hold every emission
line, so that it is not taken for the PFY of the edge's line. Save as a fixed line… writes the current windows into
config/roi_bins.json under a name, so every scan of the beamtime can use them and Maps can draw the element.

The card shows a choice {'line': label, 'roi': windows or None}: a fixed line keeps roi None (the reduced stack's own
bins); the line of the edge and custom windows set ScanSettings.roi. Linked edits (Easy mode, where one lo / hi pair
stands for the four detectors, or 'same range for all detectors' ticked) move every detector's window by the same
number of channels, so the per-detector windows of a fixed line or of the line of the edge keep their offsets. `committed` carries a choice made by the user;
the Reduce perspective writes it into the zone's settings through session.edit. Reading the spectra is a job of the
window (Cancel, progress); the result is cached by aurora.core.xrf_windows.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QInputDialog, QLabel, QMessageBox, QPushButton, QSizePolicy,
                               QSpinBox, QWidget)

from aurora.app import jobs
from aurora.app.help_text import explain_all, labelled
from aurora.app.help_text import say
from aurora.app.mode import is_advanced, mark_advanced, mark_easy, on_mode_changed
from aurora.app.theme import curve, dash, make_plot, min_height, mk_pen, on_theme_changed, with_alpha
from aurora.app.vocab import DET_LABELS, unit_suffix
from aurora.app.widgets import Card, FlowLayout, TermLabel, pair, plain_error, say_failure, set_plot_hint
from aurora.core import xrf_windows as xw
from aurora.core.constants import DETS, N_CH

SOURCES = (("stack", "the whole stack"), ("survey", "survey"), ("region", "inside the region"),     # short: Linux fonts
           ("survey_zone", "survey, inside the zone"))
CUSTOM = "custom (drag the band on the plot)"
SAVE_LINE = "Save as a fixed line…"
NO_FIXED_LINES = ("No fixed emission lines for this beamtime: AURORA starts on a standard window of the edge "
                  "element's X-rays (the preset). Check that its band covers the peak, or drag it there.")
NOMINAL_EDGE = "nominal calibration, check the peak"
CAPTION = ("The plot shows the X-rays your sample emits (its {{emission-spectrum|emission spectrum}}), one curve per "
           "detector (SDD1 to SDD4). Only the counts inside the shaded band become your spectrum: keep the preset of "
           "your element (F Kα for fluorine) unless told otherwise.")


def preset_text(label: str) -> str:
    """'F Kα' -> 'F Kα (fixed window)'; the total -> 'all X-rays (total XRF)'."""
    return "all X-rays (total XRF)" if label == xw.TOTAL_LABEL else f"{label} (fixed window)"


def axis_x(calibrated: bool) -> str:
    """The x label of the step-2 plot, short enough for the card's width at 1200 px."""
    return "channel (calibrated)" if calibrated else "channel (about 10 eV each)"


class DecadeAxis(pg.AxisItem):
    """A log axis labelled on the decades only (10³, 10⁴), the 2…9 ticks between them unlabelled. pyqtgraph puts the
    2…9 ticks in the first (labelled) level when fewer than three decades are in view, and their labels piled up in a
    plot as low as step 2's. A range inside one decade keeps pyqtgraph's ticks."""

    def logTickValues(self, minVal, maxVal, size, stdTicks):
        lo, hi = (minVal, maxVal) if minVal <= maxVal else (maxVal, minVal)
        decades = [float(v) for v in range(int(np.ceil(lo)), int(np.floor(hi)) + 1)]
        if not decades:
            return super().logTickValues(minVal, maxVal, size, stdTicks)
        between = [v + np.log10(k) for v in range(int(np.floor(lo)), int(np.ceil(hi))) for k in range(2, 10)]
        return [(1.0, decades), (None, [float(v) for v in between if lo < v < hi])]


class XrfWindowCard(Card):
    committed = Signal(object)              # {'line': str, 'roi': dict | None}
    remember_requested = Signal(object)     # the same, for 'Remember these windows for <edge>'

    def __init__(self, session, taskbar=None, parent: QWidget | None = None):
        super().__init__("XRF window", parent, help_anchor="48-xrf-windows")
        self.session = session
        self.taskbar = taskbar
        self._sd = None
        self._scan = None
        self._pix = None
        self._zone = None                    # the zone (or a stroke's provisional zone) for 'survey, inside the zone'
        self._labels: list[str] = []
        self._windows: dict[str, list[int]] = {d: list(xw.TOTAL_BOUNDS) for d in DETS}
        self._line = xw.TOTAL_LABEL
        self._custom = False
        self._updating = False
        self._spectrum = None                # (4, 256) shown, or None
        self._edge_line = None               # xw.EdgeLine when offered
        self._john = None                    # (line, windows): John Bussey's default window when offered
        # presets row
        self.preset = QComboBox()
        self.preset.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset.setMinimumContentsLength(10)
        self.preset.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)     # the label keeps its width only
        plab = labelled("preset", self.preset, "reduce.window.preset")
        plab.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        from aurora.app.help_text import term_button
        self.preset_help = term_button("xrf-window")      # Easy: 'What is the emission window?'
        self.body.addWidget(pair(plab, self.preset_help, self.preset))
        self.bins_note = QLabel(NO_FIXED_LINES)           # Easy and Advanced: no roi_bins.json line at all
        self.bins_note.setWordWrap(True)
        self.bins_note.setObjectName("muted")
        self.bins_note.hide()
        self.body.addWidget(self.bins_note)
        self.caption = TermLabel(CAPTION)                  # Easy: what the plot is and what to keep
        self.caption.setObjectName("muted")
        self.body.addWidget(self.caption)
        self.note = TermLabel("")                          # Easy: the inverse-yield note (Reduce sets it)
        self.note.setObjectName("muted")
        self.note.hide()
        self.body.addWidget(self.note)
        self._label = ""                                   # the selected sample's name (the status line)
        # the spectrum
        self.plot = make_plot(x=axis_x(False), y="counts (log scale)", axis_items={"left": DecadeAxis("left")})
        min_height(self.plot, 125)                         # 80 px of data under the axis (Easy's caption takes the rest)
        self.plot.getViewBox().disableAutoRange()
        self.plot.setLogMode(x=False, y=True)
        self.plot.getAxis("left").setStyle(maxTextLevel=0)      # DecadeAxis: text on the decades only
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
        rl.addWidget(pair(labelled("from", self.lo, "reduce.window.lo"), self.lo))
        rl.addWidget(pair(labelled("to", self.hi, "reduce.window.hi"), self.hi))
        self.det = QComboBox(); self.det.addItems(list(DET_LABELS))
        self.det_box = pair(labelled("for", self.det, "reduce.window.det"), self.det)     # Advanced
        rl.addWidget(self.det_box)
        self.save_line_btn = QPushButton(SAVE_LINE)       # Easy and Advanced
        self.save_line_btn.clicked.connect(self.save_fixed_line)
        rl.addWidget(self.save_line_btn)
        self.body.addWidget(row)
        self.ev_label = QLabel("")
        self.ev_label.setWordWrap(True)
        self.ev_label.setObjectName("muted")
        self.body.addWidget(self.ev_label)
        # Advanced: source, unlinked windows, the detector the spins edit, remember as a default
        self.adv_box = QWidget()
        al = FlowLayout(self.adv_box, spacing=6)
        self.source = QComboBox()
        self.source.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)    # the column stays narrow
        self.source.setMinimumContentsLength(12)
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
        explain_all([(self.same_range, "reduce.window.same"), (self.remember_btn, "reduce.window.remember"),
                     (self.save_line_btn, "reduce.window.save_line")])
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
        mark_easy(self.caption, self.preset_help)
        on_mode_changed(self, lambda _a: (self._describe(), self.refresh_spectrum(), self._paint_note()), call_now=False)
        self.set_stack(None, None)

    # ------------------------------------------------------------------ the stack and its presets
    def set_stack(self, sd, scan, choice: dict | None = None) -> None:
        """Show a stack (None: nothing loaded) with a choice {'line', 'roi'} (None: the stack's main line)."""
        if getattr(getattr(self, "_sd", None), "id", None) != getattr(sd, "id", None):
            self._pix = None                # the region's pixels index the previous stack: Reduce sets this one's
        self._sd, self._scan = sd, scan
        self._labels = list(sd.labels) if sd is not None else []
        proj = self.session.project
        bins = getattr(proj, "roi_bins", None)
        edge = getattr(sd, "edge", None) if sd is not None else None
        cal = getattr(proj, "calibration", None)
        self._edge_line = xw.edge_line_windows(edge, cal, bins) if sd is not None else None
        if self._edge_line is None and edge is not None:
            from aurora.core.lines import resonant_line
            # on the nominal scale, only when nothing better holds the edge's line: a fixed line of that name, or John
            # Bussey's 60:83 (which holds F Kα at 10 eV per channel)
            if not (bins is not None and bins.has(resonant_line(*edge))) and xw.john_default_windows(edge) is None:
                self._edge_line = xw.edge_line_windows(edge, cal, bins, nominal=True)
        self.bins_note.setVisible(sd is not None and not (bins is not None and bins.labels))
        self._updating = True
        try:
            self.preset.clear()
            labs = [lab for lab in self._labels if xw.bins_windows(bins, lab) is not None]
            labs += [lab for lab in (bins.labels if bins is not None else []) if bins.has(lab) and lab not in labs]
            for lab in labs:                            # every line of roi_bins.json, and the stack's own lines
                self.preset.addItem(preset_text(lab), ("line", lab))
            if self._edge_line is not None:
                self.preset.addItem(f"line of the edge element: {self._edge_line.label} {self._edge_line.line_ev:.1f} eV"
                                    + (f" ({NOMINAL_EDGE})" if self._edge_line.nominal else ""),
                                    ("edge", self._edge_line.label))
            # no fixed bins nor calibration for the edge's line (John Bussey's own folders): his default window
            john = xw.john_default_windows(getattr(sd, "edge", None)) if sd is not None else None
            self._john = john if (john is not None and john[0] not in labs and self._edge_line is None) else None
            if self._john is not None:
                lo, hi = xw.JOHN_DEFAULT
                self.preset.addItem(f"standard {lo}:{hi} ({self._john[0]})", ("john", self._john[0]))
                self.preset.setItemData(self.preset.count() - 1, "the default window of the earlier SGM XAS GUI",
                                        Qt.ToolTipRole)
            self.preset.addItem(CUSTOM, ("custom", ""))
        finally:
            self._updating = False
        for w in (self.preset, self.lo, self.hi, self.same_range, self.remember_btn):
            w.setEnabled(sd is not None)
        self._save_line_state()
        self.det.setEnabled(sd is not None and not self.same_range.isChecked())
        edge = getattr(sd, "edge_text", "") if sd is not None else ""
        self.remember_btn.setText(f"Remember windows for {edge}" if edge else "Remember windows for the edge")
        cal = getattr(proj, "calibration", None)
        self.plot.setLabel("bottom", axis_x(bool(cal is not None and getattr(cal, "calibrated", False))))
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
            if roi and not xw.same_for_all(self._windows) and is_advanced():     # Easy never flips a hidden control
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
            if self._custom and kind == "john" and self._john is not None and xw.same(self._windows, self._john[1]):
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
        elif kind == "john" and self._john is not None:
            self._line, self._custom = self._john[0], True
            self._windows = xw.normalise_windows(self._john[1])
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

    def _linked(self) -> bool:
        """The four windows are edited together: Easy mode (one lo / hi pair, the checkbox hidden) or 'same range'."""
        return self.same_range.isChecked() or not is_advanced()

    def _shown_det(self) -> int:
        """The detector whose window the lo / hi boxes show: SDD1 when linked, else the one 'for' names."""
        return 0 if self._linked() else self.det.currentIndex()

    def set_window(self, det: int | None, lo: int, hi: int, commit: bool = True) -> None:
        """Set one detector's window to [lo, hi); det None gives all four that window. A linked edit (_linked) moves
        every detector's window by the change of det's, so windows that differ per detector keep their offsets."""
        lo, hi = int(lo), int(hi)
        w = xw.normalise_windows({DETS[0]: [lo, hi]})[DETS[0]]
        if det is None:
            for d in DETS:
                self._windows[d] = list(w)
        elif self._linked():
            dlo, dhi = w[0] - self._windows[DETS[det]][0], w[1] - self._windows[DETS[det]][1]
            self._windows = xw.normalise_windows({d: [a + dlo, b + dhi] for d, (a, b) in self._windows.items()})
        else:
            self._windows[DETS[det]] = list(w)
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
        if self._linked():                              # dragging one moves all four by as much
            a, b = self._windows[DETS[k]]
            self._updating = True
            try:
                for j, r in enumerate(self.regions):
                    if j != k:
                        wj = self._windows[DETS[j]]
                        r.setRegion((wj[0] + lo - a, wj[1] + hi - b))
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
        k = self._shown_det()
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

    # ------------------------------------------------------------------ Save as a fixed line…
    def _save_line_state(self) -> None:
        from aurora.app.widgets import set_enabled
        from aurora.core.io.safety import is_protected
        proj = self.session.project
        if self._sd is None or proj is None:
            set_enabled(self.save_line_btn, False, "pick a scan in step 1")
        elif is_protected(proj.config_dir / "roi_bins.json"):
            set_enabled(self.save_line_btn, False, "this beamtime folder is protected: AURORA never writes there")
        elif getattr(self.session, "read_only", False):
            set_enabled(self.save_line_btn, False, "this window is read-only (see the status line)")
        else:
            set_enabled(self.save_line_btn, True)

    def default_line_name(self) -> str:
        """The name offered for a new fixed line: the resonant line of the stack's edge ('F Kα', 'Fe Lα'), else the
        current preset's line, else 'line 1'."""
        sd = self._sd
        if sd is not None and getattr(sd, "edge", None):
            from aurora.core.lines import resonant_line
            return resonant_line(*sd.edge)
        if self._line and self._line != xw.TOTAL_LABEL:
            return self._line
        return "line 1"

    def ask_line_name(self, default: str) -> str | None:
        """The name of the new fixed line, asked in a dialog (tests replace this method)."""
        text, ok = QInputDialog.getText(self, "Save as a fixed line",
                                        "Name of the emission line (for example F Kα or Fe Lα). The windows of the "
                                        "four detectors shown now are saved in config/roi_bins.json:", text=default)
        return text.strip() if ok and text.strip() else None

    def confirm_replace(self, name: str) -> bool:
        """Whether to replace a fixed line of that name (tests replace this method)."""
        return QMessageBox.question(self, "Save as a fixed line",
                                    f"The beamtime already has a fixed line '{name}'. Replace its windows? The "
                                    "previous roi_bins.json is kept as a backup.") == QMessageBox.Yes

    def save_fixed_line(self, name: str | None = None) -> str:
        """Write the current windows into config/roi_bins.json as a fixed line (created when missing, the previous
        file kept as a backup), reload the project's lines and say so. Returns the line saved ('' when nothing)."""
        proj, sd = self.session.project, self._sd
        if proj is None or sd is None:
            return ""
        if name is None:
            name = self.ask_line_name(self.default_line_name())
        if not name:
            return ""
        bins = proj.roi_bins
        if bins is not None and bins.has(name) and not self.confirm_replace(name):
            return ""
        from datetime import date
        from aurora import __version__
        from aurora.core.io.config import RoiBins
        windows = self.windows()
        extra = {"saved": f"AURORA {__version__}, Reduce step 2, {date.today().isoformat()}, from {sd.name}"}
        ev = xw.emission_line_ev(name)
        if ev is not None:
            extra["line_ev"] = round(ev, 2)
        target = proj.config_dir / "roi_bins.json"
        try:
            (bins or RoiBins(target)).add_line(name, windows, path=target, extra=extra,
                                               created_by=f"AURORA {__version__}")
        except (OSError, ValueError) as err:
            from aurora.app.session import _os_text
            text = f"the fixed line was not saved: {_os_text(err)}"
            self.status.setText(text)
            self.session.status.emit(text)
            return ""
        proj.read_config()
        self.session.status.emit(f"fixed line '{name}' saved in config/roi_bins.json ({xw.describe(windows)}): a "
                                 "preset of every scan now, and an elemental map in Maps")
        self.session.config_changed.emit()
        return name

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
        k = self._shown_det()
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
        k = self._shown_det()
        lo, hi = self._windows[DETS[k]]
        a, b = xw.channel_ev(cal, DETS[k], lo), xw.channel_ev(cal, DETS[k], hi)
        nominal = cal is None or not getattr(cal, "calibrated", False)
        same = xw.same_for_all(self._windows)
        total = xw.same(self._windows, xw.bins_windows(None, xw.TOTAL_LABEL)) and getattr(self._sd, "edge", None)
        if is_advanced():
            who = "all detectors" if self._linked() and same else DET_LABELS[k]
            others = ""
            if self._linked() and not same:             # one lo / hi pair, four different windows: name them all
                others = " · " + ", ".join(f"{DET_LABELS[j]} {self._windows[d][0]}:{self._windows[d][1]}"
                                           for j, d in enumerate(DETS) if j != k) + " move with it"
            if total:
                from aurora.core.lines import resonant_line
                others += (f" · total XRF holds every emission line, not only the {resonant_line(*self._sd.edge)} of the "
                           "edge" + (f" (John Bussey's GUI started with {xw.JOHN_DEFAULT[0]}:{xw.JOHN_DEFAULT[1]})"
                                     if self._john is not None else ""))
            self.ev_label.setText(f"{who}: channels {lo}:{hi} = {a:.0f}–{b:.0f} eV"
                                  + (" (10 eV/ch nominal)" if nominal else " (calibrated)") + others)
            return
        who = "All four detectors" if self._linked() and same else f"Detector {k + 1} ({DET_LABELS[k]})"
        scale = "10 eV per channel, not calibrated" if nominal else "calibrated"
        text = f"{who}: {a:.0f}–{b:.0f} eV (channels {lo} to {hi - 1}, {scale})"
        if self._linked() and not same:                 # one from / to pair, four different windows: name them all
            text += " · " + ", ".join(f"{DET_LABELS[j]} {self._windows[d][0]}:{self._windows[d][1]}"
                                      for j, d in enumerate(DETS) if j != k) + " move with it"
        if total:
            from aurora.core.lines import resonant_line
            text += (f". All X-rays count here, every emission line, not only the {resonant_line(*self._sd.edge)} "
                     "of the edge element" + (f" (the earlier SGM XAS GUI started with {xw.JOHN_DEFAULT[0]}:"
                                              f"{xw.JOHN_DEFAULT[1]})" if self._john is not None else ""))
        self.ev_label.setText(text)

    def refresh_spectrum(self) -> None:
        """Draw the spectrum of the chosen source; the stack's is read in a job the first time (cached)."""
        sd = self._sd
        self._spectrum = None
        src = self.effective_source()
        if sd is None:
            self.status.setText("")
            self._draw()
            return
        if src in ("survey", "survey_zone"):
            try:
                sv = self.session.survey()
            except Exception as err:                   # an unreadable survey: say so, keep the card usable
                sv = None
                self.status.setText(f"the survey could not be read: {plain_error(err)}")
                self.status.setToolTip(str(err))
            if sv is not None:
                self._spectrum = np.asarray(sv.spec, float)
                self.status.setText(f"survey {sv.name} at {sv.energy:.1f} eV")
                if src == "survey_zone":
                    self._survey_zone(sv)
            self._draw()
            return
        em = xw.cached_stack_emission(sd)
        if em is not None:
            self._show_emission(em, sd)
            return
        if xw.source_of(sd) is None:
            self.status.setText(say("reduce.window.no_raw"))
            self._draw()
            return
        sid = sd.id
        self.status.setText("reading the emission spectrum …")
        self._draw()
        jobs.of(self).submit(f"emission:{sid}", lambda cb, s=sd: xw.stack_emission_data(s, cb),
                             on_done=lambda em, s=sd: self._emission_read(s, em),
                             on_failed=lambda t: say_failure(self.status, "could not read the X-ray spectra", t),
                             label=f"reading the emission spectrum of {sd.name}", taskbar=self.taskbar)

    def _emission_read(self, sd, em) -> None:
        if sd is self._sd:
            self._show_emission(em, sd)

    def effective_source(self) -> str:
        """The spectrum shown: the combo's choice in Advanced mode; in Easy mode the selected sample's pixels when it
        has some on this stack, else the whole stack."""
        if is_advanced():
            return self.source.currentData()
        return "region" if self._pix is not None and len(self._pix) else "stack"

    def _show_emission(self, em, sd) -> None:
        src = self.effective_source()
        if src == "region":
            if self._pix is None or not len(self._pix):
                self.status.setText(say("reduce.window.whole"))
                self._spectrum = em.spectrum(DETS)
            else:
                self._spectrum = em.spectrum(DETS, self._pix)
                self.status.setText(say("reduce.window.inside", label=self._label or "the sample", k=len(self._pix)))
        else:
            self._spectrum = em.spectrum(DETS)
            self.status.setText(say("reduce.window.stack", name=sd.name, e=sd.n_energies, n=sd.n_pix)
                                + (f" · {em.notes[0]}" if em.notes else ""))
        self._draw()

    def _survey_zone(self, sv) -> None:
        """'survey, inside the zone': the survey's XRF summed over the survey points inside the zone (the whole
        survey, and why, when there are none)."""
        from aurora.core.zones import members
        z = self._zone
        whole = f"survey {sv.name} at {sv.energy:.1f} eV"
        if z is None:
            self.status.setText(f"no zone selected: the whole {whole}")
            return
        pix = members(z, sv.x, sv.y, sv.pitch)
        if not pix.size:
            self.status.setText(f"the zone holds no survey point: the whole {whole}")
        elif not (sv.has_spectra or sv.reload_spectra()):
            self.status.setText(f"the survey's per-point spectra could not be read again: the whole {whole}")
        else:
            self._spectrum = sv.zone_spectrum(pix)
            self.status.setText(f"{whole}, inside the zone ({len(pix)} points)")

    def set_zone(self, zone) -> None:
        """The selected zone, or a stroke's provisional zone, for 'survey, inside the zone'."""
        self._zone = zone
        if self.source.currentData() == "survey_zone" and self._sd is not None:
            self.refresh_spectrum()

    def set_note(self, text: str) -> None:
        """The Easy inverse-yield note ('' hides it)."""
        self._note = text or ""
        self._paint_note()

    def _paint_note(self) -> None:
        text = getattr(self, "_note", "")
        self.note.set_markup(text)
        self.note.setVisible(bool(text) and not is_advanced())

    def set_region(self, pix, label: str = "") -> None:
        """The current region's members on the stack (for 'inside the region'), and the sample's name."""
        self._pix = None if pix is None else np.asarray(pix, int)
        self._label = label or ""
        if (self.effective_source() == "region" or not is_advanced()) and self._sd is not None:
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
