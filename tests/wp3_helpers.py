"""Shared helpers of the WP3 (Reduce) tests: a window on a project with a stack loaded synchronously, fake pyqtgraph
mouse events for the map, real QTest drags on a map's viewport (qdrag), and extra stacks written into a fake
beamtime."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, Qt


def pump(qapp, n: int = 5) -> None:
    for _ in range(n):
        qapp.processEvents()


def open_window(qapp, project, size=(1500, 940), stack_name: str | None = None):
    """MainWindow (shown) on a discovered project, with the holder's first stack (or `stack_name`) loaded and
    selected in Reduce."""
    from aurora.app.main import MainWindow
    from aurora.core import testdata
    win = MainWindow()
    win.resize(*size)
    win.show()
    win.home.set_project(project)
    pump(qapp)
    win.jobs.wait_idle(30000)
    ses = win.session
    scan = project.find(stack_name or testdata.STACK)
    sid = ses.stack_id(scan)
    ses.stack(sid)
    win.reduce._attach(ses.stack_loaded(sid))
    ses.touch()
    ses.select_stack(sid)
    win.show_perspective("reduce")
    pump(qapp)
    win.jobs.wait_idle(30000)
    pump(qapp)
    return win, sid


def close(win) -> None:
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


class FakeClick:
    """A pyqtgraph MouseClickEvent stand-in at view coordinates (x, y) mm."""

    def __init__(self, vb, x: float, y: float, button=Qt.LeftButton, modifiers=Qt.NoModifier, double: bool = False):
        self._pos = vb.mapFromView(QPointF(x, y))
        self._scene = vb.mapViewToScene(QPointF(x, y))
        self._button, self._mods, self._double = button, modifiers, double
        self.accepted = False

    def pos(self):
        return self._pos

    def scenePos(self):
        return self._scene

    def button(self):
        return self._button

    def modifiers(self):
        return self._mods

    def double(self) -> bool:
        return self._double

    def accept(self) -> None:
        self.accepted = True

    def ignore(self) -> None:
        self.accepted = False

    def isAccepted(self) -> bool:
        return self.accepted


class FakeDrag:
    """A pyqtgraph MouseDragEvent stand-in: from p0 to p (mm), start / move / finish."""

    def __init__(self, vb, p0, p, start=False, finish=False, button=Qt.LeftButton, modifiers=Qt.NoModifier):
        self._down = vb.mapFromView(QPointF(*p0))
        self._pos = vb.mapFromView(QPointF(*p))
        self._scene_down = vb.mapViewToScene(QPointF(*p0))
        self._scene = vb.mapViewToScene(QPointF(*p))
        self._start, self._finish, self._button, self._mods = start, finish, button, modifiers

    def modifiers(self):
        return self._mods

    def pos(self):
        return self._pos

    def buttonDownPos(self, _b=None):
        return self._down

    def scenePos(self):
        return self._scene

    def buttonDownScenePos(self, _b=None):
        return self._scene_down

    def isStart(self) -> bool:
        return self._start

    def isFinish(self) -> bool:
        return self._finish

    def button(self):
        return self._button

    def accept(self, *_a) -> None:
        pass

    def ignore(self, *_a) -> None:
        pass


def click(map_view, x, y, **kw) -> None:
    map_view.vb.mouseClickEvent(FakeClick(map_view.vb, x, y, **kw))


def drag(map_view, path, button=Qt.LeftButton, modifiers=Qt.NoModifier) -> None:
    """A drag along the points of `path` (mm): start, moves, finish."""
    vb = map_view.vb
    p0 = path[0]
    for i, p in enumerate(path):
        vb.mouseDragEvent(FakeDrag(vb, p0, p, start=i == 0, finish=i == len(path) - 1, button=button,
                                   modifiers=modifiers))


def view_to_viewport(map_view, x: float, y: float):
    """A point of the map in mm, in the pixels of the map's viewport (where QTest sends mouse events)."""
    return map_view.mapFromScene(map_view.vb.mapViewToScene(QPointF(x, y)))


def qdrag(qapp, map_view, pts, button=Qt.LeftButton, modifiers=Qt.NoModifier, release=True, release_modifiers=None,
          delay: int = 15) -> None:
    """A real mouse drag on the map's viewport (QTest press, a move to each point, release), along `pts` in mm.
    `modifiers` are held from the press on (Shift at the start inverts the mode); `release_modifiers` (default: the
    same) at the release (Ctrl there forces the selected zone). release=False leaves the button down. Each event
    waits `delay` ms first: pyqtgraph drops mouse moves less than 10 ms apart (its mouseRateLimit), as it does for a
    real mouse."""
    from PySide6.QtTest import QTest
    vp = map_view.viewport()
    QTest.mousePress(vp, button, modifiers, view_to_viewport(map_view, *pts[0]))
    qapp.processEvents()
    for x, y in pts[1:]:
        QTest.qWait(delay)
        QTest.mouseEvent(QTest.MouseAction.MouseMove, vp, Qt.NoButton, modifiers, view_to_viewport(map_view, x, y))
        qapp.processEvents()
    if release:
        qrelease(qapp, map_view, pts[-1], button, modifiers if release_modifiers is None else release_modifiers, delay)


def qrelease(qapp, map_view, pt, button=Qt.LeftButton, modifiers=Qt.NoModifier, delay: int = 15) -> None:
    """Release the mouse button of a qdrag(..., release=False) at `pt` (mm)."""
    from PySide6.QtTest import QTest
    QTest.qWait(delay)
    QTest.mouseRelease(map_view.viewport(), button, modifiers, view_to_viewport(map_view, *pt))
    qapp.processEvents()


def qmove(qapp, map_view, pt, modifiers=Qt.NoModifier, delay: int = 15) -> None:
    """One more mouse move of a drag in progress, to `pt` (mm)."""
    from PySide6.QtTest import QTest
    QTest.qWait(delay)
    QTest.mouseEvent(QTest.MouseAction.MouseMove, map_view.viewport(), Qt.NoButton, modifiers,
                     view_to_viewport(map_view, *pt))
    qapp.processEvents()


def add_stack(project_root: Path, folder: str, base: str, dx: float = 0.0, created: str = "Thu Nov  5 09:35:00 2026",
              seed: int = 11) -> Path:
    """One more complete stack in raw/<day> of a fake beamtime (the grid shifted by dx mm)."""
    from aurora.core import testdata
    x, y = testdata.snake_grid()
    d = Path(project_root) / "raw" / testdata.DAY / folder
    testdata._write_stack(d, base, testdata.ENERGIES, len(testdata.ENERGIES), x + dx, y, np.random.default_rng(seed),
                          created=created)
    return d
