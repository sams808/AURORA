"""Colour tokens, light and dark schemes, the Qt style, palette and style sheet built from them, and the plot styler.

Readability rules (WCAG 2.1): text >= 4.5:1 on the background it sits on, UI parts and plot lines >= 3:1. The token
values were recomputed for both schemes; `tests/test_theme.py` checks every pair, so a token edit that breaks
readability fails the suite.

API used by the perspectives (the only source of colours in aurora/app; read them at draw time, never into module
constants):
    TOKENS / tokens()           live token table of the current scheme (updated in place by apply_theme)
    CURVES / curve(i)           one series palette for both schemes (PAL / pal() are aliases)
    dash(i) / pen(i)            series i: CURVES[i % 6] with DASHES[(i + i // 6) % 4], 24 distinct series
    LABELS / label_colour(i)    text drawn in a series colour (>= 4.5:1 on the current plot background)
    categorical(n)              up to 20 colours for categorical maps (clusters)
    pc(role) / role_pen(role)   semantic plot colours ('ink', 'fit', 'resid', 'i0', 'good', 'tey', 'spike', 'edge', ...)
    readable(colour, bg)        the colour, lightened or darkened until it reads on bg
    make_plot / style_plot      create or theme a pyqtgraph widget now and on every change
    swatch_icon, status_brush, status_text, status_icon, set_item_tone, refill_tones, set_tone
    on_theme_changed(owner, fn) call fn(scheme) after every theme change while `owner` lives
    export_tokens()             LIGHT plus CURVES on white: figures that leave the screen
    apply_theme(app)            AuroraStyle + palette + style sheet + pyqtgraph defaults, then notify
"""
from __future__ import annotations

import colorsys
import math

from PySide6.QtCore import QCoreApplication, QObject, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QProxyStyle, QStyle, QStyleFactory

# ----------------------------------------------------------------------------- tokens
# Ratios (WCAG 2.1) are listed in docs of the 0.4 foundation and checked by tests/test_theme.py.
LIGHT = {
    # surfaces
    "paper": "#F3F5F9", "card": "#FFFFFF", "alt": "#F6F8FC", "header": "#EEF2F8", "sunken": "#E8ECF3",
    "muted_bg": "#E4E9F2", "night": "#0A1430", "rail": "#101E4A", "rail_hover": "#1A2A5E", "select": "#C9ECE4",
    "tooltip_bg": "#0A1430", "action_disabled_bg": "#EEF2F8", "plot_bg": "#FFFFFF", "map_bg": "#0B1630",
    # text
    "ink": "#15213B", "muted": "#4E5D7A", "disabled": "#5F6B84", "placeholder": "#5F6B84", "link": "#0A6A5B",
    "select_fg": "#15213B", "on_action": "#FFFFFF", "tooltip_fg": "#F4FFF8", "chrome_text": "#F4FFF8",
    "crumb": "#B8C7E3", "rail_text": "#C9D6F0", "on_accent": "#0A1430",
    "ok_fg": "#0A5F52", "ok_bg": "#E3F8F3", "warn_fg": "#8A4B00", "warn_bg": "#FFF1DD", "bad_fg": "#9B1C1C",
    "bad_bg": "#FDE2E2", "plot_fg": "#2A3652", "map_fg": "#C9D6F0",
    # UI parts and lines
    "aurora": "#38E0C2", "action": "#0E7C6B", "action_dark": "#0A5F52", "edge": "#7D89A1", "focus": "#0E7C6B",
    "plot_axis": "#7D89A1", "map_axis": "#7F8FB0", "plot_ink": "#15213B", "plot_bad": "#C62828",
    "line": "#DDE3EE", "plot_grid": "#E1E6EF",
}
DARK = {
    "paper": "#0F1626", "card": "#172033", "alt": "#1B2539", "header": "#1E2942", "sunken": "#0B111E",
    "muted_bg": "#232E46", "night": "#070E20", "rail": "#0C1838", "rail_hover": "#1A2A5E", "select": "#1E4A47",
    "tooltip_bg": "#27334E", "action_disabled_bg": "#232E46", "plot_bg": "#172033", "map_bg": "#0B1630",
    "ink": "#E7EDF8", "muted": "#A9B6CF", "disabled": "#8F9BB3", "placeholder": "#97A3BC", "link": "#5FDCC5",
    "select_fg": "#E7EDF8", "on_action": "#06201B", "tooltip_fg": "#F4F7FC", "chrome_text": "#F4FFF8",
    "crumb": "#B8C7E3", "rail_text": "#C9D6F0", "on_accent": "#0A1430",
    "ok_fg": "#7FE0CC", "ok_bg": "#123B35", "warn_fg": "#F5C27E", "warn_bg": "#3B2A12", "bad_fg": "#FFA3A3",
    "bad_bg": "#43191E", "plot_fg": "#C9D3E6", "map_fg": "#C9D6F0",
    "aurora": "#38E0C2", "action": "#2EC4A6", "action_dark": "#5AD6BE", "edge": "#6F7E9C", "focus": "#38E0C2",
    "plot_axis": "#7684A2", "map_axis": "#7F8FB0", "plot_ink": "#E7EDF8", "plot_bad": "#FF7B7B",
    "line": "#2A3651", "plot_grid": "#2A3651",
}

# one series palette for both schemes: a series keeps its colour when the scheme changes, and exports on white read
CURVES = ["#2A6CCA", "#DB4A12", "#0FA175", "#B74898", "#BE881D", "#1393B4"]
CURVE_NAMES = ["blue", "vermillion", "bluish green", "reddish purple", "ochre", "sky"]
DASHES = [Qt.SolidLine, Qt.DashLine, Qt.DotLine, Qt.DashDotLine]
DASH = DASHES                                   # alias (older imports)
PAL = CURVES                                    # alias of CURVES (older imports)
# text drawn in a series colour, >= 4.5:1 on its scheme's plot background
LABELS_LIGHT = ["#2A6CCA", "#D44303", "#088762", "#B74898", "#9A6C08", "#0A809D"]
LABELS_DARK = ["#488AE9", "#E95825", "#0FA175", "#CF5EAE", "#BE881D", "#1393B4"]

