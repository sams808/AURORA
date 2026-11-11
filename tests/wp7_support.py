"""Shared set-ups of the WP7 (report) tests: synthetic projects with zones on one or two holders, a quick builder,
and a main window showing the Report perspective on such a project."""
from __future__ import annotations

import functools
from pathlib import Path

from aurora.core import testdata
from aurora.core.io.config import HolderZones, ScanSettings, Zone, ZonesFile
from aurora.core.model import Project
from aurora.core.stack import StackData


def project(tmp: Path, n_holders: int = 1) -> Project:
    return Project.discover(testdata.make_fake_project(Path(tmp) / "beamtime", n_holders=n_holders))


def bbox(proj: Project, name: str) -> tuple[str, tuple[float, float, float, float]]:
    scan = proj.find(name)
    sd = StackData.load(proj, scan)
    return f"{scan.day}_{scan.name}", sd.bbox


def two_holder_zones(proj: Project) -> ZonesFile:
    """make_fake_zones on holderA plus one zone '1 alpha' (group Group 1) over the complete stack of holderB."""
    zf = testdata.make_fake_zones(proj)
    sid, (x0, x1, y0, y1) = bbox(proj, testdata.STACK2)
    z = Zone(id=1, label="1 alpha", group="Group 1", rects=[[x0, x1, y0, y1]])
    z.settings[sid] = ScanSettings(stack=testdata.STACK2, line="total XRF", n=12)
    zf.holders["holderB"] = HolderZones(holder="holderB", day=testdata.DAY, survey=testdata.SURVEY2, zones=[z])
    return zf


def quick(monkeypatch, dpi: int = 40) -> None:
    """Small figures for builds made through the GUI (the report's content does not depend on the dpi)."""
    from aurora.core.report import ReportBuilder
    monkeypatch.setattr(ReportBuilder, "__init__", functools.partialmethod(ReportBuilder.__init__, png_dpi=dpi,
                                                                           page_png_dpi=dpi))


def pump(qapp, n: int = 5) -> None:
    for _ in range(n):
        qapp.processEvents()


def report_window(qapp, proj: Project, zf: ZonesFile, tmp: Path):
    """A shown MainWindow on `proj` with the zones of `zf` imported, on the Report perspective, check done."""
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    win.home.set_project(proj)
    pump(qapp)
    path = Path(tmp) / "zones_in.json"
    zf.save(path, export=True)
    win.session.import_zones(path)
    win.session.flush()
    win.show_perspective("report")
    pump(qapp)
    win.report.refresh_check()
    win.jobs.wait_idle(60000)
    pump(qapp)
    return win


def close(win) -> None:
    win.jobs.wait_idle(60000)
    win.session._save_timer.stop()
    win.close()
