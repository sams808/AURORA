"""WP0 structural prep, the application side (offscreen): the Session extension points, session.edit around every
zone edit, the signal combo of the scan card, the Compare library on every perspective, navigate / nav.go, the
shortcuts module and the Edit menu, home.open_path as the one way in (drag-and-drop, command line, last project at
start without a stat on the GUI thread), and the new Settings defaults."""
import json
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402

from aurora.core import testdata  # noqa: E402
from aurora.core.model import Project  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


def wait_home(qapp, win, timeout=30.0):
    win.jobs.wait_idle(int(timeout * 1000))
    pump(qapp, 10)


def window(qapp, project):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.home.set_project(project)
    pump(qapp)
    return win


def loaded_session(qapp, root):
    from aurora.app.session import Session
    ses = Session()
    ses.set_project(Project.discover(root))
    sid = ses.stack_id(ses.project.find(testdata.STACK))
    ses.stack(sid)
    return ses, sid


# ----------------------------------------------------------------------------- bundled files
def test_icons_and_style_images_come_from_resources_dir(qapp):
    import aurora
    from aurora.app import theme
    from aurora.app.icons import app_icon, mark_pixmap
    assert not app_icon().isNull() and not mark_pixmap(64).isNull()
    p = Path(theme._ui("check.svg"))
    assert p.is_file() and p.parent == aurora.resources_dir() / "ui"


# ----------------------------------------------------------------------------- Session extension points
def test_edit_nests_and_touches_once(qapp, fake_project):
    ses, sid = loaded_session(qapp, fake_project)
    changed = []
    ses.zones_changed.connect(lambda: changed.append(1))
    x0, x1, y0, y1 = ses.stack_loaded(sid).bbox
    with ses.edit("outer edit", holder="N5"):
        assert ses.editing == "outer edit"
        z = ses.new_zone()                       # an edit itself: part of the outer one
        z.rects.append([x0, x1, y0, y1])
        ses.touch()                              # deferred to the end of the edit
        assert changed == [] and z.members == {}
    assert ses.editing is None and changed == [1]
    assert z.members[sid] == list(range(12)) and z.settings[sid].n == 12
    with pytest.raises(ValueError):
        with ses.edit("fails half-way"):
            z.label = "renamed"
            raise ValueError("boom")
    assert changed == [1, 1] and ses._edit_depth == 0 and ses.editing is None
    ses._save_timer.stop()


