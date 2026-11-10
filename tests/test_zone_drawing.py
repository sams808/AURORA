"""Drawing and erasing zones on the stack map with real mouse strokes (QTest press / move / release on the map's
viewport): one stroke is one edit and one undo step, the right button and Shift erase, Esc cancels any stroke, empty
strokes write nothing, erase outlines (epolys), the zone a stroke goes to, the shared trace cache and the views that
wait until they are shown (stage A: spec items Z1, Z2, Z3, Z5, Z9, Z12, Z13). Stage B (Z4, Z6, Z7, Z8, Z10, Z11, Z14,
Z15, Z17): the shared tool bar in Easy and Advanced, drawing on the Survey map, the Survey's Zone preview (XRF emission
of the zone's survey points, XAS on a covered stack), the throttled live preview, the preview height at 1200 x 760,
the map hints and stroke highlights, 'survey, inside the zone' in step 2, the keys of both perspectives."""
import json

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from aurora.app import mode  # noqa: E402
from aurora.app.zone_tools import idle_hint  # noqa: E402
from aurora.core import testdata  # noqa: E402
from aurora.core.model import Project  # noqa: E402
from aurora.core.zones import members  # noqa: E402
from wp3_helpers import close, open_window, pump, qdrag, qmove, qrelease, view_to_viewport  # noqa: E402
from zone_helpers import WIDE, inner_box, row_points, zone_project  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def project(tmp_path):
    return Project.discover(zone_project(tmp_path / "bt"))


@pytest.fixture
def win(qapp, project):
    mode.set_advanced(False)
    w, sid = open_window(qapp, project)
    w.sid = sid
    yield w
    mode.set_advanced(False)
    close(w)


@pytest.fixture
def wide(qapp, project):
    """The window on the 12 x 4 stack (room for strokes apart, fast brushes)."""
    mode.set_advanced(False)
    w, sid = open_window(qapp, project, stack_name=WIDE)
    w.sid = sid
    yield w
    mode.set_advanced(False)
    close(w)


def _zones(ses):
    hz = ses.holder_zones(create=False)
    return list(hz.zones) if hz else []


def _box_path(x0, x1, y0, y1, n=4):
    """A drag from (x0, y0) to (x1, y1) in n moves."""
    return [(x0 + f * (x1 - x0), y0 + f * (y1 - y0)) for f in np.linspace(0, 1, n + 1)]


def _around(sd, idx, pad=0.4):
    """A rectangle drag round the pixels idx (pad pitches beyond their centres)."""
    px, py = sd.pitch
    xs, ys = sd.x[idx], sd.y[idx]
    return _box_path(float(xs.min()) - pad * px, float(xs.max()) + pad * px, float(ys.min()) - pad * py,
                     float(ys.max()) + pad * py)


# ----------------------------------------------------------------------------- the tool strings
def test_tool_names_round_trip():
    from aurora.app.mapview import ADD_TOOLS, TOOLS, split_tool, tool_name
    assert {"lasso-", "polygon-"} <= set(TOOLS) and TOOLS[:7] == ("navigate", "rect+", "rect-", "brush+", "brush-",
                                                                   "lasso+", "polygon+")
    assert set(ADD_TOOLS) == {"rect+", "brush+", "lasso+", "polygon+"}
    for t in TOOLS:
        assert tool_name(*split_tool(t)) == t
    assert split_tool("lasso-") == ("lasso", True) and split_tool("navigate") == ("navigate", False)
    assert tool_name("polygon", True) == "polygon-" and tool_name("navigate", True) == "navigate"
    with pytest.raises(ValueError):
        split_tool("rect")


