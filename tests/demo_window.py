"""A demo-loaded MainWindow for the theme and Easy-mode tests: the synthetic demo beamtime with one zone over the
complete stack, the checks run, John-format spectra in Compare, LCF / PCA / MCR-ALS / peaks run, the hypercube loaded
with elemental maps and clusters. Every perspective therefore has data, curves, legends and labels on screen."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt


def _edge(E, e0=690.0, width=1.0, white=0.6):
    return 0.2 + 0.002 * (E - E[0]) + 1.0 / (1 + np.exp(-(E - e0) / width)) + white * np.exp(-((E - e0 - 2) / 1.5) ** 2)


def write_john_csv(path: Path, label: str, E, y) -> None:
    rows = "\n".join(f"{e},{10 * v},60,83,{20 * v},60,83,{5 * v},60,83,{5 * v},60,83,2.0,{0.5 * v},{40 * v},{10 * v},False"
                     for e, v in zip(E, y))
    path.write_text(f"# sample_label: {label}\nenergy,sdd1,sdd1_roi_lo,sdd1_roi_hi,sdd2,sdd2_roi_lo,sdd2_roi_hi,sdd3,sdd3_roi_lo,"
                    "sdd3_roi_hi,sdd4,sdd4_roi_lo,sdd4_roi_hi,I0,TEY,sdd_total,sdd_mean,bad_point\n" + rows, encoding="utf-8")


def pump(qapp, n: int = 5) -> None:
    for _ in range(n):
        qapp.processEvents()


def build_demo_window(qapp, root: Path, size=(1500, 940)):
    """MainWindow (shown) on a fresh demo beamtime under `root`, every perspective filled."""
    from aurora.app.main import MainWindow
    from aurora.cli import cmd_demo
    from aurora.core import testdata
    from aurora.core.hypercube import Hypercube
    from aurora.core.model import Project
    from aurora.core.qc import count_rate_probe
    demo = root / "demo_beamtime"
    cmd_demo(demo)
    reduced = root / "reduced"
    E = np.linspace(660, 740, 161)
    a, b = _edge(E, 690, 1.0, 0.6), _edge(E, 700, 1.6, 0.2)
    for name, y in (("NaF", a), ("CaF2", b), ("glass", 0.7 * a + 0.3 * b)):
        (reduced / name).mkdir(parents=True)
        write_john_csv(reduced / name / f"{name}_full_reduced.csv", name, E, y)
    proj = Project.discover(demo)
    win = MainWindow()
    win.resize(*size)
    win.show()
    win.home.set_project(proj)
    pump(qapp)
    ses = win.session
    scan = proj.find(testdata.STACK)
    sid = ses.stack_id(scan)
    sd = ses.stack(sid)
    ses.touch()
    ses.select_stack(sid)
    # Home: a scan in the detail panel
    tree = win.home.tree
    holder_item = tree.topLevelItem(0).child(0)
    for i in range(holder_item.childCount()):
        if holder_item.child(i).text(0) == testdata.STACK:
            tree.setCurrentItem(holder_item.child(i))
    # Reduce: one zone over the stack footprint
    x0, x1, y0, y1 = sd.bbox
    z = ses.new_zone("1 alpha")
    z.group = "Minerals"
    z.rects.append([x0, x1, y0, y1])
    ses.touch()
    ses.select_zone(z.id)
    win.reduce.scan_list.setCurrentRow(0)
    # Check: the count-rate probe done synchronously, the I0 explorer read
    ck = win.check
    ck._rates = {sid: count_rate_probe(proj, scan)}
    ck.compute()
    ck.x_scan.setCurrentIndex([ck.x_scan.itemData(i) for i in range(ck.x_scan.count())].index(testdata.STACK))
    ck.explore_i0()
    # Compare and Fit
    lib = win.library
    lib.add_folder(reduced)
    for r in lib.requests:
        r.show = True
        r.fit = r.signal != "TEY"
    lib.changed.emit()
    win.compare.table.selectRow(0)
    win.reduce.send_to_compare()
    fit = win.fit
    fit.refresh()
    fit.select_target("glass", "sdd-scaled")
    fit.check_components([("NaF", "sdd-scaled"), ("CaF2", "sdd-scaled")])
    fit.bootstrap.setValue(10)
    fit.run_lcf()
    fit.run_pca()
    fit.n_comp.setValue(2)
    fit.run_mcr()
    fit.select_trace("NaF", "sdd-scaled")
    fit.pk_n.setValue(1)
    fit.pk_centres.setText("692")
    fit.run_peaks()
    win.jobs.wait_idle(60000)              # LCF and MCR-ALS run through window.jobs (WP5 I33)
    fit.bootstrap.setValue(200)
    # Maps
    mp = win.maps
    mp.set_hypercube(Hypercube.load(proj, scan))
    mp.line.setCurrentIndex(1)
    mp.draw_elements()
    mp.draw_rgb()
    mp.n_k.setValue(2)
    mp.run_clusters()
    win.jobs.wait_idle(30000)                     # elemental maps and clusters are background jobs
    pump(qapp, 10)
    return win