def test_ui_values_queue_flags_defaults_flush_and_read_only(qapp, fake_project):
    from aurora.app.session import CheckItem
    from aurora.core.flags import CHECK_SOURCE, Flag
    ses, sid = loaded_session(qapp, fake_project)
    states = []
    ses.save_state.connect(states.append)
    # flag sources and settings-default providers reach every new zone x scan settings
    ses.add_flag_source(lambda scan: [Flag("SDD saturated: SDD2", "sdd_saturated", CHECK_SOURCE, ("sdd2",))])
    ses.add_flag_source(lambda scan: 1 / 0)                         # a broken source is logged, never raised

    def default_fn(scan, st):
        st.fluo_norm = "max"
    ses.add_settings_default(default_fn)
    ses.add_settings_default(default_fn)                            # registered once
    scan = ses.scan_of(sid)
    assert "SDD saturated: SDD2 (AURORA check)" in [str(f) for f in ses.flags_of(scan)]
    x0, x1, y0, y1 = ses.stack_loaded(sid).bbox
    with ses.edit("draw"):
        z = ses.new_zone()
        z.rects.append([x0, x1, y0, y1])
    st = z.settings[sid]
    assert st.sdd == [True, False, True, True] and st.fluo_norm == "max" and len(ses._settings_defaults) == 1
    assert states[-1] == "unsaved"
    # flush writes the pending autosave at once; with nothing pending it writes nothing
    ses.flush()
    assert ses.session_file.exists() and not ses._save_timer.isActive() and states[-1].startswith("saved ")
    ses.session_file.unlink()
    ses.flush()
    assert not ses.session_file.exists()
    # view memory: saved with the session, left out of the zone tool's export, restored on open
    ses.set_ui_value("preflight_ignored", ["f1"])
    assert ses._save_timer.isActive()
    ses.flush()
    assert json.loads(ses.session_file.read_text(encoding="utf-8"))["ui"] == {"preflight_ignored": ["f1"]}
    ses.set_ui_value("preflight_ignored", ["f1"])                    # unchanged: nothing scheduled
    assert not ses._save_timer.isActive()
    ses.export_zones(fake_project / "zones_all.json")
    assert "ui" not in json.loads((fake_project / "zones_all.json").read_text(encoding="utf-8"))
    # the check queue
    seen = []
    ses.check_queue_changed.connect(lambda: seen.append(len(ses.check_queue)))
    item = CheckItem("N5", z.id, sid)
    assert ses.queue_for_check([item, ("N5", z.id, sid)]) == 1 and ses.check_queue == [item] and seen == [1]
    assert ses.queue_for_check([item]) == 0 and seen == [1]
    assert ses.unqueue_for_check([item]) == 1 and ses.check_queue == [] and seen == [1, 0]
    ses.queue_for_check([item])
    # reopening: ui values back, the queue empty
    ses.set_project(Project.discover(fake_project))
    assert ses.ui_value("preflight_ignored") == ["f1"] and ses.ui_value("missing", 3) == 3 and ses.check_queue == []
    # read-only: edits stay in memory, the file is never written
    before = ses.session_file.read_bytes()
    ses.read_only = True
    with ses.edit("an edit that cannot be saved"):
        ses.holder_zones().zones[0].label = "changed"
    assert states[-1] == "read-only"
    ses.flush()
    assert ses.session_file.read_bytes() == before and states[-1] == "read-only"


