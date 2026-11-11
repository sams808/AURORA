"""The demo's Easy path end to end (integration step 4): on a fresh `aurora demo`, in Easy mode at 1200 x 760, a sample
is drawn on the Survey with real mouse events (its Zone preview shows the emission while the drag is held, then the
spectrum of the covered stack), opened in Reduce, named with the field Add to Check opens, checked and saved, drawn in
Compare and put in a built report."""
from __future__ import annotations

import contextlib
import io

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from wp3_helpers import pump, qdrag, qrelease  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_the_demo_carries_the_easy_job_from_the_survey_to_the_report(qapp, tmp_path):
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.app.settings import settings
    from aurora.app.zone_tools import FIRST_MS
    from aurora.cli import cmd_demo
    from aurora.core import testdata
    from aurora.core.model import Project
    with contextlib.redirect_stdout(io.StringIO()):
        assert cmd_demo(tmp_path / "demo") == 0
    s = settings()
    s.setValue("report_author", "A. Researcher")       # no author prompt
    s.sync()
    mode.set_advanced(False)
    win = MainWindow()
    win.resize(1200, 760)
    win.show()
    try:
        win.home.set_project(Project.discover(tmp_path / "demo"))
        pump(qapp, 10)
        ses, sv = win.session, win.survey
        win.show_perspective("survey")
        pump(qapp, 10)
        assert not sv.sketch_card.isVisible()                       # no sketch in the demo: no card
        scan = ses.project.find(testdata.STACK)
        x0, x1, y0, y1 = sv._bbox(scan)
        xm = (x0 + x1) / 2
        path = [(x0 - 0.05 + f * (xm - x0), y0 - 0.05 + f * (y1 - y0 + 0.1)) for f in np.linspace(0, 1, 5)]
        sv.set_tool("rect+")
        qdrag(qapp, sv.map, path[:3], release=False)                  # the drag held: the live preview
        QTest.qWait(FIRST_MS + 150)
        pump(qapp)
        zp = sv.zone_preview
        live = zp.emission.data.get("per_point") if isinstance(getattr(zp.emission, "data", None), dict) else None
        assert live is not None and np.isfinite(np.asarray(live, float)).any()
        qdrag(qapp, sv.map, path[3:], release=False)
        qrelease(qapp, sv.map, path[-1])
        pump(qapp, 10)
        win.jobs.wait_idle(60000)
        pump(qapp, 10)
        z = ses.zone()
        assert z is not None and z.label == "zone 1"                # the demo's sheet IDs are not in the stack name
        sid = ses.stack_id(scan)
        assert sid in z.settings and zp.xas.isVisible() and np.isfinite(zp.xas_sum()).sum() > 10
        zp.open_btn.click()                                          # open the sample in Reduce
        pump(qapp, 10)
        win.jobs.wait_idle(60000)
        pump(qapp, 10)
        rd = win.reduce
        assert win.stack.currentWidget() is rd and ses.current_zone == z.id and ses.current_stack == sid
        assert rd.add_check_btn.isEnabled()
        QTest.mouseClick(rd.add_check_btn, Qt.LeftButton)
        pump(qapp)
        assert not rd.name_row.isHidden()                           # 'Name this sample:'
        QTest.keyClicks(rd.name_edit, "1 alpha")
        QTest.keyClick(rd.name_edit, Qt.Key_Return)
        pump(qapp)
        assert ses.zone(z.id).label == "1 alpha" and ses.zone(z.id).group == testdata.GROUP1     # the sheet's group
        win.show_perspective("check")
        pump(qapp)
        assert win.jobs.wait_idle(60000)
        pump(qapp, 10)
        ck = win.check
        rows = ck.queue_rows()
        assert [r["sample"] for r in rows] == ["1 alpha"] and rows[0]["state"] == "ready" and rows[0]["looks_ok"], rows
        ck.ask_overwrite = lambda paths: "yes"
        QTest.mouseClick(ck.save_btn, Qt.LeftButton)
        assert win.jobs.wait_idle(60000)
        pump(qapp)
        assert "added to Compare" in ck.queue_note.text(), ck.queue_note.text()
        win.show_perspective("compare")
        pump(qapp)
        cp = win.compare
        cp.replot()
        traces = [t for t in cp.plot_traces() if t.r.label == "1 alpha"]
        assert traces and all(np.isfinite(t.base).sum() > 0.9 * len(t.base) for t in traces)
        win.show_perspective("report")
        pump(qapp, 10)
        rp = win.report
        assert rp.build_btn.isEnabled(), rp.build_btn.toolTip()
        QTest.mouseClick(rp.build_btn, Qt.LeftButton)
        assert win.jobs.wait_idle(240000)
        pump(qapp, 10)
        res = rp._result
        assert res is not None and res.pdf.is_file() and res.entries >= 1, rp.status.text()
        assert (tmp_path / "demo" / "reports" / "aurora") in res.pdf.parents
    finally:
        win.jobs.wait_idle(30000)
        win.session._save_timer.stop()
        win.session.release_lock()
        win.close()
