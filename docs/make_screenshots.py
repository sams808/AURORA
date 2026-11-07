"""Grab every perspective of AURORA on the demo beamtime for the manuals (docs/img/*.png).

Run from the repo root with the project's Python:  python docs/make_screenshots.py
Writes a fresh demo folder in a temporary directory; nothing else is touched.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AURORA_SETTINGS_DIR", str(Path(tempfile.mkdtemp(prefix="aurora_docs_settings_"))))

from PySide6.QtCore import QTimer, Qt  # noqa: E402

from aurora.app.main import MainWindow, create_app, shutdown_app  # noqa: E402
from aurora.cli import cmd_demo  # noqa: E402
from aurora.core import testdata  # noqa: E402
from aurora.core.hypercube import Hypercube  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from aurora.core.qc import count_rate_probe  # noqa: E402

OUT = ROOT / "docs" / "img"
OUT.mkdir(exist_ok=True)


def edge(E, e0=690.0, width=1.0, white=0.6):
    return 0.2 + 0.002 * (E - E[0]) + 1.0 / (1 + np.exp(-(E - e0) / width)) + white * np.exp(-((E - e0 - 2) / 1.5) ** 2)


def write_john(path: Path, label: str, E, y):
    rng = np.random.default_rng(abs(hash(label)) % 1000)
    y = y + rng.normal(0, 0.01, E.size)
    rows = "\n".join(f"{e},{10 * v},60,83,{20 * v},60,83,{5 * v},60,83,{5 * v},60,83,2.0,{0.5 * v},{40 * v},{10 * v},False" for e, v in zip(E, y))
    path.write_text(f"# sample_label: {label}\nenergy,sdd1,sdd1_roi_lo,sdd1_roi_hi,sdd2,sdd2_roi_lo,sdd2_roi_hi,sdd3,sdd3_roi_lo,sdd3_roi_hi,"
                    "sdd4,sdd4_roi_lo,sdd4_roi_hi,I0,TEY,sdd_total,sdd_mean,bad_point\n" + rows, encoding="utf-8")


def main() -> int:
    demo = Path(tempfile.mkdtemp(prefix="aurora_docs_")) / "demo_beamtime"
    cmd_demo(demo)
    reduced = demo.parent / "reduced"
    E = np.linspace(660, 740, 321)
    a, b, c = edge(E, 690, 1.0, 0.6), edge(E, 700, 1.6, 0.2), edge(E, 695, 1.2, 0.9)
    for name, y in (("NaF", a), ("CaF2", b), ("BaY2F8", c), ("mixture 1", 0.5 * a + 0.3 * b + 0.2 * c), ("mixture 2", 0.2 * a + 0.5 * b + 0.3 * c)):
        (reduced / name).mkdir(parents=True)
        write_john(reduced / name / f"{name}_full_reduced.csv", name, E, y)

    app = create_app(["AURORA"])
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    proj = Project.discover(demo)
    win.home.set_project(proj)
    ses = win.session
    scan = proj.find(testdata.STACK)
    sid = ses.stack_id(scan)
    sd = ses.stack(sid)
    ses.touch(); ses.select_stack(sid)
    x0, x1, y0, y1 = sd.bbox
    z1 = ses.new_zone("1 alpha"); z1.group = "Minerals"; z1.rects.append([x0, (x0 + x1) / 2, y0, y1])
    z2 = ses.new_zone("2 beta"); z2.group = "Simple Glass"; z2.rects.append([(x0 + x1) / 2, x1, y0, y1])
    ses.touch(); ses.select_zone(z1.id)
    win.reduce.scan_list.setCurrentRow(0)
    ck = win.check
    ck._rates = {sid: count_rate_probe(proj, scan)}
    ck.compute()
    ck.x_scan.setCurrentIndex([ck.x_scan.itemData(i) for i in range(ck.x_scan.count())].index(testdata.STACK)); ck.explore_i0()
    lib = win.library
    lib.add_folder(reduced)
    for r in lib.requests:
        r.show = r.signal != "TEY"; r.fit = r.signal != "TEY"
    lib.changed.emit()
    win.compare.show_peaks.setChecked(True); win.compare.table.selectRow(0)
    fit = win.fit
    fit.refresh(); fit.target.setCurrentText("mixture 1 [sdd-scaled]")
    for i in range(fit.components.count()):
        it = fit.components.item(i)
        it.setCheckState(Qt.Checked if it.text() in ("NaF [sdd-scaled]", "CaF2 [sdd-scaled]", "BaY2F8 [sdd-scaled]") else Qt.Unchecked)
    fit.bootstrap.setValue(100); fit.run_lcf(); fit.n_comp.setValue(3); fit.run_pca(); fit.run_mcr()
    fit.pk_trace.setCurrentText("NaF [sdd-scaled]"); fit.pk_n.setValue(2); fit.run_peaks()
    mp = win.maps
    mp.set_hypercube(Hypercube.load(proj, scan)); mp.line.setCurrentIndex(1); mp.roi_mode.setCurrentIndex(1)
    mp.draw_elements(); mp.n_k.setValue(2); mp.run_clusters()
    win.report.out_dir.setText(str(demo / "reports" / "aurora"))

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
            win.grab().save(str(OUT / f"{name}.png"))
            print("wrote", name)
        if state["i"] >= len(shots):
            app.quit(); return
        shots[state["i"]][1]()
        state["i"] += 1
        QTimer.singleShot(1200, step)

    QTimer.singleShot(2500, step)
    code = app.exec()
    shutdown_app(app, win)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
