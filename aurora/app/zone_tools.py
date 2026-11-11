"""Zone editing on a map: one ZoneEditor per zone map (Reduce's stack map, the Survey plate map), the ZoneToolBar
both maps share, the PreviewThrottle of their live previews, their keyboard shortcuts and the shared zone menu.

The map (aurora.app.mapview.MapView) turns every gesture into an aurora.core.zones.Stroke. The editor:
- picks the zone the stroke goes to (target_for). An Erase stroke goes to the selected zone, or else to the zone under
  its start (which then becomes selected); with no zone there it does nothing. A Draw stroke starts a new zone when no
  zone is selected or when 'every stroke starts a new zone' is on (new_each); otherwise it goes to the selected zone,
  except a stroke that neither overlaps nor touches (within TOUCH_PITCH) the selected zone's points on this map: that
  one starts a new zone ("draw one sample, then the next"), unless Ctrl is held at release (force_add: a sample in
  two parts);
- keeps the provisional result while the stroke moves (draft(), a ZoneDraft: the points before and after, the points
  added or removed), without touching the session, and says on the map what the release will do (MapView.set_hint):
  "adds 12 points to zone 3 'NaF'", "new zone 4: 12 points", "erases 5 points from zone 3 'NaF'";
- writes the stroke at release in one session.edit(..., merge=False) (commit): one stroke is one undo step, however
  many mouse events it took and however quickly the next one follows. A stroke that would write nothing is not
  written (a Draw stroke over no measured point of the map, an Erase stroke that removes nothing), and the hint and
  the status line (message) say why. No stroke ever creates an empty zone.

While a stroke moves, the map shows the points it would add (in the target zone's colour) or remove (crossed), and
between strokes the hint says what a drag does with the tool (idle_hint). On the Survey (thousands of points) the
feedback is coalesced to one update every FEEDBACK_MS.

rect_drawn / brush_at / polygon_drawn emitted by code rather than by a stroke (scripts, the self-test, older tests)
still edit the zone as they always did (commit(..., legacy=True)): the tool's mode, the old target rule, one edit per
signal merged by time and description.
"""
from __future__ import annotations

import re
from functools import cached_property

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QWidget

from aurora.app.mapview import SHAPES, split_tool, tool_name
from aurora.app.vocab import plural
from aurora.core.io.config import Zone
from aurora.core.zones import Stroke, apply_stroke, brush_squares, members, stroke_covers, stroke_zone

TOUCH_PITCH = 1.0               # a Draw stroke within this many pitches of the selected zone's points adds to it
TOUCH_TOL = 1.25                # ... measured with this slack (jittered stage positions)
FEEDBACK_MS = 50                # the Survey's stroke feedback at most this often (thousands of points)
NO_PROJECT = "open a project first"
IDLE_DRAW = "Drag to draw a zone · right-drag erases · Ctrl+Z undoes"
IDLE_ERASE = "Drag over the selected zone to erase · right-drag draws"
IDLE_POLYGON = "Click the corners of the zone · double-click closes it · Esc cancels"
IDLE_POLYGON_ERASE = "Click the corners of the part to erase · double-click closes it · Esc cancels"
_DEFAULT_LABEL = re.compile(r"^zone \d+$")


def zone_name(z: Zone) -> str:
    """"zone 3 'NaF'", or "zone 3" when the zone still has its default label."""
    label = (z.label or "").strip()
    return f"zone {z.id}" if not label or _DEFAULT_LABEL.match(label) else f"zone {z.id} '{label}'"


def description(stroke: Stroke, zid: int | None) -> str:
    """The undo text of a stroke: 'add a rectangle to zone 3', 'erase pixels in zone 3', 'add a polygon to a new
    zone', ... (the texts of earlier versions, so Edit > Undo reads the same)."""
    where = f"zone {zid}" if zid is not None else "a new zone"
    shape = stroke.verb_shape
    if shape == "rect":
        return f"remove a rectangle from {where}" if stroke.erase else f"add a rectangle to {where}"
    if shape == "brush":
        return f"{'erase' if stroke.erase else 'paint'} pixels in {where}"
    return f"erase a polygon from {where}" if stroke.erase else f"add a polygon to {where}"


