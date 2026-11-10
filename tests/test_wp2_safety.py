"""Session and project data safety (review of 0.4.0): one AURORA per project (a lock in .aurora with pid, host and
time, a second one read-only, a stale lock taken over), edits that could not be saved are never dropped at close or at
a project switch without asking, malformed geometry is refused when a file is read, imports land on the project's
holders, a damaged Compare library is kept, results of a job started for one project never land in another, recovery
takes the newest state, rules edits are undo steps, a BOM is accepted, snapshots survive a time-zone move, and a
failing save neither blocks nor leaves temporary files."""
import datetime as dt
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("pyqtgraph")

from aurora.core import testdata  # noqa: E402
from aurora.core.io.config import ZonesFile  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from wp2_helpers import FakeClock, draw_zone, holders_json, pump, session, window  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _lock(root: Path) -> Path:
    return root / ".aurora" / "session.lock"


def _answer(monkeypatch, text: str, calls: list):
    """QMessageBox.exec of the unsaved-edits question clicks the button named `text` (and records the question)."""
    from PySide6.QtWidgets import QMessageBox

    def fake_exec(box):
        calls.append(box.text())
        btn = next(b for b in box.buttons() if b.text().replace("&", "") == text)
        btn.click()
        return 0
    monkeypatch.setattr(QMessageBox, "exec", fake_exec)


def _protect(monkeypatch, root: Path) -> None:
    from aurora.core.io.safety import ENV
    monkeypatch.setenv(ENV, os.pathsep.join(filter(None, [os.environ.get(ENV, ""), str(root)])))


