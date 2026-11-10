"""I15: a pending edit is saved to the project it belongs to, nothing is written when nothing changed, the top-bar
indicator follows the save state, and Reload keeps the place (holder, zone, stack, loaded stacks, undo history)."""
import json
import os
import time

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402

from aurora.core import testdata  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from wp2_helpers import STACK_B, draw_zone, pump, session, sid_of, two_stack_project, wait, window  # noqa: E402

SID_A = sid_of(testdata.STACK)


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_a_pending_edit_is_saved_to_its_own_project(qapp, tmp_path):
    a = testdata.make_fake_project(tmp_path / "A")
    b = testdata.make_fake_project(tmp_path / "B")
    ses = session(a)
    z = ses.new_zone("in A")
    z.rects.append([-0.6, 0.35, 4.9, 5.6])
    ses.touch()                                       # a change made outside an edit still schedules the autosave
    assert ses._save_timer.isActive()
    ses.set_project(Project.discover(b))
    wait(qapp, 1500)
    labels = [zz["label"] for zz in json.loads((a / ".aurora" / "session.json").read_text(encoding="utf-8"))
              ["holders"]["holderA"]["zones"]]
    assert labels == ["in A"]
    assert not (b / ".aurora" / "session.json").exists()
    # the same through an edit
    with ses.edit("new zone 1"):
        ses.holder_zones().zones.append(type(z)(id=1, label="in B"))
    ses.set_project(Project.discover(a))
    wait(qapp, 1200)
    assert [zz["label"] for zz in json.loads((b / ".aurora" / "session.json").read_text(encoding="utf-8"))
            ["holders"]["holderA"]["zones"]] == ["in B"]
    assert [zz.label for zz in ses.holder_zones().zones] == ["in A"]
    ses._save_timer.stop()


def test_an_edit_of_a_holder_it_did_not_name_is_still_saved(qapp, tmp_path):
    root = testdata.make_fake_project(tmp_path / "two", n_holders=2)
    ses = session(root)
    ses.set_holder("holderB")
    draw_zone(ses, "on holderB")
    ses.flush()
    ses.set_holder("holderA")
    with ses.edit("an edit named for holderA"):                 # changes holderB without saying so
        ses.holder_zones("holderB").zones[0].label = "renamed"
    ses.flush()
    on_disk = json.loads(ses.session_file.read_text(encoding="utf-8"))
    assert on_disk["holders"]["holderB"]["zones"][0]["label"] == "renamed"
    ses._save_timer.stop()


def test_opening_and_closing_without_edits_writes_nothing(qapp, fake_project):
    win = window(qapp, Project.discover(fake_project))
    ses = win.session
    sid = ses.stack_id(ses.project.find(testdata.STACK))
    win.reduce.load_stack(sid)                      # looking at a stack is not an edit
    win.jobs.wait_idle(30000); pump(qapp)
    assert ses.stack_loaded(sid) is not None
    wait(qapp, 1000)
    win.close()
    pump(qapp)
    assert not (fake_project / ".aurora" / "session.json").exists()
    assert not (fake_project / ".aurora" / "backups").exists()


def test_an_existing_session_is_not_rewritten_by_looking(qapp, fake_project):
    ses = session(fake_project)
    draw_zone(ses)
    ses.flush()
    before = ses.session_file.read_bytes()
    stamp = os.stat(ses.session_file).st_mtime_ns
    win = window(qapp, Project.discover(fake_project))
    s2 = win.session
    s2.stack(SID_A); s2.touch(); s2.select_zone(1); pump(qapp)
    wait(qapp, 1000)
    win.close()
    pump(qapp)
    assert ses.session_file.read_bytes() == before and os.stat(ses.session_file).st_mtime_ns == stamp


def test_an_edit_then_reload_keeps_both_zones(qapp, fake_project):
    ses = session(fake_project)
    draw_zone(ses, "one", [-0.6, -0.2, 4.9, 5.6])
    ses.flush()
    t0 = time.monotonic()
    draw_zone(ses, "two", [0.0, 0.35, 4.9, 5.6])
    proj = Project.discover(fake_project)
    assert ses._save_timer.isActive() and time.monotonic() - t0 < 0.2     # the edit still waits for its autosave
    ses.set_project(proj)                                                  # Reload
    assert [z.label for z in ses.holder_zones().zones] == ["one", "two"]
    on_disk = json.loads(ses.session_file.read_text(encoding="utf-8"))
    assert [z["label"] for z in on_disk["holders"]["holderA"]["zones"]] == ["one", "two"]
    ses._save_timer.stop()


