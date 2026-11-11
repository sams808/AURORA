"""The Survey's Zone preview: what a zone holds, while it is drawn and once it is written.

1. The XRF emission of the zone's survey points (EmissionPlot): the four SDDs in the series colours with their dashes
   and their sum, log y, x in channels, in counts per point (the mean over the zone's N points, so drawing changes
   the shape, not the scale), at the survey's one energy. The windows are marked read-only: the window of the map's
   channel shaded per SDD (roi_bins.json) and named above it, the other fixed lines as thin ticks at their centres
   (named beside them where the names fit), and the zone's own XRF windows on the scan below (ScanSettings.roi)
   dashed. No line is assumed: without fixed bins, or with total / TEY / I0 as the map's channel, no window shows.
   The line under it names the curves of both plots (SeriesLegend), or gives the hover readout.
2. The XAS of the zone on a stack it covers (PreviewPlot, fluorescence only): the scan is picked in 'scan' among the
   stacks the zone covers (their stage coordinates: loaded or not); the current stack when covered, else the earliest.
   A loaded stack previews live while drawing; one not loaded is read in a job (the key Reduce uses, so the two never
   read it twice) once the stroke is written, and drawn when it is in. 'Open in Reduce' opens it with the zone
   selected.
3. One summary line (what a screen reader reads): "zone 3 'NaF': 49 survey points · F Kα window 60–73 ch · on 09:27:
   36 of 121 pixels"; Easy mode names the sample by its label ("NaF: 49 survey points · …").
Empty states say what to do (no zone, an empty zone, a zone off the plate map, a zone over no scan), never bare axes.
The traces come from aurora.app.trace_cache, shared with Reduce and Check.
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from aurora.app.help_text import explain, labelled
from aurora.app.preview import PreviewPlot
from aurora.app.theme import TOKENS, curve, dash, make_plot, min_height, mk_pen, on_theme_changed, pc, with_alpha
from aurora.app.vocab import DET_LABELS, plural
from aurora.app.widgets import Card, ElidedLabel, FlowLayout, TaskBar, pair, set_plot_hint
from aurora.core.constants import DETS, N_CH
from aurora.glossary import what_is

TITLE = "Zone preview"
MIN_PANEL_PX = 140              # the emission and the XAS panel each get at least this (px at the Text size): their
EMISSION_PX = 150               # data areas keep 48 px (the XAS's sparse axis) and 80 px (the emission, under its
                                # title: EmissionPlot._fit_title) for their ticks
HEADROOM = 0.18                 # the emission's y range reaches this share of its decades above the highest count
OPEN_TEXT = "Open in Reduce"
NOT_LOADED = " (not loaded)"
MSG_NO_SURVEY = "This holder has no survey map: draw the zones on the stack map in Reduce"
MSG_NO_ZONE = "Select a zone, or draw one on the map (Draw, then drag)"
MSG_EMPTY = "{zone} is empty: drag on the map to draw it"
MSG_OFF_PLATE = "{zone} holds no survey point: it lies outside the plate map"
MSG_NO_STACK = "{zone} covers no scan of holder {holder}: draw it over a scan footprint (dashed outlines)"
MSG_NO_SPECTRA = "the survey's per-point spectra could not be read again"
MSG_READING = "reading {scan} …"
MSG_AT_RELEASE = "{scan} is not loaded: it is read when you let go"
MSG_GOOD_I0 = "reading the good-I0 scans …"
# Easy mode: the same messages in the Survey's words, naming the sample by its label (plain-language spec 5.3)
EASY_NO_SURVEY = "This holder has no survey map: outline the samples on their stack map in Reduce"
EASY_NO_ZONE = "Select a sample, or draw one on the map (Draw, then drag)"
EASY_EMPTY = MSG_EMPTY
EASY_OFF_PLATE = "{zone} holds no survey pixel: it lies outside the holder map"
EASY_NO_STACK = "{zone} covers no measured area of holder {holder}: draw it over a rectangle of the map"


def msg(name: str, **fields) -> str:
    """A message of the card in the words of the current mode: EASY_<name> in Easy mode, MSG_<name> in Advanced."""
    from aurora.app.mode import is_advanced
    text = globals()[("MSG_" if is_advanced() else "EASY_") + name]
    return text.format(**fields) if fields else text


def sample_name(z, next_id: int = 0) -> str:
    """The zone as the card names it (zone_tools.sample_name: the label in Easy mode, the zone in Advanced)."""
    from aurora.app import zone_tools
    return zone_tools.sample_name(z, next_id)


class SeriesLegend(QWidget):
    """One line naming the curves of the card's two plots, which share their colours and dashes: SDD1 to SDD4 (the
    series colours and dashes) and their sum (thick). It stands for the XAS plot's own legend, a box that covered
    most of the short panel."""
    NAMES = (*DET_LABELS, "sum")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = FlowLayout(self, spacing=6)
        lay.setContentsMargins(0, 0, 0, 0)
        self.swatches: list[QLabel] = []
        self.labels: list[QLabel] = []
        for name in self.NAMES:
            sw = QLabel()
            sw.setAccessibleName(f"{name} curve")
            t = QLabel(name)
            t.setObjectName("muted")
            t.setToolTip(what_is("sdd") if name != "sum" else "the sum of the detectors in use")
            lay.addWidget(sw)
            lay.addWidget(t)
            self.swatches.append(sw)
            self.labels.append(t)
        self.setAccessibleName("curves of the zone preview")
        self.refresh()
        on_theme_changed(self, lambda _s: self.refresh())

    def refresh(self) -> None:
        from aurora.app.theme import swatch_icon
        for i, sw in enumerate(self.swatches):
            colour, style = (pc("sum"), Qt.SolidLine) if i == len(DET_LABELS) else (curve(i), dash(i))
            sw.setPixmap(swatch_icon(colour, style, 12).pixmap(12, 12))

    def texts(self) -> list[str]:
        return [t.text() for t in self.labels]


class EmissionPlot(QWidget):
    """The XRF emission of survey points, counts per point (see the module docstring). `data` keeps what is drawn
    (per SDD and the sum, linear values) for the readout and the tests."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.plot = make_plot(x="channel", y="counts per point")
        self.plot.setLogMode(x=False, y=True)
        min_height(self.plot, EMISSION_PX)
        self.plot.getViewBox().disableAutoRange()
        self.plot.setAccessibleName("XRF emission of the zone's survey points")
        self.curves = [self.plot.plot([], [], pen=mk_pen(curve(i), 1.2, dash(i)), connect="finite")
                       for i in range(len(DETS))]
        self.sum_curve = self.plot.plot([], [], pen=mk_pen(pc("sum"), 2.0), connect="finite")
        self.readout = ElidedLabel("", Qt.ElideRight)
        self.readout.setObjectName("muted")
        self.legend = SeriesLegend()                        # the curves of both plots of the card
        self.line = QStackedWidget()                        # one line under the plot: the legend, or the hover
        self.line.addWidget(self.legend)                    # readout while the pointer is over the plot
        self.line.addWidget(self.readout)
        lay.addWidget(self.plot, 1)
        lay.addWidget(self.line)
        self.plot.installEventFilter(self)
        self.regions: list[pg.LinearRegionItem] = []        # the window of the map's channel, one per SDD
        self.ticks: list[pg.InfiniteLine] = []              # the other fixed lines
        self.zone_lines: list[pg.InfiniteLine] = []         # the zone's own windows on the scan previewed
        self.window: dict | None = None                     # sdd -> (lo, hi) of the shaded window
        self.window_label: pg.InfiniteLine | None = None    # its name above it
        self.tick_names: list[tuple[float, str]] = []       # (centre channel, line) of the ticks
        self.data: dict | None = None                       # {'per_point': (4, 256), 'sum': (256,), 'n': N}
        self.calibration = None
        self.plot.scene().sigMouseMoved.connect(self._moved)
        on_theme_changed(self, lambda _s: self._restyle())

    # ------------------------------------------------------------------ data
    def set_spectrum(self, per_point, n: int, title: str = "") -> None:
        """per_point: (4, 256) mean counts per point (None: nothing to draw)."""
        self.plot.setTitle(title)
        self._fit_title()
        self.line.setVisible(per_point is not None)
        self.line.setCurrentWidget(self.legend)
        if per_point is None:
            self.data = None
            for c in self.curves + [self.sum_curve]:
                c.setData([], [])
            return
        per_point = np.asarray(per_point, float)
        total = per_point.sum(axis=0)
        self.data = {"per_point": per_point, "sum": total, "n": int(n)}
        set_plot_hint(self.plot, None)
        x = np.arange(N_CH) + 0.5                       # channel c spans [c, c + 1): windows sit on the channel edges
        pos = []
        for k, c in enumerate(self.curves):
            y = np.where(per_point[k] > 0, per_point[k], np.nan)       # log axis: empty channels are gaps
            c.setData(x, y)
            pos.append(y[np.isfinite(y)])
        ys = np.where(total > 0, total, np.nan)
        self.sum_curve.setData(x, ys)
        pos.append(ys[np.isfinite(ys)])
        vals = np.concatenate(pos) if pos else np.array([])
        lo, hi = (float(vals.min()), float(vals.max())) if vals.size else (0.1, 1.0)
        lo = max(lo, 1e-3)
        top = np.log10(max(hi, lo * 10))
        top += HEADROOM * (top - np.log10(lo))          # room above the highest peak for the window names
        self.plot.getViewBox().setRange(xRange=(0, N_CH), yRange=(np.log10(lo), top), padding=0.04,
                                        disableAutoRange=True)

    def show_message(self, text: str) -> None:
        """An empty plot with a sentence instead of axes."""
        self.set_spectrum(None, 0, "")
        self.set_windows(None, [], None)
        set_plot_hint(self.plot, text)

    @staticmethod
    def _label_opts(position: float, colour: str, side: str = "centre") -> dict:
        """A line label on a plate of the plot's background (readable over the curves), centred on its line or beside
        it ('left' / 'right'), so the names of two windows close together do not print over each other."""
        anchor = {"left": (1.0, 0.5), "right": (0.0, 0.5)}.get(side, (0.5, 0.5))
        return {"position": position, "color": colour, "anchors": [anchor, anchor],
                "fill": pg.mkBrush(with_alpha(TOKENS["plot_bg"], 0.85))}

    def set_windows(self, current: dict | None, others, zone: dict | None, zone_label: str = "",
                    name: str = "") -> None:
        """current: sdd -> (lo, hi) of the map channel's window (shaded), named `name` above it; others: [(line,
        centre channel)] (dotted ticks, named beside them on the side away from the shaded window); zone: sdd ->
        [lo, hi] of the zone's own windows on the scan below (dashed), labelled `zone_label`."""
        for it in self.regions + self.ticks + self.zone_lines + ([self.window_label] if self.window_label else []):
            self.plot.removeItem(it)
        self.regions, self.ticks, self.zone_lines, self.window_label = [], [], [], None
        self.window = {d: (int(v[0]), int(v[1])) for d, v in current.items()} if current else None
        centre = None
        if self.window:
            for i, d in enumerate(DETS):
                lo, hi = self.window[d]
                r = pg.LinearRegionItem(values=(lo, hi), movable=False,
                                        brush=pg.mkBrush(with_alpha(curve(i), 0.10)))
                for ln in r.lines:
                    ln.setPen(mk_pen(curve(i), 1.0, dash(i)))
                r.setZValue(-5 + i)
                self.plot.addItem(r, ignoreBounds=True)
                self.regions.append(r)
            centre = float(np.mean([(lo + hi) / 2 for lo, hi in self.window.values()]))
            if name:
                self.window_label = pg.InfiniteLine(pos=centre, angle=90, pen=pg.mkPen(None), label=name,
                                                    labelOpts=self._label_opts(0.94, TOKENS["plot_fg"]))
                self.plot.addItem(self.window_label, ignoreBounds=True)
        self.tick_names = sorted((float(at), label) for label, at in others or [])
        named = self._names_that_fit(self.tick_names, centre)
        for at, label in self.tick_names:
            side = "centre" if centre is None else ("left" if at < centre else "right")
            t = pg.InfiniteLine(pos=at, angle=90, pen=mk_pen(TOKENS["muted"], 1.0, Qt.DotLine),
                                label=label if label in named else None,
                                labelOpts=self._label_opts(0.78, TOKENS["muted"], side))
            t.setZValue(-8)
            t.setToolTip(f"{label} window")
            self.plot.addItem(t, ignoreBounds=True)
            self.ticks.append(t)
        if zone:
            for i, d in enumerate(DETS):
                w = zone.get(d)
                if not w:
                    continue
                for j, v in enumerate(w[:2]):
                    ln = pg.InfiniteLine(pos=float(v), angle=90, pen=mk_pen(curve(i), 1.4, Qt.DashLine),
                                         label=zone_label if (i == 0 and j == 0 and zone_label) else None,
                                         labelOpts={"position": 0.75, "color": TOKENS["plot_fg"]})
                    self.plot.addItem(ln, ignoreBounds=True)
                    self.zone_lines.append(ln)

    def _restyle(self) -> None:
        self.sum_curve.setPen(mk_pen(pc("sum"), 2.0))
        self._fit_title()                               # the title's font follows the Text size

    def _fit_title(self) -> None:
        """The title row as tall as its text: pyqtgraph gives a title a fixed 30 px row, which leaves the short
        emission plot (EMISSION_PX) less than 80 px of data area under 'XRF at 720.0 eV · 15 survey points'."""
        pi = self.plot.getPlotItem()
        tl = pi.titleLabel
        if tl.text:
            pi.layout.setRowFixedHeight(0, max(float(tl.minimumHeight()), 1.0))

    # ------------------------------------------------------------------ hover
    def _names_that_fit(self, ticks, centre) -> set[str]:
        """The tick names that can be printed without touching each other (left to right on one row, each beside its
        tick on the side away from the shaded window): a beamtime with ten fixed lines prints the ones there is room
        for; the others are named by the hover readout and the tick's tooltip."""
        from PySide6.QtGui import QFontMetricsF
        vb = self.plot.getViewBox()
        width_px = vb.width() if vb.width() > 50 else 300.0      # before the first layout: a narrow column
        per_ch = width_px / N_CH
        fm = QFontMetricsF(self.font())
        kept, out = [], set()
        for at, label in ticks:
            w = (fm.horizontalAdvance(label) + 10) / per_ch      # in channels, with the plate's padding
            if centre is None:
                lo, hi = at - w / 2, at + w / 2
            else:
                lo, hi = (at - w, at) if at < centre else (at, at + w)
            if lo < 0 or hi > N_CH or any(lo < k_hi and hi > k_lo for k_lo, k_hi in kept):
                continue
            kept.append((lo, hi))
            out.add(label)
        return out

    def readout_text(self, ch: int) -> str:
        """'channel 66 · F Kα window · 665 eV · SDD1 12.3 · … · sum 50.1 counts per point' (eV only with a calibration;
        the window or fixed line at the channel named)."""
        if self.data is None or not 0 <= ch < N_CH:
            return ""
        parts = [f"channel {ch}"]
        if self.window and self.window_label is not None and self.window[DETS[0]][0] <= ch < self.window[DETS[0]][1]:
            parts.append(f"{self.window_label.label.toPlainText()} window")
        else:
            near = [(abs(at - (ch + 0.5)), label) for at, label in getattr(self, "tick_names", [])]
            if near and min(near)[0] <= 4:
                parts.append(f"near {min(near)[1]}")
        cal = self.calibration
        if cal is not None and getattr(cal, "calibrated", False):
            from aurora.core import xrf_windows as xw
            parts.append(f"{xw.channel_ev(cal, DETS[0], ch + 0.5):.0f} eV")
        pp = self.data["per_point"]
        parts += [f"{DET_LABELS[k]} {pp[k][ch]:.3g}" for k in range(len(DETS))]
        parts.append(f"sum {self.data['sum'][ch]:.3g} counts per point")
        return " · ".join(parts)

    def _moved(self, pos) -> None:
        if self.data is None or not self.plot.getPlotItem().sceneBoundingRect().contains(pos):
            self.line.setCurrentWidget(self.legend)
            return
        x = self.plot.getPlotItem().vb.mapSceneToView(pos).x()
        self.readout.setText(self.readout_text(int(np.floor(x))))
        self.line.setCurrentWidget(self.readout)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.plot and ev.type() == QEvent.Leave:
            self.line.setCurrentWidget(self.legend)         # the pointer left the plot: the legend again
        return False


