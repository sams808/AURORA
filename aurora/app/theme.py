"""Colour tokens, light and dark schemes, the Qt palette and style sheet built from them, and the plot styler.

Readability rules (WCAG 2.1): body and muted text >= 4.5:1 on their backgrounds, input borders and plot curves
>= 3:1. `tests/test_theme.py` checks every pair below, so a token edit that breaks readability fails the suite.

API used by the perspectives:
    TOKENS / PAL / DASH         live tables for the current scheme (updated in place by apply_theme)
    pc(role)                    plot colour for a role ('ink', 'fit', 'resid', 'edge', 'i0', 'good', 'tey', ...)
    readable(colour, bg)        the colour, lightened or darkened until it reads on bg (curves, labels)
    style_plot(widget, kind)    theme a PlotWidget / GraphicsLayoutWidget / PlotItem now and on every change
    on_theme_changed(owner, fn) call fn(scheme) after every theme change while `owner` lives
    apply_theme(app)            (main) Fusion style + palette + style sheet + pyqtgraph defaults
"""
from __future__ import annotations

import colorsys

from PySide6.QtCore import QCoreApplication, QObject, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

LIGHT = {
    "night": "#0A1430", "rail": "#101E4A", "rail_text": "#C9D6F0", "rail_hover": "#1A2A5E",
    "aurora": "#38E0C2", "action": "#0E7C6B", "action_dark": "#0A5F52", "on_action": "#FFFFFF",
    "violet": "#7C5BD6", "warn": "#C2620A",
    "paper": "#F3F5F9", "card": "#FFFFFF", "ink": "#15213B", "muted": "#4E5D7A", "disabled": "#6F7B93",
    "line": "#DDE3EE", "edge": "#7F8BA3", "alt": "#F6F8FC", "header": "#EEF2F8",
    "ok_bg": "#E3F8F3", "ok_fg": "#0A5F52", "warn_bg": "#FFF1DD", "warn_fg": "#8A4B00",
    "bad_bg": "#FDE2E2", "bad_fg": "#9B1C1C", "muted_bg": "#E4E9F2",
    "select": "#CDEFE8", "select_fg": "#0B2B26", "link": "#0B6B5C",
    "tooltip_bg": "#0A1430", "tooltip_fg": "#F4FFF8",
    "plot_bg": "#FFFFFF", "plot_fg": "#2B3856", "plot_axis": "#7F8BA3", "plot_grid": "#E1E6EF",
    "map_bg": "#0B1630", "map_fg": "#C9D6F0",
}
DARK = {
    "night": "#070C18", "rail": "#0A1222", "rail_text": "#C9D6F0", "rail_hover": "#16233F",
    "aurora": "#38E0C2", "action": "#0E7C6B", "action_dark": "#0A5F52", "on_action": "#FFFFFF",
    "violet": "#B79CFF", "warn": "#F7A54A",
    "paper": "#0E1524", "card": "#172033", "ink": "#E7EDF8", "muted": "#A9B6CF", "disabled": "#8692AA",
    "line": "#2A3651", "edge": "#6D7C9A", "alt": "#1B2539", "header": "#1D2840",
    "ok_bg": "#123B33", "ok_fg": "#7EEBD0", "warn_bg": "#3E2C12", "warn_fg": "#F7C27E",
    "bad_bg": "#43191C", "bad_fg": "#FFADAD", "muted_bg": "#26314A",
    "select": "#1F5A50", "select_fg": "#F1FFFB", "link": "#5EE6CC",
    "tooltip_bg": "#E7EDF8", "tooltip_fg": "#0A1430",
    "plot_bg": "#172033", "plot_fg": "#C9D3E6", "plot_axis": "#6D7C9A", "plot_grid": "#2A3651",
    "map_bg": "#0B1630", "map_fg": "#C9D6F0",
}
# curve palettes: Okabe-Ito based, every colour >= 3:1 on its plot background (tests/test_theme.py)
PAL_LIGHT = ["#0072B2", "#D55E00", "#009E73", "#B4479A", "#6A3D9A", "#8C6D00", "#C8501E", "#1F6F8B"]
PAL_DARK = ["#56B4E9", "#FF9F4A", "#2ED3A5", "#F08CCB", "#B79CFF", "#F0E442", "#FF7F6E", "#7FD4F0"]
# semantic plot colours per scheme
ROLES = {
    "light": {"ink": "#15213B", "fit": "#D55E00", "resid": "#5B6B8C", "edge": "#6F7B93", "i0": "#C8501E", "good": "#1E7B45",
              "tey": "#3B4660", "spike": "#D11A1A", "band": "#0E7C6B", "roi": "#0E7C6B", "pre": "#B26B00", "post": "#0072B2",
              "e0": "#7C5BD6", "diode": "#6A3D9A", "ratio": "#1E7B45"},
    "dark": {"ink": "#E7EDF8", "fit": "#FF9F4A", "resid": "#A9B6CF", "edge": "#8692AA", "i0": "#FF8A5B", "good": "#5BE38F",
             "tey": "#C9D3E6", "spike": "#FF6B6B", "band": "#38E0C2", "roi": "#38E0C2", "pre": "#F0B94A", "post": "#56B4E9",
             "e0": "#B79CFF", "diode": "#B79CFF", "ratio": "#5BE38F"},
}
DASH = [Qt.SolidLine, Qt.DashLine, Qt.DotLine, Qt.DashDotLine]