def test_reload_keeps_the_place(qapp, tmp_path):
    root = two_stack_project(tmp_path / "beamtime", n_holders=2)
    win = window(qapp, Project.discover(root))
    ses = win.session
    ses.set_holder("holderA"); pump(qapp)
    sid_b = sid_of(STACK_B)
    for sid in (SID_A, sid_b):
        win.reduce.load_stack(sid)
        win.jobs.wait_idle(30000); pump(qapp)
    draw_zone(ses, "z1")
    z2 = draw_zone(ses, "z2", [0.0, 0.35, 4.9, 5.6])
    rows = [win.reduce.scan_list.item(i).data(Qt.UserRole) for i in range(win.reduce.scan_list.count())]
    win.reduce.scan_list.setCurrentRow(rows.index(sid_b))
    win.jobs.wait_idle(30000); pump(qapp)
    assert ses.current_stack == sid_b and ses.current_zone == z2.id
    steps = ses.undo_stack.count()
    before = (ses.current_holder, ses.current_zone, len(ses._stacks), ses.current_stack)
    kept = ses.stack_loaded(sid_b)
    win.home.reload_btn.click()                              # File > Reload (F5)
    win.jobs.wait_idle(30000); pump(qapp, 10)
    assert (ses.current_holder, ses.current_zone, len(ses._stacks), ses.current_stack) == before
    assert ses.stack_loaded(sid_b) is kept                   # not read again
    assert ses.undo_stack.count() == steps
    assert win.reduce.scan_list.currentItem().data(Qt.UserRole) == sid_b
    assert list(ses.holder_keys) == ["holderA", "holderB"]
    ses._save_timer.stop()
    win.close()


def test_reload_drops_a_changed_stack_and_appends_new_holders(qapp, tmp_path):
    root = two_stack_project(tmp_path / "beamtime")
    ses = session(root)
    a, b = ses.stack(SID_A), ses.stack(sid_of(STACK_B))
    # stack B gets one more energy folder; a new holder holderE arrives later the same day
    x, y = testdata.snake_grid()
    rng = np.random.default_rng(3)
    testdata._write_point_dir(root / "raw" / testdata.DAY / STACK_B / "CaF2_f_k_695_00eV", len(x), 695.0, rng)
    raw = root / "raw" / testdata.DAY
    testdata._write_survey(raw / "200000_holderE_survey", "holderE", "200000", rng)
    testdata._write_stack(raw / "201500_KF_f_k_stack", "KF_f_k", testdata.ENERGIES,
                          len(testdata.ENERGIES), x, y, rng, created="Fri Mar 14 20:15:00 2025")
    ses.set_project(Project.discover(root))
    assert ses.stack_loaded(SID_A) is a and ses.stack_loaded(sid_of(STACK_B)) is None
    assert list(ses.holder_keys) == ["holderA", "holderE"]
    ses._save_timer.stop()


def test_holder_keys_stay_when_a_later_day_repeats_a_name(qapp, tmp_path):
    from aurora.app.session import Session
    root = testdata.make_fake_project(tmp_path / "beamtime")
    ses = session(root)
    draw_zone(ses, "kept")
    ses.flush()
    # a second day with a holder of the same name: the zone tool would key them holderA_<day>; the old key stays
    day2 = root / "raw" / "2025-03-15"
    rng = np.random.default_rng(5)
    x, y = testdata.snake_grid()
    testdata._write_survey(day2 / "010000_holderA_survey", "holderA", "010000", rng)
    testdata._write_stack(day2 / "011500_LiF_f_k_stack", "LiF_f_k", testdata.ENERGIES,
                          len(testdata.ENERGIES), x, y, rng, created="Sat Mar 15 01:15:00 2025")
    ses.set_project(Project.discover(root))
    assert list(ses.holder_keys) == ["holderA", "holderA_2025-03-15"]
    assert ses.holder_keys["holderA"][0] == testdata.DAY and [z.label for z in ses.holder_zones("holderA").zones] == ["kept"]
    fresh = Session()                                         # a later start keeps the key the session file uses
    fresh.set_project(Project.discover(root))
    assert list(fresh.holder_keys) == ["holderA", "holderA_2025-03-15"]
    assert set(fresh.holder_zones("holderA").zones[0].settings) == {SID_A}
    ses._save_timer.stop(); fresh._save_timer.stop()


def test_the_save_indicator(qapp, fake_project):
    win = window(qapp, Project.discover(fake_project))
    ses = win.session
    ind = ses._ui.indicator
    assert ind is not None and ind.objectName() == "saveind"
    bar = ind.parentWidget()
    assert bar.objectName() == "topbar"
    lay = bar.layout()
    assert lay.indexOf(ind) == lay.indexOf(win.mode_btn) - 1          # just before the Easy / Advanced switch
    assert not ind.isVisible()                                         # no session file yet: nothing to say
    draw_zone(ses)
    assert ind.text() == "unsaved" and ind.isVisible() and "within a second" in ind.toolTip()
    ses.flush()
    assert ind.text().startswith("saved ") and len(ind.text()) == len("saved 12:03")
    ses.read_only = True
    draw_zone(ses)
    assert ind.text() == "read-only"
    ses.read_only = False
    ses._save_timer.stop()
    win.close()


def test_the_indicator_reads_in_both_schemes(qapp, fake_project):
    from aurora.app import theme
    win = window(qapp, Project.discover(fake_project))
    ses = win.session
    draw_zone(ses)
    ind = ses._ui.indicator
    try:
        for scheme in ("light", "dark"):
            theme.apply_theme(appearance=scheme)
            pump(qapp)
            fg = ind.palette().color(ind.foregroundRole()).name()
            bg = theme.TOKENS["night"]
            assert theme.contrast(fg, bg) >= 4.5, (scheme, fg, bg)
    finally:
        theme.apply_theme(appearance="light")
        ses._save_timer.stop()
        win.close()
