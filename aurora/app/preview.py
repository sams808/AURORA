"""Preview of a zone's traces: fluorescence (four SDDs + sum), TEY, I0 in three linked panels, spikes marked.

A redraw whose traces have the structure of those drawn (one trace, the same detectors, settings, toggles and scheme)
moves the existing curves with setData instead of rebuilding the plots (update_traces, and set_traces, which goes the
same way): a zone being drawn redraws the preview at every stroke.

What is drawn is chosen in one 'Show ▾' menu (SDDs, sum, TEY, I0, spikes), so the controls stay one row in a narrow
column; one elided line under the plots says what the grey bands are, and gives the hover readout while the pointer
is over them; the legend is a row of its own under the fluorescence data (RowLegend). The plots keep at least
MIN_PLOT_PX (the
fluorescence panel MIN_TOP_PX); with the three panels shown, THREE_PANELS_PX at the Text size (so their ticks stay
readable; the page scrolls). Below COMPACT_BELOW_PX of plot height the TEY and I0 panels hide and the fluorescence
takes the whole height (compact), until 'TEY and I0 panels' in the menu brings them back. set_compact(True) keeps a
preview compact (the Survey's zone preview)."""
from __future__ import annotations

import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QFont, QFontMetricsF
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QMenu, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout,
                               QWidget)

from aurora.app.theme import (DASH, PAL, curve, dash, min_height, on_theme_changed, pc, role_pen,  # noqa: F401
                              style_plot, with_alpha)                     # PAL / DASH re-exported
from aurora.core.spectra import ZoneTraces

THREE_PANELS_PX = 300           # the three panels with readable ticks (g09; a page scrolls rather than squeeze them)
MIN_PLOT_PX = 180               # the compact fluorescence panel never gets less (min_plot_px: the Survey's 120)
MIN_TOP_PX = 110                # the fluorescence panel never gets less
COMPACT_BELOW_PX = 260          # below this plot height, the TEY and I0 panels hide (compact)
SHOW_TEXT = "Show ▾"
TITLES = {"pfy": "Fluorescence (each detector and the sum)",      # short: it fits the Survey's column (S-26)
          "ipfy": "Inverse yield (IPFY) of the sample", "tey": "Electron yield (TEY): the surface",
          "i0": "Beam intensity (I0) during the scan"}
SUM_NAME = "sum"            # the title says "each detector and their sum", a detector left out is named so: one
                            # legend line beside SDD1 to SDD4 (as the Survey's Zone preview)
SPIKES_NAME = "repaired points (spikes)"          # the legend draws the red dot itself
BANDS_NOTE = "Grey bands: the energy ranges before and after the edge used to scale the spectrum."


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


_QT_MAX = 16777215                  # QWIDGETSIZE_MAX
_HSPACE = 6                         # px between a legend entry's sample and text, and between entries


