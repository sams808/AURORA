"""Theme readability (extends test_foundation): the token contrast tables of both schemes, the one series palette,
the style sheet, the indicators, and a demo-loaded window in light, then dark, then light again: every plot axis,
background, legend, text item and toned table cell reads, and the round trip restores the same colours. Plus the grep
test that keeps colour literals out of aurora/app."""
import io
import re
import tokenize
from pathlib import Path

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402

from aurora.app import theme  # noqa: E402

# text pairs: >= 4.5:1 (WCAG 2.1 AA body text)
TEXT_PAIRS = ([("ink", b) for b in ("paper", "card", "alt", "header", "sunken")]
              + [("muted", b) for b in ("paper", "card", "alt", "header", "muted_bg", "select")]
              + [("disabled", b) for b in ("card", "paper", "alt", "header", "action_disabled_bg")]
              + [("placeholder", "card"), ("link", "paper"), ("link", "card"), ("select_fg", "select"),
                 ("on_action", "action"), ("on_action", "action_dark"), ("tooltip_fg", "tooltip_bg"),
                 ("chrome_text", "night"), ("crumb", "night"), ("rail_text", "rail"), ("rail_text", "rail_hover"),
                 ("on_accent", "aurora"), ("plot_fg", "plot_bg"), ("map_fg", "map_bg")]
              + [(f"{k}_fg", b) for k in ("ok", "warn", "bad") for b in (f"{k}_bg", "card", "alt")])
# UI parts and lines: >= 3:1
UI_PAIRS = [("edge", "card"), ("edge", "paper"), ("edge", "alt"), ("edge", "header"), ("focus", "card"), ("focus", "paper"),
            ("plot_axis", "plot_bg"), ("map_axis", "map_bg"), ("plot_ink", "plot_bg"), ("plot_bad", "plot_bg"),
            ("action", "card")]
NEW_KEYS = ("sunken", "placeholder", "focus", "crumb", "chrome_text", "on_accent", "action_disabled_bg", "plot_ink",
            "plot_bad", "map_axis")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.mark.parametrize("name", ["LIGHT", "DARK"])
def test_text_pairs_read(name):
    t = getattr(theme, name)
    low = [(fg, bg, round(theme.contrast(t[fg], t[bg]), 2)) for fg, bg in TEXT_PAIRS if theme.contrast(t[fg], t[bg]) < 4.5]
    assert not low, f"{name}: text below 4.5:1 {low}"


@pytest.mark.parametrize("name", ["LIGHT", "DARK"])
def test_ui_pairs_read(name):
    t = getattr(theme, name)
    low = [(fg, bg, round(theme.contrast(t[fg], t[bg]), 2)) for fg, bg in UI_PAIRS if theme.contrast(t[fg], t[bg]) < 3.0]
    assert not low, f"{name}: UI parts below 3:1 {low}"


def test_final_tokens():
    assert set(theme.LIGHT) == set(theme.DARK)
    for k in NEW_KEYS:
        assert k in theme.LIGHT
    assert "violet" not in theme.LIGHT and "warn" not in theme.LIGHT
    assert theme.LIGHT["disabled"] == "#5F6B84" and theme.LIGHT["edge"] == "#7D89A1"
    assert theme.DARK["action"] == "#2EC4A6" and theme.contrast(theme.DARK["on_action"], theme.DARK["action"]) >= 4.5
    # the map area, the rail and the top bar stay navy in both schemes
    assert theme.LIGHT["map_bg"] == theme.DARK["map_bg"] and theme.LIGHT["map_fg"] == theme.DARK["map_fg"]


def test_one_series_palette():
    assert theme.PAL is theme.CURVES and len(theme.CURVES) == 6
    assert [theme.pal(i) for i in range(8)] == [theme.curve(i) for i in range(8)]
    for c in theme.CURVES:
        for bg in (theme.LIGHT["plot_bg"], theme.DARK["plot_bg"], theme.LIGHT["map_bg"]):
            assert theme.contrast(c, bg) >= 3.0, (c, bg, round(theme.contrast(c, bg), 2))
    for labels, bg in ((theme.LABELS_LIGHT, theme.LIGHT["plot_bg"]), (theme.LABELS_DARK, theme.DARK["plot_bg"])):
        assert len(labels) == 6
        assert all(theme.contrast(c, bg) >= 4.5 for c in labels), [(c, round(theme.contrast(c, bg), 2)) for c in labels]
    series = {(theme.curve(i), theme.dash(i)) for i in range(24)}
    assert len(series) == 24                                  # 24 distinct colour + dash pairs
    assert theme.dash(6) != theme.dash(0)                     # the 7th series is not a copy of the 1st