# ----------------------------------------------------------------------------- data-safety-1: one AURORA per project
def test_a_project_another_process_holds_opens_read_only_until_it_is_gone(qapp, fake_project):
    """A second AURORA process on the project: this session is read-only and names the holder; once the holder is
    gone (its process ended without releasing, as after a crash), Reload takes the stale lock over."""
    code = ("import os, sys; from aurora.core.io.lock import ProjectLock; "
            "lock, other = ProjectLock.acquire(sys.argv[1], 'AURORA other'); "
            "print('held' if lock else 'refused', os.getpid(), flush=True); sys.stdin.readline()")
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    proc = subprocess.Popen([sys.executable, "-c", code, str(fake_project)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True, env=env)
    try:
        word, pid = proc.stdout.readline().split()         # the interpreter's pid (a venv launcher may sit between)
        assert word == "held"
        ses = session(fake_project)
        assert ses.read_only and f"process {pid}" in ses.read_only_reason
        assert "open in another AURORA" in ses.read_only_reason
        draw_zone(ses, "drawn in the second window")
        assert ses.save() is False and not ses.session_file.exists()
        assert ses.flush() is False                         # the edit is still unsaved: confirm_leave would ask
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.wait(30)
        assert _lock(fake_project).exists()                 # the holder ended without releasing (a crash)
        ses.set_project(Project.discover(fake_project))     # Reload
        assert not ses.read_only and ses._lock is not None
        draw_zone(ses, "after the other one closed")
        assert ses.flush() is True
        assert [z["label"] for z in holders_json(ses.session_file)["N5"]["zones"]] == \
            ["drawn in the second window", "after the other one closed"]
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(30)


def test_stale_and_live_locks_of_another_computer(qapp, fake_project):
    from aurora.core.io.lock import LOCK_STALE_S, LockInfo
    lock = _lock(fake_project)
    lock.parent.mkdir(parents=True)
    now = time.time()
    live = LockInfo(pid=4242, host="another-pc", user="colleague", app="AURORA 0.4.0", since=now - 60, time=now - 30,
                    token="x")
    lock.write_bytes(live.to_bytes())
    ses = session(fake_project)
    assert ses.read_only and "another-pc" in ses.read_only_reason and "colleague" in ses.read_only_reason
    ses.release_lock()
    old = LockInfo(pid=4242, host="another-pc", app="AURORA 0.4.0", since=now - 3 * LOCK_STALE_S,
                   time=now - 2 * LOCK_STALE_S, token="y")
    lock.write_bytes(old.to_bytes())
    ses2 = session(fake_project)
    assert not ses2.read_only and LockInfo.read(lock).pid == os.getpid()


def test_a_lock_taken_over_stops_the_saves_and_says_so(qapp, fake_project):
    from aurora.core.io.lock import LockInfo
    ses = session(fake_project)
    draw_zone(ses, "mine")
    assert ses.flush()
    before = ses.session_file.read_bytes()
    now = time.time()
    _lock(fake_project).write_bytes(LockInfo(pid=4242, host="another-pc", app="AURORA 0.4.0", since=now, time=now,
                                             token="theirs").to_bytes())
    warned = []
    ses.warned.connect(lambda t, m: warned.append(m))
    draw_zone(ses, "after the take-over")
    assert ses.flush() is False and ses.read_only
    assert ses.session_file.read_bytes() == before
    assert warned and "another-pc" in warned[-1]
    assert LockInfo.read(_lock(fake_project)).token == "theirs"         # their lock is left alone


def test_a_session_changed_on_disk_is_kept_before_it_is_written_over(qapp, fake_project):
    """Two sessions on one project (the reviewer's p01b): every zone drawn in either ends up in some file."""
    a = session(fake_project)
    b = session(fake_project)
    draw_zone(a, "A1"); a.flush()
    draw_zone(b, "B1"); b.flush()
    draw_zone(b, "B2"); b.flush()
    draw_zone(a, "A2"); a.flush()
    draw_zone(a, "A3"); a.flush()
    files = [a.session_file, a.session_file.with_name("session.json.bak")] + sorted(a.backup_dir.glob("*.json"))
    labels = set()
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        labels |= {z["label"] for z in d["holders"].get("N5", {}).get("zones", [])}
    assert {"A1", "A2", "A3", "B1", "B2"} <= labels
    assert any(json.loads(f.read_text(encoding="utf-8")).get("backup", {}).get("reason", "").startswith("changed on disk")
               for f in a.backup_dir.glob("*.json"))


def test_the_lock_is_released_at_close_and_at_a_project_switch(qapp, fake_project, tmp_path):
    other = testdata.make_fake_project(tmp_path / "other")
    win = window(qapp, Project.discover(fake_project))
    try:
        assert _lock(fake_project).exists()
        win.home.set_project(Project.discover(other))
        pump(qapp)
        assert not _lock(fake_project).exists() and _lock(other).exists()
    finally:
        win.session._save_timer.stop()
        assert win.close()
        pump(qapp)
    assert not _lock(other).exists()


# ----------------------------------------------------------------------------- data-safety-2: unsaved edits
def test_closing_with_edits_that_could_not_be_saved_asks(qapp, fake_project, monkeypatch):
    ses0 = session(fake_project)
    draw_zone(ses0, "saved zone")
    ses0.flush()
    ses0.release_lock()
    sf = ses0.session_file
    os.chmod(sf, stat.S_IREAD)                                  # a read-only file (or a read-only share)
    try:
        win = window(qapp, Project.discover(fake_project))
        ses = win.session
        assert ses.read_only and "read-only" in ses.read_only_reason
        draw_zone(ses, "an hour of zone work")
        calls = []
        _answer(monkeypatch, "Cancel", calls)
        assert win.close() is False and win.isVisible() and len(calls) == 1
        assert "not saved" in calls[0]
        _answer(monkeypatch, "Discard", calls)
        assert win.close() is True and len(calls) == 2
        assert [z["label"] for z in holders_json(sf)["N5"]["zones"]] == ["saved zone"]
    finally:
        os.chmod(sf, stat.S_IWRITE | stat.S_IREAD)


def test_export_from_the_question_keeps_the_edits(qapp, fake_project, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    _protect(monkeypatch, fake_project)                         # stands in for a read-only share
    win = window(qapp, Project.discover(fake_project))
    ses = win.session
    assert ses.read_only and "cannot be saved here" in ses.read_only_reason
    assert win.session._ui.state == "read-only" and win.session._ui.indicator.property("tone") == "warn"
    assert "cannot be saved here" in win.session._ui.last_message          # the non-modal box at open
    draw_zone(ses, "rescued zone")
    out = tmp_path / "rescued_zones_all.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), "JSON (*.json)"))
    calls = []
    _answer(monkeypatch, "Export zones…", calls)
    assert win.close() is True and len(calls) == 1
    assert [z.label for z in ZonesFile.load(out).holders["N5"].zones] == ["rescued zone"]
    assert not (fake_project / ".aurora").exists()              # nothing was written into the protected project


def test_opening_another_project_with_unsaved_edits_asks(qapp, fake_project, tmp_path, monkeypatch):
    other = testdata.make_fake_project(tmp_path / "other")
    _protect(monkeypatch, fake_project)
    win = window(qapp, Project.discover(fake_project))
    ses = win.session
    draw_zone(ses, "unsaved zone")
    calls = []
    _answer(monkeypatch, "Cancel", calls)
    win.home.set_project(Project.discover(other))
    pump(qapp)
    assert len(calls) == 1 and Path(ses.project.root) == fake_project
    assert [z.label for z in ses.holder_zones().zones] == ["unsaved zone"]
    assert Path(win.home.project.root) == fake_project
    _answer(monkeypatch, "Discard", calls)
    win.home.set_project(Project.discover(other))
    pump(qapp)
    assert len(calls) == 2 and Path(ses.project.root) == other
    ses._save_timer.stop()
    win.close()


def test_a_read_only_project_only_looked_at_closes_without_a_question(qapp, fake_project, monkeypatch):
    """Memberships recomputed after a stack load (touch) are not edits of the user: nothing to ask about."""
    _protect(monkeypatch, fake_project)
    win = window(qapp, Project.discover(fake_project))
    win.session._mark_dirty(user=False)
    calls = []
    _answer(monkeypatch, "Cancel", calls)
    assert win.close() is True and calls == []


def test_a_writable_project_closes_without_a_question(qapp, fake_project, monkeypatch):
    win = window(qapp, Project.discover(fake_project))
    draw_zone(win.session, "kept")
    calls = []
    _answer(monkeypatch, "Cancel", calls)
    assert win.close() is True and calls == []
    assert [z["label"] for z in holders_json(win.session.session_file)["N5"]["zones"]] == ["kept"]


# ----------------------------------------------------------------------------- data-safety-3: malformed geometry
@pytest.mark.parametrize("zone, word", [({"id": 7, "rects": [[-0.6, 0.35, 4.9]]}, "rectangle"),
                                        ({"id": 7, "paint": [[-0.5, 5.0]]}, "painted square"),
                                        ({"id": 7, "polys": [[[-0.6], [0.3], [0.0]]]}, "polygon vertex"),
                                        ({"id": 7, "erects": [[0, 1, 2]]}, "erase rectangle"),
                                        ({"id": 7, "rects": [[0, 1, "a", 2]]}, "rectangle")])
def test_malformed_geometry_is_refused_when_read(zone, word):
    with pytest.raises(ValueError, match=word):
        ZonesFile.from_json({"holders": {"N5": {"holder": "N5", "zones": [zone]}}})


@pytest.mark.parametrize("d", [{"holders": [1, 2]}, {"holders": {"N5": {"zones": [{"id": 1, "rects": 5}]}}},
                               {"goodI0": [1]}, {"holders": {"N5": {"zones": {"a": 1}}}},
                               {"holders": {"N5": {"zones": [None]}}}])
def test_a_zones_file_of_the_wrong_shape_is_a_value_error(d):
    with pytest.raises(ValueError):
        ZonesFile.from_json(d)


def test_an_import_with_a_malformed_zone_changes_nothing(qapp, fake_project, tmp_path):
    ses = session(fake_project)
    draw_zone(ses, "good zone")
    ses.flush()
    bad = tmp_path / "short_rect.json"
    bad.write_text(json.dumps({"holders": {"N5": {"holder": "N5", "zones": [
        {"id": 1, "label": "bad zone", "rects": [[-0.6, 0.35, 4.9]]}]}}}), encoding="utf-8")
    msgs = []
    ses.status.connect(msgs.append)
    n = ses.undo_stack.count()
    assert ses.import_zones(bad, "merge") == 0
    assert [z.label for z in ses.holder_zones().zones] == ["good zone"] and ses.undo_stack.count() == n
    assert "Could not read" in msgs[-1] and "4 numbers" in msgs[-1]
    with ses.edit("rename zone 1"):                 # a later edit is an undo step and is saved
        ses.holder_zones().zones[0].label = "good zone++"
    assert ses.undo_stack.count() == n + 1 and ses.flush()
    assert [z["label"] for z in holders_json(ses.session_file)["N5"]["zones"]] == ["good zone++"]


def test_a_session_with_a_malformed_zone_is_recovered_not_raised(qapp, fake_project):
    from aurora.app.session import Session
    ses = session(fake_project)
    draw_zone(ses, "first")
    ses.flush()
    draw_zone(ses, "second")
    ses.flush()
    ses.release_lock()
    d = json.loads(ses.session_file.read_text(encoding="utf-8"))
    d["holders"]["N5"]["zones"][1]["rects"] = [[-0.6, 0.35, 4.9]]
    ses.session_file.write_text(json.dumps(d), encoding="utf-8")
    s2 = Session()
    s2.set_project(Project.discover(fake_project))              # never raises
    assert [z.label for z in s2.holder_zones().zones] == ["first"]
    assert list(ses.session_file.parent.glob("session.corrupt-*.json"))


def test_a_bad_zone_in_memory_never_blocks_the_edits_of_its_holder(qapp, fake_project):
    from aurora.core.io.config import Zone
    ses = session(fake_project)
    draw_zone(ses, "good zone")
    ses.flush()
    ses.holder_zones().zones.append(Zone(id=9, label="bad", rects=[[0.0, 1.0, 2.0]]))      # not from a file
    n = ses.undo_stack.count()
    with ses.edit("rename zone 1"):
        ses.zone(1).label = "renamed"
    assert ses.dirty and ses.undo_stack.count() == n + 1
    ses._save_timer.stop()
    assert ses.flush()
    assert "renamed" in [z["label"] for z in holders_json(ses.session_file)["N5"]["zones"]]


def test_survey_import_of_a_wrong_shape_shows_a_message(qapp, fake_project, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    win = window(qapp, Project.discover(fake_project))
    bad = tmp_path / "holders_list.json"
    bad.write_text(json.dumps({"holders": [1, 2]}), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), "JSON (*.json)"))
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]) or QMessageBox.Ok)
    win.survey._import()                            # the excepthook collector fails the test if anything escapes
    assert len(shown) == 1 and "holders_list.json" in shown[0] and "unchanged" in shown[0]
    win.session._save_timer.stop()
    win.close()


