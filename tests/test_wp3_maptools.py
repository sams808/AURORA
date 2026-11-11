"""I18: map tools. A click selects (the smallest zone wins), Shift+click or a click outside every zone adds a pixel,
stray drags are ignored, right-click menus, lasso and polygon zones (pixel centres inside, John Bussey's rule),
John's _polygon_roi.json in and out, polygons rasterised for the HTML zone tool, the Reduce shortcuts."""
import json

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402

from aurora.app import mode  # noqa: E402
from aurora.core import testdata  # noqa: E402
from aurora.core.io.config import Zone, ZonesFile  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from aurora.core.zones import export_ready, members  # noqa: E402
from local_paths import john_gui  # noqa: E402
from wp3_helpers import FakeClick, click, close, drag, open_window, pump  # noqa: E402

JOHN = john_gui()                   # John Bussey's GUI folder (tests/local_paths.py), None when absent
TRIANGLE = [[-0.6, 4.9], [0.35, 4.9], [-0.6, 5.6]]
TRIANGLE_MEMBERS = [0, 1, 2, 6, 7, 8]       # by hand: snake grid 4 x 3, pitch 0.25 mm from (-0.5, 5.0)


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def win(qapp, fake_project):
    w, sid = open_window(qapp, Project.discover(fake_project))
    w.sid = sid
    yield w
    close(w)


def _two_zones(win):
    ses = win.session
    sd = ses.stack_loaded(win.sid)
    x0, x1, y0, y1 = sd.bbox
    xm = (x0 + x1) / 2
    with ses.edit("two zones"):
        z1 = ses.new_zone("left"); z1.rects.append([x0, xm - 0.05, y0, y1])
        z2 = ses.new_zone("right"); z2.rects.append([xm + 0.05, x1, y0, y1])
    ses.select_zone(z1.id)
    return sd, z1, z2


def test_a_click_selects_and_never_adds(qapp, win):
    rd, ses = win.reduce, win.session
    sd, z1, z2 = _two_zones(win)
    pump(qapp)
    rd.set_tool("rect+")
    before = [list(r) for r in z1.rects]
    cx = float(sd.x[members(z2, sd.x, sd.y, sd.pitch)].mean())
    cy = float(sd.y[members(z2, sd.x, sd.y, sd.pitch)].mean())
    click(rd.map, cx, cy)
    pump(qapp)
    assert ses.current_zone == z2.id and z1.rects == before and len(z2.rects) == 1
    # a plain click inside the selected zone adds nothing either
    click(rd.map, cx, cy)
    assert len(z2.rects) == 1 and ses.current_zone == z2.id
    # Shift+click adds exactly one rectangle (a pixel) to the selected zone
    click(rd.map, cx, cy, modifiers=Qt.ShiftModifier)
    pump(qapp)
    assert len(z2.rects) == 2 and z2.rects[-1][0] == z2.rects[-1][1] and len(z1.rects) == 1
    # a click outside the scan writes nothing (no measured point there) and says why
    ses.select_zone(z1.id)
    x0, x1, y0, y1 = sd.bbox
    n = ses.undo_stack.count()
    click(rd.map, x1 + 0.6, y1 + 0.6)
    assert len(z1.rects) == 1 and ses.undo_stack.count() == n and "outside scan" in rd.status_label.text()
    # a click on a pixel outside every zone, next to the selected zone, adds that pixel to it
    with ses.edit("zone 2 keeps its last column"):
        z2.rects[0][0] = x1 - 0.01
    taken = set(members(z1, sd.x, sd.y, sd.pitch).tolist()) | set(members(z2, sd.x, sd.y, sd.pitch).tolist())
    free = next(i for i in range(sd.n_pix) if i not in taken)
    click(rd.map, float(sd.x[free]), float(sd.y[free]))
    assert len(z1.rects) == 2 and free in members(z1, sd.x, sd.y, sd.pitch)
    # with rect-, a plain click inside the selected zone erases that pixel
    rd.set_tool("rect-")
    click(rd.map, float(sd.x[0]), float(sd.y[0]))
    assert len(z1.erects) == 1


