"""I09: a loose folder of scan folders (John Bussey's 'CLS GUI/<scan folders>') is a project of its own, with its
.aurora/ inside it and real day keys from the h5 files; an .h5 path opens its scan folder's project."""
import re

import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.io.h5 import file_day
from aurora.core.model import Project
from aurora.core.names import date_in_text, parse_timestamp

DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@pytest.fixture
def loose(tmp_path):
    """tmp/'CLS GUI' with the scans of make_fake_project; tmp = tmp_path/'share' (the test's settings live beside)."""
    return testdata.make_fake_project(tmp_path / "share" / "CLS GUI", loose=True)


def test_timestamps_in_iso_and_ctime_form():
    assert parse_timestamp("Thu Nov  5 09:27:59 2026").isoformat() == "2026-11-05T09:27:59"
    assert parse_timestamp(b"Thu Nov  5 09:28:00 2026").date().isoformat() == "2026-11-05"
    assert parse_timestamp("2026-11-05T09:27:59").hour == 9
    assert parse_timestamp("2026-11-05 23:59:01.250+00:00").date().isoformat() == "2026-11-05"
    assert parse_timestamp("2026-11-05T09:27:59Z").tzinfo is not None
    assert parse_timestamp("Fri Nov 6 00:05:00 2026").date().isoformat() == "2026-11-06"
    for bad in ("", "not a date", "Thu Foo  5 09:27:59 2026", "Thu Nov 31 09:27:59 2026"):
        assert parse_timestamp(bad) is None, bad
    assert date_in_text("2026-11-05_092759_stack_data.h5") == "2026-11-05"
    assert date_in_text("holderA_survey_2026-11-05_010013_695.00eV.h5") == "2026-11-05"
    assert date_in_text("2026-13-45_x.h5") == "" and date_in_text("no date") == ""


def test_file_day_attribute_then_name(loose, tmp_path):
    stack_h5 = next((loose / testdata.STACK).glob("*.h5"))
    assert file_day(stack_h5) == testdata.DAY                    # root attribute (ctime form)
    survey_h5 = next((loose / testdata.SURVEY).glob("*.h5"))
    assert file_day(survey_h5) == testdata.DAY                   # no attribute: the date in the file name
    junk = tmp_path / "2026-11-07_000000_broken.h5"
    junk.write_bytes(b"not hdf5")
    assert file_day(junk) == "2026-11-07"                        # unreadable: its name


def test_loose_folder_is_its_own_project(loose, tmp_path):
    proj = Project.discover(loose)
    assert proj.root == (tmp_path / "share" / "CLS GUI").resolve() and proj.raw == proj.root and proj.loose
    assert [d.date for d in proj.days] == [testdata.DAY]
    assert all(DAY_RE.fullmatch(s.day) and s.day == "2026-11-05" for s in proj.scans), [s.day for s in proj.scans]
    assert [h.name for d in proj.days for h in d.holders] == ["N5"]
    assert sorted(s.status for s in proj.scans) == ["aborted", "complete", "survey", "test"]
    # an .h5 path resolves to its scan folder's project; so does the scan folder
    h5 = next((loose / testdata.STACK).glob("*.h5"))
    assert Project.discover(h5).root == proj.root
    assert Project.discover(loose / testdata.STACK).root == proj.root
    # the raw/<day> layout keeps its day folder as the key
    fake = Project.discover(testdata.make_fake_project(tmp_path / "beamtime"))
    assert not fake.loose and [d.date for d in fake.days] == [testdata.DAY]


def test_hidden_folders_are_not_scans(loose):
    (loose / ".aurora" / "cache").mkdir(parents=True)
    proj = Project.discover(loose)
    assert not any(s.name.startswith(".") for s in proj.scans) and len(proj.scans) == 4
    assert all(not d.others for d in proj.days)


def test_loose_scans_without_a_date_take_the_folder_name(tmp_path):
    root = testdata.make_fake_project(tmp_path / "CLS GUI", loose=True)
    for h5 in (root / testdata.SURVEY).glob("*.h5"):           # a survey whose file name carries no date
        h5.rename(h5.with_name("holderA_survey.h5"))
    proj = Project.discover(root)
    days = {s.name: s.day for s in proj.scans}
    assert days[testdata.SURVEY] == "CLS GUI" and days[testdata.STACK] == testdata.DAY


# ----------------------------------------------------------------------------- with the session and the window
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_loose_stack_loads_and_the_session_lives_inside(qapp, loose):
    from aurora.app.session import Session
    share = loose.parent
    before = sorted(p.name for p in share.iterdir())
    proj = Project.discover(loose)
    ses = Session()
    ses.set_project(proj)
    assert list(ses.holder_keys) == ["N5"] and ses.holder_day("N5") == testdata.DAY
    sid = ses.stack_id(proj.find(testdata.STACK))
    assert sid == f"{testdata.DAY}_{testdata.STACK}"
    sd = ses.stack(sid)                                           # no KeyError: the complete stack
    assert sd.n_energies == len(testdata.ENERGIES) and sd.n_pix == 12 and np.isfinite(sd.i0).all()
    z = ses.new_zone("1 alpha")
    with ses.edit("add a rectangle to zone 1"):
        z.rects.append(list(sd.bbox))
    ses.flush()
    assert (loose / ".aurora" / "session.json").is_file()
    assert sorted(p.name for p in share.iterdir()) == before == ["CLS GUI"]       # nothing new beside it
    assert sid in z.settings


def test_open_path_h5_of_a_loose_folder_then_reduce(qapp, loose):
    from PySide6.QtCore import Qt
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.resize(1300, 820)
    win.show()
    h5 = next((loose / testdata.STACK).glob("*.h5"))
    win.home.open_path(h5)
    win.jobs.wait_idle(30000)
    qapp.processEvents()
    assert win.home.project is not None and win.home.project.root == loose.resolve()
    assert win.crumb.text() == f"CLS GUI ▸ {testdata.DAY} ▸ N5"
    ses = win.session
    sid = f"{testdata.DAY}_{testdata.STACK}"
    win.show_perspective("reduce")
    rows = [win.reduce.scan_list.item(i).data(Qt.UserRole) for i in range(win.reduce.scan_list.count())]
    assert sid in rows
    win.reduce.scan_list.setCurrentRow(rows.index(sid))
    win.jobs.wait_idle(30000)
    qapp.processEvents()
    sd = ses.stack_loaded(sid)
    assert sd is not None
    z = ses.new_zone("1 alpha")
    with ses.edit("add a rectangle to zone 1"):
        z.rects.append(list(sd.bbox))
    ses.select_zone(z.id)
    assert win.reduce.send_to_compare() >= 1
    labels = [r.label for r in win.library.requests]
    assert labels and all("09:27" in lab for lab in labels), labels
    ses._save_timer.stop()
    win.close()