class ZoneDraft:
    """What a stroke in progress would do: the target zone (`target_id`, None for a new zone; `new`), the map points
    the zone holds before and after it (`members_before`, `members_after`, indices into the map's points), those it
    adds or removes, the points the stroke's area covers, `reason` when the release would write nothing, and `hint`,
    the sentence shown on the map. `zone` is the provisional zone (a geometry copy with the stroke applied), built on
    first use."""

    def __init__(self, stroke: Stroke, target: Zone | None, new: bool, before: np.ndarray, after: np.ndarray,
                 covered: np.ndarray, points, reason: str = "", hint: str = ""):
        self.stroke = stroke
        self.target = target
        self.target_id = target.id if target is not None else None
        self.new = bool(new)
        self.erase = bool(stroke.erase)
        self.members_before = np.asarray(before, int)
        self.members_after = np.asarray(after, int)
        self.added = np.setdiff1d(self.members_after, self.members_before)
        self.removed = np.setdiff1d(self.members_before, self.members_after)
        self.covered = np.asarray(covered, int)
        self._points = points
        self.reason = reason
        self.hint = hint or reason

    @property
    def empty(self) -> bool:
        """True when the release would write nothing."""
        return bool(self.reason)

    @cached_property
    def zone(self) -> Zone:
        x, y, pitch = (self._points[:3] if self._points else (None, None, None))
        return stroke_zone(self.target, self.stroke, x, y, pitch)