TOKENS: dict[str, str] = dict(LIGHT)          # live: the current scheme's tokens
PAL: list[str] = list(PAL_LIGHT)              # live: the current scheme's curve palette
TEXT_SIZES = {"normal": 13, "large": 15, "larger": 17}
_STATE = {"scheme": "light", "size": 13}

STATUS_COLOURS = {
    "complete": ("ok_bg", "ok_fg"),
    "survey": ("ok_bg", "ok_fg"),
    "in_progress": ("warn_bg", "warn_fg"),
    "test": ("muted_bg", "muted"),
    "aborted": ("bad_bg", "bad_fg"),
    "empty": ("bad_bg", "bad_fg"),
    "unreadable": ("bad_bg", "bad_fg"),
    "other": ("muted_bg", "muted"),
}

UI_FONT = "Inter, 'Segoe UI', 'SF Pro Text', 'Helvetica Neue', Arial, sans-serif"
MONO_FONT = "'JetBrains Mono', Consolas, 'SF Mono', Menlo, monospace"


# ----------------------------------------------------------------------------- colour maths
def _lum(colour: str) -> float:
    c = QColor(colour)
    f = (lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
    return 0.2126 * f(c.redF()) + 0.7152 * f(c.greenF()) + 0.0722 * f(c.blueF())


def contrast(a: str, b: str) -> float:
    """WCAG 2.1 contrast ratio of two colours (1 .. 21)."""
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


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


def scheme() -> str:
    return _STATE["scheme"]


def is_dark() -> bool:
    return _STATE["scheme"] == "dark"


def pc(role: str) -> str:
    """Semantic plot colour for the current scheme."""
    return ROLES[_STATE["scheme"]].get(role, TOKENS["plot_fg"])


def pal(i: int) -> str:
    return PAL[i % len(PAL)]


def text_px() -> int:
    return _STATE["size"]


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
    p = QPalette()
    roles = {
        QPalette.Window: t["paper"], QPalette.WindowText: t["ink"], QPalette.Base: t["card"], QPalette.AlternateBase: t["alt"],
        QPalette.Text: t["ink"], QPalette.Button: t["card"], QPalette.ButtonText: t["ink"], QPalette.BrightText: t["bad_fg"],
        QPalette.Highlight: t["select"], QPalette.HighlightedText: t["select_fg"], QPalette.ToolTipBase: t["tooltip_bg"],
        QPalette.ToolTipText: t["tooltip_fg"], QPalette.PlaceholderText: t["muted"], QPalette.Link: t["link"],
        QPalette.LinkVisited: t["link"], QPalette.Light: t["card"], QPalette.Midlight: t["line"], QPalette.Mid: t["edge"],
        QPalette.Dark: t["edge"], QPalette.Shadow: "#000000",
    }
    for role, colour in roles.items():
        p.setColor(QPalette.All, role, QColor(colour))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(t["disabled"]))
    return p