# ----------------------------------------------------------------------------- data-safety-4: import keys
def test_zones_keyed_by_a_bare_holder_name_land_on_that_holder(qapp, tmp_path):
    one = testdata.make_fake_project(tmp_path / "one_day")
    s1 = session(one)
    draw_zone(s1, "imported zone", rect=[-0.6, 0.1, 4.9, 5.2])      # not the hand-drawn one's area (no duplicate)
    s1.flush()
    exp = tmp_path / "zones_all.json"
    s1.export_zones(exp)
    two = testdata.make_fake_project(tmp_path / "two_days")
    shutil.copytree(two / "raw" / testdata.DAY, two / "raw" / "2026-11-06")
    s2 = session(two)
    day1 = f"N5_{testdata.DAY}"
    assert day1 in s2.holder_keys and "N5" not in s2.holder_keys
    s2.set_holder(day1)
    draw_zone(s2, "zone drawn by hand on day 1")
    assert s2.import_zones(exp, "merge") == 1
    assert [z.label for z in s2.holder_zones(day1).zones] == ["zone drawn by hand on day 1", "imported zone"]
    assert set(s2.zones.holders) <= set(s2.holder_keys)                 # no hidden key
    s2.flush()
    s2.release_lock()
    s3 = session(two)
    assert [z.label for z in s3.holder_zones(day1).zones] == ["zone drawn by hand on day 1", "imported zone"]