class ZoneEditor(QObject):
    """The zone editor of one map. `points()` returns (x, y, pitch, label) of the points the map shows (a stack's
    pixels and its name, the survey points and the survey's name) or None; `where` is 'Reduce' or 'Survey' (the
    texts). new_each() is the Advanced 'every stroke starts a new zone'; enabled() False ignores the strokes (a map in
    another mode, Survey's Grid of zones)."""
    draft_changed = Signal(object)              # ZoneDraft, or None when no stroke is in progress
    committed = Signal(int)                     # the id of the zone a stroke was written to
    message = Signal(str)                       # a status sentence: why a stroke wrote nothing

    def __init__(self, session, map_view, points, where: str = "Reduce", parent: QObject | None = None):
        super().__init__(parent if parent is not None else map_view)
        self.session = session
        self.map = map_view
        self.points = points
        self.where = where
        self.new_each = lambda: False
        self.enabled = lambda: True
        self.throttle = None                    # the perspective's PreviewThrottle: its 'release' state shows in the hint
        self.feedback_ms = 0                    # > 0: stroke feedback coalesced (the Survey's survey points)
        self.last_committed = False             # the last stroke that ended was written
        self._pending: Stroke | None = None
        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self._feedback_due)
        self._draft: ZoneDraft | None = None
        self._serial = -1                       # the stroke the caches below belong to
        self._before: dict = {}                 # target id -> members before (this stroke)
        self._brush_mask: np.ndarray | None = None
        self._brush_done = 0
        self._near: dict = {}                   # target id -> (member coordinates in pitches, bbox)
        map_view.stroke_started.connect(self._started)
        map_view.stroke_changed.connect(self._changed)
        map_view.stroke_finished.connect(self._finished)
        map_view.stroke_cancelled.connect(self._cancelled)

    # ------------------------------------------------------------------ state
    def draft(self) -> ZoneDraft | None:
        """The stroke in progress as it would be written now (None when none)."""
        return self._draft

    def idle(self) -> None:
        """No stroke on the map (a tool, scan or holder change): a stroke in progress is cancelled (its points were
        those of the map before the change), the stroke preview goes, the hint says what a drag does with the tool."""
        self.map.cancel_drawing()
        self._draft = None
        self._pending = None
        self._feedback_timer.stop()
        self.map.clear_stroke_preview()
        self.map.set_hint(self.idle_hint(), idle=True)

    def set_tool(self, tool: str, brush_n: int = 1) -> None:
        """Give the map a tool (a stroke in progress is cancelled), with the brush square for the brush, and the idle
        hint of the tool."""
        self.map.set_tool(tool)
        self.set_brush(brush_n)
        self.idle()

    def set_brush(self, n: int) -> None:
        """The brush size (n x n points) of the next stroke, and of the square under the pointer."""
        self.map.set_brush_size(n)
        shape, erase = split_tool(self.map.vb.tool)
        self.map.set_brush_footprint(int(n) if shape == "brush" and self.map.zone_tools else 0, erase)

    def idle_hint(self) -> str:
        """idle_hint(tool) on a map with points, '' otherwise (no scan shown, a disabled editor)."""
        if not self.enabled() or self._points() is None:
            return ""
        return idle_hint(self.map.vb.tool)

    def colour_of(self, zid: int | None) -> str:
        """The colour the maps draw a zone in (theme.curve of its place in the holder); the next one for a new zone."""
        from aurora.app.theme import curve
        hz = self.session.holder_zones(create=False) if self.session.project is not None else None
        zones = hz.zones if hz else []
        for i, z in enumerate(zones):
            if z.id == zid:
                return curve(i)
        return curve(len(zones))

    def _hint_of(self, d: ZoneDraft) -> str:
        hint = d.hint
        if self.throttle is not None and getattr(self.throttle, "state", "") == "release" and not d.empty:
            hint += f" · {SLOW_HINT}"
        return hint

    def refresh_hint(self, *_a) -> None:
        """The hint of the stroke in progress again (the throttle went to 'release')."""
        if self._draft is not None:
            self.map.set_hint(self._hint_of(self._draft))

    def _show_feedback(self, d: ZoneDraft) -> None:
        pts = d._points
        if pts is None:
            self.map.clear_stroke_preview()
            return
        x, y, pitch = pts[:3]
        add, rem = (d.added, d.removed) if not d.empty else (np.zeros(0, int), np.zeros(0, int))
        self.map.show_stroke_preview((x[add], y[add]), (x[rem], y[rem]), pitch,
                                     self.colour_of(None if d.new else d.target_id))

    def _points(self):
        try:
            pts = self.points()
        except Exception:                       # noqa: BLE001  (a perspective being torn down)
            return None
        if pts is None or pts[0] is None or len(pts[0]) == 0:
            return None
        return pts

    def _reset(self, serial: int) -> None:
        if serial != self._serial:
            self._serial = serial
            self._before, self._near = {}, {}
            self._brush_mask, self._brush_done = None, 0

    # ------------------------------------------------------------------ target
    def _members_before(self, z: Zone | None, pts) -> np.ndarray:
        if z is None or pts is None:
            return np.zeros(0, int)
        if z.id not in self._before:
            x, y, pitch = pts[:3]
            self._before[z.id] = members(z, x, y, pitch)
        return self._before[z.id]

    def _apart(self, z: Zone, covered: np.ndarray, pts) -> bool:
        """Z13: True when the stroke's points neither overlap nor touch (within TOUCH_PITCH) the zone's points on this
        map. A zone without a point on this map is never 'apart' (an empty zone from New sample, a zone drawn on
        another scan: the stroke goes to it)."""
        if pts is None or covered.size == 0:
            return False
        mem = self._members_before(z, pts)
        if mem.size == 0:
            return False
        if np.intersect1d(covered, mem, assume_unique=False).size:
            return False
        x, y, pitch = pts[:3]
        px, py = float(pitch[0]), float(pitch[1])
        reach = TOUCH_PITCH * TOUCH_TOL
        cx, cy = x[covered] / px, y[covered] / py
        mx, my = x[mem] / px, y[mem] / py
        near = (mx >= cx.min() - reach) & (mx <= cx.max() + reach) & (my >= cy.min() - reach) & (my <= cy.max() + reach)
        mx, my = mx[near], my[near]
        if mx.size == 0:
            return True
        step = max(1, int(2_000_000 // max(mx.size, 1)))
        for i in range(0, cx.size, step):           # Chebyshev distance in pitches, in chunks
            d = np.maximum(np.abs(cx[i:i + step, None] - mx[None, :]), np.abs(cy[i:i + step, None] - my[None, :]))
            if (d <= reach).any():
                return False
        return True

    def target_for(self, stroke: Stroke, covered: np.ndarray | None = None) -> tuple[Zone | None, bool, str]:
        """(zone, new, reason): the zone the stroke goes to (None with new=True: a new zone), or a reason why it goes
        nowhere. `covered`: the map points the stroke's area holds (Z13 needs them; without them the selected zone)."""
        ses = self.session
        sel = ses.zone() if ses.project is not None else None
        if stroke.erase:
            if sel is not None:
                return sel, False, ""
            zid = self.map.zone_at(*stroke.start)
            z = ses.zone(zid) if zid is not None else None
            if z is None:
                return None, False, "nothing to erase here: no zone under the pointer"
            return z, False, ""
        if sel is None or self.new_each():
            return None, True, ""
        if stroke.force_add or covered is None:
            return sel, False, ""
        if self._apart(sel, np.asarray(covered, int), self._points()):
            return None, True, ""
        return sel, False, ""

    # ------------------------------------------------------------------ the draft
    def _covered(self, stroke: Stroke, pts) -> np.ndarray:
        """The map points the stroke's area holds; a brush stroke is followed incrementally (only the new centres)."""
        if pts is None:
            return np.zeros(0, int)
        x, y, pitch = pts[:3]
        if stroke.verb_shape != "brush":
            return stroke_covers(stroke, x, y, pitch)
        if self._brush_mask is None:
            self._brush_mask = np.zeros(len(x), bool)
            self._brush_done = 0
        centres = stroke.path or [list(stroke.start)]
        from aurora.core.zones import PAINT_TOL
        for cx, cy in centres[self._brush_done:]:
            for s in brush_squares(x, y, pitch, cx, cy, stroke.brush_n):
                self._brush_mask |= (np.abs(x - s[0]) <= s[2] * PAINT_TOL) & (np.abs(y - s[1]) <= s[3] * PAINT_TOL)
        self._brush_done = len(centres)
        return np.where(self._brush_mask)[0]

    def _next_id(self) -> int:
        hz = self.session.holder_zones(create=False)
        return max((z.id for z in (hz.zones if hz else [])), default=0) + 1

    def _empty_draw_reason(self, pts) -> str:
        if self.where == "Survey":
            return "no measured point there: draw over the coloured map"
        if pts is None:
            return "no scan shown: pick a scan in step 1, then draw over its pixels"
        return f"outside scan {pts[3]}: draw over its pixels"

    def make_draft(self, stroke: Stroke) -> ZoneDraft:
        """The draft of a stroke (also used at release, so the hint and the commit always agree)."""
        self._reset(stroke.serial)
        pts = self._points()
        covered = self._covered(stroke, pts)
        target, new, reason = self.target_for(stroke, covered)
        sel = self.session.zone() if self.session.project is not None else None
        before = self._members_before(target, pts)
        if stroke.erase:
            after = np.setdiff1d(before, covered)
        else:
            after = np.union1d(before, covered)
        hint = ""
        if not reason:
            if stroke.erase:
                n = int(before.size - after.size)
                if n == 0:
                    reason = f"nothing of {zone_name(target)} there to erase"
                else:
                    hint = f"erases {plural(n, 'point')} from {zone_name(target)}"
            elif covered.size == 0:
                reason = self._empty_draw_reason(pts)
            elif new:
                hint = f"new zone {self._next_id()}: {plural(int(covered.size), 'point')}"
                if sel is not None and not self.new_each():
                    hint += f" (hold Ctrl to add it to {zone_name(sel)})"
            else:
                n = int(after.size - before.size)
                hint = (f"adds {plural(n, 'point')} to {zone_name(target)}" if n else
                        f"{zone_name(target)} already holds these {plural(int(covered.size), 'point')}")
        return ZoneDraft(stroke, target, new, before, after, covered, pts, reason=reason, hint=hint)

    def _started(self, stroke: Stroke) -> None:
        if not self.enabled():
            return
        self._reset(stroke.serial)
        self._draft = None
        self.map.set_hint("")

    def _changed(self, stroke: Stroke) -> None:
        if not self.enabled():
            return
        from PySide6.QtWidgets import QApplication
        stroke.force_add = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)    # the hint follows Ctrl
        if self.feedback_ms > 0:                # coalesced: the latest stroke state, at most every feedback_ms
            self._pending = stroke
            if not self._feedback_timer.isActive():
                self._feedback_timer.start(self.feedback_ms)
            return
        self._update_draft(stroke)

    def _feedback_due(self) -> None:
        st, self._pending = self._pending, None
        if st is not None and not st.finished and self.map.vb.stroke() is st:
            self._update_draft(st)

    def _update_draft(self, stroke: Stroke) -> None:
        self._draft = self.make_draft(stroke)
        self.map.set_hint(self._hint_of(self._draft))
        self._show_feedback(self._draft)
        self.draft_changed.emit(self._draft)

    def _cancelled(self) -> None:
        self._pending = None
        self._feedback_timer.stop()
        self.map.clear_stroke_preview()
        if self._draft is not None or self.map.hint_text() != self.idle_hint():
            self._draft = None
            self.map.set_hint(self.idle_hint(), idle=True)
            self.draft_changed.emit(None)
        self._serial = -1

    def _finished(self, stroke: Stroke) -> None:
        if not self.enabled():
            return
        self._pending = None
        self._feedback_timer.stop()
        self._draft = None
        self.map.clear_stroke_preview()
        self.last_committed = self.commit(stroke) is not None
        self.draft_changed.emit(None)
        self._serial = -1

    # ------------------------------------------------------------------ the commit
    def _say(self, text: str) -> None:
        self.map.set_hint(text)
        self.message.emit(text)

    def commit(self, stroke: Stroke, legacy: bool = False) -> int | None:
        """Write the stroke: one session.edit (merge=False) creating the zone when the target is new, applying the
        stroke (aurora.core.zones.apply_stroke) and selecting the zone. Returns the zone id, None when nothing was
        written (and the hint and message say why). legacy=True: a legacy signal emitted by code (see the module
        docstring)."""
        ses = self.session
        if ses.project is None or ses.holder() is None:
            self.message.emit(NO_PROJECT)
            return None
        pts = self._points()
        x, y, pitch = pts[:3] if pts is not None else (None, None, None)
        shape = stroke.verb_shape
        if legacy:
            if shape == "brush" and pts is None:
                return None                     # a brush needs the points it snaps to
            if shape == "polygon" and len(stroke.path) < 3:
                return None
            sel = ses.zone()
            if stroke.erase:
                if sel is None:
                    self.message.emit("select a zone first")
                    return None
                target, new = sel, False
            else:
                new = sel is None or self.new_each()
                target = None if new else sel
        else:
            self._reset(stroke.serial)
            d = self.make_draft(stroke)
            self._serial = -1                   # the caches of this stroke end here: the zone is about to change
            if d.empty:
                self._say(d.reason)
                return None
            target, new = d.target, d.new
        if target is not None and target.id != ses.current_zone:
            ses.select_zone(target.id)          # an Erase stroke on the zone under it selects that zone
        kw = {} if legacy else {"merge": False}
        z = None
        with ses.edit(description(stroke, None if new else target.id), **kw):
            z = ses.new_zone() if new else target
            if z is not None:
                apply_stroke(z, stroke, x, y, pitch)
        if z is None:
            return None
        if not legacy:
            self.map.set_hint(self.idle_hint(), idle=True)
        self.committed.emit(z.id)
        return z.id


# ====================================================================== the tool bar (Survey and Reduce)
SHAPE_TEXT = {"navigate": "Move", "rect": "Rectangle", "brush": "Brush", "lasso": "Lasso", "polygon": "Polygon"}
SHAPE_ICON = {"navigate": "move", "rect": "rect", "brush": "brush", "lasso": "lasso", "polygon": "polygon"}
SHAPE_HELP = {"navigate": "zones.move", "rect": "zones.rect", "brush": "zones.brush", "lasso": "zones.lasso",
              "polygon": "zones.polygon"}
NEW_EACH_TEXT = "every stroke starts a new zone"


class ZoneToolBar(QWidget):
    """The zone tools of a map, the same on the Survey and in Reduce: [Draw | Erase], [Move] [Rectangle] [Brush]
    [Lasso] (+ [Polygon] in Advanced), the brush size (only while Brush is the shape), Undo, Clear, and in Advanced
    'every stroke starts a new zone'. A tool is a shape and a mode (mapview.tool_name): tool() is the composite string
    ('rect+', 'brush-', 'navigate') the map takes. Picking Draw or Erase while Move is the shape goes back to the last
    drawing shape, so 'Draw, then drag' always draws. Clear is only a button here: each perspective connects it."""
    tool_changed = Signal(str)
    brush_changed = Signal(int)

    def __init__(self, session, default: str = "rect+", parent: QWidget | None = None):
        super().__init__(parent)
        from PySide6.QtWidgets import QButtonGroup, QCheckBox, QPushButton, QSpinBox, QToolButton
        from aurora.app.help_text import explain
        from aurora.app.mode import mark_advanced, on_mode_changed
        from aurora.app.theme import on_theme_changed
        from aurora.app.vocab import unit_suffix
        from aurora.app.widgets import FlowLayout
        self.session = session
        self.setObjectName("zonetools")
        lay = FlowLayout(self, spacing=6)
        lay.setContentsMargins(0, 0, 0, 0)
        shape, erase = split_tool(default)
        self._shape, self._erase = shape, erase
        self._last_shape = shape if shape != "navigate" else "rect"
        self._icons: list[tuple[QToolButton, str]] = []

        def tool_button(text: str, icon: str, key: str) -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            explain(b, key)
            self._icons.append((b, icon))
            return b

        self.draw_btn = tool_button("Draw", "draw", "zones.draw")
        self.erase_btn = tool_button("Erase", "erase", "zones.erase")
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        for b in (self.draw_btn, self.erase_btn):
            self.mode_group.addButton(b)
            lay.addWidget(b)
        self.draw_btn.clicked.connect(lambda: self.set_erase(False))
        self.erase_btn.clicked.connect(lambda: self.set_erase(True))
        self.shape_group = QButtonGroup(self)
        self.shape_group.setExclusive(True)
        self.shape_buttons: dict[str, QToolButton] = {}
        for s in SHAPES:
            b = tool_button(SHAPE_TEXT[s], SHAPE_ICON[s], SHAPE_HELP[s])
            self.shape_group.addButton(b)
            self.shape_buttons[s] = b
            lay.addWidget(b)
            b.clicked.connect(lambda _c=False, ss=s: self.set_shape(ss))
        self.brush_size = QSpinBox()
        self.brush_size.setRange(1, 9)
        self.brush_size.setValue(1)
        self.brush_size.setSuffix(unit_suffix("pixels"))
        explain(self.brush_size, "zones.brush_size")
        self.brush_size.valueChanged.connect(self.brush_changed)
        lay.addWidget(self.brush_size)
        self.undo_btn = tool_button("Undo", "undo", "zones.undo")
        self.undo_btn.setCheckable(False)
        self.undo_btn.clicked.connect(lambda: self.session.undo())
        lay.addWidget(self.undo_btn)
        self.clear_btn = QPushButton("Clear")
        explain(self.clear_btn, "zones.clear")
        lay.addWidget(self.clear_btn)
        self.new_each = QCheckBox(NEW_EACH_TEXT)
        explain(self.new_each, "zones.new_each")
        lay.addWidget(self.new_each)
        stack = getattr(session, "undo_stack", None)
        if stack is not None:
            self.undo_btn.setEnabled(stack.canUndo())
            stack.canUndoChanged.connect(self.undo_btn.setEnabled)
            stack.undoTextChanged.connect(self._undo_tip)   # the step it undoes: one undo history for every edit
            self._undo_tip(stack.undoText())
        mark_advanced(self.shape_buttons["polygon"], self.new_each)
        self._show_state()
        self._paint_icons()
        on_theme_changed(self, lambda _s: self._paint_icons())
        on_mode_changed(self, self._mode_changed, call_now=False)

    # ------------------------------------------------------------------ state
    def tool(self) -> str:
        return tool_name(self._shape, self._erase)

    def shape(self) -> str:
        return self._shape

    def erase(self) -> bool:
        return self._erase

    def brush_n(self) -> int:
        return int(self.brush_size.value())

    def shape_button(self, shape: str):
        return self.shape_buttons[shape]

    def set_tool(self, tool: str) -> None:
        """Show a tool string ('lasso-', 'navigate'); emits tool_changed when it changes."""
        shape, erase = split_tool(tool)
        if shape == "navigate":
            erase = self._erase                         # Move keeps the mode shown
        self._apply(shape, erase)

    def set_shape(self, shape: str) -> None:
        """A shape, keeping the mode (R, B, L, P, V and the shape buttons)."""
        from aurora.app.mode import is_advanced
        if shape not in SHAPES or (shape == "polygon" and not is_advanced()):
            self._show_state()
            return
        self._apply(shape, self._erase)

    def set_erase(self, erase: bool) -> None:
        """Draw (False) or Erase (True); from Move, back to the last drawing shape."""
        shape = self._shape if self._shape != "navigate" else self._last_shape
        self._apply(shape, bool(erase))

    def toggle_erase(self) -> None:
        self.set_erase(not self._erase)

    def step_brush(self, d: int) -> None:
        self.brush_size.setValue(self.brush_size.value() + int(d))

    def _apply(self, shape: str, erase: bool) -> None:
        before = self.tool()
        self._shape, self._erase = shape, bool(erase)
        if shape != "navigate":
            self._last_shape = shape
        self._show_state()
        if self.tool() != before:
            self.tool_changed.emit(self.tool())

    def _show_state(self) -> None:
        for b, on in ((self.draw_btn, not self._erase), (self.erase_btn, self._erase)):
            if b.isChecked() != on:
                b.setChecked(on)
        b = self.shape_buttons.get(self._shape)
        if b is not None and not b.isChecked():
            b.setChecked(True)
        self.brush_size.setVisible(self._shape == "brush")

    def _mode_changed(self, adv: bool) -> None:
        if not adv and self._shape == "polygon":
            self.set_shape("rect")

    def _paint_icons(self) -> None:
        from aurora.app.icons import icon
        from aurora.app.theme import TOKENS
        for b, name in self._icons:
            b.setIcon(icon(name, TOKENS["ink"], 16))

    def _undo_tip(self, text: str) -> None:
        """Undo's tooltip names the step it undoes ('Undo: add a rectangle to zone 3 (Ctrl+Z)'), as Edit > Undo does:
        the history is shared with the settings and good-I0 edits, so the last step is not always a zone's."""
        from aurora.app.help_text import HELP
        from aurora.app.shortcuts import native_text
        tip = native_text(f"Undo: {text} (Ctrl+Z). One stroke is one step." if text else HELP["zones.undo"][0])
        self.undo_btn.setToolTip(tip)
        self.undo_btn.setProperty("aurora_tip", tip)
        self.undo_btn.setStatusTip(tip)


def idle_hint(tool: str) -> str:
    """The map hint while no stroke is in progress: what a drag does with this tool ('' in Move)."""
    from aurora.app.shortcuts import native_text
    shape, erase = split_tool(tool)
    if shape == "navigate":
        return ""
    if shape == "polygon":
        return IDLE_POLYGON_ERASE if erase else IDLE_POLYGON
    return native_text(IDLE_ERASE if erase else IDLE_DRAW)


# ====================================================================== the live preview, throttled
FIRST_MS = 60                   # the first preview of a stroke at most this long after it starts
INTERVAL_MS = 150               # then at most one every INTERVAL_MS while it changes
SLOW_MS = 120                   # a preview slower than this (EMA): the rest of the stroke without de-spike, then at release
EMA_ALPHA = 0.5
SLOW_HINT = "preview updates when you let go (large scan)"


class PreviewThrottle(QObject):
    """Runs run(draft, fast) for the drafts of a stroke: the first at most FIRST_MS after the stroke's first draft,
    then at most one every INTERVAL_MS, always with the latest draft (the ones in between are skipped). Each run is
    timed (an EMA): above SLOW_MS the next runs of the stroke get fast=True (no de-spike); still above, the preview
    waits for the release (state 'release'; the zone editor's hint says so). clock() is time.perf_counter (tests
    replace it)."""
    state_changed = Signal(str)                 # 'live' | 'fast' | 'release'

    def __init__(self, run, parent: QObject | None = None):
        super().__init__(parent)
        import time
        from PySide6.QtCore import QTimer
        self._run = run
        self.clock = time.perf_counter
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        self._latest = None
        self._serial = None
        self._last: float | None = None         # clock() of the last run of this stroke
        self.ema: float | None = None           # ms
        self.state = "live"
        self.runs = 0                           # previews run (all strokes), read by the tests

    def request(self, draft) -> None:
        if draft is None:
            self.cancel()
            return
        serial = getattr(getattr(draft, "stroke", None), "serial", None)
        if serial != self._serial:
            self._serial, self._last, self.ema = serial, None, None
            self._set_state("live")
        self._latest = draft
        if self.state == "release" or self._timer.isActive():
            return
        if self._last is None:
            delay = FIRST_MS
        else:
            delay = max(0.0, INTERVAL_MS - (self.clock() - self._last) * 1000.0)
        self._timer.start(int(round(delay)))

    def flush(self) -> None:
        """Run the pending draft now (nothing when none is pending)."""
        if self._timer.isActive():
            self._timer.stop()
            self._fire()

    def cancel(self) -> None:
        """The stroke ended or was cancelled: nothing pending, the next stroke starts live."""
        self._timer.stop()
        self._latest = None
        self._serial = None
        self._set_state("live")

    def pending(self) -> bool:
        return self._timer.isActive()

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)

    def _fire(self) -> None:
        d = self._latest
        if d is None or self.state == "release":
            return
        t0 = self.clock()
        self._last = t0
        try:
            self._run(d, self.state == "fast")
        finally:
            ms = (self.clock() - t0) * 1000.0
            self.runs += 1
            self.ema = ms if self.ema is None else EMA_ALPHA * ms + (1 - EMA_ALPHA) * self.ema
            if self.ema > SLOW_MS:
                self.ema = None                 # measured again in the next state
                self._set_state("fast" if self.state == "live" else "release")


