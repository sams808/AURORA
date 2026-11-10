"""I22: Easy mode never hides a setting silently: the 'advanced settings in use' chip, its tooltip, Reset to Easy
defaults, and mode switches that never write the session."""
import pytest

pytest.importorskip("pyqtgraph")

from aurora.app import mode  # noqa: E402
from aurora.core.io.config import ScanSettings  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from wp3_helpers import close, open_window, pump  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_differs_from_is_empty_for_a_default_settings():
    assert ScanSettings().differs_from(ScanSettings()) == []
    from aurora.app.reduce import CHIP_FIELDS, easy_differences
    assert "line" not in CHIP_FIELDS and "roi" not in CHIP_FIELDS
    assert easy_differences(ScanSettings(), ScanSettings()) == []
    assert easy_differences(ScanSettings(fluo_norm="max"), ScanSettings()) == ["fluo_norm"]


def test_chip_tooltip_reset_and_switches(qapp, fake_project):
    win, sid = open_window(qapp, Project.discover(fake_project))
    try:
        rd, ses = win.reduce, win.session
        sd = ses.stack_loaded(sid)
        x0, x1, y0, y1 = sd.bbox
        rd.map.rect_drawn.emit(x0, x1, y0, y1)
        pump(qapp)
        assert not rd.chip_box.isVisible()                 # Easy, default settings: no chip
        # set fn = max and SDD3 off on the card in Advanced
        mode.set_advanced(True)
        pump(qapp)
        card = rd._cards[0]
        card.fn.setCurrentIndex(card.fn.findData("max"))
        card.sdd[2].setChecked(False)
        pump(qapp)
        st = ses.zone().settings[sid]
        assert st.fluo_norm == "max" and st.sdd == [True, True, False, True]
        assert {"fn", "sdd"} <= set(st.pinned)              # a manual card edit pins its fields
        assert not rd.chip_box.isVisible()                 # Advanced shows the card itself
        mode.set_advanced(False)
        pump(qapp)
        assert rd.chip_box.isVisible() and rd.chip.text() == "advanced settings in use"
        tip = rd.chip.toolTip()
        assert "fluorescence normalisation: / I0, maximum = 1" in tip and "SDD3 off" in tip, tip
        # two mode switches never write the session
        ses.flush()
        ses.save()
        ses._save_timer.stop()
        before = ses.session_file.read_bytes()
        from PySide6.QtTest import QTest
        for on in (True, False, True, False):
            mode.set_advanced(on)
            QTest.qWait(30)
        QTest.qWait(1000)
        assert ses.session_file.read_bytes() == before
        # Reset to Easy defaults: one edit, fn i0jump, every SDD on, the chip goes
        edits = []
        real = ses.edit

        from contextlib import contextmanager

        @contextmanager
        def counted(description, holder=None):
            if ses._edit_depth == 0:
                edits.append(description)
            with real(description, holder):
                yield ses
        ses.edit = counted
        try:
            rd.reset_btn.click()
        finally:
            del ses.edit
        pump(qapp)
        assert edits == [f"reset zone {ses.current_zone} to the Easy defaults"]
        assert st.fluo_norm == "i0jump" and st.sdd == [True, True, True, True]
        assert "fn" not in st.pinned and "sdd" not in st.pinned
        assert not rd.chip_box.isVisible()
    finally:
        mode.set_advanced(False)
        close(win)


def test_a_session_without_a_de_spike_rule_is_not_called_advanced(qapp, fake_project):
    """A zone from a 0.3 session or the browser zone tool (no 'dsm': the earlier de-spike rule stays in force) shows no
    chip in Easy mode, since no control in either mode sets the rule; the step-4 line names it and Reset leaves it."""
    win, sid = open_window(qapp, Project.discover(fake_project))
    try:
        rd, ses = win.reduce, win.session
        x0, x1, y0, y1 = ses.stack_loaded(sid).bbox
        rd.map.rect_drawn.emit(x0, x1, y0, y1)
        pump(qapp)
        z = ses.zone()
        d = z.settings[sid].to_json()
        d.pop("dsm", None)
        old = ScanSettings.from_json(d)
        assert old.despike_mode == "classic"
        with ses.edit("an older session"):
            z.settings[sid] = old
        pump(qapp)
        assert not rd.chip_box.isVisible(), rd.chip.toolTip()
        assert "de-spike k = 6 (the earlier rule)" in rd.step4_line.text()
        assert rd.reset_to_easy() == 0 and ses.zone(z.id).settings[sid].despike_mode == "classic"
    finally:
        close(win)