def test_smallest_zone_wins_and_navigate_selects(qapp, win):
    rd, ses = win.reduce, win.session
    sd = ses.stack_loaded(win.sid)
    x0, x1, y0, y1 = sd.bbox
    with ses.edit("nested zones"):
        big = ses.new_zone("big"); big.rects.append([x0, x1, y0, y1])
        small = ses.new_zone("small"); small.rects.append([float(sd.x[5]), float(sd.x[5]), float(sd.y[5]), float(sd.y[5])])
    ses.select_zone(big.id)
    pump(qapp)
    assert rd.map.zone_at(float(sd.x[5]), float(sd.y[5])) == small.id
    assert rd.map.zone_at(float(sd.x[0]), float(sd.y[0])) == big.id
    rd.set_tool("navigate")
    click(rd.map, float(sd.x[5]), float(sd.y[5]))
    assert ses.current_zone == small.id and len(small.rects) == 1


def test_stray_drags_are_ignored(qapp, win):
    rd, ses = win.reduce, win.session
    sd = ses.stack_loaded(win.sid)
    px, py = sd.pitch
    x, y = float(sd.x[0]), float(sd.y[0])
    rd.set_tool("rect+")
    drag(rd.map, [(x, y), (x + 0.05 * px, y), (x + 0.1 * px, y + 0.1 * py)])
    hz = ses.holder_zones(create=False)
    assert not (hz and hz.zones)                          # a drag of 0.1 pitch adds nothing
    rd.set_tool("brush+")
    drag(rd.map, [(x, y), (x + 0.1 * px, y)])
    hz = ses.holder_zones(create=False)
    assert not (hz and hz.zones)
    rd.set_tool("lasso+")
    drag(rd.map, [(x, y), (x + 0.1 * px, y), (x, y + 0.1 * py)])
    hz = ses.holder_zones(create=False)
    assert not (hz and hz.zones)
    # a real drag still draws
    rd.set_tool("rect+")
    drag(rd.map, [(x, y), (x + 1.2 * px, y + 1.2 * py)])
    assert len(ses.zone().rects) == 1


def test_context_menus(qapp, win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMenu
    rd, ses = win.reduce, win.session
    sd, z1, z2 = _two_zones(win)
    pump(qapp)
    texts = [a.text() for a in rd.zone_menu(z1.id).actions() if a.text()]
    assert texts[:1] == ["Rename…"] and {"Duplicate", "Delete", "Copy geometry", "Zoom to zone"} <= set(texts)
    assert "Set group" not in texts and "Send to Compare" not in texts           # Easy never writes a hidden field
    mode.set_advanced(True)
    texts = [a.text() for a in rd.zone_menu(z1.id).actions() if a.text()]
    assert {"Set group", "Send to Compare"} <= set(texts)
    mode.set_advanced(False)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("13 alpha_beta", True)))
    next(a for a in rd.zone_menu(z1.id).actions() if a.text() == "Rename…").trigger()
    assert z1.label == "13 alpha_beta"
    # the right-click on the map pops the menu of the zone under the pointer (non-blocking)
    seen = []
    i = int(members(z2, sd.x, sd.y, sd.pitch)[0])
    for x, y in ((float(sd.x[i]), float(sd.y[i])), (sd.bbox[1] + 1.0, sd.bbox[3] + 1.0)):
        rd.map.vb.mouseClickEvent(FakeClick(rd.map.vb, x, y, button=Qt.RightButton))
        pump(qapp)
        assert isinstance(rd.last_menu, QMenu) and rd.last_menu.isVisible()
        seen.append([a.text() for a in rd.last_menu.actions() if a.text()])
        rd.last_menu.close()
        pump(qapp)
    assert seen[0][0] == "Rename…" and seen[1] == ["New zone here", "Paste zone", "Fit view"]
    x0, x1, y0, y1 = sd.bbox
    # duplicate, copy geometry and paste it centred where the menu opened
    dup = rd.duplicate_zone(z1.id)
    assert dup.rects == z1.rects and set(dup.settings) == set(z1.settings) and ses.current_zone == dup.id
    rd.copy_geometry(z2.id)
    pasted = rd.paste_geometry(x1 + 1.0, y1 + 1.0)
    w = z2.rects[0][1] - z2.rects[0][0]
    assert abs((pasted.rects[0][0] + pasted.rects[0][1]) / 2 - (x1 + 1.0)) < 1e-9 and abs(pasted.rects[0][1] - pasted.rects[0][0] - w) < 1e-9
    n = len(ses.holder_zones().zones)
    rd.new_zone_at(float(sd.x[3]), float(sd.y[3]))
    assert len(ses.holder_zones().zones) == n + 1 and ses.zone().members[win.sid] == [3]


