"""Shared helpers of the WP3 (Reduce) tests: a window on a project with a stack loaded synchronously, fake pyqtgraph
mouse events for the map, and extra stacks written into a fake beamtime."""
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
        self._button, self._mods, self._double = button, modifiers, double
        self.accepted = False

    def pos(self):
        return self._pos

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

    def __init__(self, vb, p0, p, start=False, finish=False, button=Qt.LeftButton):
        self._down = vb.mapFromView(QPointF(*p0))
        self._pos = vb.mapFromView(QPointF(*p))
        self._start, self._finish, self._button = start, finish, button

    def pos(self):
        return self._pos

    def buttonDownPos(self, _b=None):
        return self._down

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


def drag(map_view, path) -> None:
    """A drag along the points of `path` (mm): start, moves, finish."""
    vb = map_view.vb
    p0 = path[0]
    for i, p in enumerate(path):
        vb.mouseDragEvent(FakeDrag(vb, p0, p, start=i == 0, finish=i == len(path) - 1))


def add_stack(project_root: Path, folder: str, base: str, dx: float = 0.0, created: str = "Thu Nov  5 09:35:00 2026",
              seed: int = 11) -> Path:
    """One more complete stack in raw/<day> of a fake beamtime (the grid shifted by dx mm)."""
    from aurora.core import testdata
    x, y = testdata.snake_grid()
    d = Path(project_root) / "raw" / testdata.DAY / folder
    testdata._write_stack(d, base, testdata.ENERGIES, len(testdata.ENERGIES), x + dx, y, np.random.default_rng(seed),
                          created=created)
    return d