# ----------------------------------------------------------------------------- one stroke, one step
def test_reduce_left_drag_draws_one_step(qapp, win):
    rd, ses = win.reduce, win.session
    sd = ses.stack_loaded(win.sid)
    assert not mode.is_advanced() and ses.zone() is None and not _zones(ses)
    rd.set_tool("rect+")
    n = ses.undo_stack.count()
    qdrag(qapp, rd.map, _box_path(*inner_box(sd, 0.05)))
    zs = _zones(ses)
    assert len(zs) == 1 and len(zs[0].rects) == 1 and ses.current_zone == zs[0].id
    assert len(members(zs[0], sd.x, sd.y, sd.pitch)) > 0 and ses.undo_stack.count() == n + 1
    assert ses.undo_stack.text(n) == "add a rectangle to a new zone"
    assert rd.preview._traces and rd.map.hint_text() == idle_hint("rect+")       # back to what a drag does
    win.activateWindow()
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier)
    pump(qapp)
    from aurora.app.reduce import PREVIEW_NO_ZONE
    assert not _zones(ses) and ses.zone() is None and rd.preview._traces == []
    assert rd.preview._waiting == PREVIEW_NO_ZONE                               # 'Draw a zone on the map (drag) ...' 


def test_new_sample_then_the_first_stroke_is_one_step(qapp, win):
    rd, ses = win.reduce, win.session
    sd = ses.stack_loaded(win.sid)
    t = [0.0]
    ses.undo_clock = lambda: t[0]
    n = ses.undo_stack.count()
    rd.new_zone_btn.click(); pump(qapp)
    t[0] += 30.0                                           # however long the drawing takes
    rd.set_tool("lasso+")
    x0, x1, y0, y1 = inner_box(sd, 0.0)
    qdrag(qapp, rd.map, [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0 + 0.01)])
    z = ses.zone()
    assert len(_zones(ses)) == 1 and len(z.polys) == 1 and ses.undo_stack.count() == n + 1
    assert ses.undo_stack.text(n) == "add a polygon to a new zone"
    ses.undo()
    assert not _zones(ses)


def test_brush_stroke_is_one_edit(qapp, wide):
    rd, ses = wide.reduce, wide.session
    sd = ses.stack_loaded(wide.sid)
    rd.set_tool("brush+")
    row = row_points(sd, 1)
    xs, y = sd.x[row], float(sd.y[row[0]])
    changed = []
    ses.zones_changed.connect(lambda: changed.append(1))
    t = [100.0]
    ses.undo_clock = lambda: t[0]
    n = ses.undo_stack.count()
    path = [(float(xs[0]) + f * float(xs[5] - xs[0]), y) for f in np.linspace(0, 1, 21)]       # 20 moves
    qdrag(qapp, rd.map, path)
    z = ses.zone()
    assert len(changed) == 1 and ses.undo_stack.count() == n + 1
    assert sorted(members(z, sd.x, sd.y, sd.pitch).tolist()) == sorted(row[:6].tolist())
    first = json.loads(json.dumps(z.to_json()))         # to_json shares the zone's lists
    t[0] += 0.3                                            # a second stroke 0.3 s later: its own step
    qdrag(qapp, rd.map, [(float(xs[6]), y), (float(xs[7]), y), (float(xs[8]), y)])
    assert ses.undo_stack.count() == n + 2 and len(members(ses.zone(), sd.x, sd.y, sd.pitch)) == 9
    win = wide
    win.activateWindow()
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); pump(qapp)
    assert ses.zone(z.id).to_json() == first
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); pump(qapp)
    assert not _zones(ses)
    # a fast stroke: two mouse events 5 pitches apart paint every point in between
    row2 = row_points(sd, 2)
    qdrag(qapp, rd.map, [(float(sd.x[row2[2]]), float(sd.y[row2[2]])), (float(sd.x[row2[7]]), float(sd.y[row2[7]]))])
    got = set(members(ses.zone(), sd.x, sd.y, sd.pitch).tolist())
    assert set(row2[2:8].tolist()) <= got