def test_triangle_zone_members(qapp, fake_project):
    proj = Project.discover(fake_project)
    from aurora.core.stack import StackData
    sd = StackData.load(proj, proj.find(testdata.STACK))
    z = Zone(id=1, label="tri", polys=[TRIANGLE])
    assert members(z, sd.x, sd.y, sd.pitch).tolist() == TRIANGLE_MEMBERS
    # polygon united with a rectangle and paint, minus erase squares
    z.paint.append([float(sd.x[11]), float(sd.y[11]), sd.pitch[0], sd.pitch[1]])
    z.erase.append([float(sd.x[0]), float(sd.y[0]), sd.pitch[0], sd.pitch[1]])
    assert members(z, sd.x, sd.y, sd.pitch).tolist() == [1, 2, 6, 7, 8, 11]


def test_lasso_and_polygon_draw_polygon_zones(qapp, win):
    rd, ses = win.reduce, win.session
    assert ses.stack_loaded(win.sid) is not None
    rd.set_tool("lasso+")
    pts = [(-0.6, 4.9)] + [(-0.6 + t * 0.95, 4.9) for t in np.linspace(0, 1, 12)[1:]] + \
          [(0.35 - t * 0.95, 4.9 + t * 0.7) for t in np.linspace(0, 1, 12)[1:]]
    drag(rd.map, pts)
    pump(qapp)
    z = ses.zone()
    assert z is not None and len(z.polys) == 1 and len(z.polys[0]) >= 3 and not z.rects
    assert ses.zone().members[win.sid] == TRIANGLE_MEMBERS
    assert rd.region_caption.text().startswith(f"Region: polygon with {len(z.polys[0])} vertices · 6 of 12 pixels")
    # Esc cancels a lasso in progress
    ses.select_zone(None)
    vb = rd.map.vb
    from wp3_helpers import FakeDrag
    vb.mouseDragEvent(FakeDrag(vb, (-0.6, 4.9), (-0.6, 4.9), start=True))
    vb.mouseDragEvent(FakeDrag(vb, (-0.6, 4.9), (0.3, 4.9)))
    vb.mouseDragEvent(FakeDrag(vb, (-0.6, 4.9), (0.3, 5.5)))
    assert vb.drawing() == "lasso"
    from PySide6.QtTest import QTest
    rd.map.setFocus()
    QTest.keyClick(rd.map, Qt.Key_Escape)
    vb.mouseDragEvent(FakeDrag(vb, (-0.6, 4.9), (-0.6, 5.5), finish=True))
    assert len(ses.holder_zones().zones) == 1 and vb.drawing() == ""
    # polygon: click the vertices, double-click closes. The first corner lies on a pixel of zone 1: a plain click
    # there selects zone 1, a Shift+click starts the polygon
    mode.set_advanced(True)
    rd.set_tool("polygon+")
    click(rd.map, *TRIANGLE[0])
    assert ses.current_zone == 1 and rd.map.vb.drawing() == ""
    ses.select_zone(None)
    click(rd.map, *TRIANGLE[0], modifiers=Qt.ShiftModifier)
    assert rd.map.vb.drawing() == "polygon"
    click(rd.map, *TRIANGLE[1])
    click(rd.map, *TRIANGLE[2])
    click(rd.map, *TRIANGLE[2], double=True)
    pump(qapp)
    z2 = ses.zone()
    assert len(ses.holder_zones().zones) == 2 and z2.polys == [TRIANGLE]
    assert z2.members[win.sid] == TRIANGLE_MEMBERS
    mode.set_advanced(False)


