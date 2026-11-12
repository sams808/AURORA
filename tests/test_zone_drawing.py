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
from aurora.core.constants import DETS  # noqa: E402
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
    assert rd.z_info.full_text().startswith(f"zone {zs[0].id}: 1 rect, ")       # the info line counts the stroke
    win.activateWindow()
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier)
    pump(qapp)
    from aurora.app.reduce import EASY_PREVIEW_NO_ZONE, PREVIEW_NO_ZONE
    assert not _zones(ses) and ses.zone() is None and rd.preview._traces == []
    assert rd.preview._waiting == EASY_PREVIEW_NO_ZONE                          # 'Select a sample, or draw one ...'
    mode.set_advanced(True); pump(qapp)
    assert rd.preview._waiting == PREVIEW_NO_ZONE                               # 'Draw a zone on the map (drag) ...'
    mode.set_advanced(False); pump(qapp)


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
    assert rd.status_label.text() == f"nothing of sample {z.id} there to erase" == rd.map.hint_text()   # Easy words
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
    assert rd.region_caption.text().startswith("Region: 1 rectangle, 1 part erased")
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
        hp = sd.pitch[1] / 2                               # half a pitch off the centres: no point on an edge
        path = [(x, y - hp) for x, y in pts[:4]] + [(pts[3][0] + hp, pts[3][1] + hp)]
        qdrag(qapp, rd.map, path, release=False)
        assert rd.map.vb.drawing() == tool[:-1] and rd.map.hint_text().startswith("new sample 1")
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
    rd.map.zoom_to(x0, x1, y0 - 1.0, y1)                    # whatever the map's shape at this window size
    pump(qapp)
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
    # a rectangle beside zone 1 (touching, not over it) starts a new sample: neighbouring grains always touch
    # (persona S-01); Easy names samples and pixels
    qdrag(qapp, rd.map, _around(sd, col(3, 4)), release=False)
    from aurora.app.shortcuts import native_text
    assert rd.map.hint_text() == native_text("new sample 2: 8 pixels (hold Ctrl to add it to sample 1)")
    qrelease(qapp, rd.map, _around(sd, col(3, 4))[-1])
    pump(qapp)
    assert [z.id for z in _zones(ses)] == [1, 2] and ses.current_zone == 2
    ses.undo(); pump(qapp)
    ses.select_zone(1); pump(qapp)
    # a stroke over zone 1 adds to it, and the hint says so before the release; the line says how to start another
    qdrag(qapp, rd.map, _around(sd, col(2, 4)), release=False)
    assert rd.map.hint_text() == "adds 8 pixels to sample 1"
    qrelease(qapp, rd.map, _around(sd, col(2, 4))[-1])
    pump(qapp)
    z1 = ses.zone(1)                                       # the undo above put a copy of zone 1 back
    assert len(_zones(ses)) == 1 and len(members(z1, sd.x, sd.y, sd.pitch)) == 20
    assert rd.status_label.text() == "Added to sample 1. To start another sample, press New sample (N)."
    # a stroke apart starts zone 2 ...
    apart = _around(sd, col(8, 10))
    qdrag(qapp, rd.map, apart, release=False)
    assert rd.map.hint_text() == native_text("new sample 2: 12 pixels (hold Ctrl to add it to sample 1)")
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
    note = ck.sdd_note.text().replace("\u200b", "")       # the scan name may wrap at its underscores (breakable)
    assert note.startswith(f"{z.label} · {testdata.STACK} · {len(z.members[win.sid])} px")
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


# ============================================================================= stage B
@pytest.fixture
def sproject(tmp_path):
    from zone_helpers import survey_project
    return Project.discover(survey_project(tmp_path / "bt"))


@pytest.fixture
def swin(qapp, sproject):
    """The window on the beamtime whose survey covers both stacks, Survey shown (Easy mode)."""
    mode.set_advanced(False)
    w, sid = open_window(qapp, sproject)
    w.sid = sid
    w.show_perspective("survey")
    pump(qapp, 10)
    yield w
    mode.set_advanced(False)
    close(w)


def _visible_texts(page) -> list[str]:
    from PySide6.QtWidgets import QAbstractButton, QLabel, QWidget
    out = []
    for w in page.findChildren(QWidget):
        if w.isVisible() and isinstance(w, (QAbstractButton, QLabel)):
            out.append(getattr(w, "full_text", w.text)())
    return out


def _footprint_box(sd, pad=0.05):
    x0, x1, y0, y1 = sd.bbox
    return _box_path(x0 - pad, x1 + pad, y0 - pad, y1 + pad)


def qdclick(qapp, map_view, pt) -> None:
    """A real double-click on the map at pt (mm): press, release, double-click, release, as a mouse sends them
    (QTest.mouseDClick sends the double-click event alone, which pyqtgraph never turns into a click)."""
    vp, p = map_view.viewport(), view_to_viewport(map_view, *pt)
    QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, p)
    QTest.mouseEvent(QTest.MouseAction.MouseDClick, vp, Qt.LeftButton, Qt.NoModifier, p)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, p)
    qapp.processEvents()


def _scan_by(ses, name):
    return next(s for s in ses.stacks_of() if s.name == name)