# ----------------------------------------------------------------------------- erasing
def test_right_drag_and_shift_drag_erase(qapp, wide):
    from PySide6.QtWidgets import QMenu
    rd, ses = wide.reduce, wide.session
    sd = ses.stack_loaded(wide.sid)
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, _around(sd, np.arange(sd.n_pix)))
    z = ses.zone()
    assert len(members(z, sd.x, sd.y, sd.pitch)) == sd.n_pix
    row = row_points(sd, 0)
    n = ses.undo_stack.count()
    qdrag(qapp, rd.map, _around(sd, row[:2]), button=Qt.RightButton)            # right-drag: Erase
    assert len(z.erects) == 1 and len(members(z, sd.x, sd.y, sd.pitch)) == sd.n_pix - 2
    assert ses.undo_stack.count() == n + 1 and ses.undo_stack.text(n) == f"remove a rectangle from zone {z.id}"
    qdrag(qapp, rd.map, _around(sd, row[4:6]), modifiers=Qt.ShiftModifier)       # Shift at the start: Erase
    assert len(z.erects) == 2 and len(members(z, sd.x, sd.y, sd.pitch)) == sd.n_pix - 4
    # an erase over no point of the zone writes nothing, and says why
    n = ses.undo_stack.count()
    qdrag(qapp, rd.map, _around(sd, row[:2]), button=Qt.RightButton)
    assert ses.undo_stack.count() == n and len(z.erects) == 2
    assert rd.status_label.text() == f"nothing of zone {z.id} there to erase" == rd.map.hint_text()
    # with Erase as the tool's mode, the right button draws
    rd.set_tool("rect-")
    qdrag(qapp, rd.map, _around(sd, row[:2]), button=Qt.RightButton)
    assert len(members(z, sd.x, sd.y, sd.pitch)) == sd.n_pix - 2 and len(z.erects) == 1      # add after erase
    # a right click (no drag) still opens the zone's menu
    QTest.mouseClick(rd.map.viewport(), Qt.RightButton, Qt.NoModifier, view_to_viewport(rd.map, *(sd.x[20], sd.y[20])))
    pump(qapp)
    assert isinstance(rd.last_menu, QMenu) and rd.last_menu.isVisible()
    assert [a.text() for a in rd.last_menu.actions() if a.text()][0] == "Rename…"
    rd.last_menu.close(); pump(qapp)


def test_lasso_erase_stores_an_epoly(qapp, wide, project):
    from PySide6.QtWidgets import QGraphicsPolygonItem
    from aurora.app.theme import MAP_OVERLAY
    rd, ses = wide.reduce, wide.session
    sd = ses.stack_loaded(wide.sid)
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, _around(sd, np.arange(sd.n_pix)))
    z = ses.zone()
    rd.set_tool("lasso-")
    px, py = sd.pitch
    cx, cy = float(np.mean(sd.x)), float(np.mean(sd.y))
    ring = [(cx + 1.6 * px * np.cos(a), cy + 1.1 * py * np.sin(a)) for a in np.linspace(0, 2 * np.pi, 24)]
    n = ses.undo_stack.count()
    qdrag(qapp, rd.map, ring)
    assert len(z.epolys) == 1 and not z.polys and ses.undo_stack.count() == n + 1
    assert ses.undo_stack.text(n) == f"erase a polygon from zone {z.id}"
    from aurora.core.zones import polygon_inside
    inside = np.where(polygon_inside(z.epolys[0], sd.x, sd.y))[0]
    assert inside.size >= 4 and not set(inside.tolist()) & set(members(z, sd.x, sd.y, sd.pitch).tolist())
    assert rd.region_caption.text().startswith("Region: 1 rectangle, minus 1 erased outline")
    dashed = [it for it in rd.map._overlays if isinstance(it, QGraphicsPolygonItem) and
              it.pen().style() == Qt.DashLine and it.pen().color().name().lower() == MAP_OVERLAY["erase"].lower()]
    assert len(dashed) == 1
    # Clear empties it; the session file keeps it and reads it back
    keep = [list(map(list, p)) for p in z.epolys]
    ses.flush(); ses.save()
    d = json.loads(ses.session_file.read_text(encoding="utf-8"))
    assert d["holders"][ses.current_holder]["zones"][0]["epolys"] == keep
    ses.set_project(Project.discover(project.root))
    back = ses.holder_zones(create=False).zones[0]
    assert back.epolys == keep
    from aurora.app.session import _geometry
    from aurora.core.io.config import Zone
    assert _geometry(back) != _geometry(Zone(id=9, label="same rectangle", rects=back.rects))     # not an import duplicate
    ses.select_zone(back.id); pump(qapp)
    assert rd.clear_btn.isEnabled()
    rd.clear_btn.click(); pump(qapp)
    assert ses.zone(back.id).epolys == [] and not rd.clear_btn.isEnabled()