def test_polys_round_trip_through_the_session_file(qapp, win, fake_project):
    ses = win.session
    with ses.edit("a polygon"):
        z = ses.new_zone("tri")
        z.polys.append([list(p) for p in TRIANGLE])
    ses.flush()
    ses.save()
    d = json.loads(ses.session_file.read_text(encoding="utf-8"))
    assert d["holders"]["holderA"]["zones"][0]["polys"] == [TRIANGLE]
    ses.set_project(Project.discover(fake_project))
    back = ses.holder_zones(create=False).zones[0]
    assert back.polys == [TRIANGLE]


def test_export_rasterises_polygons_for_the_zone_tool(qapp, fake_project, tmp_path):
    from aurora.core.stack import StackData
    proj = Project.discover(fake_project)
    sd = StackData.load(proj, proj.find(testdata.STACK))
    z = Zone(id=1, label="tri", polys=[TRIANGLE], erase=[[float(sd.x[1]), float(sd.y[1]), sd.pitch[0], sd.pitch[1]]])
    want = members(z, sd.x, sd.y, sd.pitch).tolist()
    assert want == [0, 2, 6, 7, 8]
    zf = ZonesFile(project="T")
    from aurora.core.io.config import HolderZones
    zf.holders["holderA"] = HolderZones(holder="holderA", day=testdata.DAY, survey=testdata.SURVEY, zones=[z])
    from aurora.core.zones import export_json
    out = export_json(zf, {sd.id: sd})
    assert "ui" not in out and "rules" not in out and out["version"] == 1
    p = tmp_path / "zones_all.json"
    p.write_text(json.dumps(out), encoding="utf-8")
    back = ZonesFile.load(p).holders["holderA"].zones[0]
    assert back.polys == [TRIANGLE]                       # kept for AURORA
    tool = Zone(id=1, label="tri", rects=back.rects, erects=back.erects, paint=back.paint, erase=back.erase)
    assert members(tool, sd.x, sd.y, sd.pitch).tolist() == want           # the HTML tool's rule: rects and paint
    assert len(back.paint) == 5
    # two pitches on one holder: a coarse stack over the same plate still reads the same members in both
    xc, yc = testdata.snake_grid(nx=3, ny=2, pitch=0.5, x0=-0.55, y0=4.95)
    coarse = (xc, yc, (0.5, 0.5))
    exp = export_ready(z, {sd.id: sd, "coarse": coarse})
    tool2 = Zone(id=1, label="t", paint=exp["paint"], erase=exp["erase"])
    assert members(tool2, sd.x, sd.y, sd.pitch).tolist() == want
    assert members(tool2, xc, yc, (0.5, 0.5)).tolist() == members(z, xc, yc, (0.5, 0.5)).tolist()


def test_john_polygon_files(qapp, win, tmp_path):
    rd, ses = win.reduce, win.session
    poly_file = tmp_path / "BO3_test_polygon_roi.json"
    poly_file.write_text(json.dumps(TRIANGLE), encoding="utf-8")
    z = rd.load_polygon(poly_file)
    assert z is not None and z.label == "BO3_test" and z.polys == [TRIANGLE] and ses.current_zone == z.id
    assert z.members[win.sid] == TRIANGLE_MEMBERS
    assert rd.save_poly_btn.isEnabled()
    out = rd.save_polygon(tmp_path / "out_polygon_roi.json")
    assert json.loads(out.read_text(encoding="utf-8")) == TRIANGLE
    bad = tmp_path / "bad_polygon_roi.json"
    bad.write_text("[[0, 0], [1, 1]]", encoding="utf-8")
    n = len(ses.holder_zones().zones)
    assert rd.load_polygon(bad) is None and len(ses.holder_zones().zones) == n
    assert "not a polygon ROI" in rd.status_label.text()
    with ses.edit("a rect"):
        z.rects.append([0, 0, 5, 5])
    pump(qapp)
    assert not rd.save_poly_btn.isEnabled()              # John's file holds one polygon only


