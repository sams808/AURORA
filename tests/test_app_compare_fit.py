"""Compare and Fit perspectives, offscreen, with synthetic spectra files."""
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")
from PySide6.QtCore import Qt  # noqa: E402


def _edge(E, e0=690.0, width=1.0, white=0.6, slope=0.002):
    return 0.2 + slope * (E - E[0]) + 1.0 / (1 + np.exp(-(E - e0) / width)) + white * np.exp(-((E - e0 - 2) / 1.5) ** 2)


def _write_john_csv(path: Path, label: str, E, y):
    rows = "\n".join(f"{e},{10 * v},60,83,{20 * v},60,83,{5 * v},60,83,{5 * v},60,83,2.0,{0.5 * v},{40 * v},{10 * v},False" for e, v in zip(E, y))
    path.write_text(f"# sample_label: {label}\nenergy,sdd1,sdd1_roi_lo,sdd1_roi_hi,sdd2,sdd2_roi_lo,sdd2_roi_hi,sdd3,sdd3_roi_lo,sdd3_roi_hi,"
                    "sdd4,sdd4_roi_lo,sdd4_roi_hi,I0,TEY,sdd_total,sdd_mean,bad_point\n" + rows, encoding="utf-8")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_compare_and_fit(qapp, tmp_path, fake_project):
    from aurora.app.main import MainWindow
    E = np.linspace(660, 740, 161)
    a, b = _edge(E, 690, 1.0, 0.6), _edge(E, 700, 1.6, 0.2)
    d = tmp_path / "reduced"; d.mkdir()
    _write_john_csv(d / "A" / "A_full_reduced.csv" if (d / "A").mkdir() is None else d, "A", E, a)
    (d / "B").mkdir(); _write_john_csv(d / "B" / "B_full_reduced.csv", "B", E, b)
    (d / "M").mkdir(); _write_john_csv(d / "M" / "M_full_reduced.csv", "M", E, 0.7 * a + 0.3 * b)
    win = MainWindow()
    lib, cmp_, fit = win.library, win.compare, win.fit
    added = lib.add_folder(d)
    assert len(added) == 6 and cmp_.table.rowCount() == 6                       # sdd-scaled + TEY per sample
    assert [r.norm for r in lib.requests] == ["larch"] * 6
    qapp.processEvents()
    assert "6 traces plotted" in cmp_.status.text()
    # table edits propagate: hide the TEY rows, tick Fit on the sdd rows, shift one
    for i, r in enumerate(lib.requests):
        if r.signal == "TEY":
            cmp_.table.item(i, cmp_.col("Show")).setCheckState(Qt.Unchecked)
        else:
            cmp_.table.item(i, cmp_.col("Fit")).setCheckState(Qt.Checked)
    cmp_.table.item(0, cmp_.col("Shift eV")).setText("0.5")
    qapp.processEvents()
    assert lib.requests[0].shift == 0.5 and sum(r.show for r in lib.requests) == 3 and sum(r.fit for r in lib.requests) == 3
    assert "3 traces plotted" in cmp_.status.text()
    cmp_.table.selectRow(0); cmp_.show_peaks.setChecked(True); qapp.processEvents()
    assert "E0" in cmp_.diag.plotItem.titleLabel.text and "peak maxima" in cmp_.peaks_label.text()
    out = cmp_.export(tmp_path / "comparison.csv")
    assert (tmp_path / "comparison.csv").exists() and (tmp_path / "comparison_config.json").exists()
    # LCF: target M from A and B
    fit.refresh()
    fit.select_target("M", "sdd-scaled")
    fit.check_components([fit.item_key(fit.components.item(i)) for i in range(fit.components.count())
                          if fit.item_key(fit.components.item(i)) != "M [sdd-scaled]"])
    fit.bootstrap.setValue(20)
    fit.run_lcf()
    assert win.jobs.wait_idle(30000)
    r = fit.lcf_result
    assert r is not None and abs(r.total - 1) < 1e-6 and r.rmse < 0.05
    coef = {fit.lcf_table.item(i, 0).data(Qt.UserRole): float(fit.lcf_table.item(i, 1).text())
            for i in range(fit.lcf_table.rowCount())}
    assert abs(coef["A [sdd-scaled]"] - 0.7) < 0.1 and abs(coef["B [sdd-scaled]"] - 0.3) < 0.1
    fit.export_lcf(tmp_path / "lcf.csv")
    assert (tmp_path / "lcf.csv").read_text().startswith("# LCF of M [sdd-scaled]")
    # PCA + MCR-ALS + ITFA on the three sdd rows
    fit.run_pca()
    assert fit.pca_result is not None and fit.pca_result.explained[0] > 0.5
    fit.n_comp.setValue(2); fit.run_mcr()
    assert win.jobs.wait_idle(30000)
    m = fit.mcr_result
    assert m is not None and m.C.shape == (3, 2) and fit.prop_table.rowCount() == 3
    files = fit.export_mcr(tmp_path / "mcr", m, "MCR")
    assert all(p.exists() for p in files)
    fit.select_key(fit.itfa_sample, "A [sdd-scaled]"); fit.itfa_pct.setText("100, 0"); fit._add_target()
    assert fit.targets.count() == 1
    fit.run_itfa()
    assert win.jobs.wait_idle(30000)
    assert fit.itfa_result is not None and fit.itfa_table.rowCount() == 3
    # config round trip
    lib.save(tmp_path / "cfg.json", {"spacing": 0.4})
    lib.remove(list(range(len(lib.requests))))
    assert cmp_.table.rowCount() == 0
    missing, extra = lib.load(tmp_path / "cfg.json")
    assert not missing and extra["spacing"] == 0.4 and len(lib.requests) == 6 and lib.requests[0].shift == 0.5
    # zone traces from Reduce land in the library as 'none'-normalised rows
    from aurora.core.model import Project
    from aurora.core import testdata
    proj = Project.discover(fake_project)
    win.home.set_project(proj)
    ses = win.session
    sid = ses.stack_id(proj.find(testdata.STACK))
    sd = ses.stack(sid); ses.touch(); ses.select_stack(sid)
    x0, x1, y0, y1 = sd.bbox
    z = ses.new_zone("zone one"); z.rects.append([x0, x1, y0, y1]); ses.touch(); ses.select_zone(z.id)
    qapp.processEvents()
    n = win.reduce.send_to_compare()
    assert n == 2 and any(r.label.startswith("zone one") and r.norm == "none" for r in lib.requests)
    win.close()