# ----------------------------------------------------------------------------- Z4: the tool bar
def test_easy_toolbar_and_names(qapp, swin):
    from aurora.app.zone_tools import SHAPE_TEXT
    win = swin
    for key in ("survey", "reduce"):
        win.show_perspective(key)
        pump(qapp, 5)
        page = win.pages[key]
        bar = page.toolbar
        for adv in (False, True):
            mode.set_advanced(adv)
            pump(qapp, 5)
            shown = {s for s, b in bar.shape_buttons.items() if b.isVisible()}
            assert shown == ({"navigate", "rect", "brush", "lasso"} | ({"polygon"} if adv else set())), (key, adv)
            for b in (bar.draw_btn, bar.erase_btn, bar.undo_btn, bar.clear_btn):
                assert b.isVisible(), (key, adv, b.text())
            assert bar.new_each.isVisible() == adv
            bar.set_shape("rect")
            assert not bar.brush_size.isVisible()
            bar.set_shape("brush")
            assert bar.brush_size.isVisible() and page.map.vb.tool.startswith("brush")
            bar.set_shape("rect")
        mode.set_advanced(False)
        pump(qapp, 5)
        assert [bar.shape_buttons[s].text() for s in SHAPE_TEXT] == ["Move", "Rectangle", "Brush", "Lasso", "Polygon"]
        assert (bar.draw_btn.text(), bar.erase_btn.text()) == ("Draw", "Erase")
        for w in [bar.draw_btn, bar.erase_btn, bar.undo_btn, bar.clear_btn, bar.new_each, bar.brush_size,
                  *bar.shape_buttons.values()]:
            assert w.accessibleName() and w.toolTip() and w.property("aurora_help_key"), w
        assert bar.draw_btn.accessibleName() == "draw zones" and bar.erase_btn.accessibleName() == "erase from the zone"
        assert bar.brush_size.accessibleName() == "brush size in map points" and bar.brush_size.suffix() == " px"
        for t in _visible_texts(page):
            assert not any(j in t for j in ("rect+", "rect-", "brush+", "brush-", "lasso+", "polygon+")), (key, t)
    # Draw / Erase from Move go back to the last drawing shape
    sv = win.survey
    win.show_perspective("survey")
    sv.set_tool("navigate")
    sv.toolbar.erase_btn.click()
    assert sv.map.vb.tool == "rect-" and sv.toolbar.erase()
    sv.toolbar.draw_btn.click()
    assert sv.map.vb.tool == "rect+"
    # Undo follows the stack
    ses = win.session
    assert not sv.toolbar.undo_btn.isEnabled()
    sd = ses.stack_loaded(win.sid)
    qdrag(qapp, sv.map, _footprint_box(sd))
    assert sv.toolbar.undo_btn.isEnabled() and win.reduce.toolbar.undo_btn.isEnabled() and _zones(ses)
    sv.toolbar.undo_btn.click()
    pump(qapp)
    assert not _zones(ses) and not sv.toolbar.undo_btn.isEnabled()


# ----------------------------------------------------------------------------- Z15: the keys of both pages
def test_zone_shortcuts_both_perspectives(qapp, swin):
    from aurora.app import shortcuts
    win, ses = swin, swin.session
    for key, section in (("survey", "Survey"), ("reduce", "Reduce")):
        page = win.pages[key]
        other = win.reduce if key == "survey" else win.survey
        win.show_perspective(key)
        pump(qapp, 5)
        reg = {a.text(): a.shortcut().toString() for a in shortcuts.registered(win)
               if shortcuts.section_of(a) == section}
        for text, k in (("Move (pan and zoom)", "V"), ("Rectangle", "R"), ("Brush", "B"), ("Lasso", "L"),
                        ("Draw mode", "D"), ("Erase mode", "E"), ("Switch between Draw and Erase", "X"),
                        ("Brush in Erase mode", "Shift+B"), ("Larger brush", "+"), ("Smaller brush", "-"),
                        ("New sample", "N"), ("Delete the sample", "Del"), ("Fit the view", "F"),
                        ("Previous sample", "Alt+Up"), ("Next sample", "Alt+Down")):
            assert reg.get(text) == k, (section, text, reg.get(text))
        other_tool = other.map.vb.tool
        win.activateWindow()
        page.map.setFocus()

        def press(k, mods=Qt.NoModifier):
            QTest.keyClick(win, k, mods)
            pump(qapp)
            return page.map.vb.tool
        page.set_tool("navigate")
        assert press(Qt.Key_R) == "rect+" and press(Qt.Key_E) == "rect-" and press(Qt.Key_L) == "lasso-"
        assert press(Qt.Key_X) == "lasso+" and press(Qt.Key_B) == "brush+" and press(Qt.Key_D) == "brush+"
        assert press(Qt.Key_B, Qt.ShiftModifier) == "brush-" and press(Qt.Key_V) == "navigate"
        n = page.toolbar.brush_n()
        [a for a in shortcuts.registered(win) if shortcuts.section_of(a) == section and a.text() == "Larger brush"][0].trigger()
        assert page.toolbar.brush_n() == n + 1 and page.map.vb.brush_n == n + 1
        assert other.map.vb.tool == other_tool              # a key on one page never changes the other page's tool
        zones = len(_zones(ses))
        press(Qt.Key_N)
        assert len(_zones(ses)) == zones + 1 and ses.zone() is not None
        press(Qt.Key_Delete)
        assert len(_zones(ses)) == zones
        press(Qt.Key_F)
    # Esc cancels a stroke on the Survey too (map-level, before 'Cancel the running task')
    sv = win.survey
    win.show_perspective("survey"); pump(qapp)
    sv.set_tool("rect+")
    sd = ses.stack_loaded(win.sid)
    n = ses.undo_stack.count()
    qdrag(qapp, sv.map, _footprint_box(sd), release=False)
    sv.map.setFocus()
    QTest.keyClick(sv.map, Qt.Key_Escape)
    qrelease(qapp, sv.map, _footprint_box(sd)[-1])
    pump(qapp)
    assert ses.undo_stack.count() == n and not _zones(ses)