def fast_settings(st):
    """A copy of ScanSettings without the de-spike (the live preview of a large scan)."""
    import copy
    out = copy.deepcopy(st)
    out.despike = False
    return out


# ====================================================================== shortcuts
def register_shortcuts(owner, bar: ZoneToolBar, section: str) -> dict:
    """The zone-tool keys of a perspective (Help > Keyboard shortcuts lists them under `section`); they work while
    the perspective is shown. R, B, L and P keep the mode; D, E and X set or switch it."""
    from aurora.app import shortcuts
    from aurora.app.mode import mark_advanced_action
    add = lambda text, key, fn: shortcuts.add(owner, text, key, fn, section=section)   # noqa: E731
    a = {
        "navigate": add("Move (pan and zoom)", "V", lambda: bar.set_shape("navigate")),
        "rect": add("Rectangle", "R", lambda: bar.set_shape("rect")),
        "brush": add("Brush", "B", lambda: bar.set_shape("brush")),
        "lasso": add("Lasso", "L", lambda: bar.set_shape("lasso")),
        "polygon": add("Polygon", "P", lambda: bar.set_shape("polygon")),
        "draw": add("Draw mode", "D", lambda: bar.set_erase(False)),
        "erase": add("Erase mode", "E", lambda: bar.set_erase(True)),
        "switch": add("Switch between Draw and Erase", "X", bar.toggle_erase),
        "brush_erase": add("Brush in Erase mode", "Shift+B", lambda: bar.set_tool("brush-")),
        "bigger": add("Larger brush", "+", lambda: bar.step_brush(1)),
        "smaller": add("Smaller brush", "-", lambda: bar.step_brush(-1)),
    }
    mark_advanced_action(owner, a["polygon"])
    return a


