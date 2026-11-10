"""The demo beamtime carries the whole Easy job (persona items PM-01, PM-02, PS-01): on a fresh `aurora demo`, in Easy
mode, the Survey's first view holds the stack footprint on its bright pads; two samples drawn on the two halves of the
stack, sent with Add to Check and saved with Save ticked → Compare plot as two normalised spectra in Compare (no
'normalisation failed') and are offered as Fit targets. The buttons are pressed with QTest."""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


@pytest.fixture
def easy_demo(qapp, tmp_path):
    import contextlib
    import io
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.cli import cmd_demo
    from aurora.core.model import Project
    with contextlib.redirect_stdout(io.StringIO()):
        assert cmd_demo(tmp_path / "demo") == 0
    mode.set_advanced(False)
    win = MainWindow()
    win.resize(1200, 760)
    win.show()
    win.home.set_project(Project.discover(tmp_path / "demo"))
    pump(qapp)
    yield win
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


def _click(widget):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    assert widget.isVisible() and widget.isEnabled(), widget.text()
    QTest.mouseClick(widget, Qt.LeftButton)


def test_survey_first_view_holds_the_footprint_on_its_pads(qapp, easy_demo):
    from aurora.core import testdata
    win = easy_demo
    win.show_perspective("survey")
    pump(qapp)
    sv = win.survey
    assert sv._survey is not None and sv.outside_counts() == (0, 0)
    x0, x1, y0, y1 = sv.view_rect()
    fx0, fx1, fy0, fy1 = sv._bbox(win.session.project.find(testdata.STACK))
    assert x0 <= fx0 and fx1 <= x1 and y0 <= fy0 and fy1 <= y1           # the footprint is in the first view
    assert "outside the survey map" not in sv.hint.text()
    s = sv._survey
    vals = s.values("F Kα", per_i0=True, log10=False)
    inside = (s.x >= fx0) & (s.x <= fx1) & (s.y >= fy0) & (s.y <= fy1)
    assert float(np.nanmean(vals[inside])) > 3 * float(np.nanmean(vals[~inside]))   # on a bright pad
    assert not sv.sketch_card.isVisible() and sv.sketch_btn.isVisible()             # no sketch in the demo


def test_two_samples_end_in_two_spectra_in_compare_and_fit(qapp, easy_demo):
    from aurora.core import testdata
    win = easy_demo
    ses = win.session
    sid = ses.stack_id(ses.project.find(testdata.STACK))
    win.show_perspective("reduce")
    sd = ses.stack(sid)
    ses.touch()
    ses.select_stack(sid)
    pump(qapp)
    x0, x1, y0, y1 = sd.bbox
    for label, rect in (("1 alpha", [x0, (x0 + x1) / 2, y0, y1]), ("2 beta", [(x0 + x1) / 2, x1, y0, y1])):
        with ses.edit(f"draw {label}"):
            z = ses.new_zone(label)
            z.rects.append(rect)
        ses.select_zone(z.id)
        pump(qapp)
        _click(win.reduce.add_check_btn)
        pump(qapp)
    assert [z.label for z in ses.holder_zones().zones] == ["1 alpha", "2 beta"]
    win.show_perspective("check")
    pump(qapp)
    assert win.jobs.wait_idle(60000)
    pump(qapp)
    ck = win.check
    rows = ck.queue_rows()
    assert [r["sample"] for r in rows] == ["1 alpha", "2 beta"]
    assert all(r["state"] == "ready" and r["looks_ok"] and "bad" not in r["levels"] for r in rows), rows
    ck.ask_overwrite = lambda paths: "yes"
    _click(ck.save_btn)
    assert win.jobs.wait_idle(60000)
    pump(qapp)
    assert "added to Compare" in ck.queue_note.text()
    win.show_perspective("compare")
    pump(qapp)
    cp = win.compare
    cp.replot()
    traces = cp.plot_traces()
    samples = {t.r.label for t in traces}
    assert {"1 alpha", "2 beta"} <= samples
    for t in traces:
        assert np.isfinite(t.base).sum() > 0.9 * len(t.base), (t.r.label, t.r.signal, t.nr.note)
        assert not t.nr.e0_note, (t.r.label, t.nr.e0_note)                 # E0 near the tabulated F K edge
    assert "normalisation failed" not in cp.status.text() and "NaN" not in cp.status.text()
    e0 = {t.r.label: t.nr.e0 for t in traces if t.r.signal != "TEY"}
    assert all(abs(e - 696.7) < 15 for e in e0.values()), e0
    for r in win.library.requests:                         # Tutorial 5: tick Fit on the fluorescence rows
        r.fit = r.signal != "TEY"
    win.library.changed.emit()
    win.show_perspective("fit")
    pump(qapp)
    win.fit.refresh()
    targets = [win.fit.target.itemText(i) for i in range(win.fit.target.count())]
    assert any(t.startswith("1 alpha") for t in targets) and any(t.startswith("2 beta") for t in targets), targets


def test_footprints_outside_the_survey_are_in_view_and_counted(qapp, fake_project):
    """PM-02 (b, c): the test variant's survey does not reach its stacks; the first view still holds every footprint
    and zone, and the line under the map says how many lie outside the survey map."""
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.core import testdata
    from aurora.core.model import Project
    mode.set_advanced(False)
    win = MainWindow()
    win.resize(1200, 760)
    win.show()
    try:
        win.home.set_project(Project.discover(fake_project))
        win.show_perspective("survey")
        pump(qapp, 10)
        sv = win.survey
        assert sv.outside_counts() == (3, 0)                       # complete, aborted and test footprints
        assert "3 measured areas lie outside the survey map" in sv.hint.text()
        vx0, vx1, vy0, vy1 = sv.view_rect()
        for name in (testdata.STACK, testdata.ABORTED, testdata.TEST):
            x0, x1, y0, y1 = sv._bbox(win.session.project.find(name))
            assert vx0 <= x0 and x1 <= vx1 and vy0 <= y0 and y1 <= vy1, name
        r = sv._survey_rect()
        assert vx0 <= r[0] and r[1] <= vx1 and vy0 <= r[2] and r[3] <= vy1           # and the survey map too
        ses = win.session
        with ses.edit("a zone far away"):
            z = ses.new_zone("far")
            z.rects.append([3.0, 3.5, 9.0, 9.5])
        pump(qapp)
        assert sv.outside_counts() == (3, 1) and "and 1 zone lie outside" in sv.hint.text()
        sv.fit_view()
        assert sv.view_rect()[3] >= 9.5
    finally:
        win.session._save_timer.stop()
        win.close()
