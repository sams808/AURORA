"""I11: a Home that helps. Welcome actions and recent projects before a project is open; File > Open recent; the
project summary after an open; John Bussey's 'Scan Analysis Summary' of a scan (energy regions, time per map, grid,
energy folders found vs planned and incomplete maps); a failed open with Retry and Choose another, never a stat on
the GUI thread."""
import re
import threading
import time

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402

from aurora.app import mode  # noqa: E402
from aurora.core import overview, testdata  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from aurora.core.names import energy_regions  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


def wait(qapp, win, ms=30000):
    win.jobs.wait_idle(ms)
    pump(qapp, 5)


@pytest.fixture
def user_data(monkeypatch, tmp_path):
    """Help > Open the demo beamtime (and the welcome button) write the demo into tmp, not the user's data folder."""
    import platformdirs
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda *a, **k: str(tmp_path / "user data"))
    return tmp_path / "user data" / "demo_beamtime"


def _window(qapp, size=(1300, 820)):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.resize(*size)
    win.show()
    pump(qapp)
    return win


# ----------------------------------------------------------------------------- core
def test_energy_regions():
    E = np.r_[np.arange(680.0, 685.0, 0.5), np.arange(685.0, 700.0, 0.1), np.arange(700.0, 730.0001, 0.2)]
    assert energy_regions(E) == [(680.0, 685.0, 0.5), (685.0, 700.0, 0.1), (700.0, 730.0, 0.2)]
    assert energy_regions([680.0, 683.0, 686.0, 689.0, 692.0]) == [(680.0, 692.0, 3.0)]          # constant grid
    assert energy_regions(np.linspace(660, 740, 321)) == [(660.0, 740.0, 0.25)]
    assert energy_regions([695.0]) == [(695.0, 695.0, 0.0)] and energy_regions([]) == []
    assert overview.regions_text(E) == "680–685 eV in 0.5 eV steps; 685–700 eV in 0.1 eV steps; 700–730 eV in 0.2 eV steps"


def test_scan_summary_rows(fake_project):
    proj = Project.discover(fake_project)
    st = proj.find(testdata.STACK)
    rows = dict(overview.scan_rows(st, proj, incomplete=[]))
    assert rows["date"] == testdata.DAY and rows["time per map"] == "6 s per map"
    assert rows["estimated total"].startswith("5 maps × 6 s = 30 s")
    assert rows["grid"].startswith("4 × 3 points, pitch 0.250 × 0.250 mm")
    assert rows["x range"] == "-0.500 … 0.250 mm" and rows["y range"] == "5.000 … 5.500 mm"
    assert rows["energy regions"] == "680–692 eV in 3 eV steps"
    assert rows["energy folders"] == "5 found of 5 planned; every map complete"
    assert rows["grating"] == "not recorded" and rows["project"].endswith("(folder; no project id in the file)")
    ab = dict(overview.scan_rows(proj.find(testdata.ABORTED), proj))
    assert ab["energy folders"] == "3 found of 5 planned; missing 689.00, 692.00 eV; checking the maps …"
    sv = dict(overview.scan_rows(proj.find(testdata.SURVEY), proj))
    assert sv["energy"] == "695.00 eV" and sv["grid"].startswith("8 × 6 points") and "energy regions" not in sv
    assert overview.format_duration(30) == "30 s" and overview.format_duration(933) == "15.6 min"
    assert overview.format_duration(9000) == "2.5 h"


def test_incomplete_energies_on_a_truncated_map(fake_project):
    proj = Project.discover(fake_project)
    st = proj.find(testdata.STACK)
    assert overview.incomplete_energies(st) == []
    e, d = st.energy_dirs[3]
    data = (d / "sdd2_0.bin").read_bytes()
    (d / "sdd2_0.bin").write_bytes(data[:-1024])               # one pixel short in SDD2
    (st.energy_dirs[4][1] / "sdd4_0.bin").unlink()
    got = overview.incomplete_energies(st)
    assert got[0] == (e, "11 of 12 points") and got[1][0] == st.energy_dirs[4][0]
    assert got[1][1].startswith("file missing")


def test_incomplete_energies_on_the_real_beamtime(real_project, real_expect):
    """The stack of the expectations file: its incomplete maps (as the reduced cache says) and its summary rows."""
    proj = Project.discover(real_project / "raw" / real_expect["day"])
    st = proj.find(real_expect["stack"])
    assert overview.incomplete_energies(st) == [tuple(x) for x in real_expect["stack_incomplete"]]
    rows = dict(overview.scan_rows(st, proj, incomplete=[]))
    for key, value in real_expect["stack_rows"].items():
        assert rows[key] == value, key
    assert re.fullmatch(r"\d{2}G\d{5}", rows["project"]), rows["project"]      # a CLS proposal id, read from the h5
    assert rows["estimated total"].startswith(real_expect["stack_estimated_total_starts"])


