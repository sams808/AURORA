"""Blank and standalone (the window): the optional files made or picked inside AURORA (File > Choose / New sample sheet…,
Import SDD calibration…, Reduce's Save as a fixed line…), a malformed calibration in Reduce, a scan's kind set by
hand on Home, Survey's default channel and its explanations for a holder without survey or sample sheet. Items BL-08,
BL-09, BL-11, BL-12, BL-22, BL-25, BL-26 and BL-29 of the blank audit; the core is in test_blank_core.py."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import h5py
import pytest

pytest.importorskip("pyqtgraph")

from aurora.core import testdata  # noqa: E402
from aurora.core import xrf_windows as xw  # noqa: E402
from aurora.core.constants import DETS  # noqa: E402
from aurora.core.io.config import RoiBins  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from aurora.core.names import holder_from_survey  # noqa: E402

HOLDER = holder_from_survey(testdata.SURVEY)


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


def raw_only(tmp_path, name="raw_only") -> Path:
    root = testdata.make_fake_project(tmp_path / name)
    for c in list(root.iterdir()):
        if c.name != "raw":
            shutil.rmtree(c) if c.is_dir() else c.unlink()
    return root


def window(qapp, root, size=(1500, 940)):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.resize(*size)
    win.show()
    win.home.set_project(Project.discover(root))
    pump(qapp)
    win.jobs.wait_idle(30000)
    return win


def close(win):
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


def project_files(win):
    from aurora.app.project_files import ProjectFiles
    return win.findChild(ProjectFiles, "aurora_project_files")


def details(win) -> str:
    """Home's summary panel as plain text (the paths' zero-width break points removed)."""
    return win.home.details.toPlainText().replace("​", "")


def action(win, text):
    for a in win.menus["file"].actions():
        if a.text() == text:
            return a
    raise AssertionError(f"File > {text} not found")


# ----------------------------------------------------------------------------- BL-09 a malformed calibration in Reduce
def test_reduce_with_a_malformed_calibration_has_no_error(qapp, tmp_path, error_dialogs):
    from wp3_helpers import close as close3, open_window
    root = testdata.make_fake_project(tmp_path / "bt")
    (root / "config" / "sdd_calibration.json").write_text(json.dumps({"sdd1": 5}), encoding="utf-8")
    win, sid = open_window(qapp, Project.discover(root))
    try:
        wc = win.reduce.window_card
        assert wc.ev_label.text().startswith("all detectors: channels") and "nominal" in wc.ev_label.text()
        win.show_perspective("home")
        pump(qapp)
        text = details(win)
        assert "sdd1: not a JSON object (5)" in text and "Calibration" in text
        assert not error_dialogs
    finally:
        close3(win)


# ----------------------------------------------------------------------------- BL-26 Save as a fixed line…
def test_save_as_a_fixed_line_on_a_raw_only_beamtime(qapp, tmp_path, error_dialogs):
    """Without roi_bins.json: the note, the line of the edge on the nominal calibration, then Save as a fixed line…
    writes config/roi_bins.json; the line is a preset, the stack reduces with it and Maps draws its map."""
    from aurora.app.reduce_window import NO_FIXED_LINES, SAVE_LINE
    from aurora.core.hypercube import Hypercube
    from aurora.core.stack import StackData
    from wp3_helpers import close as close3, open_window
    root = raw_only(tmp_path)
    win, sid = open_window(qapp, Project.discover(root))
    try:
        ses, wc = win.session, win.reduce.window_card
        assert wc.bins_note.isVisible() and wc.bins_note.text() == NO_FIXED_LINES
        items = [wc.preset.itemText(i) for i in range(wc.preset.count())]
        assert items == ["total XRF (fixed bins)", "John Bussey's 60:83 (F Kα)", "custom"]     # it holds F Kα
        sd0, scan0 = ses.stack_loaded(sid), ses.scan_of(sid)
        sd0.edge = ("O", "K")                         # an edge John Bussey's 60:83 does not hold (O Kα is channel 52)
        wc.set_stack(sd0, scan0)
        items = [wc.preset.itemText(i) for i in range(wc.preset.count())]
        assert items[1].startswith("line of the edge: O Kα") and items[1].endswith("(nominal calibration, check the peak)")
        el = wc._edge_line
        assert el.nominal and el.windows == xw.edge_line_windows(("O", "K"), None, None, nominal=True).windows
        sd0.edge = ("F", "K")
        wc.set_stack(sd0, scan0)
        assert wc.save_line_btn.isVisible() and wc.save_line_btn.isEnabled() and wc.save_line_btn.text() == SAVE_LINE
        assert wc.default_line_name() == "F Kα"
        wc.set_window(None, 60, 75, commit=False)
        asked = []
        wc.ask_line_name = lambda default: asked.append(default) or default     # the dialog answers the default
        assert wc.save_fixed_line() == "F Kα" and asked == ["F Kα"]
        pump(qapp)
        bins = RoiBins(root / "config" / "roi_bins.json")
        assert bins.labels == ["F Kα"] and bins.bounds("F Kα") == {d: (60, 75) for d in DETS}
        assert ses.project.roi_bins.labels == ["F Kα"] and not wc.bins_note.isVisible()
        assert "F Kα (fixed bins)" in [wc.preset.itemText(i) for i in range(wc.preset.count())]
        # a second save of the same name asks first; No keeps the file
        wc.confirm_replace = lambda name: False
        assert wc.save_fixed_line("F Kα") == ""
        # the stack reduces with it (AURORA's cache no longer matches the bins), and Maps draws its map
        proj = ses.project
        scan = proj.find(testdata.STACK)
        sd = StackData.load(proj, scan)
        assert sd.labels == ["F Kα", "total XRF"] and sd.source == "reduced from raw"
        mp = win.maps
        mp.set_hypercube(Hypercube.load(proj, scan))
        pump(qapp)
        assert mp.draw_el_btn.isEnabled() and [mp.line.itemData(i) for i in range(mp.line.count())] == [None, "F Kα"]
        mp.draw_elements()
        assert win.jobs.wait_idle(20000)
        pump(qapp)
        assert [lab for lab, _g in mp.element_grids] == ["F Kα"]
        assert not error_dialogs
    finally:
        close3(win)


def test_save_as_a_fixed_line_is_refused_in_a_protected_folder(qapp, tmp_path, monkeypatch):
    from wp3_helpers import close as close3, open_window
    root = raw_only(tmp_path)
    monkeypatch.setenv("AURORA_PROTECT_DIRS", str(root))
    win, sid = open_window(qapp, Project.discover(root))
    try:
        wc = win.reduce.window_card
        assert not wc.save_line_btn.isEnabled() and "protected" in wc.save_line_btn.toolTip()
        assert wc.save_fixed_line("F Kα") == "" and not (root / "config" / "roi_bins.json").exists()
    finally:
        close3(win)


def test_maps_names_the_way_to_make_fixed_lines():
    from aurora.app.maps import NO_BINS
    assert "Save as a fixed line…" in NO_BINS and "Reduce step 2" in NO_BINS


# ----------------------------------------------------------------------------- BL-08 the scan card
def test_the_scan_card_marks_a_line_this_beamtime_does_not_define(qapp, tmp_path):
    from aurora.cli import cmd_demo
    from wp3_helpers import close as close3, open_window
    root = tmp_path / "demo"
    cmd_demo(root)
    win, sid = open_window(qapp, Project.discover(root))
    try:
        ses, rd = win.session, win.reduce
        sd = ses.stack_loaded(sid)
        rd.map.rect_drawn.emit(*sd.bbox)
        pump(qapp)
        z = ses.zone()
        with ses.edit("pin a line"):
            st = z.settings[sid]
            st.line, st.roi, st.pinned = "Mn Lα", None, ["line"]
        rd._rebuild_cards()
        pump(qapp)
        card = next(c for c in rd._cards if c.sid == sid)
        assert card.line.currentText() == "Mn Lα (not defined in this beamtime)" and card.undefined_line == "Mn Lα"
        assert card.values()["line"] == "Mn Lα" and not card.line_note.isHidden()
        assert "Save as a fixed line…" in card.line_note.text()
    finally:
        close3(win)


# ----------------------------------------------------------------------------- BL-12 the sample sheet in the window
def test_new_and_choose_sample_sheet(qapp, tmp_path, monkeypatch):
    from aurora.app.project_files import CHOOSE_SHEET, NEW_SHEET, SHEET_HEADER
    root = testdata.make_fake_project(tmp_path / "bt")
    win = window(qapp, root)
    try:
        ses, pf = win.session, project_files(win)
        assert action(win, NEW_SHEET).isEnabled() and action(win, CHOOSE_SHEET).isEnabled()
        assert ses.sheet_rows == [] and ses.sheet_choice.path is None
        p = pf.new_sheet()
        assert p == root / "samples.csv"
        lines = p.read_text(encoding="utf-8").splitlines()
        assert lines == [",".join(SHEET_HEADER), f",{HOLDER},,,,"]
        pump(qapp)
        assert "CSV not used" in details(win) and "a header but no sample row" in details(win)
        assert pf.new_sheet() == root / "samples_2.csv"                     # never written over
        (root / "samples_2.csv").unlink()
        p.write_text(f"{','.join(SHEET_HEADER)}\nS1,{HOLDER},Group 1,NaF,F K,\n", encoding="utf-8")
        ses.reread_sheet()
        assert [r.sample_id for r in ses.sheet_for(HOLDER)] == ["S1"]
        # Choose sample sheet…: a list without a holder column is offered on every holder; one undo step
        other = root / "my list.csv"
        other.write_text("Name,Formula\nquartz,SiO2\nalbite,NaAlSi3O8\n", encoding="utf-8")
        monkeypatch.setattr("aurora.app.project_files.QFileDialog.getOpenFileName", lambda *a, **k: (str(other), ""))
        n = ses.undo_stack.count()
        assert pf.choose_sheet()
        assert ses.ui_value("sample_sheet") == "my list.csv" and ses.undo_stack.count() == n + 1
        assert [r.sample_id for r in ses.sheet_for(HOLDER)] == ["quartz", "albite"]
        win.show_perspective("survey")
        pump(qapp)
        assert win.survey.sheet_table.rowCount() == 2 and win.survey.sheet_table.isVisible()
        assert "chosen with File > Choose sample sheet…" in details(win)
        ses.undo()
        pump(qapp)
        assert ses.ui_value("sample_sheet") is None and [r.sample_id for r in ses.sheet_rows] == ["S1"]
        assert win.survey.sheet_table.rowCount() == 1
        # a file that is no sample sheet is refused, with the reason
        bad = root / "bad.csv"
        bad.write_bytes(b"\x00\x01binary")
        warned = []
        monkeypatch.setattr("aurora.app.project_files.QMessageBox.warning", lambda *a: warned.append(a[2]))
        assert not pf.choose_sheet(bad) and "not a text CSV" in warned[0]
    finally:
        close(win)


def test_new_sample_sheet_and_import_are_refused_in_a_protected_folder(qapp, tmp_path, monkeypatch):
    from aurora.app.project_files import IMPORT_CALIBRATION, NEW_SHEET
    root = testdata.make_fake_project(tmp_path / "bt")
    monkeypatch.setenv("AURORA_PROTECT_DIRS", str(root))
    win = window(qapp, root)
    try:
        for text in (NEW_SHEET, IMPORT_CALIBRATION):
            a = action(win, text)
            assert not a.isEnabled() and "protected" in a.toolTip()
        warned = []
        monkeypatch.setattr("aurora.app.project_files.QMessageBox.warning", lambda *a: warned.append(a[2]))
        assert project_files(win).new_sheet() is None and not (root / "samples.csv").exists() and warned
    finally:
        close(win)


# ----------------------------------------------------------------------------- BL-11 Import SDD calibration…
def test_import_sdd_calibration(qapp, tmp_path, monkeypatch):
    root = testdata.make_fake_project(tmp_path / "bt")
    good = tmp_path / "cal" / "sdd_calibration.json"
    good.parent.mkdir()
    good.write_text(json.dumps({d: {"gain": 9.5 + 0.1 * i, "offset": 1.0} for i, d in enumerate(DETS)}),
                    encoding="utf-8")
    (root / "config" / "sdd_calibration.json").write_text(json.dumps({"sdd1": 5}), encoding="utf-8")
    win = window(qapp, root)
    try:
        ses, pf = win.session, project_files(win)
        assert not ses.project.calibration.calibrated
        bad = tmp_path / "cal" / "bad.json"
        bad.write_text("[1, 2]", encoding="utf-8")
        warned = []
        monkeypatch.setattr("aurora.app.project_files.QMessageBox.warning", lambda *a: warned.append(a[2]))
        assert pf.import_calibration(bad) is None and "not an SDD calibration AURORA can use" in warned[0]
        target = pf.import_calibration(good)
        assert target == root / "config" / "sdd_calibration.json"
        assert ses.project.calibration.calibrated and ses.project.calibration.data["sdd2"]["gain"] == pytest.approx(9.6)
        assert list((root / "config").glob("sdd_calibration.backup-*.json"))       # the previous file is kept
        pump(qapp)
        assert "config/sdd_calibration.json" in details(win)
        assert "(calibrated)" in win.reduce.window_card.ev_label.text() or win.reduce.window_card._sd is None
    finally:
        close(win)


# ----------------------------------------------------------------------------- BL-22 a scan's kind set by hand
def test_treat_as_a_sample_scan_and_back(qapp, tmp_path):
    from PySide6.QtCore import Qt
    root = testdata.make_fake_project(tmp_path / "bt")
    win = window(qapp, root)
    try:
        ses, home = win.session, win.home

        def item_of(name):
            it = home.tree.topLevelItem(0)
            stack = [it]
            while stack:
                cur = stack.pop()
                s = cur.data(0, Qt.UserRole)
                if s is not None and s.name == name:
                    return cur
                stack += [cur.child(i) for i in range(cur.childCount())]
            raise AssertionError(name)
        menu = home.scan_context_menu(item_of(testdata.TEST))
        acts = [a for a in menu.actions() if not a.isSeparator()]
        assert [a.text() for a in acts] == ["Treat as a sample scan"]
        acts[0].trigger()
        pump(qapp)
        assert ses.ui_value("kind_override") == {testdata.TEST: "sample"}
        h = ses.holder()
        assert testdata.TEST in [s.name for s in h.stacks]
        menu = home.scan_context_menu(item_of(testdata.TEST))
        assert [a.text() for a in menu.actions()] == ["Treat as a test", "Use the rules again"]
        assert home.scan_context_menu(item_of(testdata.SURVEY)) is None          # surveys have no kind menu
        # the choice is saved with the session and read at the next discovery
        ses.flush()
        again = Project.discover(root)
        assert again.find(testdata.TEST).status == "complete"
        menu.actions()[1].trigger()
        pump(qapp)
        assert ses.ui_value("kind_override") == {} and ses.project.find(testdata.TEST).status == "test"
    finally:
        close(win)


# ----------------------------------------------------------------------------- BL-25 Survey's default channel
def test_survey_opens_on_the_line_of_the_holders_edge(qapp, tmp_path):
    root = testdata.make_fake_project(tmp_path / "bt")
    day = root / "raw" / testdata.DAY
    new = day / f"{testdata.STACK[:6]}_hematite_fe_l3_stack"
    (day / testdata.STACK).rename(new)
    with h5py.File(next(new.glob("*.h5")), "a") as h:
        h["stack_metadata"].attrs["scan_name"] = "hematite_fe_l3"
    (root / "config" / "roi_bins.json").write_text(json.dumps({
        "F Kα": {d: [60, 74] for d in DETS}, "Fe Lα": {d: [66, 76] for d in DETS}}), encoding="utf-8")
    win = window(qapp, root)
    try:
        proj = win.session.project
        assert proj.find(new.name).edge == ("Fe", "L3")
        win.show_perspective("survey")
        pump(qapp)
        assert win.survey.channel.currentText() == "Fe Lα"
    finally:
        close(win)


def test_survey_without_fixed_lines_opens_on_the_total(qapp, tmp_path):
    root = raw_only(tmp_path)
    win = window(qapp, root)
    try:
        win.show_perspective("survey")
        pump(qapp)
        assert win.survey.channel.currentText() == "total"
    finally:
        close(win)


# ----------------------------------------------------------------------------- BL-29 Survey explains what is missing
@pytest.mark.parametrize("size", [(1200, 760), (1500, 940)])
def test_survey_explains_a_holder_without_survey_and_no_sheet(qapp, tmp_path, size):
    from aurora.app import mode
    from aurora.app.survey import BEFORE_SURVEY, NO_SHEET, NO_SURVEY_MAP
    root = raw_only(tmp_path)
    shutil.rmtree(root / "raw" / testdata.DAY / testdata.SURVEY)
    win = window(qapp, root, size)
    try:
        sv = win.survey
        for adv in (False, True):
            mode.set_advanced(adv)
            win.show_perspective("survey")
            pump(qapp, 10)
            assert sv.no_map_state.isVisible() and sv.no_map_state.text() == NO_SURVEY_MAP
            assert sv.holder_combo.currentText().startswith(BEFORE_SURVEY)
            assert sv.zones_card.title.text().startswith("Zones of the stacks before the first survey")
            assert sv.sheet_note.isVisible() and sv.sheet_note.text().startswith(NO_SHEET) and not sv.sheet_table.isVisible()
            assert sv.new_sheet_btn.isVisible() and sv.choose_sheet_btn.isVisible()
        mode.set_advanced(False)
    finally:
        close(win)


def test_survey_names_the_holders_a_sheet_lists(qapp, tmp_path):
    root = testdata.make_fake_project(tmp_path / "bt")
    (root / "samples.csv").write_text("Sample ID,Plate\nS1,elsewhere\nS2,other\n", encoding="utf-8")
    win = window(qapp, root)
    try:
        win.show_perspective("survey")
        pump(qapp)
        sv = win.survey
        assert not sv.no_map_state.isVisible()
        assert sv.sheet_note.text() == f"The sample sheet lists no sample on holder {HOLDER} (its holders: elsewhere, other)."
        assert not sv.sheet_actions.isVisible() and not sv.sheet_table.isVisible()
    finally:
        close(win)


# ----------------------------------------------------------------------------- BL-31 Save as plate layout…
def test_grid_of_zones_saves_a_plate_layout(qapp, tmp_path):
    from aurora.app import mode
    from aurora.core.io.config import load_plate_layout_checked
    root = testdata.make_fake_project(tmp_path / "bt")
    win = window(qapp, root)
    try:
        mode.set_advanced(True)
        win.show_perspective("survey")
        pump(qapp)
        sv = win.survey
        dlg = sv.open_grid_dialog((-0.9, 0.3, -0.5, 0.3))
        pump(qapp)
        assert dlg.layout_combo.count() == 1 and dlg.save_layout_btn.isEnabled()
        dlg.rows.setValue(1)
        dlg.cols.setValue(2)
        dlg.ask_layout_name = lambda default: "two pads"
        assert dlg.save_layout() == "two pads"
        lay, problems = load_plate_layout_checked(root / "config" / "plate_layout.json")
        assert not problems and lay[HOLDER]["groups"][0]["cells"] == [[1, 2]]
        assert dlg.layout_combo.currentText().startswith("two pads (1 × 2)")
        dlg.reject()
        dlg = sv.open_grid_dialog((-0.9, 0.3, -0.5, 0.3))
        assert [dlg.layout_combo.itemText(i) for i in range(dlg.layout_combo.count())] == ["none", "two pads (1 × 2)"]
        dlg.reject()
    finally:
        mode.set_advanced(False)
        close(win)


def test_a_bad_plate_layout_opens_the_grid_on_numbers(qapp, tmp_path, error_dialogs):
    """BL-20: a group of the wrong shape is left out: the Grid of zones dialog opens with zone numbers, and Home's
    summary names the problem."""
    from aurora.app import mode
    root = testdata.make_fake_project(tmp_path / "bt")
    (root / "config" / "plate_layout.json").write_text(json.dumps(
        {HOLDER: {"groups": [{"id": "g1", "rows": "two", "cols": 3}, {"id": "g2", "rows": 2, "cols": 2, "cells": 5}],
                  "samples": []}}), encoding="utf-8")
    win = window(qapp, root)
    try:
        assert "config/plate_layout.json" in details(win) and "rows must be a whole number" in details(win)
        mode.set_advanced(True)
        win.show_perspective("survey")
        pump(qapp)
        dlg = win.survey.open_grid_dialog((-0.9, 0.3, -0.5, 0.3))
        pump(qapp)
        assert dlg.layout_combo.count() == 1 and dlg.source() == "numbers"
        dlg.reject()
        assert not error_dialogs
    finally:
        mode.set_advanced(False)
        close(win)
