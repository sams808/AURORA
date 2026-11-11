"""I12: a grid of zones from plate_layout.json on the Survey map (Advanced). Core: zones_from_grid in reading order,
rotation, labels and groups, the layout group of a scan, snap to pads. GUI: the 'Grid of zones…' tool, the dialog
pre-filled from the layout, the dashed preview, Accept in one session.edit."""
import json

import pytest

from aurora.core import testdata
from aurora.core.model import Project
from aurora.core.propose import (Pad, grid_cells, layout_group, layout_groups, snap_to_pads, zones_from_cells,
                                 zones_from_grid)

L = ["a", "b", "c", "d", "e", "f", "g", "h"]


def _centre(z):
    x0, x1, y0, y1 = z.rects[0]
    return (x0 + x1) / 2, (y0 + y1) / 2


# ----------------------------------------------------------------------------- core
def test_zones_from_grid_in_reading_order():
    zs = zones_from_grid(bbox=(0, 4, 0, 2), rows=2, cols=4, fill=0.7, labels=L)
    assert len(zs) == 8 and [z.label for z in zs] == L and [z.id for z in zs] == list(range(1, 9))
    centres = [(0.5 + c, 1.5 - r) for r in range(2) for c in range(4)]            # top row first, left to right
    for z, (cx, cy) in zip(zs, centres):
        x, y = _centre(z)
        assert abs(x - cx) < 1e-6 and abs(y - cy) < 1e-6, (z.label, x, y, cx, cy)
        x0, x1, y0, y1 = z.rects[0]
        assert abs((x1 - x0) - 0.7) < 1e-9 and abs((y1 - y0) - 0.7) < 1e-9          # 70 % of a 1 x 1 mm cell
        assert len(z.rects) == 1 and z.comment.startswith("grid 2 x 4, row ")
    # a reversed bbox is the same grid; no labels give 'zone N' from start_id
    zs2 = zones_from_grid(bbox=(4, 0, 2, 0), rows=2, cols=4, start_id=5)
    assert [_centre(z) for z in zs2] == [_centre(z) for z in zs] and zs2[0].label == "zone 5" and zs2[-1].id == 12


def test_rotation_reverses_or_turns_the_labels():
    zs = zones_from_grid(bbox=(0, 4, 0, 2), rows=2, cols=4, fill=0.7, rotation=180, labels=L)
    assert [z.label for z in zs] == L[::-1]
    assert [_centre(z) for z in zs] == [_centre(z) for z in zones_from_grid((0, 4, 0, 2), 2, 4, labels=L)]
    # 90 and 270: the layout is cols x rows (here 4 rows of 2), turned counter-clockwise onto the 2 x 4 map grid
    sketch = [["a", "b"], ["c", "d"], ["e", "f"], ["g", "h"]]
    assert [z.label for z in zones_from_grid((0, 4, 0, 2), 2, 4, rotation=90, labels=sketch)] == list("bdfhaceg")
    assert [z.label for z in zones_from_grid((0, 4, 0, 2), 2, 4, rotation=270, labels=sketch)] == list("gecahfdb")
    assert [z.label for z in zones_from_grid((0, 4, 0, 2), 2, 4, rotation=360, labels=L)] == L
    for bad in ({"rotation": 45}, {"rows": 0}, {"fill": 0.0}, {"fill": 1.5}):
        args = dict(bbox=(0, 4, 0, 2), rows=2, cols=4) | bad
        with pytest.raises(ValueError):
            zones_from_grid(**args)


def test_groups_and_empty_layout_positions():
    cells = grid_cells((0, 2, 0, 2), 2, 2, labels=[["x", None], ["y", "z"]], groups="Group 1")
    assert [(c.label, c.empty, c.group) for c in cells] == [("x", False, "Group 1"), (None, True, "Group 1"),
                                                          ("y", False, "Group 1"), ("z", False, "Group 1")]
    zs = zones_from_grid((0, 2, 0, 2), 2, 2, labels=["p", "q"], groups=["G1", "G2", "G3", "G4"])
    assert [(z.label, z.group) for z in zs] == [("p", "G1"), ("q", "G2"), ("zone 3", "G3"), ("zone 4", "G4")]
    assert not any(c.empty for c in grid_cells((0, 2, 0, 2), 2, 2, labels=["p", "q"]))       # short list: no label
    kept = [c for c in cells if not c.empty]
    assert [z.label for z in zones_from_cells(kept, start_id=7)] == ["x", "y", "z"]


LAYOUT = {"holderA": {"samples": {"1": "alpha_beta", "2": "sample2", "3": "sample3", "4": "sample4", "5": "sample5", "6": "sample6"},
                 "groups": [{"id": "Group A 1", "rows": 2, "cols": 3, "cells": [[1, 2, 3], [4, None, 6]],
                             "match": ["alphabeta"]},
                            {"id": "Group B beakers", "rows": 2, "cols": 2, "cells": [[5, 6], [None, None]],
                             "match": ["gamma", "gam"]},
                            {"id": "names only", "match": ["zzz"], "match_names": False, "names": ["A", "B"]}]},
          "_help": "ignored"}