def window_text(channel: str, bounds: dict | None) -> str:
    """'F Kα window 60–73 ch' (first and last channel, SDD1; 'on detector 1 (SDD1)' when the detectors'
    windows differ)."""
    if not bounds:
        return ""
    lo, hi = bounds[DETS[0]]
    same = len({tuple(v) for v in bounds.values()}) == 1
    return f"{channel} window {lo}–{hi - 1} ch" + ("" if same else " on detector 1 (SDD1)")


class ZonePreviewCard(Card):
    """The Survey's Zone preview (see the module docstring). show_zone(z) shows a written zone (the selected one);
    show_draft(draft, fast) a stroke in progress; set_survey(sv, channel) the holder's survey and the map's channel."""
    open_requested = Signal(object, int)                # scan, zone id: 'Open in Reduce'

    def __init__(self, session, parent: QWidget | None = None):
        super().__init__(TITLE, parent, help_anchor="3-survey")
        self.session = session
        self._sv = None
        self._sv_error = ""
        self._channel = ""
        self._shown_sid: str | None = None              # the scan the XAS shows
        self._covered: list[tuple[str, object, int, int]] = []     # (sid, scan, pixels in the zone, pixels)
        self._failed: set[str] = set()                  # stacks whose job failed (not retried for this zone)
        self._draft_settings: dict = {}                 # (stroke serial, sid) -> the settings a draft previews with
        self.body.setSpacing(4)                         # two plots of MIN_PANEL_PX in the Survey's narrow column
        self.summary = ElidedLabel("", Qt.ElideRight)   # one line; the full text in its tooltip
        self.summary.setObjectName("muted")
        self.body.addWidget(self.summary)
        self.emission = EmissionPlot()
        self.body.addWidget(self.emission, 1)
        self.legend = self.emission.legend              # under the emission: the curves of both plots
        self.xas_row = QWidget()                        # scan, Open in Reduce (wraps at a large Text size)
        row = FlowLayout(self.xas_row, spacing=6)
        self.scan_combo = QComboBox()
        self.scan_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.scan_combo.setMinimumContentsLength(14)
        explain(self.scan_combo, "zones.preview.scan")
        row.addWidget(pair(labelled("scan", self.scan_combo, "zones.preview.scan"), self.scan_combo))
        self.open_btn = QPushButton(OPEN_TEXT)
        explain(self.open_btn, "zones.preview.open")
        self.open_btn.clicked.connect(self._open)
        row.addWidget(self.open_btn)
        self.body.addWidget(self.xas_row)
        self.xas_note = QLabel("")
        self.xas_note.setObjectName("muted")
        self.xas_note.setWordWrap(True)
        self.body.addWidget(self.xas_note)
        self.xas = PreviewPlot(min_plot_px=MIN_PANEL_PX, overlay=False)
        self.xas.set_compact(True)
        self.xas.line.hide()                            # the emission's readout line is the card's one
        self.xas.drop_legend()                          # the card's legend line names the curves: no legend row
        self.header.addWidget(self.xas.show_btn)        # its 'Show ▾' (Advanced) beside the card's title: no row
        self.xas.gl.setAccessibleName("XAS of the zone on the scan")
        self.body.addWidget(self.xas, 1)
        self.taskbar = TaskBar(self)
        self.body.addWidget(self.taskbar)
        self.scan_combo.currentIndexChanged.connect(self._scan_picked)
        self._zone_id: int | None = None
        self._draft = None
        self.show_zone(None)
        on_theme_changed(self, lambda _s: self._restyled())

    # ------------------------------------------------------------------ inputs
    def set_survey(self, sv, channel: str = "", error: str = "") -> None:
        self._sv, self._channel, self._sv_error = sv, channel or "", error
        self._failed.clear()
        self.emission.calibration = getattr(self.session.project, "calibration", None)

    def set_channel(self, channel: str) -> None:
        self._channel = channel or ""

    def show_zone(self, z) -> None:
        """The written zone z (None: no zone selected); unloaded stacks it covers are read in a job."""
        self._draft = None
        self._zone_id = z.id if z is not None else None
        self._render(z, None, None, False)

    def show_draft(self, draft, fast: bool = False) -> None:
        """A stroke in progress (aurora.app.zone_tools.ZoneDraft on the survey points)."""
        self._draft = draft
        self._render(draft.zone, np.asarray(draft.members_after, int), draft, fast)

    def refresh(self) -> None:
        """The selected zone again (an edit, an undo, a stack read, a channel change)."""
        self.show_zone(self.session.zone() if self.session.project is not None else None)

    def _restyled(self) -> None:
        """A theme change: the messages and the windows in the new scheme's colours."""
        if self._draft is None:
            self.refresh()

    # ------------------------------------------------------------------ drawing
    def _name(self, z) -> str:
        return sample_name(z, self._next_id())

    def _next_id(self) -> int:
        hz = self.session.holder_zones(create=False) if self.session.project is not None else None
        return max((zz.id for zz in (hz.zones if hz else [])), default=0) + 1

    def _say(self, emission: str, xas: str = "", summary: str | None = None) -> None:
        self.emission.show_message(emission)
        self._show_xas(False, xas)
        self.summary.setText(summary if summary is not None else emission)
        self.setAccessibleDescription(self.summary.text())

    def _show_xas(self, on: bool, note: str = "") -> None:
        self.xas_row.setVisible(on)
        self.xas.setVisible(on)
        self.xas_note.setText(note)
        self.xas_note.setVisible(bool(note))

    def _render(self, z, pix, draft, fast: bool) -> None:
        from aurora.app.zone_tools import zone_drawn
        from aurora.core.zones import members
        sv = self._sv
        if sv is None:
            self._say(self._sv_error or msg("NO_SURVEY"))
            return
        if z is None:
            self._say(msg("NO_ZONE"))
            return
        name = self._name(z)
        if not zone_drawn(z):
            self._say(msg("EMPTY", zone=name))
            return
        if pix is None:
            pix = members(z, sv.x, sv.y, sv.pitch)
        n = int(len(pix))
        bounds = self._bounds(self._channel)
        parts = [f"{name}: {plural(n, 'survey point')}"]
        if bounds:
            parts.append(window_text(self._channel, bounds))
        if n == 0:
            self.emission.show_message(msg("OFF_PLATE", zone=name))
        elif not (sv.has_spectra or sv.reload_spectra()):
            self.emission.show_message(MSG_NO_SPECTRA)
        else:
            self.emission.set_spectrum(sv.zone_spectrum(pix) / n, n,
                                       f"XRF at {sv.energy:.1f} eV · {plural(n, 'survey point')}")
        self._covered = self._covering(z)
        if not self._covered:
            h = self.session.holder()
            self._show_xas(False, msg("NO_STACK", zone=name, holder=h.name if h is not None else ""))
            if n:
                self.emission.set_windows(bounds, self._other_ticks(), None, name=self._channel)
            self._summary(parts)
            return
        self._fill_combo(draft is not None)
        sid = self.scan_combo.currentData()
        self._shown_sid = sid
        zone_roi = self._preview_xas(z, sid, draft, fast, parts)
        if n:
            scan = self.session.scan_of(sid)
            self.emission.set_windows(bounds, self._other_ticks(), zone_roi,
                                      f"XRF window of the zone on {scan.time if scan is not None else sid}",
                                      name=self._channel)
        self._summary(parts)

    def _summary(self, parts) -> None:
        self.summary.setText(" · ".join(p for p in parts if p))
        self.setAccessibleDescription(self.summary.text())

    def _bounds(self, channel: str) -> dict | None:
        """The fixed bins of the map's channel (None for total, TEY, I0 or a line without bins)."""
        bins = getattr(self.session.project, "roi_bins", None)
        sv = self._sv
        if bins is None or sv is None or channel not in sv.lines or not bins.has(channel):
            return None
        return bins.bounds(channel)

    def _other_ticks(self):
        out = []
        for lab in (self._sv.lines if self._sv is not None else []):
            if lab == self._channel:
                continue
            b = self._bounds(lab)
            if b:
                out.append((lab, float(np.mean([(lo + hi) / 2 for lo, hi in b.values()]))))
        return out

    def _covering(self, z) -> list[tuple[str, object, int, int]]:
        """(sid, scan, pixels of the zone, pixels) of every complete stack of the holder the zone covers, by time."""
        from aurora.core.zones import members
        out = []
        for s in self.session.stacks_of():
            sid = self.session.stack_id(s)
            c = self.session.coordinates(sid)
            if c is None:
                continue
            x, y, pitch, n_pix = c
            k = int(members(z, x, y, pitch).size)
            if k:
                out.append((sid, s, k, int(n_pix)))
        out.sort(key=lambda t: (getattr(t[1], "time", ""), t[0]))
        return out

    def _fill_combo(self, drafting: bool) -> None:
        want = self.scan_combo.currentData()
        sids = [c[0] for c in self._covered]
        if want not in sids:
            cur = self.session.current_stack
            want = cur if cur in sids else sids[0]
        self.scan_combo.blockSignals(True)
        self.scan_combo.clear()
        for sid, s, _k, _n in self._covered:
            loaded = self.session.stack_loaded(sid) is not None
            self.scan_combo.addItem(f"{s.time} {s.label}".strip() + ("" if loaded else NOT_LOADED), sid)
        self.scan_combo.setCurrentIndex(max(0, sids.index(want)))
        self.scan_combo.blockSignals(False)
        self.open_btn.setEnabled(not drafting and self._zone_id is not None)

    def _scan_picked(self, _i: int) -> None:
        if self._draft is None:
            self.refresh()

    def _preview_xas(self, z, sid, draft, fast: bool, parts) -> dict | None:
        """Draw the XAS of z on sid; returns the zone's own XRF windows there (or None)."""
        from aurora.app.trace_cache import zone_traces_cached
        from aurora.app.zone_tools import fast_settings
        from aurora.core import xrf_windows as xw
        from aurora.core.names import short_time
        from aurora.core.zones import members
        ses = self.session
        scan = ses.scan_of(sid)
        short = short_time(sid) or (scan.name if scan is not None else sid)
        k, n_pix = next(((c[2], c[3]) for c in self._covered if c[0] == sid), (0, 0))
        parts.append(f"on {short}: {k} of {plural(n_pix, 'pixel')}")
        sd = ses.stack_loaded(sid)
        self._show_xas(True)
        if sd is None:
            if draft is not None:
                self.xas.set_traces([], waiting=MSG_AT_RELEASE.format(scan=short))
            elif sid in self._failed:
                self.xas.set_traces([], waiting=f"{short} could not be read")
            else:
                self.xas.set_traces([], waiting=MSG_READING.format(scan=short))
                self._load(sid, short)
            return None
        self._attach(sd)
        owner = draft.target if (draft is not None and draft.target is not None) else z     # a draft copy has no settings
        st = owner.settings.get(sid) if getattr(owner, "settings", None) else None
        if st is None and scan is not None:
            key = (getattr(getattr(draft, "stroke", None), "serial", None), sid)
            st = self._draft_settings.get(key) if draft is not None else None
            if st is None:
                st = ses.default_settings(scan, sd)     # what the zone will get there (Easy rules, the rules)
                if draft is not None:
                    self._draft_settings = {key: st}
        if st is None:
            self.xas.set_traces([])
            return None
        roi = dict(st.roi) if st.roi else None
        if fast:
            st = fast_settings(st)
        if st.roi and not xw.stack_window_ready(sd, st.roi):
            import copy
            st = copy.deepcopy(st)
            st.roi = None                               # the fixed bins until Reduce has read the windows
            self.xas_note.setText(f"the XRF windows of {short} are not read yet: the fixed bins are shown")
            self.xas_note.setVisible(True)
        if st.i0_source == "good" and ses.good_i0_missing():
            self.xas.set_traces([], waiting=MSG_GOOD_I0)
            return roi
        good = ses.good_curve_for(sd.E) if st.i0_source == "good" else None
        t = zone_traces_cached(sd, members(z, sd.x, sd.y, sd.pitch), st, good)
        if t is None:
            self.xas.set_traces([])
        else:
            self.xas.update_traces([(short, t, {"edge_ev": sd.edge_ev, "edge": sd.edge_text})])
        return roi

    def _attach(self, sd) -> None:
        """Tie a loaded stack to its raw folders (the XRF windows read them), as Reduce does."""
        from aurora.core import xrf_windows as xw
        if sd is None or self.session.project is None or str(sd.id).startswith("stitched_"):
            return
        if getattr(sd, "_xrf_source", None) is None:
            scan = self.session.scan_of(sd.id)
            if scan is not None:
                xw.attach(sd, scan, self.session.project.work_root)

    def _load(self, sid: str, short: str) -> None:
        from aurora.app import jobs
        ses = self.session
        jobs.of(self).submit(f"stack:{sid}", lambda cb: ses.stack(sid, progress=cb),
                             on_done=lambda _sd, s=sid: self._loaded(s),
                             on_failed=lambda t, s=sid: self._load_failed(s, t),
                             label=f"reading {short}", taskbar=self.taskbar)

    def _loaded(self, sid: str) -> None:
        self._attach(self.session.stack_loaded(sid))
        self.session.touch()                            # the memberships for the stack just read (refreshes us)
        if self._draft is None:
            self.refresh()

    def _load_failed(self, sid: str, trace: str) -> None:
        self._failed.add(sid)
        self.xas.set_traces([], waiting="could not read the stack: " + (trace.strip().splitlines() or [""])[-1])

    def _open(self) -> None:
        sid = self._shown_sid
        scan = self.session.scan_of(sid) if sid else None
        if scan is not None and self._zone_id is not None:
            self.open_requested.emit(scan, self._zone_id)

    # ------------------------------------------------------------------ for the tests and the readers
    def xas_sum(self):
        """The fluorescence sum the XAS panel shows (None when it shows none)."""
        tr = self.xas._traces
        return tr[0][1].sum if tr else None