# ----------------------------------------------------------------------------- the panel
def test_project_summary_replaces_no_project_open(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    home = win.home
    proj = win.session.project
    home.set_project(proj)
    pump(qapp)
    text = home.details.toPlainText()
    assert "No project open" not in text and "holder" in text
    for word in ("Days", "Holders", "Scans", "Calibration", "ROI bins", "Sample sheet", "double-click a stack"):
        assert word in text, word
    assert "1 survey" in text and "1 complete" in text and "1 aborted" in text and "1 test" in text
    sheet = next(proj.root.glob("*.csv"))
    assert f"{sheet.name}, " in text
    assert home.details_card.title.full_text() == "Project"
    win.session._save_timer.stop()
    win.close()


def test_scan_summary_easy_and_advanced(qapp, fake_project):
    win = _window(qapp)
    proj = Project.discover(fake_project)
    st = proj.find(testdata.STACK)
    st.md["und_phase_linear"] = "0.0"                         # an attribute of the real files, never shown in Easy
    win.home.set_project(proj)
    pump(qapp)
    tree = win.home.tree
    holder = tree.topLevelItem(0).child(0)
    item = next(holder.child(i) for i in range(holder.childCount()) if holder.child(i).text(0) == testdata.STACK)
    tree.setCurrentItem(item)
    wait(qapp, win)
    assert not mode.is_advanced()
    page = win.home._scan_html(st)
    assert "energy regions" in page.lower() and "6 s per map" in page and "und_phase" not in page
    text = win.home.details.toPlainText()
    assert "Energy regions" in text and "every map complete" in text and "Metadata" not in text
    assert win.home.map_check(st) == [] and win.home.details_card.title.full_text() == "Scan"
    # a check that ends after a Reload belongs to the project before it: dropped
    win.home._maps_checked((win.home._generation - 1, str(st.path), [(686.0, "1 of 12 points")]))
    assert win.home.map_check(st) == []
    mode.set_advanced(True)
    pump(qapp)
    page = win.home._scan_html(st)
    assert "und_phase" in page and "Metadata" in win.home.details.toPlainText() and "6 s per map" in page
    assert "SDD geometry" in win.home.details.toPlainText()
    mode.set_advanced(False)
    tree.setCurrentItem(holder)                                # a holder row: the project summary again
    pump(qapp)
    assert "Holders" in win.home.details.toPlainText()
    win.close()


# ----------------------------------------------------------------------------- welcome and recent projects
def test_welcome_buttons_and_the_demo(qapp, user_data):
    win = _window(qapp)
    home = win.home
    es = home.empty_state
    assert es.isVisible() and [b.text() for b in es.buttons] == ["Open project…", "Open the demo beamtime",
                                                                 "Read the tutorials"]
    assert all(b.toolTip() for b in es.buttons) and es.geometry().right() <= es.parentWidget().width()
    assert not home.recent_list.isVisible()                      # nothing opened yet
    assert "No project open" in home.details.toPlainText()
    es.buttons[1].click()                                        # Open the demo beamtime
    wait(qapp, win)
    assert "demo_beamtime" in win.crumb.text() and home.project.root == user_data.resolve()
    assert not es.isVisible()
    es.buttons[2].click()                                        # Read the tutorials
    pump(qapp)
    assert win._help is not None and win._help.isVisible() and "TUTORIALS" in win._help.current_doc().upper()
    win._help.close()
    win.session._save_timer.stop()
    win.close()


def test_open_recent_menu_reopens(qapp, user_data, fake_project):
    win = _window(qapp)
    home = win.home
    win.open_demo()
    wait(qapp, win)
    demo = home.project.root
    file_menu = win.menus["file"]
    assert home.recent_menu.menuAction() in file_menu.actions()
    assert home.recent_menu.title() == "Open recent"
    home.open_path(fake_project)                                 # something else first
    wait(qapp, win)
    assert home.project.root == fake_project.resolve()
    home.recent_menu.aboutToShow.emit()
    entries = home.recent_actions()
    assert [a.data() for a in entries] == [str(fake_project.resolve()), str(demo)]
    assert entries[1].text().startswith("&2 ")
    entries[1].trigger()
    wait(qapp, win)
    assert home.project.root == demo
    # Clear list
    clear = next(a for a in home.recent_menu.actions() if a.text() == "Clear list")
    clear.trigger()
    from aurora.app.settings import recent_projects
    assert recent_projects() == [] and home.recent_actions() == []
    assert not next(a for a in home.recent_menu.actions() if a.text() == "Clear list").isEnabled()
    win.session._save_timer.stop()
    win.close()


def test_recent_list_greys_missing_projects_and_forgets(qapp, fake_project, tmp_path):
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMenu
    from aurora.app.settings import recent_projects, remember_project
    from aurora.app.theme import TOKENS
    gone = tmp_path / "share" / "beamtime that moved"
    remember_project(gone)
    remember_project(fake_project.resolve())
    win = _window(qapp)
    wait(qapp, win)                                              # the recent check job
    lst = win.home.recent_list
    assert lst.isVisible() and lst.count() == 2
    items = {lst.item(i).data(Qt.UserRole): lst.item(i) for i in range(lst.count())}
    missing, present = items[str(gone)], items[str(fake_project.resolve())]
    assert missing.text().startswith("beamtime that moved  (not found)") and "not found" in missing.toolTip()
    assert missing.foreground().color().name().lower() == TOKENS["disabled"].lower()
    assert "not found" not in present.text()
    menu_entries = [a.text() for a in win.home.recent_actions()]
    assert any("beamtime that moved  (not found)" in t for t in menu_entries)
    # right-click > Forget (the menu is built as the right-click builds it; QMenu.exec itself cannot be patched)
    assert lst.contextMenuPolicy() == Qt.CustomContextMenu
    assert lst.itemAt(lst.visualItemRect(missing).center()) is missing
    menu = win.home.recent_context_menu(missing)
    assert isinstance(menu, QMenu) and [a.text() for a in menu.actions()] == ["Open", "Forget"]
    next(a for a in menu.actions() if a.text() == "Forget").trigger()
    assert recent_projects() == [str(fake_project.resolve())]
    assert lst.count() == 1
    # double-click opens
    it = lst.item(0)
    at = lst.visualItemRect(it).center()
    QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, at)        # a double-click: press, release, ...
    QTest.mouseDClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, at)       # ... then the double-click event
    wait(qapp, win)
    assert win.home.project is not None and win.home.project.root == fake_project.resolve()
    win.session._save_timer.stop()
    win.close()


