"""Undo and redo of zone, settings and good-I0 edits: one QUndoStack per Session, one SnapshotCommand per edit.

Every `with session.edit(description):` records the JSON of the holders it touched, of the good-I0 choices and of the
settings rules, before and after (zones are small, so a snapshot is simpler and safer than an inverse per operation). Undo puts the 'before'
snapshot back, redo the 'after' one. Edits with the same description that follow each other within MERGE_WINDOW_S
merge into one step, so a run of card edits undoes at once. A stroke on a zone map is already one edit
(session.edit(..., merge=False), aurora.app.zone_tools.ZoneEditor): it never merges with the stroke before or after
it, so two quick strokes are two steps; only 'new zone N' (the New sample button) takes the first stroke into its own
step. Attaching scans and loading stacks are not edits and are never recorded.
"""
from __future__ import annotations

import json
import weakref

from PySide6.QtGui import QUndoCommand, QUndoStack

from aurora.core.io.config import json_default

UNDO_LIMIT = 100            # steps kept in the history
MERGE_WINDOW_S = 1.5        # edits with the same description closer than this become one step
MERGE_ID = 0x5A0E           # QUndoCommand.id() shared by every snapshot command (mergeWith decides)


def dumps(obj) -> str:
    """Canonical JSON of a snapshot part: sorted keys, so equal content always gives equal text."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=json_default)


class Snapshot(dict):
    """{'holders': {holder key: JSON text | None (absent)}, 'good_i0': JSON text, 'rules': JSON text, 'ui': JSON text of
    the undoable ui values (the chosen sample sheet)}: the state an edit can change."""

    @property
    def keys_touched(self) -> frozenset:
        return frozenset(self.get("holders", {}))


class SnapshotCommand(QUndoCommand):
    """One undo step: the snapshots before and after an edit, plus where the user was (holder and zone)."""

    def __init__(self, session, text: str, before: Snapshot, after: Snapshot, place_before: tuple, place_after: tuple,
                 when: float, merge: bool = True):
        super().__init__(text)
        # A weak reference: the QUndoStack (a child of the session) owns the command in C++, so its Python side holds
        # one reference PySide never gives back; a strong one here would keep the session, and through it the whole
        # window, alive after the window is destroyed (a cycle the garbage collector cannot see is garbage).
        self._session = weakref.ref(session)
        self.before, self.after = before, after
        self.place_before, self.place_after = place_before, place_after       # (holder key, zone id)
        self.when = when
        self.merge = bool(merge)        # False: a step of its own (a stroke), never merged by time or description
        self._pushed = False            # QUndoStack.push calls redo() once: the edit is already in place then

    @property
    def session(self):
        """The Session the edit belongs to, or None once it is gone."""
        return self._session()

    def id(self) -> int:                # noqa: A003 (Qt's name)
        return MERGE_ID

    def continues(self, other) -> bool:
        """Whether `other` repeats this edit: the same description, or the same verb on the zone this edit created
        ('paint pixels in a new zone' then 'paint pixels in zone 4': one brush stroke)."""
        if other.text() == self.text():
            return True
        new = " a new zone"
        zid = self.place_after[1] if self.place_after else None
        return self.text().endswith(new) and zid is not None and \
            other.text() == f"{self.text()[:-len(new)]} zone {zid}"

    def completed_by(self, other) -> bool:
        """Whether `other` gives the empty zone this edit created its first area ('new zone 3' then 'add a rectangle
        to zone 3'): one step, whatever the time between them, so Ctrl+Z removes the new zone with its area."""
        zid = self.place_after[1] if self.place_after else None
        return zid is not None and self.text() == f"new zone {zid}" and \
            other.text() in (f"add a rectangle to zone {zid}", f"paint pixels in zone {zid}",
                             f"add a polygon to zone {zid}")

    def mergeWith(self, other) -> bool:     # noqa: N802 (Qt's name)
        if not isinstance(other, SnapshotCommand):
            return False
        first = self.completed_by(other)
        if not first and not (self.merge and other.merge and self.continues(other)):
            return False
        if other.before.keys_touched != self.after.keys_touched:
            return False
        if not first and other.when - self.when > MERGE_WINDOW_S:
            return False
        if first:
            self.setText(other.text().replace(f"zone {self.place_after[1]}", "a new zone"))
        # the newer edit starts where this one ended: keep this 'before', take its 'after'
        self.after = Snapshot(holders={**self.after["holders"], **other.after["holders"]},
                              good_i0=other.after["good_i0"], rules=other.after.get("rules"),
                              ui=other.after.get("ui"))
        self.place_after = other.place_after
        self.when = other.when
        if self.after == self.before:
            self.setObsolete(True)      # the edits cancelled each other out: no step at all
        # the stack deletes `other` now, but PySide6 keeps its Python side: let go of its snapshots at least
        other.before = other.after = Snapshot(holders={})
        return True

    def undo(self) -> None:
        ses = self.session
        if ses is not None:
            ses._apply_snapshot(self.before, self.place_before, f"undone: {self.text()}")

    def redo(self) -> None:
        if not self._pushed:
            self._pushed = True
            return
        ses = self.session
        if ses is not None:
            ses._apply_snapshot(self.after, self.place_after, f"redone: {self.text()}")


def make_stack(parent) -> QUndoStack:
    st = QUndoStack(parent)
    st.setUndoLimit(UNDO_LIMIT)         # only possible while the stack is empty
    return st
