"""Zone editing on a map: one ZoneEditor per zone map (Reduce's stack map; the Survey plate map uses the same class).

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

rect_drawn / brush_at / polygon_drawn emitted by code rather than by a stroke (scripts, the self-test, older tests)
still edit the zone as they always did (commit(..., legacy=True)): the tool's mode, the old target rule, one edit per
signal merged by time and description.
"""
from __future__ import annotations

import re
from functools import cached_property

import numpy as np
from PySide6.QtCore import QObject, Signal

from aurora.app.vocab import plural
from aurora.core.io.config import Zone
from aurora.core.zones import Stroke, apply_stroke, brush_squares, members, stroke_covers, stroke_zone

TOUCH_PITCH = 1.0               # a Draw stroke within this many pitches of the selected zone's points adds to it
TOUCH_TOL = 1.25                # ... measured with this slack (jittered stage positions)
NO_PROJECT = "open a project first"
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
        """No stroke and no message on the map (a tool, scan or holder change)."""
        self._draft = None
        self.map.set_hint("")

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
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        stroke.force_add = bool(QApplication.keyboardModifiers() & Qt.ControlModifier)    # the hint follows Ctrl
        self._draft = self.make_draft(stroke)
        self.map.set_hint(self._draft.hint)
        self.draft_changed.emit(self._draft)

    def _cancelled(self) -> None:
        if self._draft is not None or self.map.hint_text():
            self._draft = None
            self.map.set_hint("")
            self.draft_changed.emit(None)
        self._serial = -1

    def _finished(self, stroke: Stroke) -> None:
        if not self.enabled():
            return
        self._draft = None
        self.commit(stroke)
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
            self.map.set_hint("")
        self.committed.emit(z.id)
        return z.id