class RowLegend(pg.LegendItem):
    """The fluorescence panel's legend in a row of its own under the data, never over the curves (the rise at the edge
    included): as many columns as the width holds (fit), so the four detectors, the sum and the spikes take one or two
    lines. A legend inside the data covered the edge, or was cut at the bottom of a low panel."""

    def __init__(self):
        super().__init__(offset=None, colCount=5, horSpacing=_HSPACE, verSpacing=0, frame=False)
        self.on_fit = None                              # called after every relayout (PreviewPlot: its min heights)
        self.layout.setContentsMargins(2, 0, 2, 0)
        self.room = 0.0                                 # the width the legend may take (fit)

    def addItem(self, item, name):
        super().addItem(item, name)
        self.fit()

    def removeItem(self, item):
        super().removeItem(item)
        self.fit()

    @staticmethod
    def _entry_px(sample, label) -> float:
        return sample.boundingRect().width() + label.item.boundingRect().width() + 12

    def fit(self, room: float | None = None) -> int:
        """Lay the entries out row by row in the most columns whose widths add up to `room` (the last room given);
        returns the number of columns."""
        if room is not None:
            self.room = float(room)
        if not self.items:
            return self.columnCount
        widths = [self._entry_px(s, lab) for s, lab in self.items]
        cols = 1
        if self.room > 0:
            for n in range(len(widths), 0, -1):
                if sum(max(widths[j::n]) for j in range(n)) <= self.room:
                    cols = n
                    break
        else:
            cols = min(len(widths), self.columnCount or 1)
        self.columnCount = cols
        lay = self.layout                               # the same grid (a replaced layout is deleted by Qt under the
        for i in range(lay.count() - 1, -1, -1):        # wrapper pyqtgraph keeps: a crash at the next font change)
            lay.removeAt(i)
        for k, (sample, label) in enumerate(self.items):
            r, c = divmod(k, cols)
            lay.addItem(sample, r, 2 * c, alignment=Qt.AlignVCenter)
            lay.addItem(label, r, 2 * c + 1)
        rows = math.ceil(len(self.items) / cols)
        # Qt keeps the rows and columns of removed items: an earlier narrow layout (five rows) kept the legend five
        # rows high. The ones not used now take no room.
        for r in range(lay.rowCount()):
            if r < rows:
                lay.setRowMinimumHeight(r, 0)
                lay.setRowMaximumHeight(r, _QT_MAX)
            else:
                lay.setRowFixedHeight(r, 0)
            lay.setRowSpacing(r, 0)
        for c in range(lay.columnCount()):
            if c < 2 * cols:
                lay.setColumnMinimumWidth(c, 0)
                lay.setColumnMaximumWidth(c, _QT_MAX)
            else:
                lay.setColumnFixedWidth(c, 0)
            if c >= 2 * cols - 1:
                lay.setColumnSpacing(c, 0)
            else:
                lay.setColumnSpacing(c, _HSPACE)
        self.rowCount = rows
        self.updateSize()
        # never wider than the room it was laid out for: its width must not hold the plots wider than the view (they
        # were cut at the right, and the page scrolled sideways, before the next fit)
        self.setMinimumWidth(1.0)
        self.updateGeometry()
        if self.on_fit is not None:
            self.on_fit()
        return cols

    def rows(self) -> int:
        return math.ceil(len(self.items) / max(1, self.columnCount)) if self.items else 0


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
        self.top = self.gl.addPlot(row=0, col=0, axisItems={"left": SparseAxis("left")})
        self.mid = self.gl.addPlot(row=1, col=0, axisItems={"left": SparseAxis("left")})
        self.low = self.gl.addPlot(row=2, col=0, axisItems={"left": SparseAxis("left")})
        self.mid.setXLink(self.top)
        self.low.setXLink(self.top)
        for p_ in (self.top, self.mid, self.low):
            p_.setLabel("left", "arb. units")              # uncalibrated: arbitrary units (short: the panels are low)
        self.low.setLabel("bottom", "energy (eV)")
        for p in (self.top, self.mid, self.low):
            p.showGrid(x=True, y=True, alpha=0.15)
            p.vb.disableAutoRange()
        for p in (self.top, self.mid):                  # linked x: one row of energies, under the lowest panel
            p.getAxis("bottom").setStyle(showValues=False)
        # the legend in a row of its own under the fluorescence data (the top panel's layout, under its bottom axis
        # and label): never over the curves; its columns follow the width (_fit_legend)
        self.legend = RowLegend()
        self.top.layout.addItem(self.legend, 5, 1)
        self.top.legend = self.legend
        self.legend.on_fit = lambda: self._apply_min() if hasattr(self, "_min_plot") else None
        self.gl.ci.layout.setRowStretchFactor(0, 5)
        self.gl.ci.layout.setRowStretchFactor(1, 2)
        self.gl.ci.layout.setRowStretchFactor(2, 2)
        lay.addWidget(self.gl, 1)
        self.bands_note = ElidedLabel(BANDS_NOTE, Qt.ElideRight)   # what the grey bands are (shown when drawn)
        self.bands_note.setObjectName("muted")
        # one line under the plots: what the grey bands are, and the hover readout while the pointer is over the
        # plots (two lines held the page 14 px taller than a 1500 x 940 window in Advanced mode)
        self.line = QStackedWidget()
        self.line.addWidget(self.bands_note)
        self.line.addWidget(self.readout)
        self.line.setCurrentWidget(self.readout)
        self.line.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._bands_line = False                        # the grey-band note is the line's idle text
        lay.addWidget(self.line)
        self.top.setMinimumHeight(MIN_TOP_PX)
        self._min_plot = int(min_plot_px)
        self._forced_compact: bool | None = None        # None: automatic (by the plot height)
        self._bands_tip = False                         # the grey-band note in the tooltip (held compact)
        self._compact = False
        self._apply_min()                               # g09: room for readable ticks
        on_theme_changed(self, lambda _s: self._apply_min())   # ... at every Text size
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
        if obj is self.gl and ev.type() == QEvent.Leave:
            self._hover_off()
        if obj is self.gl and ev.type() == QEvent.Resize:
            self._layout_rows()
            self._fit_titles()
            self._fit_legend()
            # the view seats its plots at their minimum width before the narrower titles and legend take effect (a
            # 1500 to 1200 px resize left the plots 80 px wider than the view, cut at the right): seat them again
            QTimer.singleShot(0, self, self._reseat)
        return False

    def _reseat(self) -> None:
        """Fit the plots to the view again once the titles and the legend have their new widths."""
        gl = self.gl
        if gl.centralWidget is not None and gl.centralWidget.geometry().width() > gl.width() + 0.5:
            pg.GraphicsView.setRange(gl, gl.range, padding=0, disableAutoPixel=False)

    def _fit_legend(self) -> None:
        """The legend's columns for the width beside the left axis (RowLegend.fit)."""
        from aurora.app.theme import px
        lg = self.top.legend
        if isinstance(lg, RowLegend):
            lg.fit(self._room(self.top))                # its on_fit refits the plots' minimum heights

    def _room(self, plot) -> float:
        """The width a panel's title or legend may take: the view less the panel's left axis and the layout's margins
        (wider, and the plots grow past the view and are cut at the right)."""
        from aurora.app.theme import px
        m = self.gl.ci.layout.getContentsMargins()
        pm = plot.layout.getContentsMargins()
        spare = m[0] + m[2] + pm[0] + pm[2] + px(8)
        return max(float(px(60)), self.gl.width() - plot.getAxis("left").width() - spare)

    def drop_legend(self) -> None:
        """No legend in the plot (a card that names the curves in a line of its own: the Survey's Zone preview)."""
        lg = self.top.legend
        if lg is None:
            return
        self.top.layout.removeItem(lg)
        if lg.scene() is not None:
            lg.scene().removeItem(lg)
        self.top.legend = None

    # ------------------------------------------------------------------ titles and the grey-band note
    def _set_titles(self, top: str, mid: str, low: str) -> None:
        """The panel titles (TITLES), each elided to the width of its panel (_fit_titles)."""
        self._titles = {"top": top, "mid": mid, "low": low}
        self._fit_titles()

    def _fit_titles(self) -> None:
        """pyqtgraph makes the width of a title's text the minimum width of its plot: a long title in a narrow
        column (the Survey's Zone preview, Reduce at 1200 px) pushed the right of the plot, its last energies and
        the legend, out of view. Each title is elided to the width beside the left axis; the full titles are the
        plots' tooltip."""
        titles = getattr(self, "_titles", None)
        if titles is None:
            return
        from aurora.app.theme import px
        fm = QFontMetricsF(self._title_font())
        row = self.title_row_px()
        cut = False
        for key, plot in (("top", self.top), ("mid", self.mid), ("low", self.low)):
            text = titles[key]
            room = self._room(plot)
            shown = fm.elidedText(text, Qt.ElideRight, room) if text else ""
            cut = cut or shown != text
            if plot.titleLabel.text != shown:
                plot.setTitle(shown)
            plot.layout.setRowFixedHeight(0, row)      # pyqtgraph's fixed 30 px row: the text's height instead
        tip = [t for t in titles.values() if t] if cut else []     # a tooltip only when something is not shown
        if self._bands_tip:
            tip.append(BANDS_NOTE)
        self.gl.setToolTip("\n".join(tip))

    def _show_bands_note(self, on: bool) -> None:
        """The line that says what the grey bands are, under the plots; a compact preview held compact (the
        Survey's Zone preview, whose column has no line to spare) says it in the plots' tooltip instead."""
        held = self._forced_compact is True
        self._bands_tip = bool(on and held)
        self._bands_line = bool(on and not held)
        self._hover_off()
        self._fit_titles()

    def _hover_off(self) -> None:
        """The pointer left the plots: the line under them says what the grey bands are again (when they are drawn)."""
        self.line.setCurrentWidget(self.bands_note if self._bands_line else self.readout)

    def _title_font(self) -> QFont:
        from aurora.app.theme import px
        font = QFont(self.gl.font())
        font.setPixelSize(px(14))                      # the title size of theme.restyle_plot
        return font

    def title_row_px(self) -> int:
        """The height of a panel's title row: one line of the title font (pyqtgraph gives every title 30 px)."""
        return int(math.ceil(QFontMetricsF(self._title_font()).height())) + 2

    def legend_px(self) -> int:
        """The height of the legend's row under the fluorescence data (0 without a legend or entries)."""
        lg = self.top.legend
        if not isinstance(lg, RowLegend) or not lg.items:
            return 0
        return int(math.ceil(lg.effectiveSizeHint(Qt.PreferredSize).height()))

    def _apply_min(self) -> None:
        """The plots' minimum height at the Text size: THREE_PANELS_PX with the three panels, plus the title rows of the
        TEY and I0 panels (their data keep the room they had before they had titles); min_plot_px compact. The legend's
        row is added to both, so the fluorescence data keep their height under it."""
        from aurora.app.theme import px
        compact = self._forced_compact if self._forced_compact is not None else self._compact
        extra = self.legend_px()
        self.top.setMinimumHeight(MIN_TOP_PX + extra)
        if compact:
            self.gl.setMinimumHeight(px(self._min_plot) + extra)
        else:
            self.gl.setMinimumHeight(px(max(self._min_plot, THREE_PANELS_PX)) + 2 * self.title_row_px() + extra)

    def _layout_rows(self) -> None:
        if self._forced_compact is not None:
            want = self._forced_compact
        else:
            want = self.gl.height() < COMPACT_BELOW_PX and not self.show_panels.isChecked()
        self.show_panels.setVisible(self._forced_compact is None and (want or self.show_panels.isChecked()))
        if want == self._compact:
            return
        self._compact = want
        self._apply_min()
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
            self._set_titles("", "", "")
            self._show_bands_note(False)
            set_plot_hint(self.top, self._waiting or "select a sample (zone) and a scan to see its traces")
            for p in (self.mid, self.low):
                set_plot_hint(p, " ")                 # no bare 0..1 axes on an empty preview
            return
        for p in (self.top, self.mid, self.low):
            set_plot_hint(p, None)
        multi = len(self._traces) > 1
        t0 = self._traces[0][1]
        ipfy = bool(t0.settings and getattr(t0.settings, "signal", "pfy") == "ipfy")
        self._set_titles(TITLES["ipfy" if ipfy else "pfy"]
                         + (" (raw counts)" if t0.settings and t0.settings.fluo_norm == "raw" else ""),
                         TITLES["tey"], TITLES["i0"])
        self._show_bands_note(bool(t0.pre))
        self._spike_named = False
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
                    item = self.top.plot(t.E, y, pen=pen, connect="finite", name=(f"SDD{i + 1}" + ("" if on else " (left out)")) if ti == 0 else None)
                    item.setOpacity(1.0 if on else 0.35)
                    self._items[f"sdd{i}"] = item
                    self._spikes(self.top, t.E, y, t.spikes.get(f"sdd{i + 1}"), None if multi else f"spk:sdd{i}")
            if self.show_sum.isChecked() and t.sum is not None:
                self._items["sum"] = self.top.plot(t.E, t.sum, pen=pg.mkPen(col or pc("sum"), width=2.2), connect="finite",
                                                   name=(name + " " if multi else "") + SUM_NAME)
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
        update_traces can move them. The first dots drawn on the fluorescence panel get the legend's 'repaired
        points (spikes)' entry."""
        if not self.show_spikes.isChecked() or not (idx or role):
            return
        idx = [i for i in (idx or []) if np.isfinite(y[i])]
        if idx or role:
            it = pg.ScatterPlotItem(E[idx], y[idx], size=6, brush=pg.mkBrush(pc("spike")), pen=None)
            plot.addItem(it, ignoreBounds=True)
            if role:
                self._items[role] = it
            if plot is self.top and idx:
                self._name_spikes(it)

    def _name_spikes(self, it) -> None:
        """The red dots say what they are: one legend entry, for the first dots of the fluorescence panel."""
        if not getattr(self, "_spike_named", False) and self.top.legend is not None:
            self.top.legend.addItem(it, SPIKES_NAME)
            self._spike_named = True

    def _rename_spikes(self) -> None:
        """After an update in place: the legend entry while the fluorescence panel shows dots, none without."""
        shown = [it for role, it in self._items.items()
                 if role.startswith("spk:") and role != "spk:tey" and role != "spk:i0" and len(it.data)]
        if shown:
            self._name_spikes(shown[0])
        elif getattr(self, "_spike_named", False) and self.top.legend is not None:
            self.top.legend.removeItem(SPIKES_NAME)
            self._spike_named = False

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
                st.fluo_norm if st else None, getattr(st, "signal", "pfy") if st else None, bool(t.pre), edge,
                meta.get("edge", ""), bool(edge and t.E[0] <= edge <= t.E[-1]))

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
        self._rename_spikes()
        for role, rng in (("pre", t.pre), ("post", t.post)):
            if role in items and rng:
                items[role].setRegion(list(rng))
        self._set_ranges()
        return True

    def _moved(self, pos) -> None:
        if not self._traces or not self.top.sceneBoundingRect().contains(pos):
            self._hover_off()
            return
        self.line.setCurrentWidget(self.readout)
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
