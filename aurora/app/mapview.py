"""pyqtgraph map of measured points in mm with scan footprints, zone outlines and the zone-editing tools."""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsRectItem

from aurora.app.theme import TOKENS, style_plot
from aurora.core.grid import Grid
from aurora.core.io.config import Zone

pg.setConfigOptions(imageAxisOrder="row-major")

TOOLS = ("navigate", "rect+", "rect-", "brush+", "brush-")


class _ToolViewBox(pg.ViewBox):
    """ViewBox whose left-button drag draws a rectangle or paints, depending on the tool; pans otherwise."""
    rect_drawn = Signal(float, float, float, float)     # x0, x1, y0, y1 (mm)
    brush_at = Signal(float, float, bool)               # x, y, add (True) / erase (False)
    clicked = Signal(float, float)

    def __init__(self):
        super().__init__(enableMenu=False)
        self.tool = "navigate"
        self._rubber = QGraphicsRectItem()
        self._rubber.setPen(QPen(QColor(TOKENS["warn"]), 0, Qt.DashLine))
        self._rubber.setZValue(50)
        self._rubber.hide()
        self.addItem(self._rubber, ignoreBounds=True)

    def mouseDragEvent(self, ev, axis=None):
        if ev.button() != Qt.LeftButton or self.tool == "navigate":
            return super().mouseDragEvent(ev, axis)
        ev.accept()
        p = self.mapToView(ev.pos())
        if self.tool.startswith("brush"):
            self.brush_at.emit(float(p.x()), float(p.y()), self.tool == "brush+")
            return
        p0 = self.mapToView(ev.buttonDownPos())
        r = QRectF(QPointF(min(p0.x(), p.x()), min(p0.y(), p.y())), QPointF(max(p0.x(), p.x()), max(p0.y(), p.y())))
        self._rubber.setPen(QPen(QColor(TOKENS["warn"] if self.tool == "rect+" else "#ff5555"), 0, Qt.DashLine))
        self._rubber.setRect(r)
        self._rubber.show()
        if ev.isFinish():
            self._rubber.hide()
            self.rect_drawn.emit(round(r.left(), 3), round(r.right(), 3), round(r.top(), 3), round(r.bottom(), 3))

    def mouseClickEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            p = self.mapToView(ev.pos())
            if self.tool.startswith("brush"):
                ev.accept()
                self.brush_at.emit(float(p.x()), float(p.y()), self.tool == "brush+")
                return
            if self.tool.startswith("rect"):
                ev.accept()                                   # a click = the pixel under it
                self.rect_drawn.emit(round(p.x(), 3), round(p.x(), 3), round(p.y(), 3), round(p.y(), 3))
                return
            self.clicked.emit(float(p.x()), float(p.y()))
        super().mouseClickEvent(ev)


