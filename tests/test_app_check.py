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
    from aurora.core.names import label_from_name
    assert ck.table.rowCount() == 1 and ck.table.item(0, 1).text() == label_from_name(testdata.STACK)
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
    # no roi_bins.json in the synthetic project: the new zone is on the window of the edge's own line, John Bussey's
    # 60:83 for F Kα (final polish g06#3), not the total XRF
    assert "line F Kα" in ck.sdd_note.text() and "60:83" in ck.sdd_note.text(), ck.sdd_note.text()
    win.show_perspective("check")
    assert win.rail_buttons["check"].isChecked()
    win.close()


def test_good_i0_stacks_are_read_in_a_job(qapp, tmp_path):
    """A zone previewed with 'good I0' while the stacks marked good are not loaded: Reduce never reduces them on the
    GUI thread (13 s frozen on a beamtime without sgm_report caches, threads-5). One job reads them with progress and
    Cancel, the preview says it is waiting, and it is drawn once they are in; the curve is the one of every good stack."""
    import threading
    import numpy as np
    from aurora.app.main import MainWindow
    from aurora.app.session import Session
    from aurora.core.spectra import good_i0_curve
    from aurora.core.stack import StackData
    proj = Project.discover(testdata.make_fake_project(tmp_path / "beamtime", n_holders=2))
    win = MainWindow()
    win.show()
    win.home.set_project(proj)
    qapp.processEvents()
    ses = win.session
    sid, good_sid = ses.stack_id(proj.find(testdata.STACK)), ses.stack_id(proj.find(testdata.STACK2))
    ses.set_holder("holderA")
    sd = ses.stack(sid)
    ses.zones.good_i0[good_sid] = True                       # marked good, not loaded (a reopened session)
    x0, x1, y0, y1 = sd.bbox
    z = ses.new_zone("z")
    z.rects.append([x0, x1, y0, y1])
    ses.touch()
    z.settings[sid].i0_source = "good"
    threads = []
    real = Session.stack
    gate = threading.Event()        # the job's read waits until the waiting state was checked (a fast runner's job
                                    # finished before the second update_preview, which then drew at once)

    def spy(self, s, progress=None):
        if s not in self._stacks:
            on_gui = threading.current_thread() is threading.main_thread()
            threads.append(on_gui)
            if not on_gui:
                gate.wait(30)
        return real(self, s, progress=progress)
    Session.stack = spy
    try:
        ses.select_stack(sid)
        ses.select_zone(z.id)
        win.show_perspective("reduce")
        win.reduce.update_preview()
        job = win.jobs.job("stacks:good-i0")
        assert job is not None and job.label == "reading the good-I0 scans"
        assert win.reduce.preview._traces == [] and win.reduce.preview._waiting == "reading the good-I0 scans …"
        gate.set()
        assert win.jobs.wait_idle(30000)
        qapp.processEvents()
    finally:
        gate.set()
        Session.stack = real
    assert threads == [False]                                # read once, by the job's worker
    assert ses.stack_loaded(good_sid) is not None and win.reduce.preview._traces
    good = ses.stack_loaded(good_sid)
    want = good_i0_curve(sd.E, [(good.E, good.i0_mean())])
    assert np.allclose(ses.good_curve_for(sd.E), want, equal_nan=True) and isinstance(good, StackData)
    win.session._save_timer.stop()
    win.close()
