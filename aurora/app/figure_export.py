"""Figure options of the Compare and Fit exports (John Bussey's 'Plot size'): width x height in inches, font size,
PNG resolution and formats, kept in the settings, and the 'Export figure' dialog that sets them.

Exports never grab the screen: aurora.core.figures draws the figure again with matplotlib at the chosen size, in the
export colours (theme.export_tokens(): LIGHT plus CURVES on white, whatever the screen scheme).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from aurora.app.help_text import explain, explain_buttons
from aurora.app.vocab import unit_suffix
from aurora.core.figures import FORMATS, WIDTH_PRESETS_IN, FigureOptions

DEFAULTS = {"width_in": 3.5, "height_in": 4.5, "font_pt": 8.0, "dpi": 600, "formats": list(FORMATS)}
KEY = "figure/"


def current_options() -> FigureOptions:
    """The figure options in force (settings, else the defaults: 3.5 x 4.5 in, 8 pt, 600 dpi, PNG + SVG + PDF)."""
    from aurora.app.settings import settings
    s = settings()

    def num(key, cast):
        try:
            return cast(s.value(KEY + key, DEFAULTS[key]))
        except (TypeError, ValueError):
            return DEFAULTS[key]
    fm = s.value(KEY + "formats", DEFAULTS["formats"])
    if isinstance(fm, str):
        fm = [f for f in fm.split(",") if f]
    fm = tuple(f for f in (fm or []) if f in FORMATS) or tuple(FORMATS)
    return FigureOptions(width_in=num("width_in", float), height_in=num("height_in", float), font_pt=num("font_pt", float),
                         dpi=num("dpi", int), formats=fm)


def store_options(o: FigureOptions) -> None:
    from aurora.app.settings import settings
    s = settings()
    s.setValue(KEY + "width_in", float(o.width_in))
    s.setValue(KEY + "height_in", float(o.height_in))
    s.setValue(KEY + "font_pt", float(o.font_pt))
    s.setValue(KEY + "dpi", int(o.dpi))
    s.setValue(KEY + "formats", ",".join(o.formats))
    s.sync()


def summary(o: FigureOptions | None = None) -> str:
    """'3.5 × 4.5 in, 8 pt, PNG 600 dpi (2100 × 2700 px), SVG, PDF'."""
    o = o or current_options()
    w, h = o.pixel_size()
    fm = [f"PNG {o.dpi} dpi ({w} × {h} px)" if f == "png" else f.upper() for f in o.formats]
    return f"{o.width_in:g} × {o.height_in:g} in, {o.font_pt:g} pt, " + ", ".join(fm)


def styled(o: FigureOptions, **content) -> FigureOptions:
    """A copy of the size options with the plot content and the export colours filled in."""
    from dataclasses import replace

    from aurora import __version__
    from aurora.app.theme import export_tokens
    t = export_tokens()
    meta = {"Creator": f"AURORA {__version__}"}
    if content.get("title"):
        meta["Title"] = content["title"]
    return replace(o, ink=t["ink"], axis=t["plot_axis"], fg=t["plot_fg"], bg=t["plot_bg"], metadata=meta, **content)


def export_colour(i: int, colour: str | None = None) -> tuple[str, str, str]:
    """(line colour, label colour, dash name) of series i in an exported figure: the row's colour (else CURVES[i]),
    its LABELS entry on white (else the colour darkened to 4.5:1 on white) and the dash of series i."""
    from aurora.app.theme import CURVES, DASHES, dash, export_tokens, readable
    t = export_tokens()
    c = (colour or CURVES[i % len(CURVES)]).upper()
    lab = t["labels"][CURVES.index(c)] if c in CURVES else readable(c, t["plot_bg"], 4.5)
    return c, lab, t["dashes"][DASHES.index(dash(i))]


def role_colour(role: str) -> tuple[str, str]:
    """(colour, dash name) of a semantic plot role (target, fit, resid, ...) in an exported figure."""
    from aurora.app.theme import CURVES, DASHES, ROLES, export_tokens
    t = export_tokens()
    src, style = ROLES.get(role, ("plot_fg", Qt.SolidLine))
    c = CURVES[int(src.split(":", 1)[1])] if src.startswith("curve:") else t[src]
    return c, t["dashes"][DASHES.index(style)] if style in DASHES else "solid"


class FigureDialog(QDialog):
    """'Export figure': the size, font, resolution and formats every Compare and Fit export uses."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Export figure: size and formats")
        o = current_options()
        lay = QVBoxLayout(self)
        intro = QLabel("Compare and Fit exports draw the figure again at this size, in the export colours (white "
                       "background), whatever the window looks like.")
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.width = QDoubleSpinBox(); self.width.setRange(1.0, 20.0); self.width.setDecimals(2); self.width.setSingleStep(0.25)
        self.width.setSuffix(" in"); self.width.setValue(o.width_in)
        self.height = QDoubleSpinBox(); self.height.setRange(1.0, 20.0); self.height.setDecimals(2); self.height.setSingleStep(0.25)
        self.height.setSuffix(" in"); self.height.setValue(o.height_in)
        wrow = QWidget(); wl = QHBoxLayout(wrow); wl.setContentsMargins(0, 0, 0, 0)
        wl.addWidget(self.width)
        self.presets = []
        for w, text in zip(WIDTH_PRESETS_IN, ("one column", "two columns")):
            b = QPushButton(f"{w:g} in ({text})")
            b.clicked.connect(lambda _c=False, ww=w: self.width.setValue(ww))
            explain(b, "compare.fig_preset")
            b.setToolTip(f"Width {w:g} in: {text} of a journal page.")
            b.setAccessibleName(f"width {w:g} inches, {text}")
            wl.addWidget(b)
            self.presets.append(b)
        wl.addStretch(1)
        form.addRow("width", wrow)
        form.labelForField(wrow).setBuddy(self.width)
        form.addRow("height", self.height)
        self.font = QDoubleSpinBox(); self.font.setRange(4.0, 24.0); self.font.setDecimals(1); self.font.setSingleStep(0.5)
        self.font.setSuffix(" pt"); self.font.setValue(o.font_pt)
        form.addRow("font size", self.font)
        self.dpi = QSpinBox(); self.dpi.setRange(72, 2400); self.dpi.setSingleStep(100); self.dpi.setSuffix(unit_suffix("dpi"))
        self.dpi.setValue(int(o.dpi))
        form.addRow("PNG resolution", self.dpi)
        frow = QWidget(); fl = QHBoxLayout(frow); fl.setContentsMargins(0, 0, 0, 0)
        self.fmt = {}
        for f in FORMATS:
            c = QCheckBox(f.upper()); c.setChecked(f in o.formats); fl.addWidget(c); self.fmt[f] = c
            explain(c, f"compare.fig_{f}")
        fl.addStretch(1)
        form.addRow("formats", frow)
        lay.addLayout(form)
        self.pixels = QLabel(""); self.pixels.setObjectName("muted")
        lay.addWidget(self.pixels)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Help)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.helpRequested.connect(self._help)
        explain_buttons(buttons)
        lay.addWidget(buttons)
        for w, key in ((self.width, "compare.fig_width"), (self.height, "compare.fig_height"), (self.font, "compare.fig_font"),
                       (self.dpi, "compare.fig_dpi")):
            explain(w, key)
            w.valueChanged.connect(self._update)
        for c in self.fmt.values():
            c.toggled.connect(self._update)
        self._update()

    def _help(self) -> None:
        from aurora.app.help import open_help
        open_help(self, "MANUAL.md", "64-diagnostics-and-export")

    def options(self) -> FigureOptions:
        fm = tuple(f for f in FORMATS if self.fmt[f].isChecked())
        return FigureOptions(width_in=self.width.value(), height_in=self.height.value(), font_pt=self.font.value(),
                             dpi=self.dpi.value(), formats=fm)

    def _update(self, *_) -> None:
        o = self.options()
        w, h = o.pixel_size()
        self.pixels.setText(f"PNG {w} × {h} px · PDF and SVG {o.width_in * 72:g} × {o.height_in * 72:g} pt"
                            if o.formats else "tick at least one format")
        ok = self.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
        if ok is not None:
            ok.setEnabled(bool(o.formats))

    def accept(self) -> None:
        store_options(self.options())
        super().accept()


def open_dialog(parent: QWidget | None = None) -> bool:
    """Show the 'Export figure' dialog; True when the options were changed (OK)."""
    d = FigureDialog(parent)
    ok = bool(d.exec())
    d.deleteLater()
    return ok