# ----------------------------------------------------------------------------- Z6: drawing on the Survey
def test_survey_draws_and_clicks(qapp, swin):
    from PySide6.QtWidgets import QMenu
    from zone_helpers import WIDE
    win, ses, sv = swin, swin.session, swin.survey
    sd = ses.stack_loaded(win.sid)
    assert sv.map.vb.tool == "navigate" and sv.toolbar.shape() == "navigate"     # opens in Move
    assert sv.map.zone_tools and sv.map.pitch is not None
    # Draw + Rectangle over the stack's footprint: one zone of the holder, attached to the stacks it covers
    sv.set_tool("rect+")
    n = ses.undo_stack.count()
    qdrag(qapp, sv.map, _footprint_box(sd))
    z = ses.zone()
    assert len(_zones(ses)) == 1 and ses.undo_stack.count() == n + 1 and len(z.rects) == 1
    covered = set()
    for s in ses.stacks_of():
        x, y, pitch, _n = ses.coordinates(ses.stack_id(s))
        if members(z, x, y, pitch).size:
            covered.add(ses.stack_id(s))
    assert set(z.settings) == covered == {win.sid}
    assert sv.zone_table.rowCount() == 1 and sv.map.hint_text() == idle_hint("rect+")
    c = sd.bbox
    sv._hover((c[0] + c[1]) / 2, (c[2] + c[3]) / 2)                # the hover line names the footprint and the zone
    assert testdata.STACK in sv.hint.full_text() and sv.hint.full_text().endswith(" · sample 1")
    # a stroke over no survey point writes nothing and says why
    sv.set_tool("rect+")
    ses.select_zone(None); pump(qapp)
    (vx0, vx1), (vy0, vy1) = sv.map.vb.viewRange()
    survey = ses.survey()
    below = (vy0 + float(survey.y.min()) - survey.pitch[1]) / 2           # in the view, under the plate map
    qdrag(qapp, sv.map, _box_path(0.0, 0.6, below - 0.1, below + 0.1))
    assert len(_zones(ses)) == 1 and sv.map.hint_text() == "no measured point there: draw over the coloured map"
    # Move: a click inside the zone selects it; a click on a complete footprint outside every zone opens it
    sv.set_tool("navigate")
    opened = []
    sv.open_requested.connect(lambda s: opened.append(s.name))
    c = sd.bbox
    QTest.mouseClick(sv.map.viewport(), Qt.LeftButton, Qt.NoModifier,
                     view_to_viewport(sv.map, (c[0] + c[1]) / 2, (c[2] + c[3]) / 2))
    pump(qapp)
    assert ses.current_zone == z.id and opened == []
    wx = (2.0 + 4.75) / 2
    win.show_perspective("survey"); pump(qapp)
    QTest.mouseClick(sv.map.viewport(), Qt.LeftButton, Qt.NoModifier, view_to_viewport(sv.map, wx, 5.4))
    pump(qapp)
    assert opened == [WIDE] and win.stack.currentWidget() is win.reduce
    # a double-click inside the zone over the footprint: that stack in Reduce, the zone selected
    win.show_perspective("survey"); pump(qapp)
    opened.clear()
    ses.select_zone(None); pump(qapp)
    qdclick(qapp, sv.map, ((c[0] + c[1]) / 2, (c[2] + c[3]) / 2))
    pump(qapp, 10)
    assert opened[-1:] == [testdata.STACK] and ses.current_zone == z.id and win.stack.currentWidget() is win.reduce
    # the zone's right-click menu offers its stacks in Reduce
    win.show_perspective("survey"); pump(qapp)
    m = sv.zone_menu(z.id)
    texts = [a.text() for a in m.actions() if a.text()]
    assert texts[:5] == ["Rename…", "Duplicate", "Delete", "Copy geometry", "Zoom to zone"]
    assert any(t.startswith("Open 09:27") and t.endswith("in Reduce") for t in texts)
    QTest.mouseClick(sv.map.viewport(), Qt.RightButton, Qt.NoModifier,
                     view_to_viewport(sv.map, (c[0] + c[1]) / 2, (c[2] + c[3]) / 2))
    pump(qapp)
    assert isinstance(sv.last_menu, QMenu) and sv.last_menu.isVisible()
    sv.last_menu.close(); pump(qapp)
    # Grid of zones (Advanced) still opens its dialog; the tool bar is off while it is armed
    mode.set_advanced(True); pump(qapp)
    sv.grid_btn.setChecked(True); pump(qapp)
    assert not sv.toolbar.isEnabled() and sv.map.vb.tool == "rect+" and sv.map.hint_text() == ""
    n = len(_zones(ses))
    qdrag(qapp, sv.map, _box_path(1.8, 4.9, 4.9, 5.85))
    assert sv.grid_dialog is not None and len(_zones(ses)) == n and not sv.grid_btn.isChecked()
    assert sv.toolbar.isEnabled() and sv.map.vb.tool == sv.toolbar.tool()
    sv.grid_dialog.reject(); pump(qapp)


# ----------------------------------------------------------------------------- Z7: the Survey's Zone preview
def test_survey_preview_emission(qapp, swin):
    from zone_helpers import BINS, LINE_A, LINE_B, sample_box, sample_points
    win, ses, sv = swin, swin.session, swin.survey
    zp = sv.zone_preview
    sd = ses.stack_loaded(win.sid)
    survey = ses.survey()
    assert zp.summary.text().startswith("Select a sample, or draw one on the map")    # Easy words (plain 5.3)
    sv.channel.setCurrentText(LINE_A); pump(qapp)
    sv.set_tool("rect+")
    qdrag(qapp, sv.map, _box_path(*sample_box(sd)))             # the sample's survey points
    z = ses.zone()
    pix = members(z, survey.x, survey.y, survey.pitch)
    assert set(pix.tolist()) == set(sample_points(survey, sd).tolist()) and pix.size == 12
    assert set(z.settings) == {win.sid} and len(z.members[win.sid]) == sd.n_pix     # and the whole stack
    d = zp.emission.data
    want = survey.per_point[pix].sum(axis=0) / pix.size
    assert np.allclose(d["per_point"], want) and np.allclose(d["sum"], want.sum(axis=0)) and d["n"] == pix.size
    y = zp.emission.curves[0].yData
    assert np.allclose(y[np.isfinite(y)], want[0][want[0] > 0])
    assert zp.emission.plot.getPlotItem().titleLabel.text == f"XRF at {survey.energy:.1f} eV · {pix.size} pixels"
    assert [tuple(r.getRegion()) for r in zp.emission.regions] == [tuple(BINS[LINE_A][d]) for d in DETS]
    assert [t.value() for t in zp.emission.ticks] == [np.mean([(lo + hi) / 2 for lo, hi in BINS[LINE_B].values()])]
    # Easy names the sample (its default label: "sample 1") and its pixels, the window in eV (M10); Advanced the
    # zone, its survey points and the channels, the detector in words
    assert zp.summary.full_text().startswith(f"sample 1: {pix.size} pixels · {LINE_A} window 600–740 eV")
    mode.set_advanced(True); pump(qapp)
    assert zp.summary.full_text().startswith(f"zone 1: {pix.size} survey points · {LINE_A} window 60–73 ch on "
                                             "detector 1 (SDD1)")
    mode.set_advanced(False); pump(qapp)
    sv.channel.setCurrentText(LINE_B); pump(qapp)              # the map's channel moves the shaded window
    assert [tuple(r.getRegion()) for r in zp.emission.regions] == [tuple(BINS[LINE_B][d]) for d in DETS]
    sv.channel.setCurrentText("total"); pump(qapp)             # no window for the total XRF
    assert zp.emission.regions == []

    # the sample and the plate around it: different line A / line B ratios
    def ratio(per_point):
        tot = per_point.sum(axis=0)
        return tot[60:74].sum() / tot[46:58].sum()
    sample = ratio(zp.emission.data["per_point"])
    ses.select_zone(None); pump(qapp)
    qdrag(qapp, sv.map, _box_path(0.75, 1.65, 4.45, 4.85))       # plate only
    plate = ratio(zp.emission.data["per_point"])
    assert sample > 3 and plate < 1, (sample, plate)