# ====================================================================== zone actions shared by the maps
GEOMETRY_MARK = "aurora_zone_geometry"           # the clipboard JSON of 'Copy geometry'


def clear_zone(session) -> int | None:
    """Clear the selected zone's drawing (every add and erase shape) in one edit; its id, None without a zone."""
    z = session.zone() if session.project is not None else None
    if z is None:
        return None
    with session.edit(f"clear zone {z.id}"):
        z.rects, z.erects, z.paint, z.erase, z.polys, z.epolys = [], [], [], [], [], []
    return z.id


def zone_drawn(z: Zone | None) -> bool:
    return z is not None and bool(z.rects or z.erects or z.paint or z.erase or z.polys or z.epolys)


def rename_zone(session, zid: int, label: str) -> bool:
    z = session.zone(zid)
    label = (label or "").strip()
    if z is None or not label or label == z.label:
        return False
    with session.edit(f"rename zone {zid}"):
        z.label = label
    return True


def duplicate_zone(session, zid: int) -> Zone | None:
    """A copy of the zone (drawing and settings) as a new zone, selected."""
    import copy as _copy
    from aurora.core.zones import geometry, set_geometry
    z = session.zone(zid)
    hz = session.holder_zones(create=False)
    if z is None or hz is None:
        return None
    nid = max((zz.id for zz in hz.zones), default=0) + 1
    with session.edit(f"duplicate zone {zid}"):
        dup = Zone(id=nid, label=f"{z.label} (copy)", group=z.group, comment=z.comment)
        set_geometry(dup, geometry(z))
        dup.settings = {sid: _copy.deepcopy(st) for sid, st in z.settings.items()}
        hz.zones.append(dup)
        session.current_zone = nid
    session.select_zone(nid)
    return dup


