"""pyqtgraph map of measured points in mm with scan footprints, zone outlines and the zone-editing tools.

The map stays navy in both schemes (theme 'map' kind), so colour maps look the same in light and dark. Overlay
colours come from theme.MAP_OVERLAY / theme.FOOTPRINTS / theme.CURVES; the colour map follows Settings > map colour map.

Tools are a shape and a mode (tool_name / split_tool): navigate (pan, wheel zoom), rect+ / rect- (drag a rectangle),
brush+ / brush- (paint or erase points along the drag), lasso+ / lasso- (freehand outline, a vertex every ~0.5 pitch,
closed on release) and polygon+ / polygon- (click the vertices, double-click closes). Every gesture is a stroke
(aurora.core.zones.Stroke): stroke_started, stroke_changed while it moves, then stroke_finished at release or
stroke_cancelled (Esc, Ctrl+Z, a stray drag, a tool change). The legacy signals rect_drawn, brush_at and polygon_drawn
are still emitted for the maps that use them (Maps' ROI, Survey's Grid of zones); while they are emitted by a stroke,
stroke_emitting() is True, so a zone editor that handles the strokes can tell them from the same signals emitted by
code (scripts, the self-test). A zone editor opts in to the zone-aware mouse rules with set_zone_tools(True):
- a plain click inside a zone other than the selected one selects it (the smallest zone wins where zones overlap):
  zone_clicked(id);
- a plain click outside every zone, or a Shift+click, applies the tool to the pixel under it (a 'pixel' stroke);
- a right-drag, or a drag started with Shift held, applies the opposite mode (Draw <-> Erase) with the same shape;
- a drag shorter than 0.5 pitch is a stray drag and is ignored (set_points gives the pitch);
- a right-click emits context_requested(x, y, zone id or None) for the caller's menu.
Esc (and Ctrl+Z) during a stroke cancels it before any window shortcut sees the key. In navigate, a double-click
emits double_clicked(x, y) (Survey: open the stack under a zone in Reduce).
Without it (Maps) the map behaves as it always did; set_context_menus(True) alone gives a map the right-click signal.

Feedback while drawing (the zone editor drives it): set_hint (a sentence over the top-left of the map),
show_stroke_preview (the points a stroke adds or removes: one scatter item per kind, cheap on a survey of thousands of
points), set_brush_footprint (the n x n brush square that follows the pointer, snapped to the nearest point), and the
cursor of the tool (a cross for the outlines, none over the map for the brush, whose square is the cursor).
"""
from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QKeySequence, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsRectItem, QLabel

from aurora.app.theme import MAP_OVERLAY, TOKENS, map_casing, style_plot, with_alpha
from aurora.core.grid import Grid
from aurora.core.io.config import Zone
from aurora.core.zones import Stroke, interpolate_path

TOOLS = ("navigate", "rect+", "rect-", "brush+", "brush-", "lasso+", "polygon+", "lasso-", "polygon-")
ADD_TOOLS = ("rect+", "brush+", "lasso+", "polygon+")       # the Draw tools (the others erase, or navigate)
SHAPES = ("navigate", "rect", "brush", "lasso", "polygon")
STRAY_PITCH = 0.5                 # drags shorter than this many pitches are ignored
LASSO_STEP_PITCH = 0.5            # a lasso vertex every ~0.5 pitch


def tool_name(shape: str, erase: bool = False) -> str:
    """The tool string of a shape and a mode: ('rect', True) -> 'rect-', ('navigate', any) -> 'navigate'."""
    if shape == "navigate":
        return "navigate"
    if shape not in SHAPES:
        raise ValueError(f"unknown tool shape {shape!r}")
    return f"{shape}{'-' if erase else '+'}"


def split_tool(tool: str) -> tuple[str, bool]:
    """(shape, erase) of a tool string: 'lasso-' -> ('lasso', True), 'navigate' -> ('navigate', False)."""
    if tool == "navigate":
        return "navigate", False
    if tool not in TOOLS:
        raise ValueError(f"unknown tool {tool!r}")
    return tool[:-1], tool.endswith("-")