def test_survey_preview_xas_of_covered_stack(qapp, swin):
    from aurora.app.zone_preview import EASY_NO_STACK, MSG_NO_STACK
    from aurora.core.zones import zone_traces
    from zone_helpers import WIDE
    win, ses, sv = swin, swin.session, swin.survey
    zp = sv.zone_preview
    sd = ses.stack_loaded(win.sid)
    sv.set_tool("rect+")
    qdrag(qapp, sv.map, _footprint_box(sd))
    z = ses.zone()
    assert zp.scan_combo.currentData() == win.sid and zp.xas.isVisible()
    want = zone_traces(sd, members(z, sd.x, sd.y, sd.pitch), z.settings[win.sid]).sum
    assert np.allclose(zp.xas_sum(), want, equal_nan=True)
    assert zp.summary.full_text().endswith(f"on 09:27: {sd.n_pix} of {sd.n_pix} pixels")
    # a stack not loaded: read in a job once the stroke is written (the key Reduce uses), then drawn
    wid = ses.stack_id(_scan_by(ses, WIDE))
    assert ses.stack_loaded(wid) is None
    waits = []
    real = zp.xas.set_traces
    zp.xas.set_traces = lambda traces, waiting="": (waits.append(waiting), real(traces, waiting=waiting))[1]
    ses.select_zone(None); pump(qapp)
    qdrag(qapp, sv.map, _box_path(1.9, 4.85, 4.9, 5.85))
    z2 = ses.zone()
    assert z2.id != z.id and set(z2.settings) == {wid}
    assert any(w == "reading 09:30 …" for w in waits), waits
    assert win.jobs.wait_idle(30000)
    pump(qapp, 10)
    wsd = ses.stack_loaded(wid)
    assert wsd is not None and zp.scan_combo.currentData() == wid
    assert np.allclose(zp.xas_sum(), zone_traces(wsd, members(z2, wsd.x, wsd.y, wsd.pitch), z2.settings[wid]).sum,
                       equal_nan=True)
    # a zone over no stack names the holder
    ses.select_zone(None); pump(qapp)
    qdrag(qapp, sv.map, _box_path(0.75, 1.65, 4.45, 4.85))
    assert not zp.xas.isVisible() and zp.xas_note.text() == EASY_NO_STACK.format(zone="sample 3",
                                                                                   holder=ses.holder().name)
    mode.set_advanced(True); pump(qapp)
    assert zp.xas_note.text() == MSG_NO_STACK.format(zone="zone 3", holder=ses.holder().name)
    mode.set_advanced(False); pump(qapp)
    # Open in Reduce: the stack and the zone there
    ses.select_zone(z.id); pump(qapp)
    assert zp.open_btn.isEnabled()
    zp.open_btn.click()
    pump(qapp, 10)
    win.jobs.wait_idle(30000); pump(qapp)
    assert win.stack.currentWidget() is win.reduce and ses.current_zone == z.id and ses.current_stack == win.sid


# ----------------------------------------------------------------------------- Z8: the live preview
def test_live_preview_during_drag(qapp, swin):
    from aurora.app.zone_tools import FIRST_MS
    from aurora.core.zones import zone_traces
    win, ses, sv = swin, swin.session, swin.survey
    sd = ses.stack_loaded(win.sid)
    survey = ses.survey()
    # Survey: the emission of the draft's points before the release, nothing written yet
    sv.set_tool("rect+")
    before = json.dumps(ses.zones.to_json(), sort_keys=True, default=str)
    n = ses.undo_stack.count()
    path = _footprint_box(sd)
    qdrag(qapp, sv.map, path[:3], release=False)
    QTest.qWait(FIRST_MS + 120); pump(qapp)
    d = sv.editor.draft()
    assert d is not None and d.members_after.size
    want = survey.per_point[d.members_after].sum(axis=0) / d.members_after.size
    assert np.allclose(sv.zone_preview.emission.data["per_point"], want)
    assert json.dumps(ses.zones.to_json(), sort_keys=True, default=str) == before and ses.undo_stack.count() == n
    assert sv.map.stroke_preview_counts()[0] == d.added.size
    qrelease(qapp, sv.map, path[2]); pump(qapp)
    assert ses.undo_stack.count() == n + 1 and sv.map.stroke_preview_counts() == (0, 0)
    # Reduce: the traces of the draft's pixels on the current stack, drawn in place
    rd = win.reduce
    win.show_perspective("reduce"); pump(qapp, 5)
    ses.select_zone(None); pump(qapp)
    rd.set_tool("rect+")
    n = ses.undo_stack.count()
    path = _around(sd, np.arange(6))
    qdrag(qapp, rd.map, path, release=False)
    QTest.qWait(FIRST_MS + 120); pump(qapp)
    d = rd.editor.draft()
    (name, t, _meta), = rd.preview._traces
    st = rd._draft_settings(d, sd)
    assert t.n_pix == d.members_after.size and np.allclose(t.sum, zone_traces(sd, d.members_after, st).sum,
                                                           equal_nan=True)
    assert "while drawing" in rd.region_caption.text() and ses.undo_stack.count() == n
    qrelease(qapp, rd.map, path[-1]); pump(qapp)
    assert ses.undo_stack.count() == n + 1 and "while drawing" not in rd.region_caption.text()