@pytest.mark.skipif(JOHN is None, reason="John Bussey's GUI folder (legacy/CLS_GUI_JohnBussey) is not available")
def test_john_bo3_polygon_members():
    from aurora.core.io.h5 import find_h5, xy
    from aurora.core.zones import grid_pitch
    stack = JOHN / "070810_BO3_LiSO3F_LR_fluorine_k_stack"
    x, y = xy(find_h5(stack))
    poly = json.loads((JOHN / "gui_outputs" / "reduced" / "BO3_LiSO3F_LR_fluorine_k" /
                       "BO3_LiSO3F_LR_fluorine_k_polygon_roi.json").read_text(encoding="utf-8"))
    assert members(Zone(id=1, label="BO3", polys=[poly]), x, y, grid_pitch(x, y)).tolist() == [37, 38, 41, 42, 57, 58, 61, 62]


def test_reduce_shortcuts(qapp, win):
    from PySide6.QtTest import QTest
    from aurora.app import shortcuts
    rd = win.reduce
    reg = {a.text(): a for a in shortcuts.registered(win) if shortcuts.section_of(a) == "Reduce"}
    keys = {t: a.shortcut().toString() for t, a in reg.items()}
    assert keys["Move (pan and zoom)"] == "V" and keys["Rectangle"] == "R" and keys["Brush"] == "B"
    assert keys["Brush in Erase mode"] == "Shift+B" and keys["Lasso"] == "L" and keys["Polygon"] == "P"
    assert keys["Draw mode"] == "D" and keys["Erase mode"] == "E" and keys["Switch between Draw and Erase"] == "X"
    assert keys["New sample"] == "N" and keys["Delete the sample"] == "Del"
    assert keys["Previous sample"] == "Alt+Up" and keys["Next scan"] == "]" and keys["Fit the view"] == "F"
    win.activateWindow()
    rd.map.setFocus()

    def key(k, mods=Qt.NoModifier):
        QTest.keyClick(win, k, mods)
        pump(qapp)
        return rd.map.vb.tool
    assert key(Qt.Key_L) == "lasso+" and rd.toolbar.shape_button("lasso").isChecked()
    assert key(Qt.Key_B) == "brush+" and rd.brush_n.isVisible()          # the brush is in Easy too
    assert key(Qt.Key_E) == "brush-" and rd.toolbar.erase_btn.isChecked()  # E: Erase mode, the shape kept
    assert key(Qt.Key_R) == "rect-"                       # R keeps the mode: E then R is the former rect-
    assert key(Qt.Key_X) == "rect+" and key(Qt.Key_X) == "rect-"
    assert key(Qt.Key_D) == "rect+"
    assert key(Qt.Key_B, Qt.ShiftModifier) == "brush-"
    n = rd.brush_n.value()
    reg["Larger brush"].trigger()
    assert rd.brush_n.value() == n + 1
    reg["Smaller brush"].trigger()
    assert rd.brush_n.value() == n
    assert key(Qt.Key_P) == "brush-"                      # Easy: no polygon
    mode.set_advanced(True)
    pump(qapp)
    assert key(Qt.Key_P) == "polygon-" and key(Qt.Key_D) == "polygon+"
    assert key(Qt.Key_V) == "navigate"
    QTest.keyClick(win, Qt.Key_N)
    pump(qapp)
    assert win.session.zone() is not None
    mode.set_advanced(False)


def test_survey_export_rasterises_polygon_zones(qapp, win, tmp_path):
    """Session.export_zones (Survey > Export zones...) writes polygon zones as painted squares, so the HTML zone tool,
    which knows rectangles and painted squares only, finds AURORA's members; 'polys' is kept."""
    ses = win.session
    with ses.edit("a polygon zone"):
        z = ses.new_zone("tri")
        z.polys.append([list(p) for p in TRIANGLE])
    pump(qapp)
    assert z.members[win.sid] == TRIANGLE_MEMBERS
    out = tmp_path / "zones_all.json"
    ses.export_zones(out)
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["version"] == 1 and "rules" not in d and "ui" not in d
    back = ZonesFile.load(out).holders["holderA"].zones[-1]
    assert back.polys == [TRIANGLE] and len(back.paint) == len(TRIANGLE_MEMBERS)
    sd = ses.stack_loaded(win.sid)
    tool = Zone(id=1, label="t", rects=back.rects, erects=back.erects, paint=back.paint, erase=back.erase)
    assert members(tool, sd.x, sd.y, sd.pitch).tolist() == TRIANGLE_MEMBERS