def _modifiers(ev):
    """The keyboard modifiers of a pyqtgraph mouse event (Qt.NoModifier for an event without them)."""
    return getattr(ev, "modifiers", lambda: Qt.NoModifier)()


class _ToolViewBox(pg.ViewBox):
    """ViewBox whose left-button drag draws a rectangle, paints or traces a lasso, depending on the tool (the right
    button and Shift invert the mode on a zone map); pans otherwise. With zone tools on, clicks follow the zone-aware
    rules of the module docstring."""
    rect_drawn = Signal(float, float, float, float)     # x0, x1, y0, y1 (mm)
    brush_at = Signal(float, float, bool)               # x, y, add (True) / erase (False)
    clicked = Signal(float, float)
    polygon_drawn = Signal(object)                      # [[x, y], ...] mm, closed
    zone_clicked = Signal(int)                          # a click selects this zone
    context_requested = Signal(float, float, object)    # x, y, zone id or None
    stroke_started = Signal(object)                     # Stroke
    stroke_changed = Signal(object)                     # Stroke (the same object, grown)
    stroke_finished = Signal(object)                    # Stroke (finished=True)
    stroke_cancelled = Signal()
    double_clicked = Signal(float, float)               # navigate on a zone map: a double-click

    def __init__(self):
        super().__init__(enableMenu=False)
        self.tool = "navigate"
        self.owner = None                               # the MapView (zone hit tests, pitch)
        self.brush_n = 1                                # the brush covers brush_n x brush_n points
        self._rubber = QGraphicsRectItem()
        self._rubber.setPen(QPen(QColor(MAP_OVERLAY["rect_add"]), 0, Qt.DashLine))
        self._rubber.setZValue(50)
        self._rubber.hide()
        self.addItem(self._rubber, ignoreBounds=True)
        self._trace = pg.PlotDataItem([], [], pen=pg.mkPen(MAP_OVERLAY["rect_add"], width=1.5, style=Qt.DashLine))
        self._trace.setZValue(55)
        self.addItem(self._trace, ignoreBounds=True)
        self.path: list[list[float]] = []               # lasso / polygon vertices in progress
        self._stroke: Stroke | None = None              # the stroke in progress
        self._serial = 0
        self._cancelled = False                         # the rest of this drag is ignored (Esc)
        self._drag_far = False
        self._emitting = False                          # a legacy signal is being emitted by a stroke

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

    def _view_pos(self, ev, down: bool = False) -> QPointF:
        """The event's point (or where its button went down) in mm, through the scene. A drag or click that starts
        on an axis beside the map reaches the view box from pyqtgraph's AxisItem with ev.pos() in the axis item's own
        coordinates, which mapToView would put a whole axis width away (a rectangle started in the margin, to be sure
        of the edge pixels, missed them)."""
        get = getattr(ev, "buttonDownScenePos" if down else "scenePos", None)
        if get is not None:
            return self.mapSceneToView(get())
        return self.mapToView(ev.buttonDownPos() if down else ev.pos())

    def drawing(self) -> str:
        """The shape of the stroke in progress ('rect', 'brush', 'lasso', 'polygon'), '' when none."""
        return self._stroke.shape if self._stroke is not None else ""

    def stroke(self) -> Stroke | None:
        return self._stroke

    def emitting(self) -> bool:
        return self._emitting

    def _legacy(self, signal, *args) -> None:
        """Emit a legacy signal on behalf of a stroke (stroke_emitting() is True meanwhile)."""
        self._emitting = True
        try:
            signal.emit(*args)
        finally:
            self._emitting = False

    def _colour(self, erase: bool) -> tuple[str, Qt.PenStyle]:
        return (MAP_OVERLAY["erase"], Qt.DashLine) if erase else (MAP_OVERLAY["rect_add"], Qt.DashLine)

    def _show_trace(self, extra=None) -> None:
        pts = self.path + ([extra] if extra is not None else [])
        if self.drawing() == "lasso" and len(pts) > 2:
            pts = pts + [pts[0]]
        if pts:
            a = np.asarray(pts, float)
            self._trace.setData(a[:, 0], a[:, 1])
        else:
            self._trace.setData([], [])

    def _begin(self, shape: str, erase: bool, x: float, y: float) -> Stroke:
        self._serial += 1
        st = Stroke(shape=shape, erase=bool(erase), start=(float(x), float(y)), brush_n=int(self.brush_n),
                    serial=self._serial)
        if shape == "rect":
            st.rect = (round(x, 3), round(x, 3), round(y, 3), round(y, 3))
        elif shape in ("brush", "lasso", "polygon"):
            st.path = [[float(x), float(y)]]
        if shape in ("lasso", "polygon"):
            self.path = st.path
        colour, style = self._colour(st.erase)
        self._rubber.setPen(QPen(QColor(colour), 0, style))
        self._trace.setPen(pg.mkPen(colour, width=1.5, style=style))
        self._stroke = st
        if self.owner is not None and self._zone_mode():
            self.owner.setFocus(Qt.MouseFocusReason)     # Esc and Ctrl+Z reach the map during the stroke
        self.stroke_started.emit(st)
        return st

    def _end(self) -> Stroke | None:
        """Finish the stroke in progress: drop it from the view (the caller emits)."""
        st, self._stroke = self._stroke, None
        self.path = []
        self._show_trace()
        self._rubber.hide()
        if st is not None:
            st.finished = True
        return st

    def cancel_stroke(self) -> bool:
        """Esc (Ctrl+Z, a tool change): drop the stroke in progress, whatever its shape; the rest of its drag is
        ignored and nothing is written. True when there was one."""
        st = self._stroke
        self._stroke = None
        self.path = []
        self._show_trace()
        self._rubber.hide()
        if st is None:
            return False
        if st.shape != "polygon":
            self._cancelled = True                      # a drag in progress: ignore its remaining events
        self.stroke_cancelled.emit()
        return True

    def cancel_path(self) -> bool:
        """The name of earlier versions (Esc on a lasso or polygon): cancel_stroke."""
        return self.cancel_stroke()

    def _close_polygon(self) -> None:
        pts = list(self.path)
        st = self._end()
        if st is None:
            return
        if len(pts) < 3:
            self.stroke_cancelled.emit()
            return
        st.path = [[round(float(x), 4), round(float(y), 4)] for x, y in pts]
        self.stroke_finished.emit(st)
        if not st.erase:
            self._legacy(self.polygon_drawn, list(st.path))

    def hover(self, x: float, y: float) -> None:
        if self.drawing() == "polygon":
            self._show_trace([x, y])
        if self.owner is not None:
            self.owner._move_footprint(x, y)

    # ------------------------------------------------------------------ drags
    def mouseDragEvent(self, ev, axis=None):
        button = ev.button()
        zone = self._zone_mode()
        if self.tool == "navigate" or button not in (Qt.LeftButton, Qt.RightButton) or \
                (button == Qt.RightButton and not zone):
            return super().mouseDragEvent(ev, axis)
        ev.accept()
        p = self._view_pos(ev)
        p0 = self._view_pos(ev, down=True)
        if ev.isStart():
            self._cancelled = False
            self._drag_far = False
            shape, erase = split_tool(self.tool)
            if shape == "polygon":
                return                                  # vertices are clicked, not dragged
            if zone and ((button == Qt.RightButton) != bool(_modifiers(ev) & Qt.ShiftModifier)):
                erase = not erase                       # right button or Shift: the other mode
            self._begin(shape, erase, float(p0.x()), float(p0.y()))
        if self._cancelled:
            if ev.isFinish():
                self._cancelled = False
            return
        st = self._stroke
        if st is None or st.shape == "polygon":
            return
        far = self._extent(p0, p) >= STRAY_PITCH if zone else True
        first_far = far and not self._drag_far
        self._drag_far = self._drag_far or far
        x, y = float(p.x()), float(p.y())
        if ev.isFinish():
            st.force_add = bool(_modifiers(ev) & Qt.ControlModifier)
        if st.shape == "brush":
            if self._drag_far:
                if first_far and zone:
                    self._legacy(self.brush_at, st.start[0], st.start[1], not st.erase)
                st.path.extend(interpolate_path(st.path[-1], (x, y), self._pitch()))
                self._legacy(self.brush_at, x, y, not st.erase)
                self.stroke_changed.emit(st)
            if ev.isFinish():
                self._finish_drag(st)
            return
        if st.shape == "lasso":
            pitch = self._pitch()
            step = (LASSO_STEP_PITCH * min(pitch)) if pitch else 0.0
            last = self.path[-1]
            if np.hypot(x - last[0], y - last[1]) >= step:
                self.path.append([x, y])
                if self._drag_far:
                    self.stroke_changed.emit(st)
            self._show_trace()
            if ev.isFinish():
                if not self._drag_far or len(self.path) < 3:
                    self.cancel_stroke()
                    self._cancelled = False
                    return
                self._close_polygon()
            return
        # rectangle
        x0, y0 = st.start
        r = QRectF(QPointF(min(x0, x), min(y0, y)), QPointF(max(x0, x), max(y0, y)))
        self._rubber.setRect(r)
        self._rubber.show()
        st.rect = (round(r.left(), 3), round(r.right(), 3), round(r.top(), 3), round(r.bottom(), 3))
        if self._drag_far and not ev.isFinish():
            self.stroke_changed.emit(st)
        if ev.isFinish():
            self._finish_drag(st)

    def _finish_drag(self, st: Stroke) -> None:
        """Release of a rectangle or brush drag: a stray drag (< 0.5 pitch) is cancelled, any other one finished."""
        if not self._drag_far:
            self.cancel_stroke()
            self._cancelled = False
            return
        self._end()
        self.stroke_finished.emit(st)
        if st.shape == "rect":
            self._legacy(self.rect_drawn, *st.rect)

    # ------------------------------------------------------------------ clicks
    def _pixel_action(self, x: float, y: float, mods=Qt.NoModifier) -> None:
        """The tool on the pixel under the click (a point rectangle, a brush square, or a polygon's first vertex): a
        'pixel' stroke, finished at once."""
        shape, erase = split_tool(self.tool)
        if shape == "polygon":
            self._begin("polygon", erase, x, y)
            self._show_trace()
            return
        i = self.owner._pixel_at(x, y) if self.owner is not None and shape != "brush" else None
        if i is not None:                               # the pixel under the click, even on the line between two
            x, y = float(self.owner._px[i]), float(self.owner._py[i])
        self._serial += 1
        st = Stroke(shape="pixel", via=shape, erase=erase, start=(x, y), rect=(round(x, 3), round(x, 3), round(y, 3),
                    round(y, 3)), path=[[x, y]], brush_n=int(self.brush_n), finished=True, serial=self._serial,
                    force_add=bool(mods & Qt.ControlModifier))
        self.stroke_finished.emit(st)
        if shape == "brush":
            self._legacy(self.brush_at, x, y, not erase)
        else:
            self._legacy(self.rect_drawn, *st.rect)

    def mouseClickEvent(self, ev):
        p = self._view_pos(ev)
        x, y = float(p.x()), float(p.y())
        zone = self._zone_mode()
        if ev.button() == Qt.RightButton and getattr(self.owner, "context_menus", False):
            ev.accept()
            zid = self.owner.zone_at(x, y) if self.owner is not None else None
            self.context_requested.emit(x, y, zid)
            return
        if ev.button() == Qt.LeftButton:
            if self.tool.startswith("polygon") and self.drawing() == "polygon":
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
                    if not near and self._stroke is not None:
                        self.stroke_changed.emit(self._stroke)
                return
            if zone:
                ev.accept()
                mods = ev.modifiers()
                shift = bool(mods & Qt.ShiftModifier)
                if self.tool == "navigate":
                    if ev.double():
                        self.double_clicked.emit(x, y)
                        return
                    zid = self.owner.zone_at(x, y) if self.owner is not None else None
                    if zid is not None and zid != self.owner.selected_zone:
                        self.zone_clicked.emit(int(zid))
                    else:
                        self.clicked.emit(x, y)
                    return
                if shift:
                    self._pixel_action(x, y, mods)
                    return
                zid = self.owner.zone_at(x, y) if self.owner is not None else None
                if zid is not None and zid != self.owner.selected_zone:
                    self.zone_clicked.emit(int(zid))     # select, never edit, on a plain click inside another zone
                    return
                inside_selected = zid is not None
                if self.tool in ADD_TOOLS:
                    if not inside_selected or self.tool == "polygon+":
                        self._pixel_action(x, y, mods)  # outside every zone: add the pixel (or start a polygon)
                elif inside_selected or self.tool == "polygon-":
                    self._pixel_action(x, y, mods)      # an erase tool on the selected zone: erase the pixel
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
    stroke_started = Signal(object)         # aurora.core.zones.Stroke
    stroke_changed = Signal(object)
    stroke_finished = Signal(object)
    stroke_cancelled = Signal()
    double_clicked = Signal(float, float)

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
        self._bar_words = False                                   # Easy: 'low' / 'high' at the ends, no numbers
        from aurora.app.mode import on_mode_changed
        on_mode_changed(self, lambda adv: self.set_bar_words(not adv))
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
        self.vb.stroke_started.connect(self.stroke_started)
        self.vb.stroke_changed.connect(self.stroke_changed)
        self.vb.stroke_finished.connect(self.stroke_finished)
        self.vb.stroke_cancelled.connect(self.stroke_cancelled)
        self.vb.double_clicked.connect(self.double_clicked)
        self._member_sets: dict[int, set] = {}                    # zone id -> members on the points (zone_at)
        self._stroke_items: dict[str, pg.ScatterPlotItem] = {}    # 'add' / 'remove': the stroke's provisional points
        self._footprint = QGraphicsRectItem()                     # the brush square under the pointer
        self._footprint.setZValue(58)
        self._footprint.hide()
        self.plot.addItem(self._footprint, ignoreBounds=True)
        self._footprint_n = 0                                     # 0: no brush footprint
        self._footprint_erase = False
        self.scene().sigMouseMoved.connect(self._moved)
        self._hint = QLabel(self)                                # the map hint, over the top-left of the map
        self._hint.setObjectName("maphint")
        self._hint.setWordWrap(True)
        self._hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._hint.hide()

    # ------------------------------------------------------------------ tools
    def set_tool(self, tool: str) -> None:
        if tool != self.vb.tool:
            self.vb.cancel_path()
        self.vb.tool = tool
        self.vb.setMouseEnabled(x=tool == "navigate", y=tool == "navigate")
        shape = tool.rstrip("+-")
        if shape == "navigate":
            self.vb.unsetCursor()
        elif shape == "brush" and self.zone_tools:
            self.vb.setCursor(Qt.BlankCursor)               # the brush square is the cursor
        else:
            self.vb.setCursor(Qt.CrossCursor)
        if shape != "brush":
            self.set_brush_footprint(0)

    def set_zone_tools(self, on: bool) -> None:
        """Zone-aware clicks, stray-drag filter and right-click menus (the Reduce zone editor)."""
        self.zone_tools = bool(on)
        self.context_menus = bool(on)

    def set_context_menus(self, on: bool) -> None:
        """Right-clicks emit context_requested(x, y, zone id or None) without the other zone rules (e.g. Survey)."""
        self.context_menus = bool(on)

    def set_points(self, x, y, pitch) -> None:
        """The stack's pixel centres and pitch (mm): hit tests use the members, drags are measured in pitches."""
        self._member_sets = {}
        if x is None:
            self._px = self._py = None
            self.pitch = None
            self.clear_stroke_preview()
            self._footprint.hide()
            return
        self._px, self._py = np.asarray(x, float), np.asarray(y, float)
        self.pitch = (float(pitch[0]), float(pitch[1]))

    def set_brush_size(self, n: int) -> None:
        """The brush covers n x n points (a stroke takes the size at its start)."""
        self.vb.brush_n = max(1, int(n))

    def cancel_drawing(self) -> bool:
        """Cancel the stroke in progress (rectangle, brush, lasso or polygon); True when there was one."""
        return self.vb.cancel_stroke()

    def stroke_emitting(self) -> bool:
        """True while rect_drawn / brush_at / polygon_drawn are emitted on behalf of a stroke (a zone editor that
        handles stroke_finished ignores them then)."""
        return self.vb.emitting()

    @staticmethod
    def _cancels(ev) -> bool:
        return ev.key() == Qt.Key_Escape or ev.matches(QKeySequence.Undo)

    def event(self, ev):
        # Esc (or Ctrl+Z) during a stroke cancels it before any window shortcut (Cancel the running task, Undo) sees it
        if ev.type() == QEvent.ShortcutOverride and self.vb.drawing() and self._cancels(ev):
            ev.accept()
            return True
        return super().event(ev)

    def keyPressEvent(self, ev) -> None:
        if self.vb.drawing() and self._cancels(ev) and self.vb.cancel_stroke():
            ev.accept()
            return
        super().keyPressEvent(ev)

    # ------------------------------------------------------------------ hint
    def set_hint(self, text: str, idle: bool = False) -> None:
        """A short sentence over the top-left of the map (what a stroke will do, why it wrote nothing); hidden when
        empty. idle=True (what a drag does, between strokes): shown only while the pointer is over the map, so it
        never sits on a small map's data. Screen readers get it as the map's accessible description."""
        text = text or ""
        self._hint_idle = bool(idle)
        show = bool(text) and (not idle or self.underMouse())
        if text == self._hint.text() and self._hint.isVisible() == show:
            return
        self._hint.setText(text)
        self.setAccessibleDescription(text)
        if not show:
            self._hint.hide()
            return
        bg = with_alpha(TOKENS["map_bg"], 0.8)
        self._hint.setStyleSheet(f"QLabel#maphint {{ background: rgba({bg.red()}, {bg.green()}, {bg.blue()}, "
                                 f"{bg.alpha()}); color: {TOKENS['map_fg']}; padding: 3px 6px; border-radius: 4px; }}")
        self._place_hint()
        self._hint.show()
        self._hint.raise_()

    def hint_text(self) -> str:
        return self._hint.text()

    def enterEvent(self, ev) -> None:
        super().enterEvent(ev)
        if getattr(self, "_hint_idle", False) and self._hint.text() and not self._hint.isVisible():
            self.set_hint(self._hint.text(), idle=True)

    def _place_hint(self) -> None:
        """One line when the sentence fits in 60 % of the map (at least 280 px on a narrow map), else wrapped at that
        width; QLabel's own word-wrap sizing broke even 'new zone 1: 12 points' over two lines."""
        lab = self._hint
        w = max(120, int(0.6 * self.width()), min(self.width() - 16, 280))     # a narrow map: fewer lines
        lab.setMaximumWidth(16777215)
        lab.setWordWrap(False)
        one = lab.sizeHint()
        if one.width() <= w:
            lab.resize(one)
        else:
            lab.setWordWrap(True)
            lab.resize(w, lab.heightForWidth(w))
        vp = self.viewport().geometry()
        lab.move(vp.left() + 8, vp.top() + 8)

    def hint_lines(self) -> int:
        """The number of text lines the map hint takes (0 when hidden)."""
        lab = self._hint
        if not lab.isVisible() or not lab.text():
            return 0
        line = lab.fontMetrics().lineSpacing()
        inner = lab.contentsRect().height()
        return max(1, int(round(inner / line))) if line else 1

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        hint = getattr(self, "_hint", None)              # pyqtgraph resizes the view inside its own __init__
        if hint is not None and hint.isVisible():
            self._place_hint()

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
        out = out or any(bool(polygon_inside(poly, np.array([x]), np.array([y]))[0]) for poly in z.epolys)
        return not out

    def zone_at(self, x: float, y: float) -> int | None:
        """The id of the drawn zone under (x, y) mm: on the stack, the zones whose members include the pixel under the
        point, else the zones whose drawing contains it; the smallest one (fewest members, then smallest box) wins."""
        from aurora.core.zones import members, zone_bbox
        i = self._pixel_at(x, y)
        cands = []
        for z in self._zones:
            if i is not None:
                m = self._member_sets.get(id(z))
                if m is None:
                    m = self._member_sets[id(z)] = set(members(z, self._px, self._py, self.pitch).tolist())
                if i in m:
                    bb = zone_bbox(z)
                    cands.append((len(m), (bb[1] - bb[0]) * (bb[3] - bb[2]) if bb else 0.0, -z.id, z.id))
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

    def set_bar_words(self, on: bool) -> None:
        """The colour scale's end labels: 'low' and 'high' (Easy mode: what a newcomer needs to read the colour), or the
        numeric range (Advanced mode, a map whose colours are a fixed lookup)."""
        self._bar_words = bool(on)
        self._paint_bar_ticks()

    def bar_tick_labels(self) -> list[str]:
        """The texts at the colour scale's ticks when they are fixed ('low', 'high'); [] for the numeric ticks."""
        ticks = getattr(self.bar.getAxis("right"), "_tickLevels", None)
        return [t for level in (ticks or []) for _v, t in level]

    def _paint_bar_ticks(self) -> None:
        ax = self.bar.getAxis("right")
        if self._bar_words and not getattr(self, "_fixed_lut", False):
            lo, hi = (float(v) for v in self.bar.levels())
            ax.setTicks([[(lo, "low"), (hi, "high")]])
        else:
            ax.setTicks(None)

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
        if self._bar_words:
            self._paint_bar_ticks()
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
        self._member_sets = {}
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
                    casing: bool = True, dashed: bool = False) -> QGraphicsPolygonItem:
        """An outlined polygon [[x, y], ...] in mm over its casing (dashed: an erase outline)."""
        poly = QPolygonF([QPointF(float(p[0]), float(p[1])) for p in pts])
        if casing:
            path = QPainterPath()
            path.addPolygon(poly)
            path.closeSubpath()
            self.add_casing(path, colour, width, z)
        it = QGraphicsPolygonItem(poly)
        pen = QPen(QColor(colour), width)
        pen.setCosmetic(True)
        if dashed:
            pen.setStyle(Qt.DashLine)
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
        self.add_casing(_union(zone.erects, zone.epolys, zone.erase), erase, 1.0)
        for r in zone.rects:
            self.add_rect(r[0], r[1], r[2], r[3], colour, w, fill=with_alpha(colour, 0.19) if selected else None,
                          casing=False)
        for poly in zone.polys:
            if len(poly) >= 3:
                self.add_polygon(poly, colour, w, fill=with_alpha(colour, 0.19) if selected else None, casing=False)
        for r in zone.erects:
            self.add_rect(r[0], r[1], r[2], r[3], erase, 1.0, dashed=True, casing=False)
        for poly in zone.epolys:
            if len(poly) >= 3:
                self.add_polygon(poly, erase, 1.0, casing=False, dashed=True)
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
        """Outline every pixel square (used to show the members of a zone on the stack map) as one scatter item of
        pitch-sized squares, however many pixels; no casing, which would draw a dark grid over the pixels."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        if x.size == 0:
            return
        it = pg.ScatterPlotItem(x=x, y=y, symbol=_cell_symbol(pitch), size=max(pitch), pxMode=False,
                                pen=pg.mkPen(colour, width=0.8), brush=pg.mkBrush(0, 0, 0, 0))
        it.setZValue(5)
        self.plot.addItem(it, ignoreBounds=True)
        self._overlays.append(it)

    # ------------------------------------------------------------------ stroke feedback
    def show_stroke_preview(self, add_xy, remove_xy, pitch, colour: str) -> None:
        """The points a stroke in progress would add (filled in the target zone's colour) and remove (crossed in the
        erase colour): one scatter item per kind, updated with setData."""
        from aurora.app.theme import MAP_OVERLAY
        if pitch is None:
            self.clear_stroke_preview()
            return
        size = 0.9 * min(pitch)
        for kind, xy in (("add", add_xy), ("remove", remove_xy)):
            it = self._stroke_items.get(kind)
            if it is None:
                it = pg.ScatterPlotItem(pxMode=False)
                it.setZValue(57)
                self.plot.addItem(it, ignoreBounds=True)
                self._stroke_items[kind] = it
            x, y = (np.asarray(xy[0], float), np.asarray(xy[1], float)) if xy is not None else (np.zeros(0), np.zeros(0))
            if kind == "add":
                it.setData(x=x, y=y, symbol="s", size=size, pen=pg.mkPen(colour, width=1.2),
                           brush=pg.mkBrush(with_alpha(colour, 0.45)))
            else:
                it.setData(x=x, y=y, symbol="x", size=size, pen=pg.mkPen(MAP_OVERLAY["erase"], width=1.2),
                           brush=pg.mkBrush(MAP_OVERLAY["erase"]))
            it.setVisible(bool(x.size))

    def clear_stroke_preview(self) -> None:
        for it in self._stroke_items.values():
            it.setData(x=[], y=[])
            it.setVisible(False)

    def stroke_preview_counts(self) -> tuple[int, int]:
        """(points shown as added, points shown as removed) of the stroke preview."""
        out = []
        for kind in ("add", "remove"):
            it = self._stroke_items.get(kind)
            out.append(len(it.data) if it is not None and it.isVisible() else 0)
        return out[0], out[1]

    def set_brush_footprint(self, n: int, erase: bool = False) -> None:
        """Show the n x n brush square under the pointer (0 hides it), in the add or the erase colour."""
        from aurora.app.theme import MAP_OVERLAY
        self._footprint_n = max(0, int(n))
        self._footprint_erase = bool(erase)
        if not self._footprint_n:
            self._footprint.hide()
            return
        pen = QPen(QColor(MAP_OVERLAY["erase"] if erase else MAP_OVERLAY["rect_add"]), 1.5, Qt.DashLine)
        pen.setCosmetic(True)
        self._footprint.setPen(pen)

    def footprint_rect(self) -> QRectF | None:
        """The brush square shown under the pointer (mm), None when hidden."""
        return self._footprint.rect() if self._footprint.isVisible() else None

    def _move_footprint(self, x: float, y: float) -> None:
        n = self._footprint_n
        if not n or self.pitch is None or self._px is None or self._px.size == 0:
            self._footprint.hide()
            return
        from aurora.core.zones import brush_squares
        sq = brush_squares(self._px, self._py, self.pitch, x, y, n)
        if not sq:                                   # out of the brush's reach: nothing would be painted
            self._footprint.hide()
            return
        px, py = self.pitch
        xs, ys = [s[0] for s in sq], [s[1] for s in sq]
        self._footprint.setRect(QRectF(min(xs) - px / 2, min(ys) - py / 2, max(xs) - min(xs) + px, max(ys) - min(ys) + py))
        self._footprint.show()

    def _moved(self, pos) -> None:
        if self.plot.sceneBoundingRect().contains(pos):
            p = self.vb.mapSceneToView(pos)
            self.vb.hover(float(p.x()), float(p.y()))
            self.hovered.emit(float(p.x()), float(p.y()))

    def leaveEvent(self, ev) -> None:
        self._footprint.hide()
        if getattr(self, "_hint_idle", False):
            self._hint.hide()
        super().leaveEvent(ev)


def _cell_symbol(pitch) -> QPainterPath:
    """A pixel square of a pitch (px, py) as a scatter symbol: pyqtgraph scales a symbol by `size` (here max(pitch)),
    so the path is the cell in units of that size, its sides in the pitch's own ratio."""
    px, py = float(pitch[0]), float(pitch[1])
    s = max(px, py) or 1.0
    path = QPainterPath()
    path.addRect(QRectF(-0.5 * px / s, -0.5 * py / s, px / s, py / s))
    return path


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