def test_map_overlays_read_on_navy():
    navy = theme.LIGHT["map_bg"]
    for status, (colour, _style) in theme.FOOTPRINTS.items():
        assert theme.contrast(colour, navy) >= 5.7, (status, round(theme.contrast(colour, navy), 2))
    assert len({style for _c, style in theme.FOOTPRINTS.values()}) == len(theme.FOOTPRINTS)    # own dash each
    for name, colour in theme.MAP_OVERLAY.items():
        assert theme.contrast(colour, navy) >= 4.5, (name, round(theme.contrast(colour, navy), 2))


def _map_colours(name: str) -> list[str]:
    import pyqtgraph as pg
    lut = pg.colormap.get(name).getLookupTable(nPts=256)
    return [QColor(int(r), int(g), int(b)).name() for r, g, b in lut[:, :3]]


def _outline_contrast(colour: str, m: str) -> float:
    """How well an outline in `colour` over its casing (theme.map_casing) reads on the map colour `m`: each stroke shows a
    fringe; one of them must reach the ratio against the map, and each must stand out from the one it sits on."""
    parts = [c for c, _px in theme.map_casing(colour)] + [colour]                       # outermost first
    seen = max(theme.contrast(p, m) for p in parts)
    apart = min(theme.contrast(a, b) for a, b in zip(parts, parts[1:]))
    return min(seen, apart)


def test_map_outlines_read_on_every_colour_of_every_colour_map():
    """Footprints, zones (the series colours) and the overlay marks drawn over a colour map: 3:1 on every one of its
    256 colours, for every map colour map of the Settings (without a casing they fall to 1.0:1 on the bright end)."""
    from aurora.app.settings_dialog import COLORMAPS
    overlays = [c for c, _s in theme.FOOTPRINTS.values()] + list(theme.MAP_OVERLAY.values()) + list(theme.CURVES)
    worst = min((_outline_contrast(c, m), name, c, m) for name in COLORMAPS for m in _map_colours(name) for c in overlays)
    assert worst[0] >= 3.0, worst


@pytest.mark.parametrize("level, colour", [(255, "#F2A65A"), (255, "#2A6CCA"), (64, "#2A6CCA"), (128, "#0FA175")])
def test_a_drawn_outline_stands_out_from_a_flat_map(qapp, level, colour):
    """Rendered: a footprint rectangle and a painted zone on a map of one viridis colour (bright yellow, mid teal, dark
    blue): some pixel across each edge reaches 3:1 against the map (the reviewers' 1.0-1.9:1 cases)."""
    from PySide6.QtCore import QPointF
    import numpy as np
    from aurora.app.mapview import MapView
    from aurora.core.grid import to_grid
    from aurora.core.io.config import Zone
    from aurora.core.testdata import snake_grid
    bg = _map_colours("viridis")[level]
    mv = MapView()
    mv.resize(640, 480)
    mv.show()
    mv.set_lookup(np.array([QColor(bg).getRgb()[:3]] * 256, dtype=np.ubyte))
    x, y = snake_grid(nx=8, ny=6, pitch=0.25)
    mv.set_grid(to_grid(x, y, np.zeros(x.size)))
    mv.autorange()
    mv.add_rect(-0.2, 0.4, 5.2, 5.9, colour, 1.2)
    z = Zone(id=1, label="z", paint=[[1.0, 5.5, 0.25, 0.25], [1.25, 5.5, 0.25, 0.25]])
    mv.draw_zone(z, colour, label=False)
    for _ in range(3):
        qapp.processEvents()
    img = mv.grab().toImage()
    for ex, ey in ((-0.2, 5.55), (0.875, 5.5)):                     # the rectangle's left edge, the zone's left edge
        sp = mv.mapFromScene(mv.vb.mapViewToScene(QPointF(ex, ey)))
        row = [img.pixelColor(sp.x() + dx, sp.y()).name() for dx in range(-8, 9)]
        assert theme.contrast(row[0], bg) < 1.05                        # the map itself, away from the edge
        best = max(theme.contrast(p, bg) for p in row)
        assert best >= 3.0, (ex, colour, bg, round(best, 2), row)
    mv.close()


