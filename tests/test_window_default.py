"""The window of a new sample without a fixed line for the edge's own line (final polish g06#3, blank BL-26): the
standard 60:83 for an F K stack (John Bussey's GUI default, so the saved files equal his), kept through undo / redo and
a re-attach, where a line with windows of its own needs no fixed bins of that name."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_without_roi_bins_a_new_f_k_sample_starts_on_60_83_and_keeps_it(qapp, tmp_path):
    from aurora.app.session import Session
    from aurora.core import testdata
    from aurora.core.model import Project
    from aurora.core.xrf_windows import TOTAL_LABEL
    root = testdata.make_fake_project(tmp_path / "bt")
    proj = Project.discover(root)
    assert proj.roi_bins is None or not proj.roi_bins.labels           # no fixed line for F Kα
    ses = Session()
    ses.set_project(proj)
    try:
        sid = f"{testdata.DAY}_{testdata.STACK}"
        sd = ses.stack(sid)
        ses.touch()
        assert sd.labels == [TOTAL_LABEL]
        john = {d: [60, 83] for d in ("sdd1", "sdd2", "sdd3", "sdd4")}
        assert ses.window_default(ses.scan_of(sid), sd) == {"line": "F Kα", "roi": john}
        z = ses.new_zone("A")
        with ses.edit(f"add a rectangle to zone {z.id}"):
            z.rects.append([-0.6, 0.35, 4.9, 5.6])
        st = z.settings[sid]
        assert (st.line, st.roi) == ("F Kα", john)
        done = z.to_json()
        assert ses.undo() and ses.redo()                                  # redo attaches the zone again
        z = ses.holder_zones().zones[0]
        assert z.to_json() == done
        ses.attach_scans(z)
        assert (z.settings[sid].line, z.settings[sid].roi) == ("F Kα", john)    # not 'total XRF' with 60:83
    finally:
        ses._save_timer.stop()
        ses.release_lock()
