"""Command line: `aurora` (GUI), `aurora gui <project>`, `aurora project <path>`, `aurora scan <path>`, `aurora --distcheck`.

The headless commands never import Qt, so `aurora.core` keeps working without a display.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

from aurora import APP_NAME, __version__


def _logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(message)s", stream=sys.stdout)


def cmd_project(path: Path) -> int:
    from aurora.core.model import Project
    proj = Project.discover(path)
    print(f"project {proj.root}\nraw     {proj.raw}\n{proj.calibration.describe() if proj.calibration else ''}")
    if proj.roi_bins and proj.roi_bins.labels:
        print("fixed ROI bins: " + ", ".join(proj.roi_bins.labels))
    for day in proj.days:
        print(f"\n{day.date or day.path.name}: {len(day.holders)} holders, {len(day.scans)} scans"
              + ("  (sgm_report summary present)" if day.summary else ""))
        for h in day.holders:
            print(f"  holder {h.name}: {len(h.stacks)} complete, {len(h.dropped)} dropped")
            for s in h.scans:
                flags = ", ".join(s.flags)
                print(f"    {s.time:>5} {s.kind:6} {s.status:11} {s.edge_text:7} {s.energy_text:>18} {s.n_points:>5} px  "
                      f"{s.name}" + (f"  [{flags}]" if flags else "") + (f"  ! {s.error}" if s.error else ""))
        for s in day.others:
            print(f"    {s.time:>5} {s.kind:6} {s.status:11} {s.name}")
    return 0


def cmd_scan(path: Path) -> int:
    from aurora.core.io.raw import read_point_dir
    from aurora.core.model import ScanInfo
    s = ScanInfo.from_dir(path)
    print(f"{s.name}: kind {s.kind}, status {s.status} ({s.status_reason}), edge {s.edge_text or '-'}, "
          f"{s.n_energies} energies {s.energy_text}, {s.n_points} points, h5 {s.h5}")
    for k, v in sorted(s.md.items()):
        print(f"  {k} = {v[:120]}")
    if s.kind == "survey" and s.h5 is not None:
        pd = read_point_dir(path)
        print(f"  survey point data: mcc {pd.mcc.shape}, sdd {[a.shape for a in pd.sdd]}, complete {pd.complete}")
    elif s.energy_dirs:
        e, p = s.energy_dirs[0]
        pd = read_point_dir(p)
        print(f"  first energy {e} eV: mcc {pd.mcc.shape}, sdd {[a.shape for a in pd.sdd]}, complete {pd.complete}")
    return 0


def cmd_report(args) -> int:
    from aurora.core.io.config import ZonesFile
    from aurora.core.model import Project
    from aurora.core.report import ReportBuilder
    proj = Project.discover(args.project)
    zpath = args.zones or (proj.root / ".aurora" / "session.json")
    zones = ZonesFile.load(zpath)
    out = args.out or (proj.root / "reports" / "aurora")
    builder = ReportBuilder(proj, zones, out, title=args.title, groups=args.groups or None,
                            appendix=not args.no_appendix, maps=not args.no_maps, png_dpi=args.dpi)
    res = builder.build(progress=lambda i, n, m: print(f"  {i + 1}/{n} {m}", end="\r"))
    print(f"\n{res.pdf}: {res.pages} pages, {res.entries} zone spectra, {len(res.csvs)} CSV, {len(res.figures)} figures")
    for s in res.skipped:
        print("  skipped:", s)
    return 0


def cmd_demo(folder: Path) -> int:
    """A synthetic beamtime with fixed ROI bins, a sample sheet and a few zones, for the tutorials."""
    import json
    from aurora.core import testdata
    root = testdata.make_fake_project(Path(folder))
    (root / "config" / "roi_bins.json").write_text(json.dumps({
        "_meta": {"gains": {"sdd1": 10.0, "sdd2": 10.0, "sdd3": 10.0, "sdd4": 10.0}, "n_sigma": 1.5,
                  "help": "line -> {sdd: [first channel, last channel + 1]}; the demo uses 10 eV per channel"},
        "F Kα": {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77], "line_ev": 676.8},
        "O Kα": {"sdd1": [46, 58], "sdd2": [46, 58], "sdd3": [46, 58], "sdd4": [46, 58], "line_ev": 525.0}}, indent=1), encoding="utf-8")
    from aurora.core.names import holder_from_survey
    holder = holder_from_survey(testdata.SURVEY)       # the sheet's plate column must equal the holder name
    (root / "demo samples.csv").write_bytes(("measured demo,,,\n"
                                            "Paper,Final plate#,Sample group,Sample ID,Composition,F K edge,Other edges?,Notes\n"
                                            f",{holder},Minerals,1 alpha,CaF2,y,,demo mineral\n,{holder},Simple Glass,2 beta,NaF-glass,y,,demo glass\n").encode("cp1252"))
    print(f"demo beamtime written to {root}\n  open it with:  aurora gui \"{root}\"")
    return 0


def cmd_distcheck() -> int:
    """Smoke test of a frozen build: build a synthetic project, open the window offscreen, read it back."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    tmp = Path(tempfile.mkdtemp(prefix="aurora_distcheck_"))
    os.environ["AURORA_SETTINGS_DIR"] = str(tmp / "settings")
    from aurora.core.model import Project
    from aurora.core.testdata import make_fake_project
    root = make_fake_project(tmp / "project")
    proj = Project.discover(root)
    assert len(proj.scans) == 4, proj.scans
    statuses = sorted(s.status for s in proj.scans)
    assert statuses == ["aborted", "complete", "survey", "test"], statuses
    from aurora.app.main import MainWindow, create_app, shutdown_app
    app = create_app([APP_NAME])
    win = MainWindow()
    win.home.set_project(proj)
    app.processEvents()
    assert win.home.tree.topLevelItemCount() == 1
    from aurora.app.about import versions_text
    size = f"{win.width()}x{win.height()}"
    shutdown_app(app, win)
    print(versions_text())
    print(f"distcheck ok: {len(proj.scans)} scans, statuses {statuses}, window {size}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aurora", description=f"{APP_NAME} {__version__}")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    p.add_argument("--distcheck", action="store_true", help="smoke-test this installation offscreen and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    g = sub.add_parser("gui", help="open the application (default)")
    g.add_argument("project", nargs="?", type=Path)
    s = sub.add_parser("project", help="list the days, holders and scans of a beamtime folder")
    s.add_argument("path", type=Path)
    s = sub.add_parser("scan", help="describe one scan folder and read its first energy")
    s.add_argument("path", type=Path)
    r = sub.add_parser("report", help="build the group-organised report from a zones file (headless)")
    r.add_argument("project", type=Path)
    r.add_argument("--zones", type=Path, help="zones_all.json (default: <project>/.aurora/session.json)")
    r.add_argument("--out", type=Path, help="output folder (default: <project>/reports/aurora)")
    r.add_argument("--title")
    r.add_argument("--groups", nargs="*", help="only these sample groups")
    r.add_argument("--no-appendix", action="store_true")
    r.add_argument("--no-maps", action="store_true")
    r.add_argument("--dpi", type=int, default=600, help="resolution of the figure PNGs (default 600)")
    dm = sub.add_parser("demo", help="create a small synthetic beamtime folder (the tutorials use it)")
    dm.add_argument("folder", type=Path)
    args = p.parse_args(argv)
    _logging(args.verbose)
    if args.distcheck:
        return cmd_distcheck()
    if args.cmd == "project":
        return cmd_project(args.path)
    if args.cmd == "scan":
        return cmd_scan(args.path)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "demo":
        return cmd_demo(args.folder)
    from aurora.app.main import run
    return run(getattr(args, "project", None))


if __name__ == "__main__":
    raise SystemExit(main())
