"""I14: zones are never lost. Atomic save, session.json.bak, snapshots (one per 10 min, 30 kept, one before imports
and proposals), recovery of a damaged session.json, File > Restore zones from a backup…, and an import that merges."""
import json
import os

import pytest

pytest.importorskip("pyqtgraph")

from aurora.core import testdata  # noqa: E402
from aurora.core.io.config import HolderZones, ScanSettings, Zone, ZonesFile  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from wp2_helpers import FakeClock, draw_zone, holders_json, pump, session, window  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _snapshots(root):
    d = root / ".aurora" / "backups"
    return sorted(p.name for p in d.glob("session-*.json")) if d.is_dir() else []


# ----------------------------------------------------------------------------- atomic save
def test_a_failed_replace_leaves_session_json_intact(qapp, fake_project, monkeypatch):
    ses = session(fake_project)
    draw_zone(ses, "first")
    assert ses.save()
    before = ses.session_file.read_bytes()
    draw_zone(ses, "second")
    ses._save_timer.stop()
    real = os.replace

    def broken(src, dst, *a, **k):
        if os.path.basename(str(dst)) in ("session.json", "session.json.bak"):
            assert os.path.exists(src)                      # the temporary file was written completely
            raise OSError(28, "No space left on device")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", broken)
    states = []
    ses.save_state.connect(states.append)
    assert ses.save() is False
    monkeypatch.setattr(os, "replace", real)
    assert ses.session_file.read_bytes() == before
    assert [z.label for z in ZonesFile.load(ses.session_file).holders["N5"].zones] == ["first"]
    assert states[-1] == "not saved" and ses.dirty
    assert not list(ses.session_file.parent.glob(".*.tmp"))  # no temporary file left behind
    assert ses.save() and len(holders_json(ses.session_file)["N5"]["zones"]) == 2


def test_the_previous_good_file_is_kept_as_bak(qapp, fake_project):
    ses = session(fake_project)
    draw_zone(ses, "one")
    ses.flush()
    first = ses.session_file.read_bytes()
    assert not ses.session_file.with_name("session.json.bak").exists()
    draw_zone(ses, "two")
    ses.flush()
    assert ses.session_file.with_name("session.json.bak").read_bytes() == first