def test_every_zone_edit_goes_through_session_edit(qapp, fake_project, monkeypatch):
    from contextlib import contextmanager
    from aurora.app import mode
    from aurora.app.session import Session
    edits: list[str] = []
    real = Session.edit

    @contextmanager
    def counted(self, description, holder=None):
        if self._edit_depth == 0:
            edits.append(description)
        with real(self, description, holder):
            yield self
    monkeypatch.setattr(Session, "edit", counted)
    mode.set_advanced(True)
    try:
        win = window(qapp, Project.discover(fake_project))
        ses, rd = win.session, win.reduce
        win.jobs.wait_idle(30000); pump(qapp)
        sid = ses.stack_id(ses.project.find(testdata.STACK))
        sd = ses.stack(sid)
        ses.touch(); ses.select_stack(sid); pump(qapp)
        edits.clear()
        x0, x1, y0, y1 = sd.bbox
        rd.map.set_tool("rect+"); rd.map.rect_drawn.emit(x0, x1, y0, y1); pump(qapp)
        assert edits == ["add a rectangle to a new zone"]
        z = ses.zone()
        rd.map.set_tool("brush-"); rd.map.brush_at.emit(float(sd.x[0]), float(sd.y[0]), False); pump(qapp)
        rd.map.set_tool("brush+"); rd.map.brush_at.emit(float(sd.x[0]), float(sd.y[0]), True); pump(qapp)
        rd.map.set_tool("rect-"); rd.map.rect_drawn.emit(float(sd.x[1]), float(sd.x[1]), float(sd.y[1]), float(sd.y[1])); pump(qapp)
        assert edits[1:] == [f"erase pixels in zone {z.id}", f"paint pixels in zone {z.id}", f"remove a rectangle from zone {z.id}"]
        # zone card: a focus-out without a change is no edit; a new label is one
        n = len(edits)
        rd.z_label.lineEdit().editingFinished.emit(); pump(qapp)
        assert len(edits) == n
        rd.z_label.setEditText("13 alpha_beta"); rd.z_label.lineEdit().editingFinished.emit(); pump(qapp)
        assert edits[-1] == f"rename zone {z.id}" and z.label == "13 alpha_beta"
        # scan card: SDD tick and the signal combo (stored, Advanced only)
        card = rd._cards[0]
        card.sdd[2].setChecked(False); pump(qapp)
        assert edits[-1] == f"change the settings of 09:27 in zone {z.id}" and z.settings[sid].sdd[2] is False
        assert [card.signal.itemData(i) for i in range(card.signal.count())] == ["pfy", "ipfy"]
        card.signal.setCurrentIndex(card.signal.findData("ipfy")); pump(qapp)
        assert z.settings[sid].signal == "ipfy" and edits[-1].startswith("change the settings")
        assert mode.advanced_marked(rd) and rd.cards_area in mode.advanced_marked(rd)
        # Check: good I0
        win.check._fill_i0(); pump(qapp)
        it = win.check.i0_list.item(0)
        n = len(edits)
        it.setCheckState(Qt.Checked); pump(qapp)
        assert edits[-1].startswith("mark ") and edits[-1].endswith("as good I0") and ses.zones.good_i0[it.data(Qt.UserRole)]
        from aurora.app.theme import refill_tones
        refill_tones(win.check.i0_list); pump(qapp)              # a re-coloured row is no edit
        assert len(edits) == n + 1
        win.check._auto_select(); pump(qapp)
        n = len(edits)
        win.check._auto_select(); pump(qapp)                     # the same choice again: no edit
        assert len(edits) == n
        rd.propose_zones("summary"); pump(qapp)                  # no summary on the fake beamtime: nothing proposed
        assert len(edits) == n
        # Survey: import; Reduce: delete
        ses.flush()
        out = fake_project / "zones_all.json"
        ses.export_zones(out)
        monkeypatch.setattr("aurora.app.survey.QFileDialog.getOpenFileName", lambda *a, **k: (str(out), "JSON (*.json)"))
        win.survey._import(); pump(qapp)
        assert edits[-1] == "import zones from zones_all.json"
        ses.select_zone(ses.holder_zones().zones[0].id)
        rd.del_zone_btn.click(); pump(qapp)
        assert edits[-1].startswith("delete zone") and not ses.holder_zones().zones
        # the stored signal survives a save and a reload
        ses.import_zones(out)
        ses.flush()
        d = json.loads(ses.session_file.read_text(encoding="utf-8"))
        assert d["holders"]["N5"]["zones"][0]["st"][sid]["sig"] == "ipfy"
        win.close()
    finally:
        mode.set_advanced(False)


