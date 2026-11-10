"""Empty states with a next step, and impossible actions disabled with the reason in their tooltip."""
import pytest

pytest.importorskip("pyqtgraph")

from aurora.app import mode  # noqa: E402
from aurora.core.model import Project  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


def test_a_fresh_window(qapp, fake_project):
    from aurora.app.main import MainWindow, PERSPECTIVES
    win = MainWindow()
    win.resize(1300, 820)
    win.show()
    pump(qapp)
    for key, _t in PERSPECTIVES:
        win.show_perspective(key)                     # Maps too: show_perspective ignores the mode
        pump(qapp)
        es = win.pages[key].empty_state
        assert es.isVisible(), key
        assert es.text() and es.buttons and es.buttons[0].isVisible(), key
        assert es.geometry().width() <= es.parentWidget().width(), key
    for b in (win.check.run_btn, win.maps.load_btn, win.report.build_btn, win.reduce.new_zone_btn, win.reduce.send_btn):
        assert not b.isEnabled() and "open a project" in b.toolTip(), (b.text(), b.toolTip())
    assert not win.report.prev_btn.isEnabled() and not win.report.next_btn.isEnabled()
    assert "build the report first" in win.report.prev_btn.toolTip()
    # Compare and Fit: an empty library
    assert "Add saved spectra" in win.compare.empty_state.buttons[0].text()
    assert not win.compare.export_btn.isEnabled() and "add spectra" in win.compare.export_btn.toolTip()
    assert not win.fit.lcf_export_btn.isEnabled() and "run LCF first" in win.fit.lcf_export_btn.toolTip()
    assert not win.fit.pk_export_btn.isEnabled()
    # a project: Survey and Reduce show their content
    win.home.set_project(Project.discover(fake_project))
    pump(qapp)
    for key in ("survey", "reduce"):
        win.show_perspective(key)
        pump(qapp)
        assert not win.pages[key].empty_state.isVisible(), key
    win.show_perspective("reduce")
    pump(qapp)
    assert win.reduce.zone_state.isVisible() and "drag over the map" in win.reduce.zone_state.text()   # no zone yet
    assert win.reduce.new_zone_btn.isEnabled()
    win.show_perspective("check")
    pump(qapp)
    # Easy: the empty 'Spectra to check' queue points to Reduce; Advanced: the holder checks have not run (WP4 I23)
    assert win.check.queue_state.isVisible() and win.check.queue_state.buttons[0].text() == "Go to Reduce"
    mode.set_advanced(True)
    pump(qapp)
    assert win.check.run_state.isVisible() and "holder N5" in win.check.run_state.buttons[0].text()
    mode.set_advanced(False)
    pump(qapp)
    win.show_perspective("report")
    pump(qapp)
    assert win.report.zone_state.isVisible() and not win.report.build_btn.isEnabled()
    assert "draw a zone" in win.report.build_btn.toolTip()
    win.show_perspective("home")
    pump(qapp)
    assert not win.home.empty_state.isVisible()
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


def test_fit_empty_state_goes_to_compare(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.show()
    win.show_perspective("fit")
    pump(qapp)
    es = win.fit.empty_state
    assert es.isVisible() and es.buttons[0].text() == "Go to Compare"
    es.buttons[0].click()
    pump(qapp)
    assert win.stack.currentWidget() is win.compare
    win.close()


def test_advanced_buttons_of_an_empty_state(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.show()
    win.show_perspective("compare")
    pump(qapp)
    es = win.compare.empty_state
    # References… is an Advanced control: Easy offers what it can do (saved spectra, Check) instead
    assert [b.text() for b in es.buttons] == ["Add saved spectra…", "Go to Check"]
    mode.set_advanced(True)
    pump(qapp)
    refs = es.buttons[1]
    assert refs.text() == "References…" and refs.isVisible()
    mode.set_advanced(False)
    win.close()


def test_empty_plots_show_a_hint_instead_of_axes(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.show()
    win.show_perspective("compare")
    pump(qapp)
    item = win.compare.plot.getPlotItem()
    assert getattr(item, "_aurora_hint", None) is not None and item._aurora_hint.scene() is not None
    assert not item.getAxis("left").isVisible() and not item.getAxis("bottom").isVisible()
    from aurora.app.widgets import set_plot_hint
    set_plot_hint(win.compare.plot, None)
    assert item.getAxis("left").isVisible() and item._aurora_hint.scene() is None
    win.close()