def test_a_holder_this_project_does_not_have_is_counted_apart(qapp, fake_project, tmp_path):
    ses = session(fake_project)
    p = tmp_path / "zones_all.json"
    p.write_text(json.dumps({"holders": {"Q9": {"holder": "Q9", "day": "2026-01-01", "zones": [
        {"id": 1, "label": "elsewhere", "rects": [[0, 1, 0, 1]]}]}}}), encoding="utf-8")
    msgs = []
    ses.status.connect(msgs.append)
    assert ses.import_zones(p, "merge") == 0
    assert ses.last_import.unmatched == 1 and "this project does not have" in msgs[-1]


def test_two_keys_of_one_holder_show_the_first(qapp, fake_project):
    """A session written by 0.4.0 after such an import: the zones drawn on the holder stay visible."""
    ses = session(fake_project)
    draw_zone(ses, "drawn")
    ses.flush()
    ses.release_lock()
    d = json.loads(ses.session_file.read_text(encoding="utf-8"))
    hidden = json.loads(json.dumps(d["holders"]["N5"]))
    hidden["zones"][0]["label"] = "imported"
    d["holders"]["N5_hidden"] = hidden
    ses.session_file.write_text(json.dumps(d), encoding="utf-8")
    s2 = session(fake_project)
    assert list(s2.holder_keys) == ["N5"] and s2.current_holder == "N5"      # the first key keeps the holder
    assert [z.label for z in s2.holder_zones().zones] == ["drawn"]
    assert [z.label for z in s2.zones.holders["N5_hidden"].zones] == ["imported"]     # still in the file