def test_preview_throttle_counts(qapp, swin, monkeypatch):
    import math
    import time
    from aurora.app import zone_tools as zt
    win, ses = swin, swin.session
    rd = win.reduce
    win.show_perspective("reduce"); pump(qapp, 5)
    sd = ses.stack_loaded(win.sid)
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, _around(sd, np.arange(4)))           # zone 1 on the left: the brush below adds to it
    rd.set_tool("brush+")
    xs = np.linspace(float(sd.x.min()), float(sd.x.max()), 31)
    ys = float(np.median(sd.y))
    previews = []
    monkeypatch.setattr(rd, "update_preview", lambda: previews.append(1))
    runs = rd.throttle.runs
    t0 = time.perf_counter()
    qdrag(qapp, rd.map, [(float(x), ys) for x in xs], release=False, delay=40)      # 30 moves, about 1.2 s
    assert previews == [] and rd.throttle.runs > runs                              # live while it moves
    qrelease(qapp, rd.map, (float(xs[-1]), ys)); pump(qapp)
    took = (time.perf_counter() - t0) * 1000
    drag_runs = rd.throttle.runs - runs
    assert 1 <= drag_runs <= math.ceil(took / zt.INTERVAL_MS) + 2, (drag_runs, took)
    assert previews == [1]                                                          # once more, at the release
    QTest.qWait(zt.INTERVAL_MS + 60); pump(qapp)
    assert rd.throttle.runs - runs == drag_runs and not rd.throttle.pending()       # nothing after the release
    monkeypatch.undo()
    # a slow preview: the next ones without de-spike, then only at the release (and the map says so)
    seen = []
    real = rd.throttle._run
    rd.throttle._run = lambda d, fast: (seen.append(fast), real(d, fast))[1]
    clock = iter(np.arange(0.0, 1e4, 0.2))                    # every preview measures 200 ms > SLOW_MS
    rd.throttle.clock = lambda: float(next(clock))
    ses.select_zone(None); pump(qapp)
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, [(float(x), ys) for x in xs[::3]], release=False, delay=40)
    QTest.qWait(zt.INTERVAL_MS + 80); pump(qapp)
    assert seen[:2] == [False, True] and len(seen) == 2 and rd.throttle.state == "release", seen
    assert rd.map.hint_text().endswith(zt.SLOW_HINT)
    qrelease(qapp, rd.map, (float(xs[-1]), ys)); pump(qapp)
    assert rd.throttle.state == "live" and len(seen) == 2 and rd.preview._traces


# ----------------------------------------------------------------------------- Z10: messages of the previews
def test_preview_messages(qapp, swin):
    from aurora.app.reduce import EASY_PREVIEW_NO_ZONE, PREVIEW_NO_SCAN, PREVIEW_NO_ZONE
    from aurora.app.zone_preview import EASY_NO_ZONE, EASY_OFF_PLATE, MSG_EMPTY, MSG_NO_ZONE, MSG_OFF_PLATE
    from zone_helpers import WIDE
    win, ses, sv, rd = swin, swin.session, swin.survey, swin.reduce
    sd = ses.stack_loaded(win.sid)
    zp = sv.zone_preview
    assert zp.summary.text() == EASY_NO_ZONE                   # Easy: the sample by its name (plain 5.3)
    z = ses.new_zone(); pump(qapp)                             # an empty zone
    assert zp.summary.text() == MSG_EMPTY.format(zone=f"sample {z.id}")
    with ses.edit("a zone off the plate"):
        z.rects.append([-3.0, -2.5, 8.0, 8.5])
    pump(qapp)
    assert zp.summary.full_text().startswith(f"sample {z.id}: 0 pixels") and \
        zp.emission.plot.getPlotItem()._aurora_hint.toPlainText() == EASY_OFF_PLATE.format(zone=f"sample {z.id}")
    mode.set_advanced(True); pump(qapp)                        # Advanced: the zone, the zones spec words
    assert zp.summary.full_text().startswith(f"zone {z.id}: 0 survey points") and \
        zp.emission.plot.getPlotItem()._aurora_hint.toPlainText() == MSG_OFF_PLATE.format(zone=f"zone {z.id}")
    ses.select_zone(None); pump(qapp)
    assert zp.summary.text() == MSG_NO_ZONE
    mode.set_advanced(False); pump(qapp)
    ses.select_zone(z.id); pump(qapp)
    # Reduce: no zone, an empty zone, a zone outside this scan naming the scans it covers, no scan
    win.show_perspective("reduce"); pump(qapp, 5)
    ses.select_zone(None); pump(qapp)
    assert rd.preview._waiting == EASY_PREVIEW_NO_ZONE
    zid = ses.new_zone().id; pump(qapp)
    assert rd.preview._waiting == f"sample {zid} is empty: drag on the map to draw it"
    mode.set_advanced(True); pump(qapp)
    assert rd.preview._waiting == f"zone {zid} is empty: drag on the map to draw it"
    mode.set_advanced(False); pump(qapp)
    wid = ses.stack_id(_scan_by(ses, WIDE))
    with ses.edit("a zone on the other stack"):
        ses.zone(zid).rects.append([1.9, 4.85, 4.9, 5.85])
    pump(qapp)
    assert ses.zone(zid).settings and wid in ses.zone(zid).settings and ses.stack_loaded(wid) is None
    assert rd.preview._waiting == (f"sample {zid} holds no pixel of 09:27: it lies outside this scan. Scans it covers: "
                                   "09:30 (pick one in step 1)")
    ses.select_stack(None); pump(qapp)
    ses.select_zone(None); pump(qapp)
    assert rd.preview._waiting == PREVIEW_NO_SCAN


def test_undo_after_drag_refreshes_previews(qapp, swin):
    from aurora.app.zone_preview import EASY_NO_ZONE
    win, ses, sv, rd = swin, swin.session, swin.survey, swin.reduce
    sd = ses.stack_loaded(win.sid)
    sv.set_tool("rect+")
    qdrag(qapp, sv.map, _footprint_box(sd))
    drawn = sv.zone_preview.summary.full_text()
    assert drawn.startswith("sample 1: ")                                 # Easy: the sample (plain 5.3)
    win.activateWindow()
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); pump(qapp)
    assert sv.zone_preview.summary.text() == EASY_NO_ZONE and not _zones(ses)
    QTest.keyClick(win, Qt.Key_Y, Qt.ControlModifier); pump(qapp)
    assert sv.zone_preview.summary.full_text() == drawn
    win.show_perspective("reduce"); pump(qapp, 5)
    assert rd.preview._traces and rd.region_caption.text().startswith("Region: 1 rectangle · 12 of 12 pixels")
    QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); pump(qapp)
    assert rd.preview._traces == [] and rd.region_caption.text().startswith("Nothing outlined yet")
    QTest.keyClick(win, Qt.Key_Y, Qt.ControlModifier); pump(qapp)
    assert rd.preview._traces and rd.region_caption.text().startswith("Region: 1 rectangle")


