"""The Check perspective, offscreen, on the synthetic project."""
import pytest

pytest.importorskip("pyqtgraph")

from aurora.core import testdata  # noqa: E402
from aurora.core.io.config import Zone  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from aurora.core.qc import count_rate_probe  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_check_perspective(qapp, fake_project):
    from aurora.app.main import MainWindow
    win = MainWindow()
    proj = Project.discover(fake_project)
    win.home.set_project(proj)
    qapp.processEvents()
    ck = win.check
    assert ck.table.rowCount() == 1 and ck.table.item(0, 1).text() == "alpha_beta"
    ses = win.session
    sid = ses.stack_id(proj.find(testdata.STACK))
    # the worker's job, done synchronously
    ck._rates = {sid: count_rate_probe(proj, proj.find(testdata.STACK))}
    ses.stack(sid)
    ck.compute()
    qapp.processEvents()
    assert ck.table.item(0, 5).text() == "ok" and ck.table.item(0, 10).text() == ""
    assert ck.i0_list.count() == 1 and "looks good" in ck.i0_list.item(0).text()
    ck._auto_select()
    assert ses.zones.good_i0[sid] is True
    # a zone on the stack: the SDD panel fills
    sd = ses.stack_loaded(sid)
    x0, x1, y0, y1 = sd.bbox
    z = ses.new_zone("z")
    z.rects.append([x0, x1, y0, y1])
    ses.touch()
    ses.select_stack(sid)
    ses.select_zone(z.id)
    qapp.processEvents()
    assert "total XRF" in ck.sdd_note.text()
    win.show_perspective("check")
    assert win.rail_buttons["check"].isChecked()
    win.close()