# ----------------------------------------------------------------------------- cancelling, empty strokes
def test_esc_cancels_any_stroke(qapp, wide):
    rd, ses = wide.reduce, wide.session
    sd = ses.stack_loaded(wide.sid)
    row = row_points(sd, 1)
    pts = [(float(sd.x[i]), float(sd.y[i])) for i in row[:6]]
    seen = []
    rd.map.stroke_cancelled.connect(lambda: seen.append(1))
    for tool in ("rect+", "brush+", "lasso+"):
        rd.set_tool(tool)
        n = ses.undo_stack.count()
        qdrag(qapp, rd.map, pts[:4] + [(pts[3][0], pts[3][1] + sd.pitch[1])], release=False)
        assert rd.map.vb.drawing() == tool[:-1] and rd.map.hint_text().startswith("new zone 1")
        assert rd.map.stroke_preview_counts()[0] > 0                            # the points it would add
        rd.map.setFocus()
        QTest.keyClick(rd.map, Qt.Key_Escape)
        assert rd.map.vb.drawing() == "" and rd.map.hint_text() == idle_hint(tool)
        qmove(qapp, rd.map, pts[5])
        qrelease(qapp, rd.map, pts[5])
        pump(qapp)
        assert ses.undo_stack.count() == n and not _zones(ses) and rd.map.hint_text() == idle_hint(tool)
        assert not rd.map.vb._rubber.isVisible() and len(rd.map.vb._trace.getData()[0] or []) == 0
        assert rd.map.stroke_preview_counts() == (0, 0)
    assert len(seen) == 3
    # Ctrl+Z during a stroke cancels the stroke and undoes nothing
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, pts[:3])
    z = ses.zone()
    n = ses.undo_stack.count()
    qdrag(qapp, rd.map, pts[3:6], release=False)
    rd.map.setFocus()
    QTest.keyClick(rd.map, Qt.Key_Z, Qt.ControlModifier)
    qrelease(qapp, rd.map, pts[5])
    pump(qapp)
    assert ses.undo_stack.count() == n and ses.zone(z.id) is not None and len(z.rects) == 1


def test_empty_strokes_are_not_written(qapp, win):
    rd, ses = win.reduce, win.session
    sd = ses.stack_loaded(win.sid)
    x0, x1, y0, y1 = sd.bbox
    rd.set_tool("rect+")
    n = ses.undo_stack.count()
    below = _box_path(x0, x1, y0 - 0.6, y0 - 0.3)           # inside the view, under the scan's footprint
    qdrag(qapp, rd.map, below, release=False)
    reason = f"outside scan {rd._map_points()[3]}: draw over its pixels"
    assert rd._map_points()[3] == f"09:27 {ses.scan_of(win.sid).label}"
    assert rd.map.hint_text() == reason
    qrelease(qapp, rd.map, below[-1])
    pump(qapp)
    assert not _zones(ses) and ses.undo_stack.count() == n and rd.status_label.text() == reason
    rd.set_tool("rect-")                                   # an erase with no zone anywhere
    qdrag(qapp, rd.map, _box_path(*inner_box(sd, 0.1)))
    assert rd.status_label.text() == "nothing to erase here: no zone under the pointer" and not _zones(ses)
    rd.set_tool("brush+")                                  # a brush under the scan
    qdrag(qapp, rd.map, [(x0, y0 - 0.5), (x0 + 0.3, y0 - 0.5), (x1, y0 - 0.5)])
    assert not _zones(ses) and ses.undo_stack.count() == n