def _ui(name: str) -> str:
    """Style-sheet url of a small indicator image (aurora/resources/ui), forward slashes on every platform."""
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "resources" / "ui" / name).as_posix()


def qss(t: dict[str, str] | None = None, size: int | None = None) -> str:
    t = t or TOKENS
    s = size or _STATE["size"]
    sch = "dark" if _lum(t["paper"]) < 0.18 else "light"
    up, down = _ui(f"up_{sch}.svg"), _ui(f"down_{sch}.svg")
    check, partial, dot = _ui("check.svg"), _ui("partial.svg"), _ui("dot.svg")
    k = s / 13.0
    px = lambda v: f"{max(1, round(v * k))}px"     # noqa: E731  scaled pixel size
    chips = "\n".join(f'QLabel[chip="{kind}"] {{ background: {t[bg]}; color: {t[fg]}; border-radius: 9px; padding: 2px 8px; '
                      f'font-weight: 500; font-size: {px(12)}; }}' for kind, (bg, fg) in STATUS_COLOURS.items())
    return f"""
QMainWindow, QDialog {{ background: {t['paper']}; }}
QWidget {{ color: {t['ink']}; font-size: {px(13)}; }}
QWidget:disabled {{ color: {t['disabled']}; }}
QWidget#topbar {{ background: {t['night']}; }}
QWidget#topbar QLabel {{ color: #F4FFF8; }}
QLabel#brand {{ font-weight: 700; letter-spacing: 2px; font-size: {px(15)}; color: #F4FFF8; }}
QLabel#crumb {{ color: #B9C8E4; }}
QToolButton#modebtn {{ color: #F4FFF8; background: transparent; border: 1px solid #5F75A8; border-radius: 13px;
    padding: 3px 12px; font-size: {px(12)}; }}
QToolButton#modebtn:hover {{ border-color: {t['aurora']}; }}
QToolButton#modebtn:checked {{ background: {t['aurora']}; color: #0A1430; border-color: {t['aurora']}; font-weight: 600; }}
QWidget#rail {{ background: {t['rail']}; }}
QToolButton#railbtn {{ color: {t['rail_text']}; background: transparent; border: 0; border-radius: 10px;
    padding: 8px 4px; font-size: {px(12)}; min-width: 76px; }}
QToolButton#railbtn:hover {{ background: {t['rail_hover']}; }}
QToolButton#railbtn:checked {{ background: {t['aurora']}; color: #0A1430; font-weight: 600; }}
QFrame#card {{ background: {t['card']}; border: 1px solid {t['line']}; border-radius: 12px; }}
QLabel#cardtitle {{ font-weight: 600; }}
QLabel#h1 {{ font-size: {px(18)}; font-weight: 600; }}
QLabel#muted {{ color: {t['muted']}; }}
QLabel[tone="muted"] {{ color: {t['muted']}; }}
QLabel[tone="warn"] {{ color: {t['warn_fg']}; }}
QLabel[tone="bad"] {{ color: {t['bad_fg']}; }}
QLabel[tone="ok"] {{ color: {t['ok_fg']}; }}
{chips}
QPushButton, QToolButton {{ background: {t['card']}; color: {t['ink']}; border: 1px solid {t['edge']}; border-radius: 8px;
    padding: 5px 12px; }}
QPushButton:hover, QToolButton:hover {{ border-color: {t['action']}; }}
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {{ background: {t['select']}; color: {t['select_fg']}; }}
QPushButton:disabled, QToolButton:disabled {{ color: {t['disabled']}; border-color: {t['line']}; }}
QPushButton#primary {{ background: {t['action']}; color: {t['on_action']}; border: 0; font-weight: 600; }}
QPushButton#primary:hover {{ background: {t['action_dark']}; }}
QPushButton#primary:disabled {{ background: {t['muted_bg']}; color: {t['disabled']}; }}
QTreeView, QTableView, QTextBrowser, QListView, QTextEdit, QPlainTextEdit {{ background: {t['card']}; color: {t['ink']};
    border: 1px solid {t['line']}; border-radius: 8px; alternate-background-color: {t['alt']};
    selection-background-color: {t['select']}; selection-color: {t['select_fg']}; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; }}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {{ background: {t['select']}; color: {t['select_fg']}; }}
QHeaderView::section {{ background: {t['header']}; color: {t['muted']}; border: 0; border-bottom: 1px solid {t['line']};
    padding: 4px 6px; font-weight: 500; }}
QTableCornerButton::section {{ background: {t['header']}; border: 0; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ background: {t['card']}; color: {t['ink']}; border: 1px solid {t['edge']};
    border-radius: 8px; padding: 4px 8px; min-height: {px(22)}; selection-background-color: {t['select']};
    selection-color: {t['select_fg']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {t['action']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ color: {t['disabled']}; border-color: {t['line']}; }}
QComboBox QAbstractItemView {{ background: {t['card']}; color: {t['ink']}; border: 1px solid {t['edge']};
    selection-background-color: {t['select']}; selection-color: {t['select_fg']}; }}
QCheckBox, QRadioButton {{ color: {t['ink']}; spacing: 6px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t['disabled']}; }}
QCheckBox::indicator, QGroupBox::indicator {{ width: 14px; height: 14px; border: 1px solid {t['edge']}; border-radius: 4px;
    background: {t['card']}; }}
QCheckBox::indicator:hover {{ border-color: {t['action']}; }}
QCheckBox::indicator:checked, QGroupBox::indicator:checked {{ background: {t['action']}; border-color: {t['action']};
    image: url("{check}"); }}
QCheckBox::indicator:indeterminate {{ background: {t['action']}; border-color: {t['action']}; image: url("{partial}"); }}
QCheckBox::indicator:disabled {{ background: {t['muted_bg']}; border-color: {t['line']}; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {t['edge']}; border-radius: 8px; background: {t['card']}; }}
QRadioButton::indicator:checked {{ background: {t['action']}; border-color: {t['action']}; image: url("{dot}"); }}
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
QTabBar::tab {{ background: {t['muted_bg']}; color: {t['muted']}; padding: 6px 14px; border-top-left-radius: 8px;
    border-top-right-radius: 8px; margin-right: 2px; }}
QTabBar::tab:selected {{ background: {t['card']}; color: {t['ink']}; font-weight: 600; }}
QListWidget::indicator, QTableWidget::indicator, QTreeWidget::indicator, QTableView::indicator, QListView::indicator,
QTreeView::indicator {{ width: 14px; height: 14px; border: 1px solid {t['edge']}; border-radius: 4px; background: {t['card']}; }}
QListWidget::indicator:checked, QTableWidget::indicator:checked, QTreeWidget::indicator:checked, QTableView::indicator:checked,
QListView::indicator:checked, QTreeView::indicator:checked {{ background: {t['action']}; border-color: {t['action']};
    image: url("{check}"); }}
QSplitter::handle {{ background: {t['paper']}; }}
QScrollBar:vertical {{ background: {t['paper']}; width: 12px; margin: 0; }}
QScrollBar:horizontal {{ background: {t['paper']}; height: 12px; margin: 0; }}
QScrollBar::handle {{ background: {t['edge']}; border-radius: 5px; min-height: 24px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QToolTip {{ background: {t['tooltip_bg']}; color: {t['tooltip_fg']}; border: 0; padding: 4px 6px; }}
QSlider::groove:horizontal {{ height: 4px; background: {t['line']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {t['action']}; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}
"""