# ----------------------------------------------------------------------------- recovery
def _damage(sf):
    data = sf.read_bytes()
    sf.write_bytes(data[: len(data) // 2])
    return sf.read_bytes()


def test_a_truncated_session_is_restored_from_the_bak(qapp, fake_project):
    from aurora.app.session import Session
    ses = session(fake_project)
    draw_zone(ses, "A")
    ses.flush()
    draw_zone(ses, "B")
    ses.flush()
    bak = json.loads(ses.session_file.with_name("session.json.bak").read_text(encoding="utf-8"))
    damaged = _damage(ses.session_file)
    ses2 = Session()
    told = []
    ses2.recovered.connect(told.append)
    ses2.set_project(Project.discover(fake_project))
    assert {k: h.to_json() for k, h in ses2.zones.holders.items()} == bak["holders"]
    assert [z.label for z in ses2.holder_zones().zones] == ["A"]
    corrupt = list(ses2.session_file.parent.glob("session.corrupt-*.json"))
    assert len(corrupt) == 1 and corrupt[0].read_bytes() == damaged
    assert told and "session.json was damaged; 1 zone restored from the backup of" in told[0]
    assert corrupt[0].name in told[0]
    assert ZonesFile.load(ses2.session_file).holders["N5"].zones[0].label == "A"      # a valid session exists again
    ses2.touch()
    ses2.save()
    assert corrupt[0].read_bytes() == damaged                                          # never overwritten
    ses2._save_timer.stop(); ses._save_timer.stop()


def test_without_bak_the_newest_snapshot_is_used(qapp, fake_project):
    from aurora.app.session import Session
    clock = FakeClock()
    ses = session(fake_project)
    ses.clock = clock
    draw_zone(ses, "old")
    ses.flush()                                 # snapshot 1 (first save)
    clock.advance(minutes=11)
    draw_zone(ses, "new")
    ses.flush()                                 # snapshot 2, holding both zones
    assert len(_snapshots(fake_project)) == 2
    ses.session_file.with_name("session.json.bak").unlink()
    _damage(ses.session_file)
    ses2 = Session()
    ses2.set_project(Project.discover(fake_project))
    assert [z.label for z in ses2.holder_zones().zones] == ["old", "new"]
    assert "backup" not in ses2.zones.extra                 # the snapshot's note is not carried into the session
    assert "backup" not in json.loads(ses2.session_file.read_text(encoding="utf-8"))
    ses2._save_timer.stop()


def test_nothing_to_restore_starts_empty_and_writes_nothing(qapp, fake_project):
    from aurora.app.session import Session
    ses = session(fake_project)
    draw_zone(ses)
    ses.flush()
    ses.session_file.with_name("session.json.bak").unlink(missing_ok=True)
    for p in (fake_project / ".aurora" / "backups").glob("*.json"):
        p.unlink()
    ses.session_file.write_text("{ broken", encoding="utf-8")
    ses2 = Session()
    told = []
    ses2.recovered.connect(told.append)
    ses2.set_project(Project.discover(fake_project))
    assert ses2.holder_zones(create=False) is None or not ses2.holder_zones().zones
    assert "no backup could be read" in told[0]
    assert not ses2.session_file.exists() and not ses2.dirty         # autosave stays off until a real edit
    ses2.flush()
    assert not ses2.session_file.exists()
    draw_zone(ses2)
    ses2.flush()
    assert ses2.session_file.exists() and len(list(ses2.session_file.parent.glob("session.corrupt-*"))) == 1


def test_recovery_message_in_the_window(qapp, fake_project):
    ses = session(fake_project)
    draw_zone(ses)
    ses.flush()
    draw_zone(ses)
    ses.flush()
    _damage(ses.session_file)
    win = window(qapp, Project.discover(fake_project))
    ui = win.session._ui
    assert ui.last_message.startswith("session.json was damaged; 1 zone restored")
    assert len(win.session.holder_zones().zones) == 1
    win.session._save_timer.stop()
    win.close()


# ----------------------------------------------------------------------------- snapshots
def test_snapshots_on_a_fake_clock_keep_30(qapp, fake_project):
    clock = FakeClock()
    ses = session(fake_project)
    ses.clock = clock
    z = draw_zone(ses)
    for i in range(40):
        with ses.edit(f"rename zone {z.id}"):
            z.label = f"label {i}"
        ses.flush()
        clock.advance(minutes=10)
    snaps = _snapshots(fake_project)
    assert len(snaps) == 30
    assert snaps[-1] == "session-20261108-153000.json"            # 9:00 + 39 x 10 min
    # within 10 minutes: no new snapshot
    clock.advance(minutes=-5)
    with ses.edit(f"rename zone {z.id}"):
        z.label = "one more"
    ses.flush()
    assert len(_snapshots(fake_project)) == 30 and _snapshots(fake_project)[-1] == snaps[-1]


def test_a_proposal_gets_a_snapshot_first(qapp, fake_project):
    ses = session(fake_project)
    draw_zone(ses, "kept")
    ses.flush()
    n = len(_snapshots(fake_project))
    with ses.edit("propose 2 zones"):
        hz = ses.holder_zones()
        hz.zones += [Zone(id=8, label="p1", rects=[[0, 0.1, 5, 5.1]]), Zone(id=9, label="p2", rects=[[0, 0.1, 5.2, 5.3]])]
    snaps = _snapshots(fake_project)
    assert len(snaps) == n + 1
    d = json.loads((fake_project / ".aurora" / "backups" / snaps[-1]).read_text(encoding="utf-8"))
    assert [z["label"] for z in d["holders"]["N5"]["zones"]] == ["kept"] and d["backup"]["reason"] == "propose 2 zones"
    ses._save_timer.stop()


# ----------------------------------------------------------------------------- import
def _file_with(tmp_path, zones, name="zones_all.json"):
    zf = ZonesFile(project="other")
    zf.holders["N5"] = HolderZones(holder="N5", day=testdata.DAY, survey=testdata.SURVEY, zones=zones)
    p = tmp_path / name
    zf.save(p, export=True)
    return p


def _two_zones(ses):
    a = draw_zone(ses, "A", [-0.6, -0.2, 4.9, 5.6])
    b = draw_zone(ses, "B", [0.0, 0.35, 4.9, 5.6])
    return a, b


def test_import_merge_skips_duplicates(qapp, fake_project, tmp_path):
    ses = session(fake_project)
    a, b = _two_zones(ses)
    ses.flush()
    msgs = []
    ses.status.connect(msgs.append)
    b2 = Zone(id=2, label="B prime", rects=[list(r) for r in b.rects])          # same geometry as B
    c = Zone(id=2, label="C", rects=[[-0.3, 0.1, 4.9, 5.2]])
    p = _file_with(tmp_path, [b2, c])
    steps = ses.undo_stack.count()
    assert ses.import_zones(p) == 1                                              # merge is the default
    labels = [z.label for z in ses.holder_zones().zones]
    assert labels == ["A", "B", "C"] and ses.last_import.duplicates == 1
    assert [z.id for z in ses.holder_zones().zones] == [1, 2, 3]                # C's taken id 2 becomes 3
    assert msgs[-1].startswith("imported 1, skipped 1 duplicate, backup saved as session-")
    assert ses.undo_stack.count() == steps + 1                                    # one step
    ses.undo()
    assert [z.label for z in ses.holder_zones().zones] == ["A", "B"]
    ses._save_timer.stop()


def test_import_replace_keeps_a_snapshot_of_what_it_replaced(qapp, fake_project, tmp_path):
    ses = session(fake_project)
    a, b = _two_zones(ses)
    b2 = Zone(id=2, label="B prime", rects=[list(r) for r in b.rects])
    c = Zone(id=3, label="C", rects=[[-0.3, 0.1, 4.9, 5.2]])
    p = _file_with(tmp_path, [b2, c])
    assert ses.import_zones(p, policy="replace") == 2
    assert [z.label for z in ses.holder_zones().zones] == ["B prime", "C"]
    snap = ses.last_import.backup
    d = json.loads(snap.read_text(encoding="utf-8"))
    assert [z["label"] for z in d["holders"]["N5"]["zones"]] == ["A", "B"]
    assert ses.import_zones(p, policy="skip") == 0 and [z.label for z in ses.holder_zones().zones] == ["B prime", "C"]
    ses._save_timer.stop()


def test_import_of_a_malformed_file_changes_nothing(qapp, fake_project, tmp_path):
    ses = session(fake_project)
    _two_zones(ses)
    before = {k: h.to_json() for k, h in ses.zones.holders.items()}
    steps = ses.undo_stack.count()
    for text in ("{ not json", "[1, 2]", '{"holders": [1]}', '{"version": "x"}'):
        bad = tmp_path / "bad.json"
        bad.write_text(text, encoding="utf-8")
        assert ses.import_zones(bad) == 0
        assert {k: h.to_json() for k, h in ses.zones.holders.items()} == before
        assert ses.last_import.error and ses.undo_stack.count() == steps
    assert ses.import_zones(tmp_path / "missing.json") == 0
    ses._save_timer.stop()


def test_import_in_the_window_asks_per_holder(qapp, fake_project, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from aurora.app import import_dialog
    win = window(qapp, Project.discover(fake_project))
    ses = win.session
    a, b = _two_zones(ses)
    asked = []

    def answer(parent, path, rows):
        asked.append([(r.key, r.existing, r.incoming, r.duplicates) for r in rows])
        return {"N5": "replace"}
    monkeypatch.setattr(import_dialog, "ask_policies", answer)
    p = _file_with(tmp_path, [Zone(id=1, label="X", rects=[list(r) for r in a.rects])])
    assert ses.import_zones(p) == 1
    assert asked == [[("N5", 2, 1, 1)]] and [z.label for z in ses.holder_zones().zones] == ["X"]
    # the same zones as here: every choice gives the same result, so nothing is asked
    asked.clear()
    ses.export_zones(tmp_path / "same.json")
    ses.import_zones(tmp_path / "same.json")
    assert asked == [] and [z.label for z in ses.holder_zones().zones] == ["X"]
    # cancelled: nothing changes
    monkeypatch.setattr(import_dialog, "ask_policies", lambda *a: None)
    q = _file_with(tmp_path, [Zone(id=5, label="Y", rects=[[0, 0.1, 5, 5.1]])], "y.json")
    assert ses.import_zones(q) == 0 and ses.last_import.cancelled and [z.label for z in ses.holder_zones().zones] == ["X"]
    # a malformed file: a message box naming it
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a, **k: shown.append(text)))
    bad = tmp_path / "broken_zones.json"
    bad.write_text("{ nope", encoding="utf-8")
    assert ses.import_zones(bad) == 0 and len(shown) == 1 and "broken_zones.json" in shown[0]
    ses._save_timer.stop()
    win.close()


def test_import_dialog_defaults_to_merge(qapp, tmp_path):
    from aurora.app.import_dialog import ImportDialog
    from aurora.app.session import ImportRow
    from test_a11y import check_root
    d = ImportDialog(tmp_path / "zones_all.json", [ImportRow("N5", "N5", 2, 3, 1), ImportRow("N6", "N6", 1, 1, 0)])
    assert d.policies() == {"N5": "merge", "N6": "merge"}
    d.set_policy("N6", "skip")
    assert d.policies() == {"N5": "merge", "N6": "skip"}
    assert d.table.item(0, 3).text() == "1"
    d.show(); pump(qapp)
    assert check_root("import", d) == []
    d.close(); d.deleteLater()


# ----------------------------------------------------------------------------- restore from a backup
def test_restore_from_a_backup(qapp, fake_project, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from aurora.app import mode
    from aurora.app.import_dialog import RestoreDialog
    from test_a11y import check_root
    clock = FakeClock()
    win = window(qapp, Project.discover(fake_project))
    ses = win.session
    ses.clock = clock
    ui = ses._ui
    file_actions = win.menus["file"].actions()
    assert ui.restore_action in file_actions and not ui.restore_action.isVisible()       # Advanced only
    mode.set_advanced(True)
    try:
        assert ui.restore_action.isVisible() and ui.restore_action.isEnabled()
        draw_zone(ses, "first")
        ses.flush()                                     # snapshot with 1 zone
        clock.advance(minutes=20)
        draw_zone(ses, "second")
        ses.flush()                                     # snapshot with 2 zones, .bak with 1
        backups = ses.backups()
        snaps = [b for b in backups if b.kind == "snapshot"]
        assert [b.zones for b in snaps] == [2, 1] and snaps[0].time > snaps[1].time
        assert snaps[0].reason == "autosave" and snaps[0].holders == 1
        assert any(b.kind == "previous save" and b.zones == 1 for b in backups)
        d = RestoreDialog(ses, win)
        d.show(); pump(qapp)
        assert d.table.rowCount() == len(backups)
        row = next(i for i in range(d.table.rowCount()) if d.table.item(i, 4).text() == snaps[1].path.name)
        assert d.table.item(row, 3).text() == "1" and d.table.item(row, 2).text() == "1"
        assert check_root("restore", d) == []
        d.table.selectRow(row)
        assert d.chosen() == snaps[1].path and d.ok.isEnabled()
        d.close(); d.deleteLater()
        # the menu entry: the dialog answers with the older snapshot
        monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
        monkeypatch.setattr(RestoreDialog, "chosen", lambda self: snaps[1].path)
        ui.restore_action.trigger()
        pump(qapp)
        assert [z.label for z in ses.holder_zones().zones] == ["first"]
        assert "restored 1 zone from the backup of" in win.statusBar().currentMessage()
        ses.undo()
        assert [z.label for z in ses.holder_zones().zones] == ["first", "second"]
    finally:
        mode.set_advanced(False)
        ses._save_timer.stop()
        win.close()


def test_backups_are_never_written_into_a_protected_folder(qapp, fake_project, monkeypatch):
    ses = session(fake_project)
    draw_zone(ses)
    monkeypatch.setenv("AURORA_PROTECT_DIRS", os.pathsep.join([os.environ.get("AURORA_PROTECT_DIRS", ""),
                                                               str(fake_project)]))
    assert ses.backup_now("test") is None
    assert ses.save() is False and not ses.session_file.exists()
    ses._save_timer.stop()