def geometry_text(z: Zone) -> str:
    """The clipboard JSON of 'Copy geometry' (Reduce's 'Paste zone' reads it)."""
    import json
    from aurora.core.zones import geometry
    return json.dumps({GEOMETRY_MARK: 1, "label": z.label, **geometry(z)})


def zone_menu(parent, session, zid: int, map_view, extra=()):
    """The right-click menu of a zone on a map: Rename…, Duplicate, Delete, Copy geometry, Zoom to zone, then the
    `extra` (text, callable) entries (the Survey's 'Open <scan> in Reduce')."""
    from PySide6.QtWidgets import QApplication, QInputDialog, QMenu
    from aurora.core.zones import zone_bbox
    m = QMenu(parent)
    z = session.zone(zid)
    if z is None:
        return m

    def rename():
        label, ok = QInputDialog.getText(parent, "Rename the sample", "label", text=z.label)
        if ok:
            rename_zone(session, zid, label)
    m.addAction("Rename…", rename)
    m.addAction("Duplicate", lambda: duplicate_zone(session, zid))
    m.addAction("Delete", lambda: session.delete_zone(zid))
    m.addSeparator()
    m.addAction("Copy geometry", lambda: QApplication.clipboard().setText(geometry_text(z)))
    bb = zone_bbox(z)
    za = m.addAction("Zoom to zone", lambda: map_view.zoom_to(*bb) if bb is not None else None)
    za.setEnabled(bb is not None)
    if extra:
        m.addSeparator()
        for text, fn in extra:
            m.addAction(text, fn)
    return m