# map overlays on the navy map background (both schemes)
MAP_OVERLAY = {
    "rect_add": "#F5C27E",       # rect+ rubber band
    "erase": "#FF7B7B",          # erase squares, rect- rubber band and erase rectangles
    "roi": "#38E0C2",            # ROI outline
}
FOOTPRINTS = {                   # scan footprints on the survey map: colour and its own dash (plus a legend entry)
    "complete": ("#F2A65A", Qt.SolidLine),
    "aborted": ("#FF5555", Qt.DashLine),
    "in_progress": ("#A78BFA", Qt.DotLine),
    "test": ("#A9B6CF", Qt.DashDotLine),
}

# semantic plot roles: (source, pen style). 'curve:N' = CURVES[N], otherwise a token name.
ROLES = {
    "ink": ("plot_ink", Qt.SolidLine), "target": ("plot_ink", Qt.SolidLine), "raw": ("plot_ink", Qt.SolidLine),
    "sum": ("plot_ink", Qt.SolidLine),
    "fit": ("curve:1", Qt.DashLine), "resid": ("muted", Qt.SolidLine), "i0": ("curve:1", Qt.SolidLine),
    "good": ("curve:2", Qt.DashLine), "diode": ("curve:5", Qt.SolidLine), "ratio": ("curve:2", Qt.SolidLine),
    "tey": ("plot_fg", Qt.SolidLine), "spike": ("plot_bad", Qt.SolidLine), "edge": ("plot_axis", Qt.DashLine),
    "edge_label": ("plot_fg", Qt.SolidLine), "e0": ("curve:3", Qt.DotLine), "pre": ("curve:4", Qt.DashLine),
    "post": ("curve:0", Qt.DashLine), "band": ("action", Qt.SolidLine), "region": ("edge", Qt.SolidLine),
    "roi": ("aurora", Qt.SolidLine),
}

TOKENS: dict[str, str] = dict(LIGHT)          # live: the current scheme's tokens
LABELS: list[str] = list(LABELS_LIGHT)        # live: the current scheme's label colours
TEXT_SIZES = {"normal": 13, "large": 15, "larger": 17}
_STATE = {"scheme": "light", "size": 13, "applying": False}

# chips and status cells: tone -> (background token, text token)
TONES = {"ok": ("ok_bg", "ok_fg"), "warn": ("warn_bg", "warn_fg"), "bad": ("bad_bg", "bad_fg"),
         "muted": ("muted_bg", "muted")}
STATUS_TONE = {"complete": "ok", "survey": "ok", "in_progress": "warn", "test": "muted", "aborted": "bad",
               "empty": "bad", "unreadable": "bad", "other": "muted"}
STATUS_COLOURS = {k: TONES[v] for k, v in STATUS_TONE.items()}          # kept for older imports (chips)
STATUS_TEXT = {"ok": ("✓", "ok"), "warn": ("!", "check"), "bad": ("✗", "bad"), "muted": ("·", "")}
TONE_ROLE = int(Qt.UserRole) + 0x7A0          # item data role holding the tone of a status / flag cell
TONE_ICON_ROLE = TONE_ROLE + 1                # ... and whether the cell carries the tone's icon

UI_FONT = "Inter, 'Segoe UI', 'SF Pro Text', 'Helvetica Neue', Arial, sans-serif"
MONO_FONT = "'JetBrains Mono', Consolas, 'SF Mono', Menlo, monospace"