def test_layout_group_of_a_scan():
    gs = layout_groups(LAYOUT, "holderA")
    assert [g["id"] for g in gs] == ["Group A 1", "Group B beakers", "names only"]
    assert gs[0]["labels"] == [["alpha_beta", "sample2", "sample3"], ["sample4", None, "sample6"]] and gs[2]["labels"] == ["A", "B"]
    assert layout_group(LAYOUT, "holderA", "alpha_beta")["id"] == "Group A 1"
    assert layout_group(LAYOUT, "holderA", "gamma")["id"] == "Group B beakers"            # the longest word wins
    assert layout_group(LAYOUT, "holderA", "sample6_test")["id"] in ("Group A 1", "Group B beakers")    # a sample name matches
    assert layout_group(LAYOUT, "holderA", "B_sample") is None                                # match_names false, < 3 letters
    assert layout_group(LAYOUT, "holderE", "alpha_beta") is None and layout_groups(LAYOUT, "holderE") == []


def test_snap_to_pads_skips_a_cell_without_a_pad_and_lists_it():
    cells = grid_cells((0, 2, 0, 2), 2, 2, labels=["tl", "tr", "bl", "br"])
    pads = [Pad(1, 0.2, 0.6, 1.3, 1.7, 9, 1.0),          # top left, small
            Pad(2, 1.1, 1.9, 1.1, 1.9, 25, 1.0),         # top right, almost the whole cell
            Pad(3, 1.4, 1.6, 0.4, 0.6, 9, 1.0)]          # bottom right; bottom left has none
    kept, skipped = snap_to_pads(cells, pads, pitch=(0.1, 0.1))
    assert [c.label for c in kept] == ["tl", "tr", "br"] and [c.label for c in skipped] == ["bl"]
    assert skipped[0].name == "row 2, col 1"
    tl = kept[0].rect
    assert tl == pytest.approx((0.15, 0.65, 1.25, 1.75))                 # the pad's footprint, half a pitch beyond
    tr = kept[1].rect
    assert tr == pytest.approx((1.05, 1.95, 1.05, 1.95))
    # a pad that spans two cells is cut on the cell border
    wide = [Pad(1, 0.5, 1.5, 1.4, 1.6, 30, 1.0)]
    kept, skipped = snap_to_pads(cells[:2], wide, pitch=(0.0, 0.0))
    assert [c.rect for c in kept] == [pytest.approx((0.5, 1.0, 1.4, 1.6)), pytest.approx((1.0, 1.5, 1.4, 1.6))]


# ----------------------------------------------------------------------------- GUI
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


@pytest.fixture
def window(qapp, tmp_path):
    """The demo beamtime (sample sheet for holderA) with a plate_layout.json group matching its alpha_beta stack."""
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.cli import cmd_demo
    root = tmp_path / "demo_beamtime"
    cmd_demo(root)
    (root / "config" / "plate_layout.json").write_text(json.dumps(LAYOUT), encoding="utf-8")
    mode.set_advanced(True)
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    win.home.set_project(Project.discover(root))
    win.show_perspective("survey")
    pump(qapp)
    yield win
    mode.set_advanced(False)
    win.session._save_timer.stop()
    win.close()


def test_grid_tool_is_advanced_and_arms_rect_plus(qapp, window):
    from aurora.app import mode
    sv = window.survey
    assert sv.grid_btn.isVisible() and sv.grid_btn.isEnabled() and sv.grid_btn.toolTip()
    sv.grid_btn.click()
    assert sv.grid_btn.isChecked() and sv.map.vb.tool == "rect+" and "drag a rectangle" in sv.hint.text()
    sv._rect_drawn(0.1, 0.1, 0.2, 0.2)                         # a click: nothing opens
    assert sv.grid_dialog is None and sv.grid_btn.isChecked() and "a click selects nothing" in sv.hint.text()
    sv.map.rect_drawn.emit(-0.8, 0.2, -0.4, 0.3)               # the rect+ tool's signal
    pump(qapp)
    assert sv.grid_dialog is not None and not sv.grid_btn.isChecked() and sv.map.vb.tool == "navigate"
    sv.grid_dialog.reject()
    pump(qapp)
    assert sv.grid_dialog is None and sv._grid_preview is None
    mode.set_advanced(False)
    pump(qapp)
    assert sv.grid_btn.isHidden()
    mode.set_advanced(True)