# ----------------------------------------------------------------------------- Z11: what the map shows
def test_map_feedback(qapp, wide):
    from aurora.app.theme import MAP_OVERLAY
    rd, ses = wide.reduce, wide.session
    sd = ses.stack_loaded(wide.sid)
    m = rd.map
    rd.set_tool("rect+")
    assert m.hint_text() == idle_hint("rect+") and m.vb.cursor().shape() == Qt.CrossCursor
    rd.set_tool("rect-")
    assert m.hint_text() == idle_hint("rect-") == ("Drag over the selected sample to erase · a click erases one pixel "
                                                   "· right-drag draws")
    rd.set_tool("navigate")
    assert m.hint_text() == ""
    # the points a stroke adds, in the new zone's colour; the points an erase removes, crossed
    rd.set_tool("rect+")
    row = row_points(sd, 1)
    qdrag(qapp, m, _around(sd, row[:4]), release=False)
    d = rd.editor.draft()
    assert m.stroke_preview_counts() == (4, 0) and d.added.size == 4
    add = m._stroke_items["add"]
    assert abs(add.opts["brush"].color().alpha() - 0.45 * 255) <= 1 and add.opts["symbol"] == "s"
    qrelease(qapp, m, _around(sd, row[:4])[-1]); pump(qapp)
    assert m.stroke_preview_counts() == (0, 0)
    qdrag(qapp, m, _around(sd, row[:2]), button=Qt.RightButton, release=False)
    assert m.stroke_preview_counts() == (0, 2)
    rem = m._stroke_items["remove"]
    assert rem.opts["symbol"] == "x" and rem.opts["pen"].color().name().lower() == MAP_OVERLAY["erase"].lower()
    assert m.vb._rubber.pen().color().name().lower() == MAP_OVERLAY["erase"].lower()      # the band in Erase colour
    qrelease(qapp, m, _around(sd, row[:2])[-1], button=Qt.RightButton); pump(qapp)
    # the brush square follows the pointer, snapped to the nearest point, n x n pitches
    rd.set_tool("brush+")
    rd.brush_n.setValue(3)
    assert m.vb.cursor().shape() == Qt.BlankCursor
    qmove(qapp, m, (float(sd.x[row[5]]) + 0.3 * sd.pitch[0], float(sd.y[row[5]]) - 0.2 * sd.pitch[1]))
    r = m.footprint_rect()
    assert r is not None and np.isclose(r.center().x(), sd.x[row[5]], atol=1e-3)
    assert np.isclose(r.center().y(), sd.y[row[5]], atol=1e-3)
    assert np.isclose(r.width(), 3 * sd.pitch[0]) and np.isclose(r.height(), 3 * sd.pitch[1])
    qmove(qapp, m, (float(sd.x[row[5]]), float(sd.y.max()) + 3 * sd.pitch[1]))         # beyond the brush's reach
    assert m.footprint_rect() is None
    rd.set_tool("rect+")
    assert m.footprint_rect() is None and m._footprint_n == 0


def test_member_pixels_are_one_item(qapp, wide):
    """Z17: the selected zone's member pixels are one scatter item of pitch-sized squares, not one item per pixel."""
    import pyqtgraph as pg
    rd, ses = wide.reduce, wide.session
    sd = ses.stack_loaded(wide.sid)
    rd.set_tool("rect+")
    qdrag(qapp, rd.map, _around(sd, np.arange(sd.n_pix)))
    z = ses.zone()
    pix = members(z, sd.x, sd.y, sd.pitch)
    items = [it for it in rd.map._overlays if isinstance(it, pg.ScatterPlotItem)]
    # two items whatever the size: the member pixels filled (every zone, M11) and outlined (the selected zone)
    assert len(items) == 2 and all(len(it.data) == pix.size == sd.n_pix for it in items)
    for it in items:
        xs, ys = it.getData()
        assert set(zip(np.round(xs, 6), np.round(ys, 6))) == set(zip(np.round(sd.x[pix], 6), np.round(sd.y[pix], 6)))
        br = it.boundingRect()                               # pitch-sized squares, not points
        assert np.ptp(sd.x) + sd.pitch[0] - 1e-6 <= br.width() <= np.ptp(sd.x) + 2 * sd.pitch[0]


# ----------------------------------------------------------------------------- Z14: survey, inside the zone
def test_window_source_survey_inside_the_zone(qapp, swin):
    from aurora.core.constants import DETS as _D
    win, ses, sv, rd = swin, swin.session, swin.survey, swin.reduce
    sd = ses.stack_loaded(win.sid)
    survey = ses.survey()
    sv.set_tool("rect+")
    qdrag(qapp, sv.map, _footprint_box(sd))
    z = ses.zone()
    mode.set_advanced(True)
    win.show_perspective("reduce"); pump(qapp, 5)
    wc = rd.window_card
    wc.source.setCurrentIndex(wc.source.findData("survey_zone")); pump(qapp)
    assert wc.source.currentText() == "survey, inside the zone" and wc.source.isVisible()
    pix = members(z, survey.x, survey.y, survey.pitch)
    assert np.allclose(wc._spectrum, survey.zone_spectrum(pix)) and len(_D) == wc._spectrum.shape[0]
    assert wc.status.text().endswith(f"inside the zone ({pix.size} points)")
    wc.source.setCurrentIndex(wc.source.findData("survey")); pump(qapp)
    assert np.allclose(wc._spectrum, survey.spec)                  # the whole survey, as before
    wc.source.setCurrentIndex(wc.source.findData("survey_zone")); pump(qapp)
    ses.select_zone(None); pump(qapp)
    assert np.allclose(wc._spectrum, survey.spec) and wc.status.text().startswith("no zone selected")
    mode.set_advanced(False)


# ============================================================================= stage C: the review
@pytest.fixture
def small(qapp, project):
    """The window at the smallest size (1200 x 760, Easy): Reduce's stack map is narrow, its axis wide beside it."""
    mode.set_advanced(False)
    w, sid = open_window(qapp, project, size=(1200, 760))
    w.sid = sid
    yield w
    mode.set_advanced(False)
    close(w)


def _beside_the_map(map_view):
    """A point (mm) on the left axis of a map, level with the middle of the view: outside the view box, where a drag
    started in the margin begins."""
    ax = map_view.plot.getAxis("left")
    sp = ax.sceneBoundingRect().center()
    assert not map_view.vb.sceneBoundingRect().contains(sp)
    p = map_view.vb.mapSceneToView(sp)
    return float(p.x()), float(p.y())