def chip_style(kind: str) -> str:
    """Kept for callers outside the app style sheet (rich text); widgets use the `chip` property instead."""
    bg, fg = STATUS_COLOURS.get(kind, ("muted_bg", "muted"))
    return (f"background: {TOKENS[bg]}; color: {TOKENS[fg]}; border-radius: 9px; padding: 2px 8px; "
            f"font-weight: 500; font-size: 12px;")


def set_tone(widget, tone: str | None) -> None:
    """Colour a label by meaning ('muted', 'warn', 'bad', 'ok') through the style sheet, so it follows the theme."""
    widget.setProperty("tone", tone or "")
    st = widget.style()
    st.unpolish(widget)
    st.polish(widget)


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


def _restyle(obj, kind: str) -> None:
    import pyqtgraph as pg
    t = TOKENS
    bg, fg, axis = (t["map_bg"], t["map_fg"], t["map_fg"]) if kind == "map" else (t["plot_bg"], t["plot_fg"], t["plot_axis"])
    if isinstance(obj, (pg.PlotWidget, pg.GraphicsLayoutWidget)):
        obj.setBackground(bg)
    tick_font = QFont()
    tick_font.setPointSizeF(max(8.5, 9.0 * text_px() / 13.0))
    for pi in _plot_items(obj):
        for name in ("left", "bottom", "right", "top"):
            ax = pi.getAxis(name)
            if ax is None:
                continue
            ax.setPen(pg.mkPen(axis))
            ax.setTextPen(pg.mkPen(fg))
            ax.setTickFont(tick_font)
            style = dict(ax.labelStyle or {})
            style["color"] = fg
            style["font-size"] = f"{max(11, round(12 * text_px() / 13))}px"
            ax.labelStyle = style
            ax._updateLabel()
        tl = pi.titleLabel
        if tl is not None:
            tl.opts["color"] = fg
            tl.setText(tl.text or "", color=fg)
        if pi.legend is not None:
            pi.legend.setLabelTextColor(fg)
            pi.legend.setBrush(pg.mkBrush(QColor(bg).red(), QColor(bg).green(), QColor(bg).blue(), 200))
            pi.legend.setPen(pg.mkPen(t["line"] if kind != "map" else axis))
            for _sample, label in getattr(pi.legend, "items", []):
                label.setText(label.text, color=fg)