def test_layout_prefills_and_accept_is_one_edit(qapp, window, monkeypatch):
    sv, ses = window.survey, window.session
    dlg = sv.open_grid_dialog((-0.9, 0.3, -0.5, 0.3))
    pump(qapp)
    assert dlg.isVisible() and dlg.layout_combo.currentText().startswith("Group A 1")
    assert (dlg.rows.value(), dlg.cols.value()) == (2, 3) and dlg.source() == "layout"
    assert "matches the holder's scans" in dlg.summary.text()
    zones, skipped = sv._grid_preview
    assert [z.label for z in zones] == ["alpha_beta", "sample2", "sample3", "sample4", "sample6"]
    assert [c.name for c, why in skipped] == ["row 2, col 2"] and skipped[0][1] == "empty on the layout"
    assert "skipped: row 2, col 2 (empty on the layout)" in dlg.summary.text()
    # the preview is drawn: dashed rectangles in the rect+ colour, the empty cell dotted
    assert len(sv.map._overlays) >= 2 * len(zones) + len(skipped)
    # rotation 180 reverses the assignment
    dlg.rotation.setCurrentIndex(dlg.rotation.findData(180))
    assert [z.label for z in sv._grid_preview[0]] == ["sample6", "sample4", "sample3", "sample2", "alpha_beta"]
    dlg.rotation.setCurrentIndex(0)
    # 8 zones without the layout: 2 x 4, zone numbers
    dlg.layout_combo.setCurrentIndex(0)
    dlg.rows.setValue(2)
    dlg.cols.setValue(4)
    dlg.labels.setCurrentIndex(dlg.labels.findData("numbers"))
    assert len(sv._grid_preview[0]) == 8
    n_before = len(ses.holder_zones().zones) if ses.holder_zones(create=False) else 0
    first = dlg.start_id()
    calls = []
    real_edit = ses.edit

    def counting(description, holder=None):
        calls.append(description)
        return real_edit(description, holder)

    monkeypatch.setattr(ses, "edit", counting)
    dlg.accept()
    pump(qapp)
    assert calls == ["grid of 8 zones"]
    hz = ses.holder_zones(create=False)
    assert len(hz.zones) == n_before + 8 and [z.label for z in hz.zones[-8:]] == [f"zone {first + i}" for i in range(8)]
    assert [z.id for z in hz.zones[-8:]] == list(range(first, first + 8))
    assert sv.zone_table.rowCount() == len(hz.zones) and sv._grid_preview is None and sv.grid_dialog is None
    assert "added 8 zones" in sv.hint.text()
    sv.redraw_map()                                            # any later redraw keeps the note in the hint line
    assert "added 8 zones to holder holderA" in sv.hint.text() and "points, pitch" in sv.hint.text()
    # the next grid continues the numbering
    dlg = sv.open_grid_dialog((-0.9, 0.3, -0.5, 0.3))
    assert dlg.start_id() == max(z.id for z in hz.zones) + 1
    dlg.reject()


def test_sheet_labels_and_snap_to_pads_in_the_dialog(qapp, window):
    sv = window.survey
    dlg = sv.open_grid_dialog((-0.9, 0.3, -0.5, 0.3))
    dlg.layout_combo.setCurrentIndex(0)                        # no layout group: the sheet rows of holderA
    assert dlg.source() == "sheet" and dlg.first_row.isEnabled()
    dlg.rows.setValue(1)
    dlg.cols.setValue(2)
    zones, _skipped = sv._grid_preview
    assert [(z.label, z.group) for z in zones] == [("1 alpha", "Group 1"), ("2 beta", "Group 2")]
    dlg.first_row.setValue(2)
    assert [z.label for z in sv._grid_preview[0]] == ["2 beta", f"zone {dlg.start_id() + 1}"]
    # snap: the left cell has a pad, the right one none
    x0, x1, y0, y1 = dlg.bbox
    mid = (x0 + x1) / 2
    dlg._pads = [Pad(1, x0 + 0.1, mid - 0.1, y0 + 0.1, y1 - 0.1, 9, 1.0)]
    dlg.snap.setChecked(True)
    zones, skipped = sv._grid_preview
    assert [z.label for z in zones] == ["2 beta"] and [(c.name, why) for c, why in skipped] == [("row 1, col 2", "no pad")]
    assert "row 1, col 2 (no pad)" in dlg.summary.text() and "row 1, col 2 (no pad)" in dlg.skipped_text()
    dlg.accept()
    pump(qapp)
    assert "skipped row 1, col 2 (no pad)" in sv.hint.text()


def test_dialog_controls_are_explained(qapp, window):
    from test_a11y import check_root
    dlg = window.survey.open_grid_dialog((-0.9, 0.3, -0.5, 0.3))
    pump(qapp)
    assert check_root("grid of zones", dlg) == []
    dlg.reject()


def test_a_holder_change_closes_the_grid(qapp, tmp_path):
    from aurora.app import mode
    from aurora.app.main import MainWindow
    mode.set_advanced(True)
    win = MainWindow()
    win.show()
    win.home.set_project(Project.discover(testdata.make_fake_project(tmp_path / "bt", n_holders=2)))
    win.show_perspective("survey")
    pump(qapp)
    dlg = win.survey.open_grid_dialog((-0.9, 0.3, -0.5, 0.3))
    assert win.survey._grid_preview is not None and dlg.groups == []          # no plate_layout.json: none offered
    assert dlg.source() == "numbers" and (dlg.rows.value(), dlg.cols.value()) == (2, 2)
    win.session.set_holder("holderB")
    pump(qapp)
    assert win.survey.grid_dialog is None and win.survey._grid_preview is None and not dlg.isVisible()
    mode.set_advanced(False)
    win.session._save_timer.stop()
    win.close()