def test_a_stroke_started_beside_the_map_starts_where_the_press_was(qapp, small):
    """A rectangle started in the margin left of the stack (on the axis) begins under the pointer, so it takes the
    edge column. pyqtgraph hands such a drag over from the axis with the positions in the axis' own coordinates, which
    put the stroke a whole axis width to the right (0.32 mm on this map: the first column was missed)."""
    win, rd, ses = small, small.reduce, small.session
    sd = ses.stack_loaded(win.sid)
    x_ax, y_mid = _beside_the_map(rd.map)
    assert x_ax < float(sd.x.min()) - sd.pitch[0]               # left of the stack, beyond its first column
    rd.set_tool("rect+")
    # a click on the axis is a click outside the scan: nothing written, and the map says why (it added a pixel)
    n = ses.undo_stack.count()
    QTest.mouseClick(rd.map.viewport(), Qt.LeftButton, Qt.NoModifier, view_to_viewport(rd.map, x_ax, y_mid))
    pump(qapp)
    assert ses.undo_stack.count() == n and not _zones(ses)
    assert rd.map.hint_text() == f"outside scan {rd._map_points()[3]}: draw over its pixels"
    starts = []
    rd.map.stroke_started.connect(lambda st: starts.append(st.start))
    cols = np.unique(np.round(sd.x, 6))
    end = (float(cols[1]), float(sd.y.max()) + 0.4 * sd.pitch[1])
    qdrag(qapp, rd.map, [(x_ax, float(sd.y.min()) - 0.4 * sd.pitch[1]), ((x_ax + end[0]) / 2, y_mid), end])
    assert len(starts) == 1 and abs(starts[0][0] - x_ax) < 0.02, (starts, x_ax)
    z = ses.zone()
    first_col = np.where(np.isclose(sd.x, cols[0]))[0]
    got = set(members(z, sd.x, sd.y, sd.pitch).tolist())
    assert set(first_col.tolist()) <= got and abs(z.rects[0][0] - x_ax) < 0.02


def test_the_map_hint_is_one_line_when_it_fits(qapp, small):
    """A short sentence stays on one line (QLabel's own word-wrap broke 'erases 4 points from zone 1' and even
    'new zone 1: 12 points' over two or three lines), a long one wraps at its width."""
    rd, ses = small.reduce, small.session
    sd = ses.stack_loaded(small.sid)
    m = rd.map
    rd.set_tool("rect+")
    box = _box_path(*inner_box(sd, 0.05))
    qdrag(qapp, m, box, release=False)
    assert m.hint_text().startswith("new sample 1: ") and m.hint_lines() == 1
    qrelease(qapp, m, box[-1]); pump(qapp)
    rd.set_tool("rect-")
    two = _around(sd, np.array([0, 1]))
    qdrag(qapp, m, two, release=False)
    assert m.hint_text().startswith("erases 2 pixels from sample 1") and m.hint_lines() == 1, m.hint_text()
    qrelease(qapp, m, two[-1]); pump(qapp)
    long = "a sentence much longer than the map is wide, which has to wrap over several lines to be read at all"
    m.set_hint(long)
    assert m.hint_lines() >= 2 and m._hint.width() <= max(120, int(0.6 * m.width()), min(m.width() - 16, 280))
    assert m._hint.geometry().right() < m.width()


def test_a_scan_change_cancels_the_stroke_in_progress(qapp, wide):
    """A stroke whose map changes under it (another scan picked, a holder changed) is dropped, not written with the
    old map's points."""
    rd, ses, win = wide.reduce, wide.session, wide
    sd = ses.stack_loaded(win.sid)
    rd.set_tool("rect+")
    n = ses.undo_stack.count()
    path = _around(sd, row_points(sd, 1)[:4])
    qdrag(qapp, rd.map, path, release=False)
    assert rd.map.vb.drawing() == "rect"
    other = ses.stack_id(_scan_by(ses, testdata.STACK))
    ses.stack(other)
    ses.select_stack(other); pump(qapp)
    assert rd.map.vb.drawing() == "" and rd.map.stroke_preview_counts() == (0, 0)
    qrelease(qapp, rd.map, path[-1]); pump(qapp)
    assert ses.undo_stack.count() == n and not _zones(ses)


def test_zone_preview_names_its_curves_and_windows(qapp, swin):
    """The Zone preview names its curves in one line under the emission (SDD1-SDD4 and the sum, the colours and dashes
    of both plots), which gives way to the hover readout over the plot; the XAS panel has no legend box over its
    short plot. The shaded window carries the map channel's name, the other lines' names sit beside their ticks."""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    from zone_helpers import LINE_A, LINE_B, sample_box
    win, ses, sv = swin, swin.session, swin.survey
    zp = sv.zone_preview
    sd = ses.stack_loaded(win.sid)
    assert not zp.legend.isVisible()                            # no zone: a sentence, no legend
    sv.channel.setCurrentText(LINE_A); pump(qapp)
    sv.set_tool("rect+")
    qdrag(qapp, sv.map, _box_path(*sample_box(sd)))
    pump(qapp)
    assert zp.legend.isVisible() and zp.legend.texts() == ["SDD1", "SDD2", "SDD3", "SDD4", "sum"]
    assert all(not sw.pixmap().isNull() for sw in zp.legend.swatches)
    assert zp.xas.isVisible() and zp.xas.top.legend is None and zp.xas._traces
    em = zp.emission
    assert em.window_label is not None and em.window_label.label.toPlainText() == LINE_A
    (tick,) = em.ticks
    assert tick.label.toPlainText() == LINE_B and tick.value() < em.window_label.value()
    assert tick.label.anchors[1][0] == pytest.approx(1.0)      # line B's name left of its tick, away from line A
    top = em.plot.getViewBox().viewRange()[1][1]
    assert top > np.log10(np.nanmax(em.data["sum"]))            # room above the highest peak for the names
    lo, hi = em.window["sdd1"]
    assert em.readout_text(lo).split(" · ")[1] == f"{LINE_A} window"
    assert em.readout_text(int(tick.value())).split(" · ")[1] == f"near {LINE_B}"
    # ten lines a few channels apart: only the names that fit are printed, never over each other
    crowd = [(f"line {i}", 120.0 + 4 * i) for i in range(10)]
    em.set_windows(dict(em.window), crowd, None, name=LINE_A)
    named = [t for t in em.ticks if getattr(t, "label", None) is not None]
    assert 1 <= len(named) < 10 and len(em.ticks) == 10
    boxes = sorted((t.label.mapRectToScene(t.label.boundingRect()).left(),
                    t.label.mapRectToScene(t.label.boundingRect()).right()) for t in named)
    assert all(a[1] <= b[0] + 1 for a, b in zip(boxes, boxes[1:])), boxes
    assert em.readout_text(124).split(" · ")[1] == "near line 1"
    zp.refresh(); pump(qapp)
    # the hover readout takes the legend's line while the pointer is over the plot, and gives it back
    pi = em.plot.getPlotItem()
    em._moved(pi.vb.mapViewToScene(pi.vb.viewRect().center()))
    assert em.line.currentWidget() is em.readout and em.readout.text().startswith("channel ")
    QApplication.sendEvent(em.plot, QEvent(QEvent.Leave))
    assert em.line.currentWidget() is em.legend
    sv.channel.setCurrentText("total"); pump(qapp)
    assert em.window_label is None and em.regions == []


