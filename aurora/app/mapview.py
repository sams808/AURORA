"""pyqtgraph map of measured points in mm with scan footprints, zone outlines and the zone-editing tools.

The map stays navy in both schemes (theme 'map' kind), so colour maps look the same in light and dark. Overlay
colours come from theme.MAP_OVERLAY / theme.FOOTPRINTS / theme.CURVES; the colour map follows Settings > map colour map.

Tools: navigate (pan, wheel zoom), rect+ / rect- (drag a rectangle), brush+ / brush- (paint or erase pixels), lasso+
(freehand polygon, a vertex every ~0.5 pitch, closed on release, Esc cancels) and polygon+ (click the vertices,
double-click closes, Esc cancels). A zone editor opts in to the zone-aware mouse rules with set_zone_tools(True):
- a plain click inside a zone other than the selected one selects it (the smallest zone wins where zones overlap):
  zone_clicked(id);
- a plain click outside every zone, or a Shift+click, applies the tool to the pixel under it;
- a drag shorter than 0.5 pitch is a stray drag and is ignored (set_points gives the pitch);
- a right-click emits context_requested(x, y, zone id or None) for the caller's menu.
Without it (Survey, Maps) the map behaves as it always did; set_context_menus(True) alone gives a map the right-click
signal (Survey can reuse Reduce's zone menu).
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsRectItem

from aurora.app.theme import MAP_OVERLAY, TOKENS, map_casing, style_plot, with_alpha
from aurora.core.grid import Grid
from aurora.core.io.config import Zone

TOOLS = ("navigate", "rect+", "rect-", "brush+", "brush-", "lasso+", "polygon+")
ADD_TOOLS = ("rect+", "brush+", "lasso+", "polygon+")
STRAY_PITCH = 0.5                 # drags shorter than this many pitches are ignored
LASSO_STEP_PITCH = 0.5            # a lasso vertex every ~0.5 pitch


class _ToolViewBox(pg.ViewBox):
    """ViewBox whose left-button drag draws a rectangle, paints or traces a lasso, depending on the tool; pans
    otherwise. With zone tools on, clicks follow the zone-aware rules of the module docstring."""
    rect_drawn = Signal(float, float, float, float)     # x0, x1, y0, y1 (mm)
    brush_at = Signal(float, float, bool)               # x, y, add (True) / erase (False)
    clicked = Signal(float, float)
    polygon_drawn = Signal(object)                      # [[x, y], ...] mm, closed
    zone_clicked = Signal(int)                          # a click selects this zone
    context_requested = Signal(float, float, object)    # x, y, zone id or None

    def __init__(self):
        super().__init__(enableMenu=False)
        self.tool = "navigate"
        self.owner = None                               # the MapView (zone hit tests, pitch)
        self._rubber = QGraphicsRectItem()
        self._rubber.setPen(QPen(QColor(MAP_OVERLAY["rect_add"]), 0, Qt.DashLine))
        self._rubber.setZValue(50)
        self._rubber.hide()
        self.addItem(self._rubber, ignoreBounds=True)
        self._trace = pg.PlotDataItem([], [], pen=pg.mkPen(MAP_OVERLAY["rect_add"], width=1.5, style=Qt.DashLine))
        self._trace.setZValue(55)
        self.addItem(self._trace, ignoreBounds=True)
        self.path: list[list[float]] = []               # lasso / polygon vertices in progress
        self._drawing = ""                              # '' | 'lasso' | 'polygon'
        self._cancelled = False
        self._drag_far = False

    # ------------------------------------------------------------------ helpers
    def _pitch(self):
        return getattr(self.owner, "pitch", None)

    def _extent(self, p0, p1) -> float:
        """Distance between two view points in pitches (inf when no pitch is known)."""
        pitch = self._pitch()
        if not pitch:
            return float("inf")
        return max(abs(p1.x() - p0.x()) / pitch[0], abs(p1.y() - p0.y()) / pitch[1])

    def _zone_mode(self) -> bool:
        return bool(getattr(self.owner, "zone_tools", False))

    def drawing(self) -> str:
        return self._drawing

    def _show_trace(self, extra=None) -> None:
        pts = self.path + ([extra] if extra is not None else [])
        if self._drawing == "lasso" and len(pts) > 2:
            pts = pts + [pts[0]]
        if pts:
            a = np.asarray(pts, float)
            self._trace.setData(a[:, 0], a[:, 1])
        else:
            self._trace.setData([], [])

    def cancel_path(self) -> bool:
        """Esc: drop the lasso or polygon in progress. True when there was one."""
        had = bool(self._drawing)
        self.path = []
        if self._drawing == "lasso":
            self._cancelled = True                      # the rest of this drag is ignored
        self._drawing = ""
        self._show_trace()
        self._rubber.hide()
        return had

    def _close_polygon(self) -> None:
        pts = self.path
        self.path, self._drawing = [], ""
        self._show_trace()
        if len(pts) >= 3:
            self.polygon_drawn.emit([[round(float(x), 4), round(float(y), 4)] for x, y in pts])

    def hover(self, x: float, y: float) -> None:
        if self._drawing == "polygon":
            self._show_trace([x, y])

    # ------------------------------------------------------------------ drags
    def mouseDragEvent(self, ev, axis=None):
        if ev.button() != Qt.LeftButton or self.tool == "navigate":
            return super().mouseDragEvent(ev, axis)
        ev.accept()
        p = self.mapToView(ev.pos())
        p0 = self.mapToView(ev.buttonDownPos())
        zone = self._zone_mode()
        if ev.isStart():
            self._cancelled = False
            self._drag_far = False
        if self._cancelled:
            if ev.isFinish():
                self._cancelled = False
            return
        far = self._extent(p0, p) >= STRAY_PITCH if zone else True
        self._drag_far = self._drag_far or far
        if self.tool.startswith("brush"):
            if not self._drag_far:
                return                                  # a stray drag: nothing painted
            if zone and far and not getattr(self, "_brush_started", False):
                self.brush_at.emit(float(p0.x()), float(p0.y()), self.tool == "brush+")
                self._brush_started = True
            self.brush_at.emit(float(p.x()), float(p.y()), self.tool == "brush+")
            if ev.isFinish():
                self._brush_started = False
            return
        if self.tool == "lasso+":
            pitch = self._pitch()
            step = (LASSO_STEP_PITCH * min(pitch)) if pitch else 0.0
            if ev.isStart() or self._drawing != "lasso":
                self._drawing = "lasso"
                self.path = [[float(p0.x()), float(p0.y())]]
            last = self.path[-1]
            if np.hypot(p.x() - last[0], p.y() - last[1]) >= step:
                self.path.append([float(p.x()), float(p.y())])
            self._show_trace()
            if ev.isFinish():
                if not self._drag_far or len(self.path) < 3:
                    self.cancel_path()
                    self._cancelled = False
                    return
                self._close_polygon()
            return
        if self.tool == "polygon+":
            return                                      # vertices are clicked, not dragged
        r = QRectF(QPointF(min(p0.x(), p.x()), min(p0.y(), p.y())), QPointF(max(p0.x(), p.x()), max(p0.y(), p.y())))
        colour = MAP_OVERLAY["rect_add"] if self.tool == "rect+" else MAP_OVERLAY["erase"]
        self._rubber.setPen(QPen(QColor(colour), 0, Qt.DashLine))
        self._rubber.setRect(r)
        self._rubber.show()
        if ev.isFinish():
            self._rubber.hide()
            if not self._drag_far:
                return                                  # a stray drag (< 0.5 pitch): ignored
            self.rect_drawn.emit(round(r.left(), 3), round(r.right(), 3), round(r.top(), 3), round(r.bottom(), 3))

    # ------------------------------------------------------------------ clicks
    def _pixel_action(self, x: float, y: float) -> None:
        """The tool on the pixel under the click (a point rectangle, a brush square, or a polygon's first vertex)."""
        if self.tool.startswith("brush"):
            self.brush_at.emit(x, y, self.tool == "brush+")
        elif self.tool == "polygon+":
            self._drawing = "polygon"
            self.path = [[x, y]]
            self._show_trace()
        elif self.tool in ("rect+", "rect-", "lasso+"):
            self.rect_drawn.emit(round(x, 3), round(x, 3), round(y, 3), round(y, 3))

    def mouseClickEvent(self, ev):
        p = self.mapToView(ev.pos())
        x, y = float(p.x()), float(p.y())
        zone = self._zone_mode()
        if ev.button() == Qt.RightButton and getattr(self.owner, "context_menus", False):
            ev.accept()
            zid = self.owner.zone_at(x, y) if self.owner is not None else None
            self.context_requested.emit(x, y, zid)
            return
        if ev.button() == Qt.LeftButton:
            if self.tool == "polygon+" and self._drawing == "polygon":
                ev.accept()
                last = self.path[-1]
                pitch = self._pitch()
                near = pitch and abs(x - last[0]) < 0.5 * pitch[0] and abs(y - last[1]) < 0.5 * pitch[1]
                if not near:
                    self.path.append([x, y])
                if ev.double():
                    self._close_polygon()
                else:
                    self._show_trace()
                return
            if zone:
                ev.accept()
                shift = bool(ev.modifiers() & Qt.ShiftModifier)
                if self.tool == "navigate":
                    zid = self.owner.zone_at(x, y) if self.owner is not None else None
                    if zid is not None and zid != self.owner.selected_zone:
                        self.zone_clicked.emit(int(zid))
                    else:
                        self.clicked.emit(x, y)
                    return
                if shift:
                    self._pixel_action(x, y)
                    return
                zid = self.owner.zone_at(x, y) if self.owner is not None else None
                if zid is not None and zid != self.owner.selected_zone:
                    self.zone_clicked.emit(int(zid))     # select, never edit, on a plain click inside another zone
                    return
                inside_selected = zid is not None
                if self.tool in ADD_TOOLS:
                    if not inside_selected or self.tool == "polygon+":
                        self._pixel_action(x, y)        # outside every zone: add the pixel (or start a polygon)
                elif inside_selected:
                    self._pixel_action(x, y)            # an erase tool on the selected zone: erase the pixel
                return
            if self.tool.startswith("brush"):
                ev.accept()
                self.brush_at.emit(x, y, self.tool == "brush+")
                return
            if self.tool.startswith("rect"):
                ev.accept()                                   # a click = the pixel under it
                self.rect_drawn.emit(round(x, 3), round(x, 3), round(y, 3), round(y, 3))
                return
            self.clicked.emit(x, y)
        super().mouseClickEvent(ev)


class MapView(pg.GraphicsLayoutWidget):
    rect_drawn = Signal(float, float, float, float)
    brush_at = Signal(float, float, bool)
    clicked = Signal(float, float)
    hovered = Signal(float, float)
    polygon_drawn = Signal(object)
    zone_clicked = Signal(int)
    context_requested = Signal(float, float, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vb = _ToolViewBox()
        self.vb.owner = self
        self.plot = self.addPlot(viewBox=self.vb)
        self.plot.setAspectLocked(True)
        self.plot.setLabel("bottom", "x (mm)")
        self.plot.setLabel("left", "y (mm)")
        self.bar = colour_bar()                                  # the colour scale of the map, beside it
        self.addItem(self.bar, row=0, col=1)
        style_plot(self, "map")                                  # navy in both schemes, light axes (the bar's too)
        for ax in ("bottom", "left"):
            self.plot.getAxis(ax).enableAutoSIPrefix(False)      # never "x (mm) (x0.001)"
        self.plot.showGrid(False, False)
        self.image = pg.ImageItem(axisOrder="row-major")
        self.image.setZValue(-10)
        self.plot.addItem(self.image)
        self._fixed_lut = False                                  # True: categorical / RGB, never the colour map
        self._cmap = None
        self.apply_colormap_setting()
        from aurora.app.settings_dialog import on_settings_changed
        on_settings_changed(self, self.apply_colormap_setting)
        self._overlays: list = []
        self._zones: list[Zone] = []                             # the zones drawn (hit tests, context menus)
        self.selected_zone: int | None = None
        self.zone_tools = False
        self.context_menus = False
        self.pitch: tuple[float, float] | None = None
        self._px: np.ndarray | None = None
        self._py: np.ndarray | None = None
        self._data_rect: QRectF | None = None
        self.vb.disableAutoRange()          # ranges are set from the data: pyqtgraph's auto-range of an ImageItem is
        self.vb.rect_drawn.connect(self.rect_drawn)   # garbage while the view has no geometry, and it then sticks
        self.vb.brush_at.connect(self.brush_at)
        self.vb.clicked.connect(self.clicked)
        self.vb.polygon_drawn.connect(self.polygon_drawn)
        self.vb.zone_clicked.connect(self.zone_clicked)
        self.vb.context_requested.connect(self.context_requested)
        self.scene().sigMouseMoved.connect(self._moved)

    # ------------------------------------------------------------------ tools
    def set_tool(self, tool: str) -> None:
        if tool != self.vb.tool:
            self.vb.cancel_path()
        self.vb.tool = tool
        self.vb.setMouseEnabled(x=tool == "navigate", y=tool == "navigate")

    def set_zone_tools(self, on: bool) -> None:
        """Zone-aware clicks, stray-drag filter and right-click menus (the Reduce zone editor)."""
        self.zone_tools = bool(on)
        self.context_menus = bool(on)

    def set_context_menus(self, on: bool) -> None:
        """Right-clicks emit context_requested(x, y, zone id or None) without the other zone rules (e.g. Survey)."""
        self.context_menus = bool(on)

    def set_points(self, x, y, pitch) -> None:
        """The stack's pixel centres and pitch (mm): hit tests use the members, drags are measured in pitches."""
        if x is None:
            self._px = self._py = None
            self.pitch = None
            return
        self._px, self._py = np.asarray(x, float), np.asarray(y, float)
        self.pitch = (float(pitch[0]), float(pitch[1]))

    def cancel_drawing(self) -> bool:
        return self.vb.cancel_path()

    def event(self, ev):
        # Esc ends a lasso or polygon in progress before any window shortcut (Cancel the running task) sees it
        if ev.type() == QEvent.ShortcutOverride and ev.key() == Qt.Key_Escape and self.vb.drawing():
            ev.accept()
            return True
        return super().event(ev)

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Escape and self.vb.cancel_path():
            ev.accept()
            return
        super().keyPressEvent(ev)

    # ------------------------------------------------------------------ zones under a point
    def _pixel_at(self, x: float, y: float) -> int | None:
        if self._px is None or self.pitch is None or self._px.size == 0:
            return None
        px, py = self.pitch
        hit = np.where((np.abs(self._px - x) <= px / 2) & (np.abs(self._py - y) <= py / 2))[0]
        if not hit.size:
            return None
        return int(hit[np.argmin((self._px[hit] - x) ** 2 + (self._py[hit] - y) ** 2)])

    @staticmethod
    def _contains(z: Zone, x: float, y: float, pitch) -> bool:
        from aurora.core.zones import PAINT_TOL, RECT_TOL, polygon_inside
        hx, hy = (pitch[0] * RECT_TOL, pitch[1] * RECT_TOL) if pitch else (0.0, 0.0)
        inside = any(r[0] - hx <= x <= r[1] + hx and r[2] - hy <= y <= r[3] + hy for r in z.rects)
        inside = inside or any(abs(x - s[0]) <= s[2] * PAINT_TOL and abs(y - s[1]) <= s[3] * PAINT_TOL for s in z.paint)
        inside = inside or any(bool(polygon_inside(poly, np.array([x]), np.array([y]))[0]) for poly in z.polys)
        if not inside:
            return False
        out = any(r[0] - hx <= x <= r[1] + hx and r[2] - hy <= y <= r[3] + hy for r in z.erects)
        out = out or any(abs(x - s[0]) <= s[2] * PAINT_TOL and abs(y - s[1]) <= s[3] * PAINT_TOL for s in z.erase)
        return not out

    def zone_at(self, x: float, y: float) -> int | None:
        """The id of the drawn zone under (x, y) mm: on the stack, the zones whose members include the pixel under the
        point, else the zones whose drawing contains it; the smallest one (fewest members, then smallest box) wins."""
        from aurora.core.zones import members, zone_bbox
        i = self._pixel_at(x, y)
        cands = []
        for z in self._zones:
            if i is not None:
                m = members(z, self._px, self._py, self.pitch)
                if i in set(m.tolist()):
                    bb = zone_bbox(z)
                    cands.append((m.size, (bb[1] - bb[0]) * (bb[3] - bb[2]) if bb else 0.0, -z.id, z.id))
            elif self._contains(z, x, y, self.pitch):
                bb = zone_bbox(z)
                cands.append((0, (bb[1] - bb[0]) * (bb[3] - bb[2]) if bb else 0.0, -z.id, z.id))
        return min(cands)[3] if cands else None

    # ------------------------------------------------------------------ image
    def set_colormap(self, name: str) -> None:
        try:
            self._cmap = pg.colormap.get(name)
        except Exception:                                        # an unknown name in the settings: keep viridis
            self._cmap = pg.colormap.get("viridis")
        self.bar.setColorMap(self._cmap)
        if not self._fixed_lut:
            self.image.setLookupTable(self._cmap.getLookupTable(nPts=256))

    def apply_colormap_setting(self) -> None:
        """Settings > map colour map (at creation and each time the Settings dialog is accepted)."""
        from aurora.app.settings_dialog import get
        self.set_colormap(get("map_colormap"))

    def colormap_name(self) -> str:
        return getattr(self._cmap, "name", "") or ""

    def set_lookup(self, lut) -> None:
        """A fixed lookup table (categorical clusters) or None (RGB images): the colour-map setting no longer applies,
        and the map has no colour scale (the cluster list is its legend)."""
        self._fixed_lut = True
        self.image.setLookupTable(lut)
        if self.bar in self.ci.items:
            self.ci.removeItem(self.bar)

    def set_scale_label(self, text: str) -> None:
        """The quantity of the map on its colour scale ('counts / I0', see scale_label)."""
        ax = self.bar.getAxis("left")
        if ax.labelText != text:                     # every slider step draws the map: no relayout for the same text
            ax.setLabel(text)

    def scale_levels(self) -> tuple[float, float] | None:
        """The values at the two ends of the colour scale (None for a map without one)."""
        return tuple(float(v) for v in self.bar.levels()) if self.bar in self.ci.items else None

    def set_grid(self, g: Grid | None) -> None:
        if g is None or g.img.size == 0:
            self.image.clear()
            return
        img = g.img.astype(float)
        fin = np.isfinite(img)
        lo, hi = (float(np.nanmin(img)), float(np.nanmax(img))) if fin.any() else (0.0, 1.0)
        if hi <= lo:
            hi = lo + 1.0
        self.image.setImage(img, levels=(lo, hi), autoLevels=False)
        self.bar.setLevels((lo, hi))
        dx = g.dx if np.isfinite(g.dx) else 0.1
        dy = g.dy if np.isfinite(g.dy) else dx
        self._data_rect = QRectF(float(g.xs[0]) - dx / 2, float(g.ys[0]) - dy / 2, len(g.xs) * dx, len(g.ys) * dy)
        self.image.setRect(self._data_rect)

    def autorange(self) -> None:
        """Fit the view to the image (plus a margin), from the known data rectangle."""
        if self._data_rect is not None:
            self.vb.setRange(self._data_rect, padding=0.04, disableAutoRange=True)

    def zoom_to(self, x0: float, x1: float, y0: float, y1: float, margin: float = 0.15) -> None:
        """Show the box (mm) with a margin (Zoom to zone)."""
        w, h = max(x1 - x0, 1e-3), max(y1 - y0, 1e-3)
        self.vb.setRange(QRectF(x0 - margin * w, y0 - margin * h, w * (1 + 2 * margin), h * (1 + 2 * margin)),
                         padding=0.0, disableAutoRange=True)

    # ------------------------------------------------------------------ overlays
    def clear_overlays(self) -> None:
        for it in self._overlays:
            self.plot.removeItem(it)
        self._overlays.clear()
        self._zones.clear()
        self.selected_zone = None

    def add_casing(self, path: QPainterPath, colour: str, width: float, z: float = 10) -> None:
        """The casing of an outline in `colour`, `width` px wide, along `path` (mm), just below it: a dark stroke 2 px
        wider over a rim of its hue 4 px wider (theme.map_casing), so it reads on the dark, mid and bright tones."""
        if path.isEmpty():
            return
        for k, (col, extra) in enumerate(map_casing(colour)):
            it = QGraphicsPathItem(path)
            pen = QPen(QColor(col), width + extra)
            pen.setCosmetic(True)
            pen.setJoinStyle(Qt.MiterJoin)
            it.setPen(pen)
            it.setBrush(QBrush(Qt.NoBrush))
            it.setZValue(z - 0.5 + 0.1 * k)                # under every outline of its level, the rim lowest
            self.plot.addItem(it, ignoreBounds=True)
            self._overlays.append(it)

    def add_rect(self, x0: float, x1: float, y0: float, y1: float, colour: str, width: float = 1.5,
                 dashed: bool = False, fill=None, z: int = 10, style=None, casing: bool = True) -> QGraphicsRectItem:
        """An outlined rectangle in mm over its casing. `fill` is a QColor (with alpha) or a colour string; `style` a
        Qt pen style."""
        if casing:
            path = QPainterPath()
            path.addRect(QRectF(x0, y0, x1 - x0, y1 - y0))
            self.add_casing(path, colour, width, z)
        it = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
        pen = QPen(QColor(colour), width)
        pen.setCosmetic(True)
        if style is not None:
            pen.setStyle(style)
        elif dashed:
            pen.setStyle(Qt.DashLine)
        it.setPen(pen)
        it.setBrush(QBrush(fill if isinstance(fill, QColor) else QColor(fill)) if fill is not None else QBrush(Qt.NoBrush))
        it.setZValue(z)
        self.plot.addItem(it, ignoreBounds=True)          # overlays never drive the auto-range: the image does
        self._overlays.append(it)
        return it

    def add_polygon(self, pts, colour: str, width: float = 1.5, fill=None, z: int = 10,
                    casing: bool = True) -> QGraphicsPolygonItem:
        """An outlined polygon [[x, y], ...] in mm over its casing."""
        poly = QPolygonF([QPointF(float(p[0]), float(p[1])) for p in pts])
        if casing:
            path = QPainterPath()
            path.addPolygon(poly)
            path.closeSubpath()
            self.add_casing(path, colour, width, z)
        it = QGraphicsPolygonItem(poly)
        pen = QPen(QColor(colour), width)
        pen.setCosmetic(True)
        it.setPen(pen)
        it.setBrush(QBrush(fill if isinstance(fill, QColor) else QColor(fill)) if fill is not None else QBrush(Qt.NoBrush))
        it.setZValue(z)
        self.plot.addItem(it, ignoreBounds=True)
        self._overlays.append(it)
        return it

    def add_label(self, x: float, y: float, text: str, colour: str, anchor=(0, 1)) -> pg.TextItem:
        """A zone or footprint label: map_fg text on a map_bg plate (80 %), bordered in the zone's colour, so it reads
        whatever the colour (12:1)."""
        t = pg.TextItem(text, color=TOKENS["map_fg"], anchor=anchor, fill=pg.mkBrush(with_alpha(TOKENS["map_bg"], 0.8)),
                        border=pg.mkPen(colour, width=1))
        t.setPos(x, y)
        t.setZValue(20)
        self.plot.addItem(t, ignoreBounds=True)
        self._overlays.append(t)
        return t

    def draw_zone(self, zone: Zone, colour: str, selected: bool = False, label: bool = True) -> None:
        self._zones.append(zone)
        if selected:
            self.selected_zone = zone.id
        w = 2.5 if selected else 1.2
        erase = MAP_OVERLAY["erase"]
        self.add_casing(_union(zone.rects, zone.polys, zone.paint), colour, w)        # one casing round the zone
        self.add_casing(_union(zone.erects, [], zone.erase), erase, 1.0)
        for r in zone.rects:
            self.add_rect(r[0], r[1], r[2], r[3], colour, w, fill=with_alpha(colour, 0.19) if selected else None,
                          casing=False)
        for poly in zone.polys:
            if len(poly) >= 3:
                self.add_polygon(poly, colour, w, fill=with_alpha(colour, 0.19) if selected else None, casing=False)
        for r in zone.erects:
            self.add_rect(r[0], r[1], r[2], r[3], erase, 1.0, dashed=True, casing=False)
        for s in zone.paint:
            self.add_rect(s[0] - s[2] / 2, s[0] + s[2] / 2, s[1] - s[3] / 2, s[1] + s[3] / 2, colour, w,
                          fill=with_alpha(colour, 0.31 if selected else 0.15), casing=False)
        for s in zone.erase:
            self.add_rect(s[0] - s[2] / 2, s[0] + s[2] / 2, s[1] - s[3] / 2, s[1] + s[3] / 2, erase, 1.0, dashed=True,
                          casing=False)
        if label:
            pts = [(r[0], r[3]) for r in zone.rects] + [(s[0] - s[2] / 2, s[1] + s[3] / 2) for s in zone.paint]
            pts += [(min(p[0] for p in poly), max(p[1] for p in poly)) for poly in zone.polys if poly]
            if pts:
                x, y = min(pts, key=lambda p: (-p[1], p[0]))
                self.add_label(x, y, zone.label, colour)

    def draw_pixels(self, x: np.ndarray, y: np.ndarray, pitch: tuple[float, float], colour: str) -> None:
        """Outline every pixel square (used to show the members of a zone on the stack map); no casing, which would
        draw a dark grid over the pixels."""
        px, py = pitch
        for xi, yi in zip(x, y):
            self.add_rect(xi - px / 2, xi + px / 2, yi - py / 2, yi + py / 2, colour, 0.8, z=5, casing=False)

    def _moved(self, pos) -> None:
        if self.plot.sceneBoundingRect().contains(pos):
            p = self.vb.mapSceneToView(pos)
            self.vb.hover(float(p.x()), float(p.y()))
            self.hovered.emit(float(p.x()), float(p.y()))


def _union(rects, polys, squares) -> QPainterPath:
    """The outline of a zone's rectangles [x0, x1, y0, y1], polygons and painted squares [cx, cy, w, h] (mm) as one
    path: its casing follows the zone's edge, not every square inside it. A square counts out to PAINT_TOL of its size
    (the membership rule), so the small gaps of a jittered grid close."""
    from aurora.core.zones import PAINT_TOL
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)                 # a place covered twice is inside (the union), not a hole
    for r in rects:
        path.addRect(QRectF(r[0], r[2], r[1] - r[0], r[3] - r[2]))
    for poly in polys:
        if len(poly) >= 3:
            a = np.asarray(poly, float)[:, :2]
            area = float(np.dot(a[:, 0], np.roll(a[:, 1], -1)) - np.dot(a[:, 1], np.roll(a[:, 0], -1)))
            if area < 0:
                a = a[::-1]                          # turned like addRect's rectangles, so the windings add up
            path.addPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in a]))
            path.closeSubpath()
    for s in squares:
        hx, hy = s[2] * PAINT_TOL, s[3] * PAINT_TOL
        path.addRect(QRectF(s[0] - hx, s[1] - hy, 2 * hx, 2 * hy))
    return path.simplified() if path.elementCount() else path


def colour_bar(levels=(0.0, 1.0), cmap=None) -> pg.ColorBarItem:
    """A slim, fixed colour scale for an intensity map (its levels follow the image; the user cannot drag them)."""
    bar = pg.ColorBarItem(values=tuple(float(v) for v in levels), width=12, colorMap=cmap, interactive=False)
    bar.getAxis("right").enableAutoSIPrefix(False)
    return bar


def scale_label(what: str = "counts", per_i0: bool = False, log: bool = False, inverted: bool = False) -> str:
    """The quantity an intensity map shows, for its colour scale: 'counts', 'counts / I0', 'log10(counts / I0 + 1)'."""
    q = f"{what} / I0" if per_i0 else what
    q = f"log10({q} + 1)" if log else q
    return q + (", inverted" if inverted else "")
