"""I20: Easy Reduce in John Bussey's four numbered steps, ending in 'Add to Check'."""
import pytest

pytest.importorskip("pyqtgraph")

from aurora.app import mode  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from wp3_helpers import close, open_window, pump  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def demo(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    win.jobs.wait_idle(30000)
    yield win
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


TODAY = ["load_all_btn", "scan_list", "new_zone_btn", "del_zone_btn", "propose_btn", "stitch_btn", "zone_list",
         "brush_n", "target_new", "map_line", "map_sdd", "map_mode", "map_energy", "map_i0", "map_log", "map",
         "z_label", "z_group", "z_comment", "send_btn", "cards_area"]


def test_easy_shows_four_numbered_steps_within_the_limit(qapp, demo):
    from test_easy_mode import interactive
    win = demo
    assert not mode.is_advanced()
    win.show_perspective("reduce")
    pump(qapp, 5)
    rd = win.reduce
    ws = interactive(win.pages["reduce"])
    assert 0 < len(ws) <= 18, [type(w).__name__ for w in ws]
    titles = rd.step_titles()
    assert [t[:3] for t in titles] == ["1 ·", "2 ·", "3 ·", "4 ·"], titles
    assert titles[0] == "1 · Scans of holder N5" and titles[1] == "2 · XRF window"
    assert titles[2] == "3 · Region" and titles[3] == "4 · Reduce"
    # step 3: navigate / rectangle / lasso, Clear, New sample, Delete, the sample label
    shown = {t for t, b in rd.tool_buttons.items() if b.isVisible()}
    assert shown == {"navigate", "rect+", "lasso+"}
    for w in (rd.clear_btn, rd.new_zone_btn, rd.del_zone_btn, rd.z_label, rd.region_caption):
        assert w.isVisible()
    assert rd.region_caption.text().startswith("Region: 1 rectangle · 12 of 12 points of ")
    # step 4: the read-only line and the primary 'Add to Check'; the preview keeps one toggle
    assert rd.step4_line.isVisible() and rd.add_check_btn.isVisible() and rd.add_check_btn.objectName() == "primary"
    assert rd.step4_line.text().startswith("I0 mcc ch1 (Au mesh) · TEY mcc ch4 · zone sum")
    assert "de-spike k = 6" in rd.step4_line.text() and rd.step4_line.text().endswith("SDD1-4")
    assert rd.preview.all_scans.isVisible() and rd.preview.all_scans.text() == "all scans of the sample"
    assert not rd.preview.toggles.isVisible()
    # step 2: preset, spectrum, lo / hi
    wc = rd.window_card
    assert wc.preset.isVisible() and wc.lo.isVisible() and wc.hi.isVisible() and not wc.adv_box.isVisible()


def test_advanced_shows_every_control_of_the_old_layout(qapp, demo):
    win = demo
    mode.set_advanced(True)
    win.show_perspective("reduce")
    pump(qapp, 5)
    rd = win.reduce
    hidden = [n for n in TODAY if not getattr(rd, n).isVisible()]
    hidden += [t for t, b in rd.tool_buttons.items() if not b.isVisible()]
    hidden += [n for n in ("show_sdd", "show_sum", "show_tey", "show_i0", "show_spikes", "all_scans")
               if not getattr(rd.preview, n).isVisible()]
    assert not hidden, hidden
    assert rd._cards and rd._cards[0].isVisible()
    assert rd.map_card.title.text() == "Stack map" and rd.zone_card.title.text() == "Zone"
    assert not rd.step4_line.isVisible() and not rd.add_check_btn.isVisible()
    assert [t[:3] for t in rd.step_titles()] != ["1 ·", "2 ·", "3 ·", "4 ·"]
    mode.set_advanced(False)


def test_one_rectangle_and_add_to_check(qapp, fake_project):
    win, sid = open_window(qapp, Project.discover(fake_project))
    try:
        rd, ses = win.reduce, win.session
        sd = ses.stack_loaded(sid)
        assert not rd.add_check_btn.isEnabled()
        x0, x1, y0, y1 = sd.bbox
        rd.map.rect_drawn.emit(x0, x1, y0, y1)
        pump(qapp)
        assert rd.add_check_btn.isEnabled()
        rd.add_check_btn.click()
        pump(qapp)
        z = ses.zone()
        assert len(ses.check_queue) == 1 and tuple(vars(ses.check_queue[0]).values()) == (ses.current_holder, z.id, sid)
        st = z.settings[sid]
        assert st.fluo_norm == "i0jump" and st.k == 6 and st.sdd == [True, True, True, True]
        rd.add_check_btn.click()                                 # queued once
        assert len(ses.check_queue) == 1 and "already in Check" in rd.status_label.text()
    finally:
        close(win)


def test_a_saturated_sdd_is_off_and_named(qapp, fake_project):
    from aurora.core import testdata
    proj = Project.discover(fake_project)
    proj.find(testdata.STACK).summary["sdd_saturated"] = ["sdd3"]
    win, sid = open_window(qapp, proj)
    try:
        rd, ses = win.reduce, win.session
        sd = ses.stack_loaded(sid)
        x0, x1, y0, y1 = sd.bbox
        rd.map.rect_drawn.emit(x0, x1, y0, y1)
        pump(qapp)
        rd.add_to_check()
        st = ses.zone().settings[sid]
        assert st.sdd == [True, True, False, True]
        assert "SDD1-4 (SDD3 saturated, left out)" in rd.step4_line.text()
        assert not rd.chip_box.isVisible()                      # the saturated SDD off is the Easy default
    finally:
        close(win)


def test_nothing_selected_away_from_the_footprint(qapp, fake_project):
    win, sid = open_window(qapp, Project.discover(fake_project))
    try:
        rd, ses = win.reduce, win.session
        sd = ses.stack_loaded(sid)
        x0, x1, y0, y1 = sd.bbox
        rd.map.rect_drawn.emit(x1 + 2.0, x1 + 3.0, y1 + 2.0, y1 + 3.0)
        pump(qapp)
        assert ses.zone() is not None and "nothing selected" in rd.region_caption.text()
        assert not rd.add_check_btn.isEnabled() and "draw over the stack footprint" in rd.add_check_btn.toolTip()
        assert rd.add_to_check() == 0 and not ses.check_queue
    finally:
        close(win)