# ----------------------------------------------------------------------------- data-safety-5: damaged library
def test_a_damaged_library_is_kept_and_nothing_is_written_over_it(qapp, fake_project):
    from aurora.app.library import Library
    aur = fake_project / ".aurora"
    aur.mkdir()
    text = json.dumps({"rows": [{"label": "a", "signal": "fluo"}], "sources": {}}, indent=1)
    (aur / "library.json").write_text(text[: int(len(text) * 0.9)], encoding="utf-8")
    (aur / "library_spectra.npz").write_bytes(b"arrays of zone traces")
    lib = Library()
    warned = []
    lib.warning.connect(warned.append)
    lib.set_project(Project.discover(fake_project))
    assert len(warned) == 1 and "could not be read" in warned[0]
    assert lib.flush() is False                                 # closing writes nothing
    assert not (aur / "library.json").exists()
    kept = list(aur.glob("library.corrupt-*.json"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == text[: int(len(text) * 0.9)]
    assert [p.read_bytes() for p in aur.glob("library_spectra.corrupt-*.npz")] == [b"arrays of zone traces"]


# ----------------------------------------------------------------------------- threads-2 / threads-3: project switch
def test_checks_started_in_one_project_never_land_in_another(qapp, tmp_path, monkeypatch):
    from aurora.core import qc
    a = testdata.make_fake_project(tmp_path / "A", n_holders=3)
    b = testdata.make_fake_project(tmp_path / "B")
    real = qc.probe_holders
    started, release = threading.Event(), threading.Event()

    def slow(*args, **kw):
        res = real(*args, **kw)
        started.set()
        release.wait(30)
        return res
    monkeypatch.setattr(qc, "probe_holders", slow)
    win = window(qapp, Project.discover(a))
    try:
        win.check.run_every_holder()
        assert started.wait(60)
        win.home.set_project(Project.discover(b))
        pump(qapp)
        release.set()
        assert win.jobs.wait_idle(60000)
        pump(qapp)
        b_ids = {win.session.stack_id(s) for s in Project.discover(b).scans}
        stored = set(qc.load_qc(Project.discover(b)))
        assert stored <= b_ids and not stored, stored
        assert not set(win.check._results) - b_ids
    finally:
        release.set()
        win.jobs.wait_idle(60000)
        win.session._save_timer.stop()
        win.close()


def test_a_stack_read_for_one_project_is_never_stored_in_another(qapp, tmp_path, monkeypatch):
    from aurora.core.stack import StackData
    a = testdata.make_fake_project(tmp_path / "A")
    b = tmp_path / "B"
    shutil.copytree(a, b)
    ses = session(a)
    sid = ses.stack_id(ses.project.find(testdata.STACK))
    started, release = threading.Event(), threading.Event()
    roots = []

    def fake_load(project, scan, progress=None, **kw):
        roots.append(Path(project.root))
        if len(roots) == 1:
            started.set()
            release.wait(30)
        return SimpleNamespace(x=[0.0], y=[0.0], pitch=(0.25, 0.25), n_pix=1, root=Path(project.root))
    monkeypatch.setattr(StackData, "load", staticmethod(fake_load))
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("sd", ses.stack(sid)))
    t.start()
    assert started.wait(30)
    ses.set_project(Project.discover(b))
    release.set()
    t.join(30)
    assert out["sd"].root == b and ses.stack_loaded(sid).root == b
    assert roots == [a, b]


# ----------------------------------------------------------------------------- data-safety-11: recovery order
def test_recovery_takes_the_newest_state_not_the_bak(qapp, fake_project):
    from aurora.app.session import Session
    clock = FakeClock()
    ses = session(fake_project)
    ses.clock = clock
    draw_zone(ses, "zone 1"); ses.flush()                       # 09:00, snapshot
    clock.advance(minutes=3)
    draw_zone(ses, "zone 2"); ses.flush()                       # 09:03, no snapshot
    clock.advance(minutes=8)
    draw_zone(ses, "zone 3"); ses.flush()                       # 09:11, snapshot; .bak holds zones 1-2
    ses.release_lock()
    data = ses.session_file.read_bytes()
    ses.session_file.write_bytes(data[: len(data) // 2])
    s2 = Session()
    msgs = []
    s2.recovered.connect(msgs.append)
    s2.set_project(Project.discover(fake_project))
    assert [z.label for z in s2.holder_zones().zones] == ["zone 1", "zone 2", "zone 3"]
    assert "09:11" in msgs[-1]
    bak = next(b for b in s2.backups() if b.kind == "previous save")
    assert bak.time == dt.datetime(2026, 11, 8, 9, 3)          # the time of the state it holds


# ----------------------------------------------------------------------------- data-safety-12: untick all
def test_untick_all_over_two_holders_is_undone_for_both(qapp, tmp_path):
    from aurora.app.session import CheckItem
    root = testdata.make_fake_project(tmp_path / "proj", n_holders=2)
    win = window(qapp, Project.discover(root))
    ses = win.session
    items = []
    try:
        for hk in ("N5", "N6"):
            ses.set_holder(hk)
            pump(qapp)
            sid = ses.stack_id(ses.stacks_of(hk)[0])
            ses.stack(sid)
            ses.touch()
            z = draw_zone(ses, f"zone of {hk}")
            items.append(CheckItem(hk, z.id, sid))
        ses.set_holder("N5")
        pump(qapp)
        ses.queue_for_check(items)
        win.jobs.wait_idle(60000); pump(qapp, 20)
        win.check._refresh_queue()
        win.jobs.wait_idle(60000); pump(qapp, 20)

        def ticks():
            return {it.holder: ses.zones.holders[it.holder].zones[0].settings[it.sid].include for it in items}
        assert len(win.check._rows) == 2
        win.check.set_all_ok(True)
        pump(qapp)
        assert ticks() == {"N5": True, "N6": True}
        win.check.set_all_ok(False)
        pump(qapp)
        assert ticks() == {"N5": False, "N6": False}
        ses.undo()
        pump(qapp)
        assert ticks() == {"N5": True, "N6": True}
    finally:
        win.jobs.wait_idle(60000)
        ses._save_timer.stop()
        win.close()


# ----------------------------------------------------------------------------- data-safety-13: rules
def test_a_rules_save_is_its_own_undo_step_and_restore_brings_rules_back(qapp, fake_project):
    ses = session(fake_project)
    z = draw_zone(ses, "zone A")
    with ses.edit(f"rename zone {z.id}"):
        z.label = "zone B"
    rule = {"name": "my rule", "enabled": True, "scope": {"kind": "project"}, "filter": {}, "patch": {"k": 5.0}}
    with ses.edit("save the settings rules"):
        ses.zones.rules = [rule]
    assert ses.undo_stack.undoText() == "save the settings rules"
    ses.undo()
    assert ses.zones.rules == [] and ses.zone(z.id).label == "zone B"
    ses.redo()
    assert ses.zones.rules == [rule]
    ses.flush()
    snap = ses.backup_now("test")
    with ses.edit("save the settings rules"):
        ses.zones.rules = []
    ses.restore_backup(snap)
    assert ses.zones.rules == [rule]


def test_unknown_rule_keys_survive_a_round_trip():
    from aurora.core import rules as rl
    item = {"name": "r", "enabled": True, "scope": {"kind": "project"}, "filter": {}, "patch": {},
            "priority": 3, "comment": "from a newer AURORA"}
    assert rl.rules_to_json(rl.rules_from_json([item])) == [item]


# ----------------------------------------------------------------------------- data-safety-17: BOM
def test_a_session_and_a_zones_file_with_a_bom_read_normally(qapp, fake_project, tmp_path):
    ses = session(fake_project)
    draw_zone(ses, "zone 1"); ses.flush()
    draw_zone(ses, "zone 2"); ses.flush()
    ses.release_lock()
    data = ses.session_file.read_bytes()
    ses.session_file.write_bytes(b"\xef\xbb\xbf" + data)
    s2 = session(fake_project)
    assert [z.label for z in s2.holder_zones().zones] == ["zone 1", "zone 2"]
    assert not list(ses.session_file.parent.glob("session.corrupt-*.json"))
    exp = tmp_path / "zones_all.json"
    exp.write_bytes(b"\xef\xbb\xbf" + data)
    assert len(ZonesFile.load(exp).holders["N5"].zones) == 2


# ----------------------------------------------------------------------------- data-safety-18: time zones
def test_a_newest_snapshot_in_the_future_does_not_stop_the_snapshots(qapp, fake_project):
    clock = FakeClock(dt.datetime(2026, 11, 6, 15, 0, 0))          # Saskatoon, UTC-6
    ses = session(fake_project)
    ses.clock = clock
    draw_zone(ses, "at CLS"); ses.flush()
    ses.release_lock()
    later = FakeClock(dt.datetime(2026, 11, 6, 13, 30, 0))         # the same afternoon in Pullman, UTC-8
    s2 = session(fake_project)
    s2.clock = later
    for i in range(3):
        draw_zone(s2, f"in Pullman {i}")
        s2.flush()
        later.advance(minutes=10)
    names = sorted(p.name for p in s2.backup_dir.glob("session-*.json"))
    assert len(names) == 4, names                                  # 15:00, then 13:30, 13:40 and 13:50


# ----------------------------------------------------------------------------- data-safety-19: failing saves
def test_a_read_only_target_fails_at_once_and_leaves_no_temporary_file(tmp_path):
    from aurora.core.io.textio import write_text_atomic
    target = tmp_path / "session.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, stat.S_IREAD)
    try:
        t0 = time.monotonic()
        with pytest.raises(PermissionError):
            write_text_atomic(target, "{\"a\": 1}")
        assert time.monotonic() - t0 < 0.5
    finally:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
    assert target.read_text(encoding="utf-8") == "{}" and not list(tmp_path.glob(".*.tmp"))


def test_a_write_that_fails_half_way_removes_its_temporary_file(tmp_path, monkeypatch):
    import builtins
    import numpy as np
    from aurora.core.io.cache import save_npz
    from aurora.core.io.textio import write_text_atomic
    real_open = builtins.open

    class Full:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def write(self, data):
            self.f.write(data[:10])
            raise OSError(28, "No space left on device")

        def __getattr__(self, name):
            return getattr(self.f, name)

    def full_open(path, mode="r", *a, **k):
        f = real_open(path, mode, *a, **k)
        return Full(f) if "w" in mode and ".tmp" in str(path) else f
    monkeypatch.setattr(builtins, "open", full_open)
    with pytest.raises(OSError):
        write_text_atomic(tmp_path / "library.json", "x" * 5000)
    with pytest.raises(OSError):
        save_npz(tmp_path / "_reduced_cache.npz", a=np.zeros(1000))
    monkeypatch.setattr(builtins, "open", real_open)
    assert [p.name for p in tmp_path.iterdir() if "tmp" in p.name or p.suffix in (".json", ".npz")] == []


def test_a_failed_save_says_why_in_plain_words(qapp, fake_project):
    ses = session(fake_project)
    draw_zone(ses, "one")
    assert ses.flush()
    msgs = []
    ses.status.connect(msgs.append)
    os.chmod(ses.session_file, stat.S_IREAD)
    try:
        draw_zone(ses, "two")
        t0 = time.monotonic()
        assert ses.flush() is False
        assert time.monotonic() - t0 < 1.5
    finally:
        os.chmod(ses.session_file, stat.S_IWRITE | stat.S_IREAD)
    text = next(m for m in msgs if m.startswith("could not save the session"))
    assert "\\\\" not in text and str(ses.session_file.parent) in text, text