# ----------------------------------------------------------------------------- colour maths
def _lum(colour: str) -> float:
    c = QColor(colour)
    f = (lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * f(c.redF()) + 0.7152 * f(c.greenF()) + 0.0722 * f(c.blueF())


def contrast(a, b) -> float:
    """WCAG 2.1 contrast ratio of two colours (1 .. 21)."""
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def composite(fg, bg) -> str:
    """The opaque colour of `fg` (with its alpha) painted over the opaque `bg`."""
    f, b = QColor(fg), QColor(bg)
    a = f.alphaF()
    return QColor.fromRgbF(f.redF() * a + b.redF() * (1 - a), f.greenF() * a + b.greenF() * (1 - a),
                           f.blueF() * a + b.blueF() * (1 - a)).name()


def with_alpha(colour, alpha: float) -> QColor:
    """QColor of `colour` with an alpha of 0..1 (never append hex digits: QColor reads '#AARRGGBB')."""
    c = QColor(colour)
    c.setAlphaF(max(0.0, min(1.0, float(alpha))))
    return c


def readable(colour: str | None, bg: str | None = None, min_ratio: float = 3.0) -> str:
    """`colour` lightened (on a dark background) or darkened (on a light one) until it reaches `min_ratio`
    against `bg` (default: the plot background). Hue is kept, so a trace keeps its identity in both schemes."""
    bg = bg or TOKENS["plot_bg"]
    colour = colour or pc("ink")
    if contrast(colour, bg) >= min_ratio:
        return colour
    c = QColor(colour)
    h, l, s = colorsys.rgb_to_hls(c.redF(), c.greenF(), c.blueF())
    up = _lum(bg) < 0.18
    for _ in range(40):
        l = min(1.0, l + 0.025) if up else max(0.0, l - 0.025)
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        out = QColor.fromRgbF(r, g, b).name()
        if contrast(out, bg) >= min_ratio:
            return out
    return TOKENS["plot_fg"]


def _srgb_to_lin(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _lin_to_srgb(v: float) -> float:
    return 12.92 * v if v <= 0.0031308 else 1.055 * (max(v, 0.0) ** (1 / 2.4)) - 0.055


def _to_oklch(colour: str) -> tuple[float, float, float]:
    c = QColor(colour)
    r, g, b = (_srgb_to_lin(v) for v in (c.redF(), c.greenF(), c.blueF()))
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, math.hypot(a, bb), math.atan2(bb, a)


def _oklch_rgb(L: float, C: float, h: float) -> tuple[float, float, float]:
    a, b = C * math.cos(h), C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)


def _from_oklch(L: float, C: float, h: float) -> str:
    """sRGB hex of an OKLCH colour; the chroma is reduced until the colour fits the sRGB gamut."""
    lo, hi = 0.0, C
    rgb = _oklch_rgb(L, C, h)
    if not all(-1e-6 <= v <= 1 + 1e-6 for v in rgb):
        for _ in range(30):
            mid = (lo + hi) / 2
            if all(-1e-6 <= v <= 1 + 1e-6 for v in _oklch_rgb(L, mid, h)):
                lo = mid
            else:
                hi = mid
        rgb = _oklch_rgb(L, lo, h)
    r, g, b = (min(1.0, max(0.0, _lin_to_srgb(min(1.0, max(0.0, v))))) for v in rgb)
    return QColor.fromRgbF(r, g, b).name().upper()


# ----------------------------------------------------------------------------- live accessors
def scheme() -> str:
    return _STATE["scheme"]


def is_dark() -> bool:
    return _STATE["scheme"] == "dark"


def tokens() -> dict[str, str]:
    """A copy of the current scheme's tokens."""
    return dict(TOKENS)


def export_tokens() -> dict:
    """Colours for figures that leave the screen (exports, report, matplotlib): LIGHT plus CURVES on white,
    whatever the screen scheme. Keys of LIGHT plus 'curves', 'labels' and 'dashes'."""
    out: dict = dict(LIGHT)
    out["plot_bg"] = "#FFFFFF"
    out["curves"] = list(CURVES)
    out["labels"] = list(LABELS_LIGHT)
    out["dashes"] = ["solid", "dash", "dot", "dashdot"]
    return out


def curve(i: int) -> str:
    """Series colour i (the same in both schemes)."""
    return CURVES[int(i) % len(CURVES)]


def pal(i: int) -> str:
    """Alias of curve()."""
    return curve(i)


def dash(i: int):
    """Pen style of series i: with curve(i) it gives 24 distinct series (colour-blind readers also get the dash)."""
    i = int(i)
    return DASHES[(i + i // len(CURVES)) % len(DASHES)]


def mk_pen(colour, width: float = 1.4, style=Qt.SolidLine) -> QPen:
    p = QPen(QColor(colour))
    p.setWidthF(float(width))
    p.setStyle(style)
    p.setCosmetic(True)
    return p


def pen(i: int, width: float = 1.4) -> QPen:
    """Cosmetic pen of series i (colour and dash)."""
    return mk_pen(curve(i), width, dash(i))


def label_colour(i: int) -> str:
    """Text colour for series i on the current plot background (>= 4.5:1)."""
    return LABELS[int(i) % len(LABELS)]


def label_for(colour: str | None) -> str:
    """Text colour for a series drawn in `colour`: its LABELS entry when it is a CURVES colour, otherwise the colour
    adjusted to 4.5:1 on the current plot background."""
    if not colour:
        return TOKENS["plot_fg"]
    up = QColor(colour).name().upper()
    if up in CURVES:
        return LABELS[CURVES.index(up)]
    return readable(up, TOKENS["plot_bg"], 4.5)


def categorical(n: int) -> list[str]:
    """n colours for categorical maps (clusters): CURVES first, then the same hues at +15 % and -15 % OKLCH lightness,
    then at +7.5 %; at most 20 distinct colours, repeated beyond."""
    out = list(CURVES)
    for dl in (0.15, -0.15, 0.075):
        for c in CURVES:
            L, C, h = _to_oklch(c)
            out.append(_from_oklch(min(0.97, max(0.12, L + dl)), C, h))
    out = out[:20]
    n = max(0, int(n))
    return [out[i % len(out)] for i in range(n)]


def pc(role: str) -> str:
    """Semantic plot colour of a role for the current scheme."""
    src = ROLES.get(role, ("plot_fg", Qt.SolidLine))[0]
    if src.startswith("curve:"):
        return curve(int(src.split(":", 1)[1]))
    return TOKENS[src]


def role_dash(role: str):
    return ROLES.get(role, ("plot_fg", Qt.SolidLine))[1]


def role_pen(role: str, width: float = 1.4) -> QPen:
    return mk_pen(pc(role), width, role_dash(role))


def text_px() -> int:
    return _STATE["size"]


def px(v: float) -> int:
    """A pixel size scaled with the text size (13 px base)."""
    return max(1, round(v * _STATE["size"] / 13.0))


# ----------------------------------------------------------------------------- notifier
class ThemeNotifier(QObject):
    changed = Signal(str)


def notifier() -> ThemeNotifier | None:
    """The notifier lives as a child of the QApplication (never at module level: it must die with the app)."""
    app = QCoreApplication.instance()
    if app is None:
        return None
    n = app.findChild(ThemeNotifier, "aurora_theme_notifier")
    if n is None:
        n = ThemeNotifier(app)
        n.setObjectName("aurora_theme_notifier")
    return n


class _Hook(QObject):
    """Child of the owner: Qt disconnects it when the owner is destroyed, so callbacks never hit dead widgets."""

    def __init__(self, owner: QObject, fn):
        super().__init__(owner)
        self._fn = fn

    @Slot(str)
    def fire(self, s: str) -> None:
        try:
            self._fn(s)
        except RuntimeError:            # the owner's C++ side is already gone
            pass


def on_theme_changed(owner: QObject, fn) -> None:
    """Call fn(scheme) after each theme change for as long as `owner` lives."""
    n = notifier()
    if n is not None:
        n.changed.connect(_Hook(owner, fn).fire)


# ----------------------------------------------------------------------------- cells: swatches, status, tones
def _pixmap(px_: int) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(px_ * 2, px_ * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    return pm, p


def swatch_icon(colour, dash_style=Qt.SolidLine, px: int = 12) -> QIcon:
    """A px-sized colour swatch for table cells (ink text next to it): a filled square for a solid series, a light
    square crossed by the dash pattern otherwise. A fresh icon each call (never cached at module level)."""
    pm, p = _pixmap(px)
    r = QRectF(0.5, 0.5, px - 1.0, px - 1.0)
    col = QColor(colour)
    p.setPen(mk_pen(TOKENS["edge"], 1.0))
    if dash_style == Qt.SolidLine:
        p.setBrush(QBrush(col))
        p.drawRoundedRect(r, 2.5, 2.5)
    else:
        p.setBrush(QBrush(with_alpha(colour, 0.22)))
        p.drawRoundedRect(r, 2.5, 2.5)
        line = QPen(col)
        line.setWidthF(2.0)
        line.setCapStyle(Qt.FlatCap)
        pattern = {Qt.DashLine: [2.0, 1.2], Qt.DotLine: [0.7, 1.0], Qt.DashDotLine: [2.0, 0.9, 0.7, 0.9]}
        line.setDashPattern(pattern.get(dash_style, [2.0, 1.2]))
        p.setPen(line)
        p.drawLine(QPointF(1.5, px / 2), QPointF(px - 1.5, px / 2))
    p.end()
    return QIcon(pm)


def tone_of(kind: str | None) -> str:
    """'ok' / 'warn' / 'bad' / 'muted' for a tone or a scan status ('complete', 'aborted', ...)."""
    if kind in TONES:
        return kind
    return STATUS_TONE.get(kind or "", "muted")


def status_brush(kind: str | None) -> QBrush:
    """Text brush of a status or flag cell, read from the current tokens (call it when the cell is filled)."""
    return QBrush(QColor(TOKENS[TONES[tone_of(kind)][1]]))


def status_text(kind: str | None) -> tuple[str, str]:
    """(icon glyph, word) of a tone: ('✓', 'ok'), ('!', 'check'), ('✗', 'bad'). No colour-only cue."""
    return STATUS_TEXT[tone_of(kind)]


def status_icon(kind: str | None, px: int = 12) -> QIcon:
    """A small painted icon of a tone (check mark, exclamation mark, cross) in the tone's text colour."""
    tone = tone_of(kind)
    pm, p = _pixmap(px)
    pn = QPen(QColor(TOKENS[TONES[tone][1]]))
    pn.setWidthF(max(1.6, px / 7.0))
    pn.setCapStyle(Qt.RoundCap)
    pn.setJoinStyle(Qt.RoundJoin)
    p.setPen(pn)
    s = float(px)
    if tone == "ok":
        path = QPainterPath(QPointF(0.18 * s, 0.55 * s))
        path.lineTo(0.42 * s, 0.78 * s)
        path.lineTo(0.84 * s, 0.24 * s)
        p.drawPath(path)
    elif tone == "warn":
        p.drawLine(QPointF(0.5 * s, 0.14 * s), QPointF(0.5 * s, 0.6 * s))
        p.drawPoint(QPointF(0.5 * s, 0.84 * s))
    elif tone == "bad":
        p.drawLine(QPointF(0.22 * s, 0.22 * s), QPointF(0.78 * s, 0.78 * s))
        p.drawLine(QPointF(0.78 * s, 0.22 * s), QPointF(0.22 * s, 0.78 * s))
    else:
        p.drawPoint(QPointF(0.5 * s, 0.5 * s))
    p.end()
    return QIcon(pm)


def set_item_tone(item, tone: str | None, column: int | None = None, icon: bool = True) -> None:
    """Colour an item-view cell by meaning (and give it the tone's icon), remembering the tone so refill_tones()
    recolours it after a theme change. `column` for QTreeWidgetItem."""
    if column is None:
        item.setData(TONE_ROLE, tone)
        item.setData(TONE_ICON_ROLE, bool(icon and tone))
        if tone:
            item.setForeground(status_brush(tone))
            if icon:
                item.setIcon(status_icon(tone))
    else:
        item.setData(column, TONE_ROLE, tone)
        item.setData(column, TONE_ICON_ROLE, bool(icon and tone))
        if tone:
            item.setForeground(column, status_brush(tone))
            if icon:
                item.setIcon(column, status_icon(tone))


def refill_tones(view) -> None:
    """Re-read the tone colours of every toned cell of a QTableWidget, QListWidget or QTreeWidget."""
    from PySide6.QtWidgets import QListWidget, QTableWidget, QTreeWidget, QTreeWidgetItemIterator

    def one(item, col=None):
        tone = item.data(TONE_ROLE) if col is None else item.data(col, TONE_ROLE)
        if not tone:
            return
        ic = item.data(TONE_ICON_ROLE) if col is None else item.data(col, TONE_ICON_ROLE)
        set_item_tone(item, tone, col, bool(ic))

    if isinstance(view, QTableWidget):
        for r in range(view.rowCount()):
            for c in range(view.columnCount()):
                it = view.item(r, c)
                if it is not None:
                    one(it)
    elif isinstance(view, QListWidget):
        for i in range(view.count()):
            one(view.item(i))
    elif isinstance(view, QTreeWidget):
        it = QTreeWidgetItemIterator(view)
        while it.value() is not None:
            for c in range(view.columnCount()):
                one(it.value(), c)
            it += 1


def chip_style(kind: str) -> str:
    """Kept for callers outside the app style sheet (rich text); widgets use the `chip` property instead."""
    bg, fg = TONES[tone_of(kind)]
    return (f"background: {TOKENS[bg]}; color: {TOKENS[fg]}; border-radius: 9px; padding: 2px 8px; "
            f"font-weight: 500; font-size: 12px;")


def set_tone(widget, tone: str | None) -> None:
    """Colour a label by meaning ('muted', 'warn', 'bad', 'ok') through the style sheet, so it follows the theme."""
    widget.setProperty("tone", tone or "")
    st = widget.style()
    st.unpolish(widget)
    st.polish(widget)


# ----------------------------------------------------------------------------- style (indicators)
class AuroraStyle(QProxyStyle):
    """Fusion with AURORA's own check boxes, radio buttons and item-view check indicators, drawn from the tokens at
    paint time: card fill with an edge border, action fill with an on_action tick, a dash for partial, alt fill with
    a line border when disabled. Checkable list rows therefore always show a box in both schemes."""

    def __init__(self):
        super().__init__(QStyleFactory.create("Fusion"))
        self.setObjectName("aurora_style")

    def pixelMetric(self, metric, option=None, widget=None):
        if metric in (QStyle.PM_IndicatorWidth, QStyle.PM_IndicatorHeight, QStyle.PM_ExclusiveIndicatorWidth,
                      QStyle.PM_ExclusiveIndicatorHeight):
            return px(16)
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):
        if element in (QStyle.PE_IndicatorCheckBox, QStyle.PE_IndicatorItemViewItemCheck):
            self._check(element, option, painter)
            return
        if element == QStyle.PE_IndicatorRadioButton:
            self._radio(option, painter)
            return
        super().drawPrimitive(element, option, painter, widget)

    @staticmethod
    def _square(rect) -> QRectF:
        s = min(rect.width(), rect.height(), px(16))
        return QRectF(rect.x() + (rect.width() - s) / 2 + 0.5, rect.y() + (rect.height() - s) / 2 + 0.5, s - 1.0, s - 1.0)

    def _check(self, element, option, painter) -> None:
        t = TOKENS
        st = option.state
        enabled = bool(st & QStyle.State_Enabled)
        on, partial = bool(st & QStyle.State_On), bool(st & QStyle.State_NoChange)
        r = self._square(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if not enabled:
            fill, border, mark = t["alt"], t["line"], t["disabled"]
        elif on or partial:
            fill, border, mark = t["action"], t["action"], t["on_action"]
        else:
            fill, border, mark = t["card"], t["edge"], t["on_action"]
        focus = element == QStyle.PE_IndicatorCheckBox and enabled and bool(st & QStyle.State_HasFocus)
        bp = mk_pen(t["focus"] if focus else border, 2.0 if focus else 1.0)
        bp.setCosmetic(False)
        painter.setPen(bp)
        painter.setBrush(QBrush(QColor(fill)))
        rr = r.adjusted(0.5, 0.5, -0.5, -0.5) if focus else r
        painter.drawRoundedRect(rr, 3.5, 3.5)
        w = r.width()
        mp = QPen(QColor(mark))
        mp.setWidthF(max(1.6, w / 8.0))
        mp.setCapStyle(Qt.RoundCap)
        mp.setJoinStyle(Qt.RoundJoin)
        painter.setPen(mp)
        if partial:
            painter.drawLine(QPointF(r.left() + 0.27 * w, r.center().y()), QPointF(r.right() - 0.27 * w, r.center().y()))
        elif on:
            path = QPainterPath(QPointF(r.left() + 0.24 * w, r.top() + 0.52 * w))
            path.lineTo(r.left() + 0.43 * w, r.top() + 0.71 * w)
            path.lineTo(r.left() + 0.77 * w, r.top() + 0.30 * w)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        painter.restore()

    def _radio(self, option, painter) -> None:
        t = TOKENS
        st = option.state
        enabled = bool(st & QStyle.State_Enabled)
        on = bool(st & QStyle.State_On)
        r = self._square(option.rect)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        if not enabled:
            fill, border, mark = t["alt"], t["line"], t["disabled"]
        elif on:
            fill, border, mark = t["action"], t["action"], t["on_action"]
        else:
            fill, border, mark = t["card"], t["edge"], t["on_action"]
        focus = enabled and bool(st & QStyle.State_HasFocus)
        bp = mk_pen(t["focus"] if focus else border, 2.0 if focus else 1.0)
        bp.setCosmetic(False)
        painter.setPen(bp)
        painter.setBrush(QBrush(QColor(fill)))
        painter.drawEllipse(r.adjusted(0.5, 0.5, -0.5, -0.5) if focus else r)
        if on:
            d = r.width() * 0.36
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(mark)))
            painter.drawEllipse(QRectF(r.center().x() - d / 2, r.center().y() - d / 2, d, d))
        painter.restore()


# ----------------------------------------------------------------------------- palette and style sheet
def system_scheme() -> str:
    app = QGuiApplication.instance()
    try:
        if app is not None and app.styleHints().colorScheme() == Qt.ColorScheme.Dark:
            return "dark"
    except AttributeError:
        pass
    return "light"


def build_palette(t: dict[str, str]) -> QPalette:
    """The Qt palette of a token table: Active, Inactive and Disabled groups all from tokens."""
    p = QPalette()
    roles = {
        QPalette.Window: t["paper"], QPalette.WindowText: t["ink"], QPalette.Base: t["card"], QPalette.AlternateBase: t["alt"],
        QPalette.Text: t["ink"], QPalette.Button: t["card"], QPalette.ButtonText: t["ink"], QPalette.BrightText: t["bad_fg"],
        QPalette.Highlight: t["select"], QPalette.HighlightedText: t["select_fg"], QPalette.ToolTipBase: t["tooltip_bg"],
        QPalette.ToolTipText: t["tooltip_fg"], QPalette.PlaceholderText: t["placeholder"], QPalette.Link: t["link"],
        QPalette.LinkVisited: t["link"], QPalette.Light: t["card"], QPalette.Midlight: t["line"], QPalette.Mid: t["edge"],
        QPalette.Dark: t["edge"], QPalette.Shadow: t["night"], QPalette.Accent: t["action"],
    }
    disabled = dict(roles)
    disabled.update({QPalette.WindowText: t["disabled"], QPalette.Text: t["disabled"], QPalette.ButtonText: t["disabled"],
                     QPalette.Button: t["action_disabled_bg"], QPalette.Base: t["action_disabled_bg"],
                     QPalette.Highlight: t["muted_bg"], QPalette.HighlightedText: t["disabled"],
                     QPalette.PlaceholderText: t["disabled"]})
    for group, table in ((QPalette.Active, roles), (QPalette.Inactive, roles), (QPalette.Disabled, disabled)):
        for role, colour in table.items():
            p.setColor(group, role, QColor(colour))
    return p


def _ui(name: str) -> str:
    """Style-sheet url of a small indicator image (aurora/resources/ui), forward slashes on every platform."""
    from aurora import resources_dir
    return (resources_dir() / "ui" / name).as_posix()


def qss(t: dict[str, str] | None = None, size: int | None = None) -> str:
    t = t or TOKENS
    s = size or _STATE["size"]
    sch = "dark" if _lum(t["paper"]) < 0.18 else "light"
    up, down = _ui(f"up_{sch}.svg"), _ui(f"down_{sch}.svg")
    k = s / 13.0
    px_ = lambda v: f"{max(1, round(v * k))}px"     # noqa: E731  scaled pixel size
    chips = "\n".join(f'QLabel[chip="{kind}"] {{ background: {t[bg]}; color: {t[fg]}; border-radius: 9px; padding: 2px 8px; '
                      f'font-weight: 500; font-size: {px_(12)}; }}' for kind, (bg, fg) in {**STATUS_COLOURS, **TONES}.items())
    tones = "\n".join(f'QLabel[tone="{tone}"] {{ color: {t[fg]}; }}' for tone, (_bg, fg) in TONES.items())
    return f"""
QMainWindow, QDialog {{ background: {t['paper']}; }}
QWidget {{ color: {t['ink']}; font-size: {px_(13)}; }}
QWidget:disabled {{ color: {t['disabled']}; }}
QWidget#topbar {{ background: {t['night']}; }}
QWidget#topbar QLabel {{ color: {t['chrome_text']}; }}
QWidget#topbar QLabel#brand {{ font-weight: 700; letter-spacing: 2px; font-size: {px_(15)}; color: {t['chrome_text']}; }}
QWidget#topbar QLabel#crumb, QLabel#crumb {{ color: {t['crumb']}; }}
QWidget#topbar QToolButton#modebtn {{ color: {t['chrome_text']}; background: transparent; border: 1px solid {t['map_axis']};
    border-radius: 13px; padding: 3px 12px; font-size: {px_(12)}; font-weight: 400; }}
QWidget#topbar QToolButton#modebtn:hover {{ border-color: {t['aurora']}; }}
QWidget#topbar QToolButton#modebtn:checked {{ background: {t['aurora']}; color: {t['on_accent']}; border-color: {t['aurora']};
    font-weight: 600; }}
QWidget#topbar QToolButton#modebtn:focus {{ border: 2px solid {t['chrome_text']}; padding: 2px 11px; }}
QWidget#topbar QToolButton#holderbtn {{ color: {t['chrome_text']}; background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 2px 6px; font-size: {px_(13)}; font-weight: 600; }}
QWidget#topbar QToolButton#holderbtn:hover {{ border-color: {t['map_axis']}; }}
QWidget#topbar QToolButton#holderbtn:focus {{ border: 2px solid {t['chrome_text']}; padding: 1px 5px; }}
QWidget#topbar QToolButton#holderbtn::menu-indicator {{ image: none; width: 0; }}
QWidget#rail {{ background: {t['rail']}; }}
QWidget#rail QToolButton#railbtn {{ color: {t['rail_text']}; background: transparent; border: 0; border-radius: 10px;
    padding: 8px 4px; font-size: {px_(12)}; font-weight: 400; min-width: 76px; }}
QWidget#rail QToolButton#railbtn:hover {{ background: {t['rail_hover']}; }}
QWidget#rail QToolButton#railbtn:checked {{ background: {t['aurora']}; color: {t['on_accent']}; font-weight: 600; }}
QWidget#rail QToolButton#railbtn:focus {{ border: 2px solid {t['chrome_text']}; padding: 6px 2px; }}
QWidget#rail QToolButton#railbtn:checked:focus {{ border: 2px solid {t['on_accent']}; }}
QFrame#card {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 12px; }}
QLabel#cardtitle {{ font-weight: 600; }}
QToolButton#cardhelp, QToolButton#termhelp {{ color: {t['muted']}; background: transparent; border: 1px solid {t['line']}; border-radius: 10px;
    padding: 0px 6px; font-size: {px_(12)}; font-weight: 600; }}
QToolButton#cardhelp:hover, QToolButton#termhelp:hover {{ color: {t['ink']}; border-color: {t['action']}; }}
QToolButton#cardhelp:focus, QToolButton#termhelp:focus {{ border: 2px solid {t['focus']}; padding: 0px 5px; }}
QFrame#emptystate {{ background: {t['card']}; border: 1px solid {t['edge']}; border-radius: 12px; }}
QFrame#banner {{ background: {t['warn_bg']}; border-bottom: 1px solid {t['line']}; }}
QFrame#banner QLabel {{ color: {t['warn_fg']}; }}
QFrame#guidestrip {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 10px; }}
QFrame#emptystate QLabel#emptytext {{ color: {t['ink']}; font-size: {px_(14)}; }}
QFrame#taskbar QLabel {{ color: {t['muted']}; }}
QLabel#h1 {{ font-size: {px_(18)}; font-weight: 600; }}
QLabel#muted {{ color: {t['muted']}; }}
{tones}
{chips}
QPushButton, QToolButton {{ background: {t['card']}; color: {t['ink']}; border: 1px solid {t['edge']}; border-radius: 8px;
    padding: 5px 12px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {t['action']}; }}
QPushButton:pressed, QToolButton:pressed {{ background: {t['select']}; color: {t['select_fg']}; }}
QToolButton:checked {{ background: {t['select']}; color: {t['select_fg']}; border: 1px solid {t['action']}; font-weight: 600; }}
QPushButton:focus, QToolButton:focus {{ border: 2px solid {t['focus']}; padding: 4px 11px; }}
QPushButton:disabled, QToolButton:disabled {{ background: {t['action_disabled_bg']}; color: {t['disabled']};
    border: 1px solid {t['edge']}; }}
QPushButton#primary {{ background: {t['action']}; color: {t['on_action']}; border: 1px solid {t['action']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {t['action_dark']}; border-color: {t['action_dark']}; }}
QPushButton#primary:focus {{ border: 2px solid {t['ink']}; padding: 4px 11px; }}
QPushButton#primary:disabled {{ background: {t['action_disabled_bg']}; color: {t['disabled']}; border: 1px solid {t['edge']}; }}
QAbstractItemView {{ background: {t['card']}; color: {t['ink']}; border: 1px solid {t['line']}; border-radius: 8px;
    alternate-background-color: {t['alt']}; selection-background-color: {t['select']}; selection-color: {t['select_fg']}; }}
QAbstractItemView:focus {{ border: 2px solid {t['focus']}; }}
QTextBrowser, QTextEdit, QPlainTextEdit {{ background: {t['card']}; color: {t['ink']}; border: 1px solid {t['line']};
    border-radius: 8px; selection-background-color: {t['select']}; selection-color: {t['select_fg']}; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{ background: {t['select']};
    color: {t['select_fg']}; }}
QHeaderView {{ background: {t['header']}; border: 0; }}
QHeaderView::section {{ background: {t['header']}; color: {t['muted']}; border: 0; border-bottom: 1px solid {t['line']};
    border-right: 1px solid {t['line']}; padding: 4px 6px; font-weight: 500; }}
QTableCornerButton::section {{ background: {t['header']}; border: 0; border-bottom: 1px solid {t['line']}; }}
QAbstractScrollArea::corner {{ background: {t['header']}; border: 0; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {t['card']}; color: {t['ink']}; border: 1px solid {t['edge']};
    border-radius: 8px; padding: 4px 8px; min-height: {px_(22)}; selection-background-color: {t['select']};
    selection-color: {t['select_fg']}; placeholder-text-color: {t['placeholder']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t['focus']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ background: {t['action_disabled_bg']};
    color: {t['disabled']}; border-color: {t['edge']}; }}
QComboBox QAbstractItemView {{ background: {t['card']}; color: {t['ink']}; border: 1px solid {t['edge']}; border-radius: 0;
    selection-background-color: {t['select']}; selection-color: {t['select_fg']}; }}
QCheckBox, QRadioButton {{ color: {t['ink']}; spacing: 6px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t['disabled']}; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 18px;
    border-left: 1px solid {t['line']}; border-top-right-radius: 8px; background: {t['alt']}; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 18px;
    border-left: 1px solid {t['line']}; border-bottom-right-radius: 8px; background: {t['alt']}; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {t['select']}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up}"); width: 9px; height: 9px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{down}"); width: 9px; height: 9px; }}
QComboBox {{ padding-right: 22px; }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 20px; border: 0; }}
QComboBox::down-arrow {{ image: url("{down}"); width: 10px; height: 10px; }}
QHeaderView::up-arrow {{ image: url("{up}"); width: 9px; height: 9px; }}
QHeaderView::down-arrow {{ image: url("{down}"); width: 9px; height: 9px; }}
QGroupBox {{ border: 1px solid {t['line']}; border-radius: 8px; margin-top: 10px; padding-top: 6px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t['muted']}; }}
QMenuBar {{ background: {t['paper']}; color: {t['ink']}; }}
QMenuBar::item:selected {{ background: {t['select']}; color: {t['select_fg']}; }}
QMenu {{ background: {t['card']}; color: {t['ink']}; border: 1px solid {t['edge']}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t['select']}; color: {t['select_fg']}; }}
QMenu::item:disabled {{ color: {t['disabled']}; }}
QMenu::separator {{ height: 1px; background: {t['line']}; margin: 4px 8px; }}
QProgressBar {{ border: 1px solid {t['line']}; border-radius: 6px; background: {t['card']}; color: {t['ink']}; text-align: center;
    height: 14px; }}
QProgressBar::chunk {{ background: {t['aurora']}; border-radius: 6px; }}
QStatusBar {{ background: {t['paper']}; color: {t['muted']}; }}
QStatusBar QLabel {{ color: {t['muted']}; }}
QTabWidget::pane {{ background: {t['paper']}; border: 0; top: -1px; }}
QTabWidget > QWidget {{ background: {t['paper']}; }}
QTabBar::tab {{ background: {t['header']}; color: {t['muted']}; padding: 6px 14px; border: 1px solid {t['line']};
    border-bottom: 0; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }}
QTabBar::tab:hover {{ color: {t['ink']}; }}
QTabBar::tab:selected {{ background: {t['card']}; color: {t['ink']}; font-weight: 600; }}
QTabBar::tab:focus {{ border: 2px solid {t['focus']}; border-bottom: 0; padding: 5px 13px; }}
QSplitter::handle {{ background: {t['paper']}; }}
QScrollArea {{ background: transparent; border: 0; }}
QScrollArea > QWidget#qt_scrollarea_viewport {{ background: transparent; }}
QScrollArea#pageview {{ background: {t['sunken']}; border: 1px solid {t['line']}; border-radius: 8px; }}
QScrollArea#pageview > QWidget#qt_scrollarea_viewport {{ background: {t['sunken']}; }}
QScrollArea#pageview QLabel {{ background: {t['sunken']}; }}
QScrollBar:vertical {{ background: {t['paper']}; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: {t['paper']}; height: 12px; margin: 0; }}
QScrollBar::handle {{ background: {t['edge']}; border-radius: 5px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {t['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {t['tooltip_bg']}; color: {t['tooltip_fg']}; border: 0; padding: 4px 6px; }}
QSlider::groove:horizontal {{ height: 4px; background: {t['edge']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['action']}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}
QSlider::handle:horizontal:focus {{ border: 2px solid {t['ink']}; }}
"""


# ----------------------------------------------------------------------------- pyqtgraph
def _plot_items(obj) -> list:
    import pyqtgraph as pg
    if isinstance(obj, pg.PlotItem):
        return [obj]
    if isinstance(obj, pg.PlotWidget):
        return [obj.getPlotItem()]
    if isinstance(obj, pg.GraphicsLayoutWidget):
        out = []

        def walk(layout):
            for item in list(getattr(layout, "items", {}).keys()):
                if isinstance(item, pg.PlotItem):
                    out.append(item)
                elif isinstance(item, pg.GraphicsLayout):
                    walk(item)
        walk(obj.ci)
        return out
    return []


def _autohide_legend(lg) -> None:
    """A legend is hidden while it is empty (pyqtgraph draws an empty frame otherwise)."""
    if getattr(lg, "_aurora_autohide", False):
        lg.setVisible(bool(lg.items))
        return
    lg._aurora_autohide = True
    for name in ("addItem", "removeItem", "clear"):
        orig = getattr(lg, name)

        def wrapped(*a, _orig=orig, _lg=lg, **kw):
            out = _orig(*a, **kw)
            try:
                _lg.setVisible(bool(_lg.items))
            except RuntimeError:
                pass
            return out
        setattr(lg, name, wrapped)
    lg.setVisible(bool(lg.items))


def legend_brush(kind: str = "plot") -> QColor:
    """Legend background: the plot (or map) background at 90 % alpha."""
    return with_alpha(TOKENS["map_bg" if kind == "map" else "plot_bg"], 0.9)


def restyle_plot(obj, kind: str = "plot") -> None:
    """Apply the current tokens to a pyqtgraph widget or PlotItem once (style_plot also does it on every change)."""
    import pyqtgraph as pg
    t = TOKENS
    if kind == "map":
        bg, fg, axis, frame = t["map_bg"], t["map_fg"], t["map_axis"], t["map_axis"]
    else:
        bg, fg, axis, frame = t["plot_bg"], t["plot_fg"], t["plot_axis"], t["line"]
    if isinstance(obj, (pg.PlotWidget, pg.GraphicsLayoutWidget)):
        obj.setBackground(bg)
    try:
        obj.setProperty("aurora_plot_kind", kind)
    except (AttributeError, RuntimeError):
        pass
    tick_font = QFont()
    tick_font.setPixelSize(px(12))
    for pi in _plot_items(obj):
        pi.setProperty("aurora_plot_kind", kind)
        for name in ("left", "bottom", "right", "top"):
            ax = pi.getAxis(name)
            if ax is None:
                continue
            ax.setPen(pg.mkPen(axis))
            ax.setTextPen(pg.mkPen(fg))
            ax.setTickFont(tick_font)
            style = dict(ax.labelStyle or {})
            style["color"] = fg
            style["font-size"] = f"{px(13)}px"
            ax.labelStyle = style
            ax._updateLabel()
        tl = pi.titleLabel
        if tl is not None:
            tl.opts["color"] = fg
            tl.opts["size"] = f"{px(14)}px"
            tl.setText(tl.text or "", color=fg, size=f"{px(14)}px")
        lg = pi.legend
        if lg is not None:
            lg.setLabelTextColor(fg)
            lg.setLabelTextSize(f"{px(12)}px")
            lg.setBrush(pg.mkBrush(legend_brush(kind)))
            lg.setPen(pg.mkPen(frame))
            for _sample, label in getattr(lg, "items", []):
                label.setText(label.text, color=fg, size=f"{px(12)}px")
            _autohide_legend(lg)


def style_plot(obj, kind: str = "plot") -> None:
    """Theme a pyqtgraph widget now and after every theme change. kind 'plot' (spectra) or 'map' (images, navy)."""
    restyle_plot(obj, kind)
    on_theme_changed(obj, lambda _s, o=obj, k=kind: restyle_plot(o, k))


def make_plot(kind: str = "plot", title: str | None = None, x: str | None = None, y: str | None = None,
              legend: str | None = None, parent=None):
    """A themed pg.PlotWidget: background, axes, ticks, titles and legend in px; restyles live on a theme change.
    legend None / 'inside' (top left of the data) / 'outside' (a column right of the data)."""
    import pyqtgraph as pg
    w = pg.PlotWidget(parent)
    pi = w.getPlotItem()
    if title:
        w.setTitle(title)
    if x:
        w.setLabel("bottom", x)
    if y:
        w.setLabel("left", y)
    if legend == "inside":
        w.addLegend(offset=(10, 10))
    elif legend == "outside":
        lg = pg.LegendItem(offset=None)
        pi.layout.addItem(lg, 2, 3)
        pi.legend = lg
    style_plot(w, kind)
    return w


def _pg_defaults() -> None:
    import pyqtgraph as pg
    pg.setConfigOptions(imageAxisOrder="row-major", background=TOKENS["plot_bg"], foreground=TOKENS["plot_fg"], antialias=True)
    from aurora.app import pgcompat
    pgcompat.install()                  # SVG export on Qt 6.12 (closed paths)


# ----------------------------------------------------------------------------- apply
def resolve_scheme(appearance: str) -> str:
    return appearance if appearance in ("light", "dark") else system_scheme()


def apply_theme(app=None, appearance: str | None = None, text_size: str | None = None) -> str:
    """Set the scheme (light / dark / system) and the text size, then: AuroraStyle (first time), palette, font,
    style sheet, pyqtgraph defaults, and tell every registered widget. Returns the scheme applied."""
    from aurora.app.settings_dialog import get
    app = app or QGuiApplication.instance()
    appearance = appearance or get("appearance")
    size_key = text_size or get("text_size")
    _STATE["applying"] = True                   # the colour-scheme signals raised here are not OS changes
    try:
        return _apply(app, appearance, size_key)
    finally:
        _STATE["applying"] = False


def _apply(app, appearance: str, size_key: str) -> str:
    # 1. resolve the scheme ('system': hand the colour scheme back to the OS first, then read it)
    if appearance not in ("light", "dark") and app is not None:
        try:
            app.styleHints().setColorScheme(Qt.ColorScheme.Unknown)
        except AttributeError:
            pass
    sch = resolve_scheme(appearance)
    # 2. update the live tables in place
    _STATE["scheme"] = sch
    _STATE["size"] = TEXT_SIZES.get(size_key, 13)
    TOKENS.clear()
    TOKENS.update(DARK if sch == "dark" else LIGHT)
    LABELS[:] = LABELS_DARK if sch == "dark" else LABELS_LIGHT
    if app is not None:
        # 3. title bars and native dialogs follow an explicit Light or Dark ('system' keeps following the OS)
        if appearance in ("light", "dark"):
            try:
                app.styleHints().setColorScheme(Qt.ColorScheme.Dark if sch == "dark" else Qt.ColorScheme.Light)
            except AttributeError:
                pass
        # 4. the style, the first time only (the application owns it)
        if hasattr(app, "setStyle") and not app.property("aurora_style_set"):
            app.setStyle(AuroraStyle())
            app.setProperty("aurora_style_set", True)
        # 5. palette, 6. font, 7. style sheet, 8. plot defaults
        app.setPalette(build_palette(TOKENS))
        f = app.font()
        f.setPointSizeF(10.0 * _STATE["size"] / 13.0)
        app.setFont(f)
        if hasattr(app, "setStyleSheet"):
            app.setStyleSheet(qss())
        _pg_defaults()
        # 9. listeners: plots restyle, perspectives redraw, views re-fill their tones, the help window reloads
        n = notifier()
        if n is not None:
            n.changed.emit(sch)
    return sch


def follow_system(app) -> None:
    """Re-apply when the OS switches between light and dark while Appearance is 'system'."""
    from aurora.app.settings_dialog import get

    def changed(*_):
        if not _STATE["applying"] and get("appearance") == "system":
            apply_theme(app)
    try:
        hints = app.styleHints()
        hook = _Hook(app, lambda _s: changed())
        hints.colorSchemeChanged.connect(lambda *_a: hook.fire(""))
    except AttributeError:
        pass


__all__ = ["LIGHT", "DARK", "TOKENS", "CURVES", "PAL", "DASH", "DASHES", "LABELS", "MAP_OVERLAY", "FOOTPRINTS", "ROLES",
           "AuroraStyle", "apply_theme", "build_palette", "categorical", "composite", "contrast", "curve", "dash",
           "export_tokens", "label_colour", "label_for", "make_plot", "on_theme_changed", "pal", "pc", "pen", "qss",
           "readable", "refill_tones", "restyle_plot", "role_pen", "set_item_tone", "set_tone", "status_brush",
           "status_icon", "status_text", "style_plot", "swatch_icon", "tokens", "with_alpha"]