def style_plot(obj, kind: str = "plot") -> None:
    """Theme a pyqtgraph widget now and after every theme change. kind 'plot' (spectra) or 'map' (images, navy)."""
    _restyle(obj, kind)
    on_theme_changed(obj, lambda _s, o=obj, k=kind: _restyle(o, k))


def _pg_defaults() -> None:
    import pyqtgraph as pg
    pg.setConfigOptions(imageAxisOrder="row-major", background=TOKENS["plot_bg"], foreground=TOKENS["plot_fg"], antialias=True)


# ----------------------------------------------------------------------------- apply
def resolve_scheme(appearance: str) -> str:
    return appearance if appearance in ("light", "dark") else system_scheme()


def apply_theme(app=None, appearance: str | None = None, text_size: str | None = None) -> str:
    """Set the scheme (light / dark / system), the text size, the Fusion style, palette, style sheet and plot defaults,
    then tell every registered widget. Returns the scheme applied."""
    from aurora.app.settings_dialog import get
    app = app or QGuiApplication.instance()
    appearance = appearance or get("appearance")
    size_key = text_size or get("text_size")
    sch = resolve_scheme(appearance)
    _STATE["scheme"] = sch
    _STATE["size"] = TEXT_SIZES.get(size_key, 13)
    TOKENS.clear()
    TOKENS.update(DARK if sch == "dark" else LIGHT)
    PAL[:] = PAL_DARK if sch == "dark" else PAL_LIGHT
    if app is not None:
        try:
            app.setStyle("Fusion")
        except AttributeError:
            pass
        try:
            app.styleHints().setColorScheme(Qt.ColorScheme.Dark if sch == "dark" else Qt.ColorScheme.Light)
        except AttributeError:
            pass
        app.setPalette(build_palette(TOKENS))
        f = app.font()
        f.setPointSizeF(10.0 * _STATE["size"] / 13.0)
        app.setFont(f)
        app.setStyleSheet(qss())
        _pg_defaults()
        n = notifier()
        if n is not None:
            n.changed.emit(sch)
    return sch


def follow_system(app) -> None:
    """Re-apply when the OS switches between light and dark while Appearance is 'system'."""
    from aurora.app.settings_dialog import get

    def changed(*_):
        if get("appearance") == "system":
            apply_theme(app)
    try:
        hints = app.styleHints()
        hook = _Hook(app, lambda _s: changed())
        hints.colorSchemeChanged.connect(lambda *_a: hook.fire(""))
    except AttributeError:
        pass