# ----------------------------------------------------------------------------- the zone a stroke goes to
def test_target_zone_rules(qapp, wide):
    rd, ses = wide.reduce, wide.session
    sd = ses.stack_loaded(wide.sid)
    cols = np.unique(np.round(sd.x, 6))
    col = lambda a, b: np.where((sd.x >= cols[a] - 1e-6) & (sd.x <= cols[b] + 1e-6))[0]      # noqa: E731
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, _around(sd, col(0, 2)))
    z1 = ses.zone()
    assert z1.id == 1 and len(members(z1, sd.x, sd.y, sd.pitch)) == 12
    # a stroke that touches zone 1 adds to it, and the hint says so before the release
    qdrag(qapp, rd.map, _around(sd, col(3, 4)), release=False)
    assert rd.map.hint_text() == "adds 8 points to zone 1"
    qrelease(qapp, rd.map, _around(sd, col(3, 4))[-1])
    pump(qapp)
    assert len(_zones(ses)) == 1 and len(members(z1, sd.x, sd.y, sd.pitch)) == 20
    # a stroke apart starts zone 2 ...
    apart = _around(sd, col(8, 10))
    qdrag(qapp, rd.map, apart, release=False)
    assert rd.map.hint_text() == "new zone 2: 12 points (hold Ctrl to add it to zone 1)"
    qrelease(qapp, rd.map, apart[-1])
    pump(qapp)
    assert [z.id for z in _zones(ses)] == [1, 2] and ses.current_zone == 2
    # ... unless Ctrl is held at the release: a sample in two parts
    ses.undo(); pump(qapp)
    ses.select_zone(1); pump(qapp)
    qdrag(qapp, rd.map, apart, release_modifiers=Qt.ControlModifier)
    assert [z.id for z in _zones(ses)] == [1] and len(members(ses.zone(1), sd.x, sd.y, sd.pitch)) == 32
    # Advanced 'every stroke starts a new zone': a touching stroke makes a new zone too
    mode.set_advanced(True); pump(qapp)
    rd.target_new.setChecked(True)
    qdrag(qapp, rd.map, _around(sd, col(5, 5)))
    assert [z.id for z in _zones(ses)] == [1, 2] and ses.current_zone == 2
    rd.target_new.setChecked(False)
    mode.set_advanced(False); pump(qapp)
    # an Erase with nothing selected erases from the zone under its start, which becomes selected
    ses.select_zone(None); pump(qapp)
    rd.set_tool("rect-")
    qdrag(qapp, rd.map, _around(sd, col(0, 0)))
    assert ses.current_zone == 1 and len(members(ses.zone(1), sd.x, sd.y, sd.pitch)) == 28
    assert len(members(ses.zone(2), sd.x, sd.y, sd.pitch)) == 4


def test_rect_minus_then_plus_restores_the_zone(qapp, wide):
    """Z12 on the map: the newest stroke wins, one undo step per stroke."""
    rd, ses = wide.reduce, wide.session
    sd = ses.stack_loaded(wide.sid)
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, _around(sd, np.arange(sd.n_pix)))
    z = ses.zone()
    want = sorted(members(z, sd.x, sd.y, sd.pitch).tolist())
    box = _around(sd, row_points(sd, 1)[3:7])
    n = ses.undo_stack.count()
    rd.set_tool("rect-")
    qdrag(qapp, rd.map, box)
    assert len(members(z, sd.x, sd.y, sd.pitch)) == sd.n_pix - 4
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, box)
    assert sorted(members(z, sd.x, sd.y, sd.pitch).tolist()) == want and z.erects == []
    assert ses.undo_stack.count() == n + 2
    ses.undo()
    assert len(members(ses.zone(z.id), sd.x, sd.y, sd.pitch)) == sd.n_pix - 4


# ----------------------------------------------------------------------------- the legacy signals
def test_legacy_signals_are_emitted_once_and_never_edit_twice(qapp, win):
    rd, ses = win.reduce, win.session
    sd = ses.stack_loaded(win.sid)
    got = []
    rd.map.rect_drawn.connect(lambda *a: got.append(a))
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, _box_path(*inner_box(sd, 0.05)))
    z = ses.zone()
    assert len(got) == 1 and len(z.rects) == 1 and ses.undo_stack.count() == 1     # Maps / Grid of zones still hear it
    assert not rd.map.stroke_emitting()
    x0, x1, y0, y1 = sd.bbox
    rd.map.rect_drawn.emit(x0, x1, y0, y1)                 # emitted by code: an edit, as before
    pump(qapp)
    assert len(z.rects) == 2 and ses.undo_stack.count() == 2


