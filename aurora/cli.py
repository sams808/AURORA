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
    from aurora.app.main import MainWindow, create_app
    app = create_app([APP_NAME])
    win = MainWindow()
    win.home.set_project(proj)
    app.processEvents()
    assert win.home.tree.topLevelItemCount() == 1
    from aurora.app.about import versions_text
    print(versions_text())
    print(f"distcheck ok: {len(proj.scans)} scans, statuses {statuses}, window {win.width()}x{win.height()}")
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
    args = p.parse_args(argv)
    _logging(args.verbose)
    if args.distcheck:
        return cmd_distcheck()
    if args.cmd == "project":
        return cmd_project(args.path)
    if args.cmd == "scan":
        return cmd_scan(args.path)
    from aurora.app.main import run
    return run(getattr(args, "project", None))


if __name__ == "__main__":
    raise SystemExit(main())
