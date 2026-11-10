"""Zone geometry and trace maths on synthetic data, plus the stack / survey loaders on the fake project."""
import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.io.config import ScanSettings, Zone
from aurora.core.lines import edge_energy, line_of_mode, main_line
from aurora.core.model import Project
from aurora.core.stack import StackData
from aurora.core.survey import SurveyData
from aurora.core.zones import (despike, good_i0_curve, grid_pitch, i0_score, interp, members, normalise, run_median,
                               zone_traces)


def test_grid_pitch_snake():
    x, y = testdata.snake_grid(nx=6, ny=4, pitch=0.25)
    px, py = grid_pitch(x, y)
    assert np.isclose(px, 0.25) and np.isclose(py, 0.25)
    # turnaround points between rows must not corrupt the x pitch
    x2 = np.insert(x, 6, x[5]); y2 = np.insert(y, 6, y[5] + 0.12)
    assert np.isclose(grid_pitch(x2, y2)[0], 0.25)


def test_members_rect_paint_erase():
    x, y = testdata.snake_grid(nx=6, ny=4, pitch=0.25, x0=0.0, y0=0.0)
    pitch = (0.25, 0.25)
    z = Zone(id=1, label="a", rects=[[0.0, 0.5, 0.0, 0.25]])          # touches columns 0..2, rows 0..1
    m = members(z, x, y, pitch)
    assert sorted(x[m].round(2).tolist()) == sorted([0.0, 0.25, 0.5] * 2)
    assert len(m) == 6
    # the 0.45-pitch tolerance: a rectangle ending 0.1 mm short of a pixel centre still takes it
    z2 = Zone(id=2, label="b", rects=[[0.0, 0.15, 0.0, 0.0]])
    assert len(members(z2, x, y, pitch)) == 2                        # x = 0 and x = 0.25
    # erase rectangle and paint / erase squares
    z.erects = [[0.5, 0.5, 0.0, 0.25]]
    assert len(members(z, x, y, pitch)) == 4
    z.paint = [[1.25, 0.75, 0.25, 0.25]]
    m = members(z, x, y, pitch)
    assert len(m) == 5 and any(np.isclose(x[m], 1.25) & np.isclose(y[m], 0.75))
    z.erase = [[1.25, 0.75, 0.25, 0.25]]
    assert len(members(z, x, y, pitch)) == 4
    assert members(Zone(id=3, label="empty"), x, y, pitch).size == 0


def test_a_rectangle_takes_edge_pixels_unlike_johns_strict_polygon():
    """rect+ (the browser zone tool's rule) takes a pixel whose square touches the rectangle; John Bussey's GUI turned
    a rectangle into a polygon and kept only the centres inside (AURORA's lasso+ / polygon rule). The manuals say so."""
    from pathlib import Path
    x, y = testdata.snake_grid(nx=6, ny=4, pitch=0.25, x0=0.0, y0=0.0)
    x0, x1, y0, y1 = 0.1, 0.65, 0.1, 0.4
    n_rect = len(members(Zone(id=1, label="r", rects=[[x0, x1, y0, y1]]), x, y, (0.25, 0.25)))
    n_poly = len(members(Zone(id=2, label="p", polys=[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]), x, y, (0.25, 0.25)))
    assert (n_rect, n_poly) == (12, 2)                       # 4 x 3 pixels against 2 x 1
    docs = Path(__file__).resolve().parent.parent / "aurora" / "resources" / "docs"
    manual = " ".join((docs / "MANUAL.md").read_text(encoding="utf-8").split())
    guide = " ".join((docs / "USER_GUIDE.md").read_text(encoding="utf-8").split())
    assert ("It differs from John Bussey's GUI, which turned a rectangle into a polygon and took only the pixels whose "
            "centre lies inside") in manual
    assert "Unlike John Bussey's GUI, a rectangle also takes the edge pixels whose square it touches" in guide


