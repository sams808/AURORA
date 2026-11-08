"""Shared helpers of the WP2 session tests: synthetic projects with a second stack, sessions, windows, a fake clock."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np

from aurora.core import testdata

STACK_B = "095500_CaF2_f_k_stack"            # a second complete stack of holder N5, on the same grid


def pump(qapp, n: int = 5) -> None:
    for _ in range(n):
        qapp.processEvents()


def wait(qapp, ms: int) -> None:
    from PySide6.QtTest import QTest
    QTest.qWait(ms)
    qapp.processEvents()


def two_stack_project(root: Path, n_holders: int = 1) -> Path:
    """The synthetic beamtime plus a second complete stack over the same 4 x 3 grid, written with testdata._write_stack."""
    root = testdata.make_fake_project(root, n_holders=n_holders)
    x, y = testdata.snake_grid()
    testdata._write_stack(root / "raw" / testdata.DAY / STACK_B, "CaF2_f_k", testdata.ENERGIES,
                          len(testdata.ENERGIES), x, y, np.random.default_rng(11), created="Thu Nov  5 09:55:00 2026")
    return root


def sid_of(name: str) -> str:
    return f"{testdata.DAY}_{name}"


def session(root: Path):
    from aurora.app.session import Session
    from aurora.core.model import Project
    ses = Session()
    ses.set_project(Project.discover(root))
    return ses


def draw_zone(ses, label: str = "", rect=None):
    """A new zone of the current holder with one rectangle (the whole 4 x 3 grid by default): two edits."""
    rect = rect or [-0.6, 0.35, 4.9, 5.6]
    z = ses.new_zone(label)
    with ses.edit(f"add a rectangle to zone {z.id}"):
        z.rects.append(list(rect))
    return z


def holders_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["holders"]


class FakeClock:
    """A settable datetime.now for Session.clock."""

    def __init__(self, start=dt.datetime(2026, 11, 8, 9, 0, 0)):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, **kw) -> None:
        self.t += dt.timedelta(**kw)


def window(qapp, project):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    win.home.set_project(project)
    pump(qapp)
    win.jobs.wait_idle(30000)
    pump(qapp)
    return win


def close(qapp, win) -> None:
    win.session._save_timer.stop()
    win.close()
    win.deleteLater()
    pump(qapp)