def test_the_survey_zones_table_shows_its_zones(qapp, swin):
    """At 1200 x 760 the zones table shows two zones without a sideways scroll bar (no second row number), and the
    zone a stroke creates is the selected row."""
    win, ses, sv = swin, swin.session, swin.survey
    win.resize(1200, 760); pump(qapp, 10)
    sd = ses.stack_loaded(win.sid)
    sv.set_tool("rect+")
    qdrag(qapp, sv.map, _footprint_box(sd))
    ses.select_zone(None); pump(qapp)
    qdrag(qapp, sv.map, _box_path(1.9, 4.85, 4.9, 5.85))
    pump(qapp, 5)
    t = sv.zone_table
    assert t.rowCount() == 2 and not t.verticalHeader().isVisible()
    assert not t.horizontalScrollBar().isVisible()
    for r in range(2):
        rect = t.visualRect(t.model().index(r, 1))
        assert rect.bottom() <= t.viewport().height() + 1, (r, rect, t.viewport().height())
    assert [i.row() for i in t.selectionModel().selectedRows()] == [1] and ses.current_zone == 2
    assert t.minimumHeight() >= t.horizontalHeader().sizeHint().height() + 2 * t.verticalHeader().defaultSectionSize()


def test_undo_names_the_step_it_undoes(qapp, swin):
    """The tool bar's Undo undoes the last edit of the one history (a setting edited on a card is a step too): its
    tooltip names that step, as Edit > Undo does."""
    from aurora.app.help_text import HELP
    from aurora.app.shortcuts import native_text
    idle_tip = native_text(HELP["zones.undo"][0])                    # ⌘Z on macOS
    win, ses, sv = swin, swin.session, swin.survey
    bar = sv.toolbar
    assert bar.undo_btn.toolTip() == idle_tip and not bar.undo_btn.isEnabled()
    sd = ses.stack_loaded(win.sid)
    sv.set_tool("rect+")
    qdrag(qapp, sv.map, _footprint_box(sd))
    want = native_text("Undo: add a rectangle to a new zone (Ctrl+Z). One stroke is one step.")
    assert bar.undo_btn.toolTip() == want == win.reduce.toolbar.undo_btn.toolTip()
    z = ses.zone()
    with ses.edit(f"rename zone {z.id}"):
        z.label = "sample A"
    assert bar.undo_btn.toolTip().startswith(f"Undo: rename zone {z.id} ")
    bar.undo_btn.click(); pump(qapp)
    assert ses.zone(z.id).label != "sample A" and bar.undo_btn.toolTip() == want
    bar.undo_btn.click(); pump(qapp)
    assert not _zones(ses) and bar.undo_btn.toolTip() == idle_tip


def test_polygon_draw_and_erase_on_the_survey_in_advanced(qapp, swin):
    """Advanced: P, then a click on each corner and a double-click on the last draws a polygon zone on the plate map
    (one step); X switches to Erase and the same clicks cut an erased outline out of it (epolys); Esc drops a polygon
    half drawn. Clicks only, real QTest events."""
    from zone_helpers import sample_box
    win, ses, sv = swin, swin.session, swin.survey
    mode.set_advanced(True); pump(qapp)
    sd = ses.stack_loaded(win.sid)
    survey = ses.survey()
    x0, x1, y0, y1 = sample_box(sd)
    win.activateWindow()
    sv.map.setFocus()
    QTest.keyClick(win, Qt.Key_P); pump(qapp)
    assert sv.map.vb.tool == "polygon+" and sv.toolbar.shape() == "polygon"
    gap = qapp.doubleClickInterval() + 60                      # separate clicks, never read as a double-click

    def polygon(pts):
        for pt in pts[:-1]:
            QTest.mouseClick(sv.map.viewport(), Qt.LeftButton, Qt.NoModifier, view_to_viewport(sv.map, *pt))
            pump(qapp)
            QTest.qWait(gap)
        qdclick(qapp, sv.map, pts[-1])
        pump(qapp)
    n = ses.undo_stack.count()
    pad = 0.08
    polygon([(x0 - pad, y0 - pad), (x1 + pad, y0 - pad), (x1 + pad, y1 + pad), (x0 - pad, y1 + pad)])
    z = ses.zone()
    assert z is not None and len(z.polys) == 1 and len(z.polys[0]) == 4
    assert ses.undo_stack.count() == n + 1 and ses.undo_stack.text(n) == "add a polygon to a new zone"
    inside = members(z, survey.x, survey.y, survey.pitch)
    assert inside.size and set(z.settings) == {win.sid}
    QTest.keyClick(win, Qt.Key_X); pump(qapp)
    assert sv.map.vb.tool == "polygon-"
    xm = (x0 + x1) / 2
    polygon([(x0 - pad, y0 - pad), (xm, y0 - pad), (xm, y1 + pad), (x0 - pad, y1 + pad)])
    assert len(z.epolys) == 1 and ses.undo_stack.count() == n + 2
    left = members(z, survey.x, survey.y, survey.pitch)
    assert 0 < left.size < inside.size and (survey.x[left] > xm).all()
    # Esc drops a polygon in progress: nothing written
    QTest.mouseClick(sv.map.viewport(), Qt.LeftButton, Qt.NoModifier, view_to_viewport(sv.map, x0, y0)); pump(qapp)
    QTest.qWait(gap)
    QTest.mouseClick(sv.map.viewport(), Qt.LeftButton, Qt.NoModifier, view_to_viewport(sv.map, x1, y0)); pump(qapp)
    assert sv.map.vb.drawing() == "polygon"
    sv.map.setFocus()
    QTest.keyClick(sv.map, Qt.Key_Escape); pump(qapp)
    assert sv.map.vb.drawing() == "" and ses.undo_stack.count() == n + 2
    mode.set_advanced(False); pump(qapp)
    assert sv.map.vb.tool != "polygon-"                          # Easy has no polygon: back to the rectangle