def test_run_median_and_despike():
    y = np.array([1.0, 1.0, 1.0, 1.0, 8.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    assert run_median(y, 5)[4] == 1.0
    out, spikes = despike(y + np.linspace(0, 0.01, 10), 6)
    assert spikes == [4]
    assert np.isclose(out[4], (out[3] + out[5]) / 2, atol=1e-9)
    flat, sp = despike(np.ones(10), 6)
    assert sp == [] and np.array_equal(flat, np.ones(10))           # MAD = 0 -> nothing removed
    yn = y + np.linspace(0, 0.01, 10); yn[6] = np.nan
    out2, sp2 = despike(yn, 6)
    assert sp2 == [4] and np.isnan(out2[6]) and np.isfinite(out2[4])


# ----------------------------------------------------------------------------- strokes (zone drawing, stage A)
TRIANGLE = [[-0.6, 4.9], [0.35, 4.9], [-0.6, 5.6]]
TRIANGLE_MEMBERS = [0, 1, 2, 6, 7, 8]           # the 4 x 3 snake grid, pitch 0.25 mm from (-0.5, 5.0)


def _grid():
    x, y = testdata.snake_grid()
    return x, y, (0.25, 0.25)


def _m(z, x, y, pitch):
    return sorted(members(z, x, y, pitch).tolist())


def test_stroke_geometry_core():
    """apply_stroke on the 4 x 3 snake grid for rectangles, brushes, lassos and clicks, in Draw and Erase mode."""
    from aurora.core.zones import Stroke, apply_stroke, brush_squares, interpolate_path, stroke_covers, stroke_zone
    x, y, pitch = _grid()
    z = Zone(id=1, label="z")
    apply_stroke(z, Stroke(shape="rect", rect=(-0.5, -0.25, 5.0, 5.25)), x, y, pitch)
    assert z.rects == [[-0.5, -0.25, 5.0, 5.25]] and _m(z, x, y, pitch) == [0, 1, 6, 7]
    apply_stroke(z, Stroke(shape="rect", erase=True, rect=(-0.5, -0.5, 5.0, 5.0)), x, y, pitch)
    assert z.erects == [[-0.5, -0.5, 5.0, 5.0]] and _m(z, x, y, pitch) == [1, 6, 7]
    # brush: n x n squares round the nearest point (an even n reaches to the + side), the erase squares there go
    assert brush_squares(x, y, pitch, 0.01, 4.98, 2) == [[0.0, 5.0, 0.25, 0.25], [0.0, 5.25, 0.25, 0.25],
                                                         [0.25, 5.0, 0.25, 0.25], [0.25, 5.25, 0.25, 0.25]]
    assert brush_squares(np.array([]), np.array([]), pitch, 0.0, 0.0) == []
    assert brush_squares(x, y, pitch, -0.5, 4.0) == [] and len(brush_squares(x, y, pitch, -0.5, 4.0, 7)) == 49   # reach
    apply_stroke(z, Stroke(shape="brush", path=[[0.01, 4.98]], brush_n=2), x, y, pitch)
    assert _m(z, x, y, pitch) == [1, 2, 3, 4, 5, 6, 7]
    apply_stroke(z, Stroke(shape="brush", erase=True, path=[[0.24, 5.26]]), x, y, pitch)
    assert len(z.erase) == 1 and _m(z, x, y, pitch) == [1, 2, 3, 5, 6, 7]
    apply_stroke(z, Stroke(shape="brush", path=[[0.25, 5.25]]), x, y, pitch)
    assert z.erase == [] and _m(z, x, y, pitch) == [1, 2, 3, 4, 5, 6, 7]       # painting over an erased point
    # a click: the point rectangle (or one brush square)
    apply_stroke(z, Stroke(shape="pixel", rect=(0.25, 0.25, 5.5, 5.5)), x, y, pitch)
    assert z.rects[-1] == [0.25, 0.25, 5.5, 5.5] and 11 in _m(z, x, y, pitch)
    z2 = Zone(id=2, label="b")
    apply_stroke(z2, Stroke(shape="pixel", via="brush", start=(0.24, 5.49), path=[[0.24, 5.49]]), x, y, pitch)
    assert z2.paint == [[0.25, 5.5, 0.25, 0.25]] and _m(z2, x, y, pitch) == [11]
    # lasso: the centres inside (John Bussey's rule), and as an erase outline (epolys)
    z3 = Zone(id=3, label="t")
    apply_stroke(z3, Stroke(shape="lasso", path=TRIANGLE), x, y, pitch)
    assert z3.polys == [TRIANGLE] and _m(z3, x, y, pitch) == TRIANGLE_MEMBERS
    z4 = Zone(id=4, label="all", rects=[[-0.5, 0.25, 5.0, 5.5]])
    apply_stroke(z4, Stroke(shape="lasso", erase=True, path=TRIANGLE), x, y, pitch)
    assert z4.epolys == [TRIANGLE] and _m(z4, x, y, pitch) == [3, 4, 5, 9, 10, 11]
    assert stroke_covers(Stroke(shape="polygon", path=TRIANGLE), x, y, pitch).tolist() == TRIANGLE_MEMBERS
    # the provisional zone is a copy: the zone itself never changes during a drag
    before = z4.to_json()
    d = stroke_zone(z4, Stroke(shape="rect", rect=(-0.5, 0.25, 5.0, 5.5)), x, y, pitch)
    assert z4.to_json() == before and _m(d, x, y, pitch) == list(range(12))
    assert _m(stroke_zone(None, Stroke(shape="rect", rect=(-0.5, -0.5, 5.0, 5.0)), x, y, pitch), x, y, pitch) == [0]
    # a fast brush: two mouse events 5 pitches apart paint every point in between
    path = interpolate_path((0.0, 0.0), (1.25, 0.0), pitch)
    assert len(path) == 10 and path[-1] == [1.25, 0.0] and max(np.diff([0.0] + [p[0] for p in path])) <= 0.125 + 1e-12
    assert interpolate_path((0.0, 0.0), (1.0, 1.0), None) == [[1.0, 1.0]]
    xw, yw = testdata.snake_grid(nx=12, ny=2, pitch=0.25, x0=0.0, y0=0.0)
    st = Stroke(shape="brush", path=[[0.0, 0.0]] + interpolate_path((0.0, 0.0), (1.25, 0.0), pitch))
    z5 = Zone(id=5, label="fast")
    apply_stroke(z5, st, xw, yw, pitch)
    assert sorted(np.round(xw[members(z5, xw, yw, pitch)], 3).tolist()) == [0.0, 0.25, 0.5, 0.75, 1.0, 1.25]


def test_add_after_erase_wins_on_the_map_it_was_drawn_on():
    """Z12: a Draw stroke over erased points takes them back (the newest stroke wins), in the same geometry edit;
    the erase items it touches become erase squares on the points it does not cover."""
    from aurora.core.zones import Stroke, apply_stroke
    x, y, pitch = _grid()
    full = [-0.5, 0.25, 5.0, 5.5]
    z = Zone(id=1, label="z", rects=[list(full)])
    middle = (-0.25, 0.0, 5.0, 5.5)
    apply_stroke(z, Stroke(shape="rect", erase=True, rect=middle), x, y, pitch)
    assert len(_m(z, x, y, pitch)) == 6
    apply_stroke(z, Stroke(shape="rect", rect=middle), x, y, pitch)       # rect- then rect+ over the same rectangle
    assert _m(z, x, y, pitch) == list(range(12)) and z.erects == [] and z.erase == []
    # a Draw stroke over part of an erased area: the rest stays erased (erase squares at this map's pitch)
    apply_stroke(z, Stroke(shape="rect", erase=True, rect=tuple(full)), x, y, pitch)
    assert _m(z, x, y, pitch) == []
    apply_stroke(z, Stroke(shape="rect", rect=(-0.5, -0.5, 5.0, 5.5)), x, y, pitch)  # the first column back
    assert _m(z, x, y, pitch) == [0, 7, 8] and z.erects == [] and len(z.erase) == 9
    # an erase outline under a lasso, and the erase squares of a brush
    z2 = Zone(id=2, label="p", rects=[list(full)], epolys=[TRIANGLE])
    apply_stroke(z2, Stroke(shape="rect", rect=(-0.5, -0.5, 5.0, 5.0)), x, y, pitch)
    assert 0 in _m(z2, x, y, pitch) and z2.epolys == [] and _m(z2, x, y, pitch) == [0, 3, 4, 5, 9, 10, 11]
    assert len(z2.erase) == 5                                               # 1, 2, 6, 7, 8 stay erased
    # without the map's points nothing is replaced (geometry only)
    z3 = Zone(id=3, label="q", rects=[list(full)], erects=[list(middle)])
    apply_stroke(z3, Stroke(shape="rect", rect=middle))
    assert z3.erects == [list(middle)]


def test_erase_polygons_round_trip_copy_and_export(tmp_path):
    from aurora.core.io.config import HolderZones, ZonesFile
    from aurora.core.zones import export_json, export_ready, geometry, set_geometry, shape_text
    x, y, pitch = _grid()
    z = Zone(id=1, label="z", rects=[[-0.5, 0.25, 5.0, 5.5]], epolys=[TRIANGLE])
    assert shape_text(z) == "1 rectangle, minus 1 erased outline"
    assert shape_text(Zone(id=2, label="p", polys=[TRIANGLE], epolys=[TRIANGLE, TRIANGLE])) == \
        "polygon with 3 vertices, minus 2 erased outlines"
    back = Zone.from_json(z.to_json())
    assert back.epolys == [TRIANGLE] and "epolys" not in back.extra
    assert "epolys" not in Zone(id=3, label="e").to_json()                 # written only when there is one
    seen = Zone.from_json({"id": 4, "label": "s", "epolys": []})
    assert seen.to_json()["epolys"] == []                                   # kept as it was read
    with pytest.raises(ValueError, match="erase polygon"):
        Zone.from_json({"id": 5, "label": "bad", "epolys": [[[0, 0], [1]]]})
    # Copy / paste geometry carries the erase outlines
    g = geometry(z)
    assert g["epolys"] == [TRIANGLE]
    moved = Zone(id=6, label="m")
    set_geometry(moved, g, dx=1.0, dy=-1.0)
    assert moved.epolys == [[[p[0] + 1.0, p[1] - 1.0] for p in TRIANGLE]]
    # the session file (ZonesFile)
    zf = ZonesFile(project="T")
    zf.holders["N5"] = HolderZones(holder="N5", day=testdata.DAY, survey=testdata.SURVEY, zones=[z])
    path = tmp_path / "session.json"
    zf.save(path)
    assert ZonesFile.load(path).holders["N5"].zones[0].epolys == [TRIANGLE]
    # the HTML zone tool knows rectangles and squares only: the erase outline reaches it as erase squares, on every
    # stack, whatever its pitch
    want = members(z, x, y, pitch).tolist()
    assert want == [3, 4, 5, 9, 10, 11]
    xc, yc = testdata.snake_grid(nx=3, ny=2, pitch=0.5, x0=-0.55, y0=4.95)
    exp = export_ready(z, {"fine": (x, y, pitch), "coarse": (xc, yc, (0.5, 0.5))})
    assert exp["epolys"] == [TRIANGLE]
    tool = Zone(id=1, label="t", rects=exp["rects"], erects=exp["erects"], paint=exp["paint"], erase=exp["erase"])
    assert members(tool, x, y, pitch).tolist() == want
    assert members(tool, xc, yc, (0.5, 0.5)).tolist() == members(z, xc, yc, (0.5, 0.5)).tolist()
    out = export_json(zf, {"fine": (x, y, pitch)})
    assert out["holders"]["N5"]["zones"][0]["erase"]


# reference copies of the zone tool's loops (aurora.core.spectra before they were vectorised), kept for equality
def _run_median_ref(y, w=5):
    y = np.asarray(y, float); n = len(y); h = w // 2
    out = np.full(n, np.nan)
    for i in range(n):
        seg = y[max(0, i - h): i + h + 1]
        seg = seg[np.isfinite(seg)]
        if seg.size:
            out[i] = np.sort(seg)[seg.size // 2]
    return out


def _hampel_hits_ref(y, med, res, g, k):
    out = []
    for i in range(len(y)):
        if not np.isfinite(res[i]):
            continue
        seg = y[max(0, i - 2): i + 3]
        seg = seg[np.isfinite(seg)]
        loc = float(np.sort(np.abs(seg - med[i]))[seg.size // 2]) if seg.size else 0.0
        s = max(loc, g)
        if s > 0 and abs(res[i]) > k * 1.4826 * s:
            out.append(i)
    return out


def _nanmedian_ref(a, axis=None):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return float(np.nanmedian(a)) if axis is None else np.nanmedian(a, axis=axis)


def _references(monkeypatch):
    from aurora.core import spectra
    monkeypatch.setattr(spectra, "run_median", _run_median_ref)
    monkeypatch.setattr(spectra, "_hampel_hits", _hampel_hits_ref)
    monkeypatch.setattr(spectra, "_nanmedian", _nanmedian_ref)


def _traces_of_the_fake_stack(root):
    root = testdata.make_fake_project(root)
    proj = Project.discover(root)
    sd = StackData.load(proj, proj.find(testdata.STACK))
    s = sd.sum_over(np.arange(sd.n_pix))
    det = s["det"][sd.labels[0]]
    return [det[:, k] for k in range(det.shape[1])] + [s["i0"], s["tey"]]


def test_spectra_vectorised_equal(monkeypatch, tmp_path):
    """run_median, hampel's test and the medians of despike_coherent are vectorised: the same values as the zone
    tool's loops, bit for bit, on 300 random traces with NaN (and inf) and on the fake stack's traces. Speed, as
    ratios in the same process (robust to the machine's load): hampel on four 2000-point traces at least 10 times
    faster than the loops (on 341 points the fixed cost of a few numpy calls blurs the ratio under load); the whole
    coherent build_traces of 4 x 341 energies at least 5 times (it also runs the coherence test, the noise floor and
    the normalisation, which were never loops)."""
    import time

    from aurora.core import spectra
    from aurora.core.zones import ZoneSums
    rng = np.random.default_rng(1)
    traces = []
    for trial in range(300):
        n = int(rng.integers(0, 400))
        y = rng.poisson(50, n).astype(float) + 200 * (rng.random(n) < 0.03)
        y[rng.random(n) < 0.05] = np.nan
        if trial % 7 == 0 and n:
            y[rng.random(n) < 0.02] = np.inf
        traces.append(y)
    traces += _traces_of_the_fake_stack(tmp_path / "bt")
    seen = []
    real_hits = spectra._hampel_hits

    def spy(y, med, res, g, k):
        seen.append((y, med, res, g, k))
        return real_hits(y, med, res, g, k)
    monkeypatch.setattr(spectra, "_hampel_hits", spy)
    for y in traces:
        for w in (1, 3, 4, 5, 7):
            assert np.array_equal(spectra.run_median(y, w), _run_median_ref(y, w), equal_nan=True)
        for k in (3.0, 6, 10.0):
            got = spectra.hampel(y, k)
            if len(y):
                yy, med, res, g, kk = seen[-1]
                assert got == _hampel_hits_ref(yy, med, res, g, kk)
                assert real_hits(yy, med, res, 0.0, kk) == _hampel_hits_ref(yy, med, res, 0.0, kk)
        m = rng.normal(size=(len(y), 3)); m[rng.random(m.shape) < 0.3] = np.nan
        a, b = _nanmedian_ref(y), spectra._nanmedian(y)
        assert (np.isnan(a) and np.isnan(b)) or a == b
        assert np.array_equal(_nanmedian_ref(m, axis=1), spectra._nanmedian(m, axis=1), equal_nan=True)
    monkeypatch.setattr(spectra, "_hampel_hits", real_hits)
    # despike_coherent: the same spikes and values with the vectorised code and with the reference loops
    E = np.linspace(670.0, 720.0, 341)
    edge = 1.0 / (1.0 + np.exp(-(E - 690.0) / 1.5))
    det = np.stack([rng.poisson(3000 * (0.3 + edge) * (1 + 0.1 * k)) for k in range(4)], axis=1).astype(float)
    det[50, 1] += 4000                                       # a loose-electron spike on SDD2
    det[200, :] *= 1.3                                       # a feature of all four detectors: kept
    det[120, 3] = np.nan
    dets = [det[:, k] for k in range(4)]
    new = spectra.despike_coherent(dets, 6.0)
    sums = ZoneSums(E=E, n_pix=36, i0=2.5e4 * (1 + 0.01 * rng.standard_normal(341)),
                    tey=1e3 * (0.5 + edge) + rng.standard_normal(341), det={"F Kα": det}, main="F Kα", edge_ev=690.0)
    st = ScanSettings(line="F Kα", despike=True, despike_mode="coherent")
    t_new = spectra.build_traces(sums, st)

    def best(f, n):
        b = float("inf")
        for _ in range(n):
            t0 = time.perf_counter(); f(); b = min(b, time.perf_counter() - t0)
        return b
    long = [rng.poisson(3000, 2000).astype(float) + 4000 * (rng.random(2000) < 0.01) for _ in range(4)]

    def hampels():
        for d in long:
            spectra.hampel(d, 6.0)
    fast = best(lambda: spectra.build_traces(sums, st), 5)
    fast_h = best(hampels, 7)
    with monkeypatch.context() as mp:
        _references(mp)
        old = spectra.despike_coherent(dets, 6.0)
        t_old = spectra.build_traces(sums, st)
        slow = best(lambda: spectra.build_traces(sums, st), 3)
        slow_h = best(hampels, 3)
    assert new[1] == old[1] and 50 in new[1][1] and 200 not in new[1][1]
    assert all(np.array_equal(p, q, equal_nan=True) for p, q in zip(new[0], old[0]))
    assert t_new.spikes == t_old.spikes and np.array_equal(t_new.sum, t_old.sum, equal_nan=True)
    assert slow_h / fast_h >= 10, f"hampel x 4: {fast_h * 1e3:.1f} ms against {slow_h * 1e3:.1f} ms with the loops"
    assert slow / fast >= 5, f"build_traces (coherent): {fast * 1e3:.1f} ms against {slow * 1e3:.1f} ms with the loops"


def test_survey_keeps_its_per_point_spectra(tmp_path):
    from aurora.core.io.raw import read_point_dir
    root = testdata.make_fake_project(tmp_path / "bt", n_holders=3)
    proj = Project.discover(root)
    scan = proj.find(testdata.SURVEY)
    sv = SurveyData.load(scan, proj.roi_bins)
    assert sv.has_spectra and sv.per_point.shape == (48, 4, 256) and sv.per_point.dtype == np.float32
    raw = read_point_dir(scan.path)
    pix = np.array([0, 5, 7, 30])
    want = np.stack([s[pix].sum(axis=0) for s in raw.sdd]).astype(float)
    assert np.array_equal(sv.zone_spectrum(pix), want)
    assert np.allclose(sv.zone_spectrum(pix), sv.per_point[pix].sum(axis=0))
    assert np.array_equal(sv.zone_spectrum(np.arange(sv.n)), sv.spec.astype(float))      # the whole plate
    assert np.array_equal(sv.zone_spectrum([]), np.zeros((4, 256)))
    sv.release_spectra()
    with pytest.raises(ValueError, match="not in memory"):
        sv.zone_spectrum(pix)
    assert sv.reload_spectra() and np.array_equal(sv.zone_spectrum(pix), want)
    assert SurveyData.load(scan, proj.roi_bins, keep_spectra=False).per_point is None


def test_normalise_modes():
    E = np.linspace(680, 730, 101)
    i0 = np.full(101, 2.0)
    y = 2.0 * (0.5 + 0.3 * (E - 680) / 50 + 2.0 * (E >= 700))       # sloped pre-edge, step of 2 at 700 eV
    raw = normalise(E, y, "raw", i0)
    assert np.array_equal(raw.y, y) and raw.scale == 1.0
    assert np.allclose(normalise(E, y, "i0", i0).y, y / 2)
    pre = normalise(E, y, "i0pre", i0)
    assert pre.pre == (680.0, 687.5) and pre.post == (720.0, 730.0)
    assert np.allclose(pre.y[E < 700], 0, atol=1e-9)                # the pre-edge line is removed exactly
    jump = normalise(E, y, "i0jump", i0)
    assert np.isclose(jump.y[(E >= 720)].mean(), 1.0)
    assert np.isclose(jump.scale, 2.0)
    area = normalise(E, y, "area", i0)
    assert np.isclose(np.trapezoid(area.y, E), 1.0)
    mx = normalise(E, y, "max", i0)
    assert np.isclose(np.nanmax(mx.y), 1.0)
    explicit = normalise(E, y, "i0jump", i0, pre=[680, 690], post=[710, 730])
    assert explicit.pre == (680.0, 690.0) and explicit.post == (710.0, 730.0)
    assert np.isnan(normalise(E, y, "i0", None).y).all()


def _uf4_like():
    """The real F K grid (680-685 eV in 0.5 eV, 685-700 in 0.1, 700-730 in 0.2) and a UF4-like PFY / I0: edge at
    686.6 eV (E0 of 141456_UF4 is 686.66), white line at 691.8 eV, a sloped background. Synthetic."""
    E = np.r_[np.arange(680.0, 685.0, 0.5), np.arange(685.0, 700.0, 0.1), np.arange(700.0, 730.0001, 0.2)]
    y = (30 + 0.4 * (E - 680) + 250 / (1 + np.exp(-(E - 686.6) / 0.8))
         + 150 * np.exp(-0.5 * ((E - 691.8) / 1.2) ** 2) + 80 / (1 + np.exp(-(E - 715) / 4)))
    return E, y


def test_automatic_pre_window_stays_below_the_edge():
    """The zone tool's first 15 % (680-687.5 eV) reached the UF4 / UF5 edges at 686.5-686.8 eV, and the white line
    became the minimum of the Easy trace (-0.27 on the real 141456_UF4; larch's pre_edge gives +1.49 on the same
    arrays). The automatic window now ends 5 eV below E0 and says so."""
    E, y = _uf4_like()
    i0 = np.full(E.size, 2.0)
    n = normalise(E, y, "i0jump", i0, edge_ev=696.7)
    assert 681.0 < n.pre[1] <= 686.6 - 5.0 + 0.1 and n.pre[0] == 680.0
    wl = np.argmin(np.abs(E - 691.8))
    assert n.y[wl] == np.nanmax(n.y) and n.y[wl] > 1.0            # the white line is the maximum, above the jump
    assert np.abs(n.y[E < 684]).max() < 0.05                       # the pre-edge sits at 0
    assert "ends 5 eV below E0 686.6 eV" in n.pre_note
    # an edge far above the start keeps the zone tool's window, without a note
    late = normalise(E, np.interp(E - 10, E, y), "i0jump", i0, edge_ev=696.7)
    assert np.allclose(late.pre, (680.0, 687.5)) and late.pre_note == ""
    # a scan that starts less than 5 eV below E0: a constant level from the first energy, said
    short = normalise(E[E >= 683.0], y[E >= 683.0], "i0jump", i0[E >= 683.0], edge_ev=696.7)
    assert short.pre == (683.0, 683.0) and "the scan starts" in short.pre_note and "first energy" in short.pre_note
    # build_traces: one window for the sum, the SDD traces and TEY, and the note in the zone's notes
    from aurora.core.spectra import build_traces
    from aurora.core.zones import ZoneSums
    det = np.column_stack([g * y for g in (1.0, 0.8, 1.2, 0.6)])
    sums = ZoneSums(E=E, n_pix=4, i0=np.full(E.size, 10.0), tey=0.01 * y, det={"F Kα": det}, main="F Kα", edge_ev=696.7)
    t = build_traces(sums, ScanSettings(line="F Kα", despike=False))
    assert t.pre == n.pre and any("ends 5 eV below E0" in s for s in t.notes)
    assert t.sum[wl] == np.nanmax(t.sum) and np.allclose(t.tey, t.sum)    # TEY is proportional here: same window
    assert all(int(np.nanargmax(d)) == wl for d in t.det)


def test_negative_edge_jump_keeps_the_orientation():
    """A signal that falls across the edge (the real U N45 IPFY scans) was divided by its negative jump and shown as a
    rising edge. larch's pre_edge divides by |step|; so does every mode now, with a note."""
    E = np.linspace(660, 740, 321)
    y = 2.0 - 1.0 / (1 + np.exp(-(E - 690) / 1.0))
    n = normalise(E, y, "i0jump", np.ones_like(E))
    assert np.isclose(n.y[-10], -1.0, atol=1e-6) and n.scale > 0 and "edge step negative" in n.step_note
    a = normalise(E, y - 3.0, "area", np.ones_like(E))
    assert a.scale > 0 and np.all(a.y < 0) and "area negative" in a.step_note
    up = normalise(E, 3.0 - y, "i0jump", np.ones_like(E))
    assert np.isclose(up.y[-10], 1.0, atol=1e-6) and up.step_note == ""


def test_i0_score_and_good_curve():
    E = np.linspace(680, 730, 51)
    good = 2.5 + 0.01 * np.sin(E)
    s = i0_score(good)
    assert s.nonpos == 0 and s.max_step < 0.01 and s.rough < 0.01 and s.looks_good(2.4)
    bad = good.copy(); bad[20] = 0.0
    s2 = i0_score(bad)
    assert s2.nonpos == 1 and s2.max_step > 0.9 and not s2.looks_good(2.4)
    curve = good_i0_curve(E, [(E, good), (E + 1000, good)])          # the second curve does not overlap
    assert curve is not None and np.allclose(curve, good)
    assert good_i0_curve(E, [(E + 1000, good)]) is None
    assert np.isnan(interp(E, good, 600))


def test_stack_survey_and_zone_traces(fake_project):
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    sd = StackData.load(proj, scan)                                  # no cache: reduced from raw, cached by AURORA
    assert sd.source == "reduced from raw" and sd.labels == ["total XRF"] and sd.main == "total XRF"
    assert sd.n_energies == 5 and sd.n_pix == 12 and sd.valid.all()
    assert sd.edge == ("F", "K") and 680 < sd.edge_ev < 700
    assert np.isclose(sd.pitch[0], 0.25) and np.isclose(sd.pitch[1], 0.25)
    sd2 = StackData.load(proj, scan, allow_reduce=False)
    assert sd2.source.startswith("aurora cache") and np.array_equal(sd2.roi_pix_det, sd.roi_pix_det)
    x0, x1, y0, y1 = sd.bbox
    z = Zone(id=1, label="z", rects=[[x0, x1, y0, y1]])
    pix = members(z, sd.x, sd.y, sd.pitch)
    assert len(pix) == 12
    st = ScanSettings(fluo_norm="i0jump", tey_norm="i0", despike=True, k=6)
    t = zone_traces(sd, pix, st)
    assert t is not None and t.n_pix == 12 and t.line == "total XRF"
    assert t.sum is not None and np.isfinite(t.sum).all()
    # the edge jump has size 1; the demo's total XRF / I0 is flat within its noise, so its sign is the noise's
    assert np.isclose(abs(np.nanmean(t.sum[t.E >= t.post[0]])), 1.0)
    # the ticked detectors AVERAGE to the sum trace (per-SDD scale = sum scale / number ticked)
    assert all(d is not None for d in t.det) and np.allclose(np.mean([d for d in t.det], axis=0), t.sum, rtol=1e-6, atol=1e-9)
    assert np.isfinite(t.tey).all() and t.i0_used is t.i0
    st2 = ScanSettings(sdd=[True, False, False, False], fluo_norm="raw", despike=False)
    t2 = zone_traces(sd, pix, st2)
    assert np.array_equal(t2.sum, t2.det_raw[0]) and all(v == [] for v in t2.spikes.values())
    assert zone_traces(sd, [], st) is None
    sv = SurveyData.load(proj.find(testdata.SURVEY), proj.roi_bins)
    assert sv.n == 48 and sv.lines == [] and sv.channels == ["total", "TEY", "I0"] and sv.i0_usable
    v = sv.values("total", per_i0=True, log10=True)
    assert v.shape == (48,) and np.isfinite(v).all()
    g = sv.grid(v)
    assert g.img.shape == (6, 8)


def test_lines_helpers():
    assert line_of_mode("PFY F Kα") == "F Kα" and line_of_mode("IPFY O Kα") == "O Kα" and line_of_mode("TEY") is None
    assert main_line(["O Kα", "F Kα", "total XRF"], ("F", "K")) == "F Kα"
    assert main_line(["O Kα", "F Kα", "total XRF"], ("U", "N45"), "IPFY O Kα") == "O Kα"
    assert main_line(["O Kα", "total XRF"], ("U", "N45")) == "O Kα"
    assert 680 < edge_energy("F", "K") < 700 and 1400 < edge_energy("Se", "L3L2") < 1450
    assert edge_energy("Xx", "K") is None