# ----------------------------------------------------------------------------- wiring of the window
def test_library_everywhere_navigation_and_edit_menu(qapp, tmp_path):
    from aurora.app import mode, nav, shortcuts
    from aurora.app.main import MainWindow
    from PySide6.QtWidgets import QDialog, QWidget
    proj = Project.discover(testdata.make_fake_project(tmp_path / "two", n_holders=2))
    win = window(qapp, proj)
    for w in (win.reduce, win.check, win.maps, win.fit):
        assert w.library is win.library
    assert win.compare.lib is win.library and win.library.project is proj
    titles = [a.text() for a in win.menuBar().actions()]
    assert titles[:3] == ["&File", "&Edit", "&View"] and win.menus["edit"] is not None
    # navigate: holder, zone, stack
    ses = win.session
    assert list(ses.holder_keys) == ["N5", "N6"]
    nav.go(win.check, "reduce", holder="N6")
    pump(qapp)
    assert ses.current_holder == "N6" and win.stack.currentWidget() is win.reduce
    sid = ses.stack_id(proj.find(testdata.STACK))
    dlg = QDialog(win.survey)                                     # from a dialog: found through its parent
    nav.go(dlg, "survey", sid=sid)
    pump(qapp)
    assert ses.current_holder == "N5" and win.stack.currentWidget() is win.survey
    assert win.reduce.scan_list.currentItem().data(Qt.UserRole) == sid
    win.jobs.wait_idle(30000); pump(qapp)
    with ses.edit("a zone to jump to"):
        z = ses.new_zone("target")
    ses.select_zone(None)
    assert win.navigate("reduce", holder="N5", zone=z.id) and ses.current_zone == z.id
    assert not mode.is_advanced() and win.navigate("maps") is False
    assert win.stack.currentWidget() is win.reduce and "Advanced mode" in win.statusBar().currentMessage()
    # shortcuts: menu entries from a widget inside the window, and from one added later (adopt)
    fired = []
    a = shortcuts.add(win.reduce, "Do it", "Ctrl+Alt+J", lambda: fired.append("j"), menu="edit", section="Reduce")
    assert a in win.menus["edit"].actions() and a.shortcut().toString() == "Ctrl+Alt+J"
    late = QWidget()
    b = shortcuts.add(late, "Later entry", None, lambda: fired.append("late"), menu="file")
    sub = shortcuts.add_menu(late, "Open recent", "file")
    assert b not in win.menus["file"].actions()
    late.setParent(win.reduce)
    assert shortcuts.adopt(win) == 2
    acts = win.menus["file"].actions()
    settings_i = next(i for i, x in enumerate(acts) if x.text() == "&Settings…")
    assert acts.index(b) < settings_i and acts.index(sub.menuAction()) < settings_i
    assert shortcuts.adopt(win) == 0                               # placed once
    c = shortcuts.add(win.reduce, "Rectangle tool", "R", lambda: fired.append("r"), section="Reduce")
    assert c in win.reduce.actions() and c.shortcutContext() == Qt.WindowShortcut
    reg = shortcuts.registered(win)
    assert {a, b, c} <= set(reg) and shortcuts.section_of(a) == "Reduce" and shortcuts.section_of(b) == "File"
    a.trigger(); b.trigger(); c.trigger()
    assert fired == ["j", "late", "r"]
    with pytest.raises(ValueError):
        shortcuts.add(win, "x", None, lambda: None, menu="tools")
    win.close()