class MapView(pg.GraphicsLayoutWidget):
    rect_drawn = Signal(float, float, float, float)
    brush_at = Signal(float, float, bool)
    clicked = Signal(float, float)
    hovered = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vb = _ToolViewBox()
        self.plot = self.addPlot(viewBox=self.vb)
        self.plot.setAspectLocked(True)
        self.plot.setLabel("bottom", "x (mm)")
        self.plot.setLabel("left", "y (mm)")
        style_plot(self, "map")                                  # navy in both schemes, light axes
        for ax in ("bottom", "left"):
            self.plot.getAxis(ax).enableAutoSIPrefix(False)      # never "x (mm) (x0.001)"
        self.plot.showGrid(False, False)
        self.image = pg.ImageItem()
        self.image.setZValue(-10)
        self.plot.addItem(self.image)
        self._cmap = pg.colormap.get("viridis")
        self.image.setLookupTable(self._cmap.getLookupTable(nPts=256))
        self._overlays: list = []
        self._data_rect: QRectF | None = None
        self.vb.disableAutoRange()          # ranges are set from the data: pyqtgraph's auto-range of an ImageItem is
        self.vb.rect_drawn.connect(self.rect_drawn)   # garbage while the view has no geometry, and it then sticks
        self.vb.brush_at.connect(self.brush_at)
        self.vb.clicked.connect(self.clicked)
        self.scene().sigMouseMoved.connect(self._moved)

    # ------------------------------------------------------------------ image
    def set_tool(self, tool: str) -> None:
        self.vb.tool = tool
        self.vb.setMouseEnabled(x=tool == "navigate", y=tool == "navigate")

    def set_colormap(self, name: str) -> None:
        self._cmap = pg.colormap.get(name)
        self.image.setLookupTable(self._cmap.getLookupTable(nPts=256))

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
        dx = g.dx if np.isfinite(g.dx) else 0.1
        dy = g.dy if np.isfinite(g.dy) else dx
        self._data_rect = QRectF(float(g.xs[0]) - dx / 2, float(g.ys[0]) - dy / 2, len(g.xs) * dx, len(g.ys) * dy)
        self.image.setRect(self._data_rect)

    def autorange(self) -> None:
        """Fit the view to the image (plus a margin), from the known data rectangle."""
        if self._data_rect is not None:
            self.vb.setRange(self._data_rect, padding=0.04, disableAutoRange=True)

    # ------------------------------------------------------------------ overlays
    def clear_overlays(self) -> None:
        for it in self._overlays:
            self.plot.removeItem(it)
        self._overlays.clear()

    def add_rect(self, x0: float, x1: float, y0: float, y1: float, colour: str, width: float = 1.5,
                 dashed: bool = False, fill: str | None = None, z: int = 10) -> QGraphicsRectItem:
        it = QGraphicsRectItem(QRectF(x0, y0, x1 - x0, y1 - y0))
        pen = QPen(QColor(colour), width)
        pen.setCosmetic(True)
        if dashed:
            pen.setStyle(Qt.DashLine)
        it.setPen(pen)
        it.setBrush(QBrush(QColor(fill)) if fill else QBrush(Qt.NoBrush))
        it.setZValue(z)
        self.plot.addItem(it, ignoreBounds=True)          # overlays never drive the auto-range: the image does
        self._overlays.append(it)
        return it

    def add_label(self, x: float, y: float, text: str, colour: str, anchor=(0, 1)) -> pg.TextItem:
        t = pg.TextItem(text, color=colour, anchor=anchor)
        t.setPos(x, y)
        t.setZValue(20)
        self.plot.addItem(t, ignoreBounds=True)
        self._overlays.append(t)
        return t

    def draw_zone(self, zone: Zone, colour: str, selected: bool = False, label: bool = True) -> None:
        w = 2.5 if selected else 1.2
        for r in zone.rects:
            self.add_rect(r[0], r[1], r[2], r[3], colour, w, fill=(colour + "30") if selected else None)
        for r in zone.erects:
            self.add_rect(r[0], r[1], r[2], r[3], "#ff5555", 1.0, dashed=True)
        for s in zone.paint:
            self.add_rect(s[0] - s[2] / 2, s[0] + s[2] / 2, s[1] - s[3] / 2, s[1] + s[3] / 2, colour, w,
                          fill=(colour + "50") if selected else (colour + "25"))
        for s in zone.erase:
            self.add_rect(s[0] - s[2] / 2, s[0] + s[2] / 2, s[1] - s[3] / 2, s[1] + s[3] / 2, "#ff5555", 1.0, dashed=True)
        if label:
            pts = [(r[0], r[3]) for r in zone.rects] + [(s[0] - s[2] / 2, s[1] + s[3] / 2) for s in zone.paint]
            if pts:
                x, y = min(pts, key=lambda p: (-p[1], p[0]))
                self.add_label(x, y, zone.label, colour)

    def draw_pixels(self, x: np.ndarray, y: np.ndarray, pitch: tuple[float, float], colour: str) -> None:
        """Outline every pixel square (used to show the members of a zone on the stack map)."""
        px, py = pitch
        for xi, yi in zip(x, y):
            self.add_rect(xi - px / 2, xi + px / 2, yi - py / 2, yi + py / 2, colour, 0.8, z=5)

    def _moved(self, pos) -> None:
        if self.plot.sceneBoundingRect().contains(pos):
            p = self.vb.mapSceneToView(pos)
            self.hovered.emit(float(p.x()), float(p.y()))
