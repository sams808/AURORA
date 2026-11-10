"""Grab every perspective of AURORA on the demo beamtime for the manuals (aurora/resources/docs/img/*.png).

Run from the repo root with the project's Python:

    python docs/make_screenshots.py                          # light scheme, Advanced mode, into the bundled docs
    python docs/make_screenshots.py --scheme dark --out DIR  # the dark set, somewhere else

Advanced mode, so every control the manual describes is on screen. Writes a fresh demo folder in a temporary
directory outside the home folder (its path is on screen; see demo_root) and removes it at the end; the settings go
to a temporary folder too: nothing else is touched.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AURORA_SETTINGS_DIR", str(Path(tempfile.mkdtemp(prefix="aurora_docs_settings_"))))

OUT = ROOT / "aurora" / "resources" / "docs" / "img"          # the manuals bundled with the application
SIZE = (1500, 940)


def demo_root() -> Path:
    """A new temporary folder whose path names no account: Home's Scans card, the Scan panel's Folder row and the
    Report output show the demo's full path, and the temporary folder of Windows lies inside the user's profile.
    Windows: under %PUBLIC%\\Documents; macOS: /Users/Shared; else the system temporary folder (/tmp)."""
    home = Path.home().resolve()
    cands = [Path(os.environ["PUBLIC"]) / "Documents"] if os.environ.get("PUBLIC") else []
    cands += [Path("/Users/Shared"), Path(tempfile.gettempdir())]
    for c in cands:
        try:
            c = c.resolve()
            if c.is_dir() and os.access(c, os.W_OK) and not c.is_relative_to(home):
                return Path(tempfile.mkdtemp(prefix="aurora_docs_", dir=c))
        except OSError:
            continue
    raise SystemExit("no writable folder outside the home folder for the demo: the shots would show the account name")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--scheme", choices=["light", "dark"], default="light")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--easy", action="store_true", help="Easy mode instead of Advanced")
    args = ap.parse_args(argv)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    from PySide6.QtCore import QTimer, Qt
    from aurora.app import mode
    from aurora.app.main import MainWindow, create_app, shutdown_app
    from aurora.app.settings import settings
    from aurora.app.theme import apply_theme
    from aurora.cli import cmd_demo
    from aurora.core import testdata
    from aurora.core.hypercube import Hypercube
    from aurora.core.model import Project
    from aurora.core.qc import count_rate_probe

    base = demo_root()                                # never under the home folder (its path is on screen)
    demo = base / "demo_beamtime"
    cmd_demo(demo)                                    # raw data, processed/reduced spectra and references
    s = settings()
    s.setValue("appearance", args.scheme)
    s.setValue("reference_dir", str(demo / "references"))
    s.setValue("report_author", "A. Researcher")      # a placeholder: never the account of the machine that makes the shots
    s.sync()
    app = create_app(["AURORA"])
    apply_theme(app, appearance=args.scheme)
    mode.set_advanced(not args.easy)
    win = MainWindow()
    win.resize(*SIZE)
    win.show()
    proj = Project.discover(demo)
    win.home.set_project(proj)
    ses = win.session
    scan = proj.find(testdata.STACK)
    sid = ses.stack_id(scan)
    sd = ses.stack(sid)
    ses.touch(); ses.select_stack(sid)
    tree = win.home.tree
    holder_item = tree.topLevelItem(0).child(0)
    for i in range(holder_item.childCount()):
        if holder_item.child(i).text(0) == testdata.STACK:
            tree.setCurrentItem(holder_item.child(i))
    x0, x1, y0, y1 = sd.bbox
    z1 = ses.new_zone("1 alpha"); z1.group = "Minerals"; z1.rects.append([x0, (x0 + x1) / 2, y0, y1])
    z2 = ses.new_zone("2 beta"); z2.group = "Simple Glass"; z2.rects.append([(x0 + x1) / 2, x1, y0, y1])
    ses.touch(); ses.select_zone(z1.id)
    win.reduce.scan_list.setCurrentRow(0)
    from aurora.app.session import CheckItem
    ses.queue_for_check([CheckItem(ses.current_holder, z.id, sid) for z in (z1, z2)])     # Reduce's Add to Check
    ck = win.check
    ck._rates = {sid: count_rate_probe(proj, scan)}
    ck.compute()
    ck.x_scan.setCurrentIndex([ck.x_scan.itemData(i) for i in range(ck.x_scan.count())].index(testdata.STACK)); ck.explore_i0()
    lib = win.library
    lib.add_folder(demo / "processed" / "reduced")
    for r in lib.requests:
        r.show = r.signal != "TEY"; r.fit = r.signal != "TEY"
    lib.changed.emit()
    win.compare.show_peaks.setChecked(True); win.compare.table.selectRow(0)
    fit = win.fit
    fit.refresh(); fit.select_target("mixture 1", "sdd-scaled")
    fit.check_components(["NaF [sdd-scaled]", "CaF2 [sdd-scaled]", "BaY2F8 [sdd-scaled]"])
    fit.bootstrap.setValue(100); fit.run_lcf(); fit.n_comp.setValue(3); fit.run_pca(); fit.run_mcr()
    fit.select_trace("NaF", "sdd-scaled"); fit.pk_n.setValue(2); fit.run_peaks()
    mp = win.maps
    mp.set_hypercube(Hypercube.load(proj, scan)); mp.line.setCurrentIndex(1); mp.roi_mode.setCurrentIndex(1)
    mp.draw_elements(); mp.n_k.setValue(2); mp.run_clusters()
    win.report.out_dir.setText(str(demo / "reports" / "aurora"))
    win.jobs.wait_idle(120000)                        # fits, clusters, elemental maps and the Check rows
    win.check.queue.selectRow(0)
    win.report.build()                                # the Pages card shows the first page of a real build
    win.jobs.wait_idle(180000)

    shots = [("home", lambda: win.show_perspective("home")), ("survey", lambda: win.show_perspective("survey")),
             ("reduce", lambda: win.show_perspective("reduce")), ("check", lambda: win.show_perspective("check")),
             ("compare", lambda: win.show_perspective("compare")),
             ("fit_lcf", lambda: (win.show_perspective("fit"), fit.tabs.setCurrentIndex(0))),
             ("fit_mcr", lambda: fit.tabs.setCurrentIndex(1)), ("fit_peaks", lambda: fit.tabs.setCurrentIndex(3)),
             ("maps_explore", lambda: (win.show_perspective("maps"), mp.tabs.setCurrentIndex(0))),
             ("maps_elements", lambda: mp.tabs.setCurrentIndex(1)), ("maps_clusters", lambda: mp.tabs.setCurrentIndex(3)),
             ("report", lambda: win.show_perspective("report"))]
    state = {"i": 0}

    def step():
        if state["i"] > 0:
            name = shots[state["i"] - 1][0]
            win.grab().save(str(out / f"{name}.png"))
            print("wrote", out / f"{name}.png")
        if state["i"] >= len(shots):
            app.quit(); return
        shots[state["i"]][1]()
        state["i"] += 1
        QTimer.singleShot(1200, step)

    QTimer.singleShot(2500, step)
    code = app.exec()
    ses._save_timer.stop()
    shutdown_app(app, win)
    remove_later(base)                                # a shared folder (Public Documents): leave nothing behind
    return code


def remove_later(folder: Path) -> None:
    """Remove `folder` once this process has ended: the hypercube cache inside it stays memory-mapped until then."""
    import subprocess
    code = ("import shutil, sys, time, pathlib\np = pathlib.Path(sys.argv[1])\nfor _ in range(120):\n"
            "    shutil.rmtree(p, ignore_errors=True)\n    if not p.exists(): break\n    time.sleep(1)\n")
    subprocess.Popen([sys.executable, "-c", code, str(folder)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    print(f"the demo folder {folder} is removed once this process has ended")


if __name__ == "__main__":
    raise SystemExit(main())