# ----------------------------------------------------------------------------- caches and hidden views
def test_trace_cache_shared(qapp, win, monkeypatch):
    """After a stroke, with Reduce shown and Check hidden, the zone's traces are computed once (the live preview of
    the stroke's last state, which the commit's preview then reads from the cache); showing Check reuses them. A
    hidden Check or Survey does nothing on a zone edit until it is shown."""
    import aurora.core.zones as zmod
    from aurora.app import trace_cache
    rd, ses, ck, sv = win.reduce, win.session, win.check, win.survey
    sd = ses.stack_loaded(win.sid)
    calls, sdd, maps, keys = [], [], [], []
    real = zmod.zone_traces

    def counted(*a, **k):
        calls.append(a[0].id)
        keys.append(tuple(np.asarray(a[1], int).tolist()))
        return real(*a, **k)
    monkeypatch.setattr(zmod, "zone_traces", counted)
    real_sdd, real_redraw = ck._update_sdd, sv.redraw_map
    monkeypatch.setattr(ck, "_update_sdd", lambda: (sdd.append(1), real_sdd())[1])
    monkeypatch.setattr(sv, "redraw_map", lambda: (maps.append(1), real_redraw())[1])
    trace_cache.clear()
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, _box_path(*inner_box(sd, 0.05)))
    z = ses.zone()
    assert z is not None and not ck.isVisible() and not sv.isVisible()
    final = tuple(members(z, sd.x, sd.y, sd.pitch).tolist())
    assert set(calls) == {win.sid} and keys.count(final) == 1 and maps == []      # the committed zone: once
    sdd.clear()
    rd.set_tool("rect-")                                   # an edit of the selected zone: Check stays idle
    qdrag(qapp, rd.map, _around(sd, np.array([0])))
    final = tuple(members(z, sd.x, sd.y, sd.pitch).tolist())
    assert keys.count(final) == 1 and sdd == [] and maps == []
    n_calls = len(calls)
    win.show_perspective("check"); pump(qapp)
    assert sdd and len(calls) == n_calls                    # drawn from the cache, with the zone as it is now
    assert ck.sdd_note.text().startswith(f"{z.label} · {testdata.STACK} · {len(z.members[win.sid])} px")
    win.show_perspective("survey"); pump(qapp)
    assert maps == [1] and sv.zone_table.rowCount() == 1
    # a reloaded stack is a new object: the cache misses
    hits = trace_cache.stats["hits"]
    win.show_perspective("reduce"); pump(qapp)
    rd.update_preview()
    assert trace_cache.stats["hits"] > hits and len(calls) == n_calls


def test_survey_spectra_kept_for_two_holders(qapp, tmp_path):
    from aurora.app.session import Session
    proj = Project.discover(testdata.make_fake_project(tmp_path / "bt", n_holders=3))
    ses = Session()
    ses.set_project(proj)
    keys = list(ses.holder_keys)
    assert len(keys) == 3
    for k in keys:
        ses.set_holder(k)
        assert ses.survey(k).has_spectra
    assert [ses.survey(k).has_spectra for k in keys] == [False, True, True]
    ses._save_timer.stop()


def test_preview_moves_its_curves_in_place(qapp, win):
    rd, ses = win.reduce, win.session
    sd = ses.stack_loaded(win.sid)
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, _box_path(*inner_box(sd, 0.05)))
    pv = rd.preview
    (name, t, meta), = pv._traces
    items = dict(pv._items)
    assert {"sdd0", "sum", "tey", "i0"} <= set(items)
    from aurora.core.zones import zone_traces
    pix = members(ses.zone(), sd.x, sd.y, sd.pitch)[:3]
    t2 = zone_traces(sd, pix, ses.zone().settings[win.sid])
    pv.update_traces([(name, t2, meta)])
    assert pv._items == items                               # the same curves ...
    assert np.array_equal(items["sum"].getData()[1], t2.sum, equal_nan=True)     # ... with the new data
    assert np.array_equal(items["sdd1"].getData()[1], t2.det[1], equal_nan=True)
    pv.show_sdd.setChecked(False)                           # another structure: rebuilt
    assert "sdd0" not in pv._items and pv._items["sum"] is not items["sum"]
    pv.show_sdd.setChecked(True)