def test_categorical_colours():
    c20 = theme.categorical(20)
    assert len(c20) == 20 and len(set(c20)) == 20 and c20[:6] == theme.CURVES
    assert theme.categorical(3) == theme.CURVES[:3]
    assert all(QColor(c).isValid() for c in c20)
    assert theme.categorical(25)[20] == c20[0]


def test_export_tokens_are_light_on_white(qapp):
    theme.apply_theme(qapp, appearance="dark")
    try:
        ex = theme.export_tokens()
        assert ex["plot_bg"] == "#FFFFFF" and ex["ink"] == theme.LIGHT["ink"] and ex["curves"] == theme.CURVES
        assert ex["labels"] == theme.LABELS_LIGHT
        assert theme.tokens()["plot_bg"] == theme.DARK["plot_bg"]
    finally:
        theme.apply_theme(qapp, appearance="light")


def test_system_appearance_follows_the_os(qapp, monkeypatch):
    calls = []
    hints = qapp.styleHints()
    real = hints.setColorScheme
    monkeypatch.setattr(type(hints), "setColorScheme", lambda self, s: (calls.append(s), real(s)))
    theme.apply_theme(qapp, appearance="dark")
    assert calls[-1] == Qt.ColorScheme.Dark                   # title bars and native dialogs follow an explicit choice
    assert theme.apply_theme(qapp, appearance="system") == theme.system_scheme()
    assert calls[-1] == Qt.ColorScheme.Unknown                # 'system' hands the scheme back to the OS
    theme.apply_theme(qapp, appearance="light")
    assert calls[-1] == Qt.ColorScheme.Light
    assert theme.scheme() == "light" and theme.TOKENS["paper"] == theme.LIGHT["paper"]


@pytest.mark.parametrize("name", ["LIGHT", "DARK"])
def test_qss_formats(name):
    t = getattr(theme, name)
    css = theme.qss(t, 13)
    assert css.count("{") == css.count("}") and "{t[" not in css
    for key in ("paper", "card", "ink", "focus", "placeholder", "action_disabled_bg", "sunken", "crumb", "header"):
        assert t[key] in css, key
    assert "QToolButton:checked" in css and "QPushButton#primary:disabled" in css and "QAbstractScrollArea::corner" in css
    assert "QScrollArea#pageview" in css and "placeholder-text-color" in css
    big = theme.qss(t, 17)
    assert "font-size: 17px" in big                          # every px value scales with the text size


def test_status_and_swatch_helpers(qapp):
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem
    theme.apply_theme(qapp, appearance="light")
    assert theme.status_text("ok") == ("✓", "ok") and theme.status_text("bad")[0] == "✗" and theme.status_text("warn")[0] == "!"
    assert theme.status_text("aborted") == theme.status_text("bad") and theme.tone_of("complete") == "ok"
    assert theme.status_brush("bad").color().name().upper() == theme.LIGHT["bad_fg"]
    assert not theme.swatch_icon(theme.curve(1), theme.dash(1)).isNull() and not theme.status_icon("warn").isNull()
    tw = QTableWidget(1, 1)
    it = QTableWidgetItem("invalid")
    theme.set_item_tone(it, "bad")
    tw.setItem(0, 0, it)
    theme.apply_theme(qapp, appearance="dark")
    theme.refill_tones(tw)
    assert tw.item(0, 0).foreground().color().name().upper() == theme.DARK["bad_fg"]
    theme.apply_theme(qapp, appearance="light")
    theme.refill_tones(tw)
    assert tw.item(0, 0).foreground().color().name().upper() == theme.LIGHT["bad_fg"]
    tw.deleteLater()


def _colours(img) -> set[str]:
    return {img.pixelColor(x, y).name().upper() for x in range(img.width()) for y in range(img.height())}