def test_a_failed_open_never_blocks_and_offers_retry(qapp, tmp_path, monkeypatch):
    import pathlib
    win = _window(qapp)
    home = win.home
    target = tmp_path / "share" / "unreachable beamtime"

    real_is_dir = pathlib.Path.is_dir
    calls = []                                                   # (thread, path) of every look at the share

    def slow_is_dir(self, *a, **k):
        if "unreachable beamtime" not in str(self):
            return real_is_dir(self, *a, **k)
        calls.append((threading.current_thread() is threading.main_thread(), str(self)))
        time.sleep(5)                                            # the share does not answer for 5 s, then fails
        raise OSError(f"{self}: the network share does not answer")

    with monkeypatch.context() as m:
        m.setattr(pathlib.Path, "is_dir", slow_is_dir)
        t0 = time.perf_counter()
        home.open_path(target)
        took = time.perf_counter() - t0
        assert took < 0.2, took
        pump(qapp)
        assert "reading" in home.path_label.text() and not home.open_btn.isEnabled()
        wait(qapp, win, 30000)
    assert calls and not any(main for main, _p in calls), calls          # only the discovery job looked at it
    assert home.project is None
    fs = home.failure_state
    assert fs.isVisible() and not home.empty_state.isVisible()
    assert [b.text() for b in fs.buttons] == ["Retry", "Choose another…"]
    assert "Could not open the project" in fs.text() and "does not answer" in fs.text()
    assert "unreachable beamtime" in fs.text().replace("​", "")
    assert "Traceback" not in home.details.toPlainText()                # Easy: the reason, not the traceback
    assert home.path_label.text() == "could not open the project"
    assert "Could not open the project" in home.details.toPlainText()
    # Retry opens the same path again (now a real folder): the panel goes and the project shows
    testdata.make_fake_project(target)
    fs.buttons[0].click()
    wait(qapp, win)
    assert home.project is not None and home.project.root == target.resolve()
    assert not fs.isVisible() and "No project open" not in home.details.toPlainText()
    # a failed open while a project is open keeps it, with a way back
    home.open_path(tmp_path / "nowhere")
    wait(qapp, win)
    assert fs.isVisible() and [b.text() for b in fs.buttons][-1] == "Keep the open project"
    fs.buttons[-1].click()
    pump(qapp)
    assert not fs.isVisible() and home.project.root == target.resolve() and "Holders" in home.details.toPlainText()
    win.session._save_timer.stop()
    win.close()


def test_quiet_failure_keeps_the_welcome(qapp, tmp_path):
    win = _window(qapp)
    win.home.open_path(tmp_path / "gone", quiet=True)
    wait(qapp, win)
    assert win.home.empty_state.isVisible() and not win.home.failure_state.isVisible()
    assert "could not be opened" in win.home.path_label.text()
    win.close()


def test_welcome_list_follows_the_theme(qapp, tmp_path):
    """The greyed entry follows the theme (it is re-filled on a theme change)."""
    from aurora.app.settings import remember_project
    from aurora.app.theme import TOKENS, apply_theme
    remember_project(tmp_path / "missing")
    win = _window(qapp)
    wait(qapp, win)
    try:
        apply_theme(qapp, appearance="dark")
        pump(qapp)
        it = win.home.recent_list.item(0)
        assert it.foreground().color().name().lower() == TOKENS["disabled"].lower()
    finally:
        apply_theme(qapp, appearance="light")
    pump(qapp)
    assert win.home.recent_list.item(0).foreground().color().name().lower() == TOKENS["disabled"].lower()
    win.close()