def test_drop_and_command_line_go_through_open_path(qapp, fake_project, monkeypatch, tmp_path):
    from PySide6.QtGui import QDragEnterEvent, QDropEvent
    from aurora.app.main import MainWindow
    win = MainWindow()
    calls = []
    win.home.open_path = lambda p, quiet=False: calls.append((Path(p), quiet))
    md = QMimeData()
    md.setUrls([QUrl.fromLocalFile(str(fake_project))])
    enter = QDragEnterEvent(QPointF(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    win.dragEnterEvent(enter)
    assert enter.isAccepted()
    win.dropEvent(QDropEvent(QPointF(5, 5), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
    assert calls == [(fake_project, False)]
    empty = QMimeData()
    empty.setText("not a path")
    ev = QDragEnterEvent(QPointF(5, 5), Qt.CopyAction, empty, Qt.LeftButton, Qt.NoModifier)
    ev.ignore()
    win.dragEnterEvent(ev)
    assert not ev.isAccepted()
    # aurora <folder> and aurora <file>.h5 == aurora gui <path>; the other commands are unchanged
    import aurora.app.main as main_mod
    from aurora import cli
    ran = []
    monkeypatch.setattr(main_mod, "run", lambda project=None: ran.append(project) or 0)
    assert cli.main([str(fake_project)]) == 0 and ran[-1] == fake_project
    assert cli.main(["gui", str(fake_project)]) == 0 and ran[-1] == fake_project
    assert cli.main([str(tmp_path / "x.h5")]) == 0 and ran[-1] == tmp_path / "x.h5"
    assert cli.main([]) == 0 and ran[-1] is None
    assert cli.main(["project", str(fake_project)]) == 0 and len(ran) == 4
    with pytest.raises(SystemExit):
        cli.main([str(tmp_path / "does not exist")])
    win.close()


def test_open_path_h5_and_last_project_without_a_gui_stat(qapp, fake_project, monkeypatch, tmp_path):
    import pathlib
    from aurora.app.main import MainWindow, open_at_start
    from aurora.app.settings import remember_project
    # an .h5 file opens its scan folder's project, in the worker
    win = MainWindow()
    h5 = next((fake_project / "raw" / testdata.DAY / testdata.STACK).glob("*.h5"))
    win.home.open_path(h5)
    wait_home(qapp, win)
    assert win.home.project is not None and win.home.project.root == fake_project.resolve()
    assert win.session.project is win.home.project and win.library.project is win.home.project
    win.close()
    # the last project at start: never looked at on the GUI thread, a calm note when it is gone
    gone = tmp_path / "share" / "beamtime that moved"
    remember_project(gone)
    main_stats = []
    real_stat = pathlib.Path.stat

    def stat(self, *a, **k):
        if threading.current_thread() is threading.main_thread() and "beamtime that moved" in str(self):
            main_stats.append(str(self))
        return real_stat(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "stat", stat)
    gone.exists()                                                  # the hook sees a GUI-thread stat ...
    assert main_stats and main_stats[0].endswith("beamtime that moved")
    main_stats.clear()                                             # ... and none must happen from here on
    win = MainWindow()
    opened = []
    real_open = win.home.open_path
    win.home.open_path = lambda p, quiet=False: (opened.append((Path(p), quiet)), real_open(p, quiet=quiet))
    open_at_start(win, None)
    assert opened == []                                            # from the event loop, after the window is up
    pump(qapp)
    assert opened == [(gone, True)]
    wait_home(qapp, win)
    assert main_stats == [] and win.home.project is None
    from aurora.app import mode
    welcome = "No project open" if mode.is_advanced() else "Getting started"      # the panel before a project is open
    assert "could not be opened" in win.home.path_label.text() and welcome in win.home.details.toPlainText()
    # the command-line folder wins; with the setting off nothing opens
    opened.clear()
    open_at_start(win, fake_project)
    assert opened == [(fake_project, False)]
    wait_home(qapp, win)
    from aurora.app.settings import settings
    s = settings(); s.setValue("open_last_project", False); s.sync()
    opened.clear()
    open_at_start(win, None)
    pump(qapp)
    assert opened == []
    win.close()


def test_settings_defaults_author_and_update_check(qapp):
    from aurora.app.settings import settings
    from aurora.app.settings_dialog import DEFAULTS, SettingsDialog, get
    assert DEFAULTS["report_author"] == "" and get("report_author") == ""
    assert DEFAULTS["update_check"] is False and get("update_check") is False
    d = SettingsDialog()
    assert d.author.text() == ""
    d.accept()
    assert settings().value("report_author") == "" and get("report_author") == ""
    s = settings(); s.setValue("report_author", "Sami Soudani"); s.sync()            # a name set by the user is kept
    d = SettingsDialog()
    assert d.author.text() == "Sami Soudani"
    d.accept()
    assert get("report_author") == "Sami Soudani"


def test_demo_action_adds_the_spectra_to_an_older_demo(qapp, tmp_path, monkeypatch):
    import platformdirs
    from aurora.app.main import MainWindow
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda *a, **k: str(tmp_path / "data"))
    root = testdata.make_fake_project(tmp_path / "data" / "demo_beamtime")        # as an earlier version wrote it
    win = MainWindow()
    win.demo_action.trigger()
    wait_home(qapp, win)
    assert (root / "references" / "NaF_F_K_ref.csv").is_file()
    assert (root / "processed" / "reduced" / "mixture 2" / "mixture 2_full_reduced.csv").is_file()
    assert win.session.project is not None and win.session.project.root == root.resolve()
    win.close()