@pytest.mark.parametrize("sch", ["light", "dark"])
def test_indicators_always_show_a_box(qapp, sch):
    """AuroraStyle draws check boxes and item-view check indicators from the tokens in both schemes."""
    from PySide6.QtWidgets import QCheckBox, QListWidget, QListWidgetItem, QVBoxLayout, QWidget
    theme.apply_theme(qapp, appearance=sch)
    t = theme.TOKENS
    w = QWidget(); lay = QVBoxLayout(w)
    on = QCheckBox("on"); on.setChecked(True); off = QCheckBox("off"); on.setFocusPolicy(Qt.NoFocus); off.setFocusPolicy(Qt.NoFocus)
    lw = QListWidget(); lw.setFocusPolicy(Qt.NoFocus)
    for state in (Qt.Checked, Qt.Unchecked):
        it = QListWidgetItem("row"); it.setFlags(it.flags() | Qt.ItemIsUserCheckable); it.setCheckState(state); lw.addItem(it)
    for x in (on, off, lw):
        lay.addWidget(x)
    w.resize(260, 220); w.show(); qapp.processEvents()
    assert qapp.property("aurora_style_set")
    assert t["action"] in _colours(on.grab().toImage())
    assert t["edge"] in _colours(off.grab().toImage())
    view = _colours(lw.viewport().grab().toImage())
    assert t["action"] in view and t["edge"] in view           # checked and unchecked rows both show a box
    w.close(); w.deleteLater()
    theme.apply_theme(qapp, appearance="light")


def test_make_plot_and_legend(qapp, monkeypatch):
    import pyqtgraph as pg
    theme.apply_theme(qapp, appearance="light")
    w = theme.make_plot(title="a title", x="energy (eV)", y="counts", legend="inside")
    pi = w.getPlotItem()
    assert w.property("aurora_plot_kind") == "plot" and w.backgroundBrush().color().name().upper() == theme.LIGHT["plot_bg"]
    ax = pi.getAxis("bottom")
    assert ax.style["tickFont"].pixelSize() == 12 and ax.labelStyle["font-size"] == "13px"
    assert pi.titleLabel.opts["size"] == "14px"
    assert not pi.legend.isVisible()                          # hidden while empty
    pi.plot([0, 1], [0, 1], pen=theme.pen(0), name="one")
    assert pi.legend.isVisible() and pi.legend.items[0][1].opts["color"] in (theme.LIGHT["plot_fg"], QColor(theme.LIGHT["plot_fg"]))
    pi.legend.clear()
    assert not pi.legend.isVisible()
    out = theme.make_plot(legend="outside")
    assert isinstance(out.getPlotItem().legend, pg.LegendItem)
    theme.apply_theme(qapp, appearance="light", text_size="larger")
    assert ax.style["tickFont"].pixelSize() == round(12 * 17 / 13)
    theme.apply_theme(qapp, appearance="light", text_size="normal")
    w.deleteLater(); out.deleteLater()


# ----------------------------------------------------------------------------- the demo window
def _foregrounds(view):
    """(row, column, colour) of every item foreground the app set in a table, list or tree."""
    from PySide6.QtWidgets import QListWidget, QTableWidget, QTreeWidget, QTreeWidgetItemIterator
    out = []

    def add(r, c, fg):
        if fg is not None:
            col = fg.color() if hasattr(fg, "color") else QColor(fg)
            out.append((r, c, col.name()))
    if isinstance(view, QTableWidget):
        for r in range(view.rowCount()):
            for c in range(view.columnCount()):
                it = view.item(r, c)
                if it is not None:
                    add(r, c, it.data(Qt.ForegroundRole))
    elif isinstance(view, QListWidget):
        for r in range(view.count()):
            add(r, 0, view.item(r).data(Qt.ForegroundRole))
    elif isinstance(view, QTreeWidget):
        it = QTreeWidgetItemIterator(view)
        n = 0
        while it.value() is not None:
            for c in range(view.columnCount()):
                add(n, c, it.value().data(c, Qt.ForegroundRole))
            n += 1
            it += 1
    return out


def _check_window(win, sch: str) -> dict:
    import pyqtgraph as pg
    from PySide6.QtWidgets import QAbstractItemView
    t = theme.LIGHT if sch == "light" else theme.DARK
    seen = {}
    views = win.findChildren(pg.GraphicsView)
    assert len(views) >= 15, len(views)
    for gv in views:
        kind = gv.property("aurora_plot_kind")
        assert kind in ("plot", "map"), f"pyqtgraph view not themed: {gv.parent()}"
        bg, fg = (t["map_bg"], t["map_fg"]) if kind == "map" else (t["plot_bg"], t["plot_fg"])
        assert gv.backgroundBrush().color().name().upper() == bg.upper(), (sch, kind)
        for item in gv.scene().items():
            if isinstance(item, pg.PlotItem):
                for name in ("left", "bottom"):
                    pen = item.getAxis(name).textPen().color().name().upper()
                    assert pen == fg.upper(), (sch, kind, name, pen)
                    seen[(id(item), name)] = (pen, item.getAxis(name).pen().color().name().upper())
                lg = item.legend
                if lg is not None and lg.items:
                    back = theme.composite(lg.opts["brush"].color(), bg)
                    for _s, label in lg.items:
                        col = pg.mkColor(label.opts["color"]).name()
                        assert theme.contrast(col, back) >= 4.5, (sch, label.text, col, back)
            elif isinstance(item, pg.TextItem):
                back = bg
                if item.fill.style() != Qt.NoBrush:
                    back = theme.composite(item.fill.color(), bg)
                col = item.color.name()
                assert theme.contrast(col, back) >= 4.5, (sch, item.textItem.toPlainText(), col, back)
    for view in win.findChildren(QAbstractItemView):
        for r, c, col in _foregrounds(view):
            for base in (t["card"], t["alt"]):
                assert theme.contrast(col, base) >= 4.5, (sch, type(view).__name__, r, c, col)
    return seen


def test_demo_window_light_dark_light(qapp, tmp_path):
    from demo_window import build_demo_window, pump
    from aurora.app.main import PERSPECTIVES
    from aurora.app import mode
    mode.set_advanced(True)                                   # every plot and table on screen
    theme.apply_theme(qapp, appearance="light")
    win = build_demo_window(qapp, tmp_path)
    for key, _t in PERSPECTIVES:
        win.show_perspective(key)
        pump(qapp, 2)
    snaps = []
    for sch in ("light", "dark", "light"):
        theme.apply_theme(qapp, appearance=sch)
        pump(qapp, 3)
        snaps.append(_check_window(win, sch))
        # the perspectives redrew their curves with the new colours (the LCF target is drawn in plot_ink)
        target = win.fit.lcf_plot.plotItem.listDataItems()[0]
        ink = (theme.LIGHT if sch == "light" else theme.DARK)["plot_ink"]
        assert target.opts["pen"].color().name().upper() == ink
    assert snaps[0] == snaps[2] and snaps[0] != snaps[1]
    # compare: the Label column is ink text plus a swatch, never a coloured text
    lab = win.compare.table.item(0, win.compare.col("Label"))          # the column order of WP5 I29
    assert lab.data(Qt.ForegroundRole) is None and not lab.icon().isNull()
    # every MapView follows Settings > map colour map (the categorical cluster map keeps its own colours)
    from aurora.app.mapview import MapView
    from aurora.app.settings_dialog import SettingsDialog
    d = SettingsDialog(win)
    d.cmap.setCurrentText("magma")
    d.accept()
    for mv in win.findChildren(MapView):
        if not mv._fixed_lut:
            assert mv.colormap_name() == "magma"
    assert win.maps.cluster_map._fixed_lut and win.maps.rgb_view._fixed_lut
    assert MapView().colormap_name() == "magma"                # new views read the setting at creation
    win.close()


# ----------------------------------------------------------------------------- no colour literals in aurora/app
FORBIDDEN = ("Qt.red", "Qt.darkYellow", "Qt.darkGreen", 'background="w"', "background='w'")
HEX = re.compile(r"#(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{3})\b")


def test_no_colour_literals_outside_theme():
    app_dir = Path(theme.__file__).resolve().parent
    string_types = {tokenize.STRING} | ({tokenize.FSTRING_MIDDLE} if hasattr(tokenize, "FSTRING_MIDDLE") else set())
    bad = []
    for p in sorted(app_dir.glob("*.py")):
        if p.name in ("theme.py", "icons.py"):
            continue
        src = p.read_text(encoding="utf-8")
        bad += [(p.name, name) for name in FORBIDDEN if name in src]
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in string_types and HEX.search(tok.string):
                bad.append((p.name, tok.start[0], tok.string[:60]))
    assert not bad, bad
