"""WP5 I31: E0 choices (smoothed derivative, half step, tabulated, fixed, locked to another row), shown in the table
and the diagnostics, saved with the config, with a calm note when E0 lies far from the tabulated edge."""
import numpy as np
import pytest

from aurora.core.compare import (E0_SEARCH_EV, Spectrum, TraceRequest, derivative_e0, load_config, process,
                                 save_config)


def _edge(E, e0=696.0, width=1.0):
    return 1 / (1 + np.exp(-(E - e0) / width))


def test_seeded_noise_probe_deriv():
    """The scratch probe (synthetic edge at 696 eV, 1 eV wide, 0.2 eV step, 2 % noise, 100 draws): the unsmoothed
    derivative scattered by about 4.4 eV, all of it from 3 draws whose maximum sat at the first or last point; the
    smoothed one stays below 0.5 eV, and no normalisation is NaN."""
    E = np.linspace(680, 730, 251)
    base = _edge(E)
    e0s, nan_rows = [], 0
    for s in range(100):
        y = base + np.random.default_rng(s).normal(0, 0.02, E.size)
        r = process(Spectrum("a", "x", E, y), TraceRequest("a", "x", e0_mode="deriv"))
        e0s.append(r.e0)
        nan_rows += int(np.isnan(r.y).any())
    assert np.std(e0s) < 0.5 and abs(np.mean(e0s) - 696.0) < 0.3 and nan_rows == 0
    raw = [derivative_e0(E, base + np.random.default_rng(s).normal(0, 0.02, E.size), width_ev=0) for s in range(100)]
    # the plain gradient, ends excluded (0.7 eV): still twice the scatter of the smoothed one
    assert np.std(raw) > 1.5 * np.std(e0s) and min(raw) > E[0] and max(raw) < E[-1]


def test_half_step_ignores_a_sharp_pre_edge_peak():
    E = np.linspace(680, 730, 251)
    y = _edge(E) + 0.35 * np.exp(-0.5 * ((E - 690) / 0.4) ** 2)
    rd = process(Spectrum("a", "x", E, y), TraceRequest("a", "x", e0_mode="deriv"))
    rh = process(Spectrum("a", "x", E, y), TraceRequest("a", "x", e0_mode="half"))
    assert abs(rd.e0 - 690) < 1.5                                  # the derivative is caught by the pre-edge peak
    assert abs(rh.e0 - 696) < 0.6                                  # the half step is not


def test_fixed_and_table_are_exact():
    E = np.linspace(680, 730, 251)
    s = Spectrum("a", "x", E, _edge(E))
    assert process(s, TraceRequest("a", "x", e0_mode="fixed", e0=693.25)).e0 == 693.25
    assert process(s, TraceRequest("a", "x", e0_mode="table"), edge_ev=696.7).e0 == 696.7
    from aurora.core.lines import edge_energy
    tab = edge_energy("F", "K")
    assert process(s, TraceRequest("a", "x", e0_mode="table"), edge_ev=tab).e0 == tab
    # 'table' without a known edge falls back to the derivative and says so
    r = process(s, TraceRequest("a", "x", e0_mode="table"))
    assert abs(r.e0 - 696) < 0.5 and "no tabulated edge" in r.e0_note


def test_search_window_around_the_tabulated_edge():
    E = np.linspace(640, 740, 501)
    y = _edge(E, 696.0) + 2.0 * _edge(E, 650.0, 0.5)               # a stronger edge of another element far below
    free = process(Spectrum("a", "x", E, y), TraceRequest("a", "x"))
    near = process(Spectrum("a", "x", E, y), TraceRequest("a", "x"), edge_ev=696.7)
    assert abs(free.e0 - 650) < 1 and abs(near.e0 - 696) < 0.5 and 696.7 - E0_SEARCH_EV < near.e0


def test_e0_is_never_a_data_or_window_end():
    """A start-up transient of the first points (the real TEY traces of my_beamtime) and a stronger edge just above
    the search window both used to give E0 at an end (680.0 / 716.6 eV) and an all-NaN normalisation."""
    E = np.linspace(680, 730, 251)
    start = _edge(E, 690.0)
    start[:2] = (-1.0, -0.5)                                       # the detector settling on the first two points
    above = _edge(E, 690.0) + 10 * _edge(E, 722.0, 3.0)            # derivative still rising at 696.7 + 20 eV
    for y in (start, above):
        e0 = derivative_e0(E, y, 1.5, (696.7 - E0_SEARCH_EV, 696.7 + E0_SEARCH_EV))
        assert abs(e0 - 690.0) < 0.3, e0
        r = process(Spectrum("a", "x", E, y), TraceRequest("a", "x"), edge_ev=696.7)
        assert abs(r.e0 - 690.0) < 0.3 and np.isfinite(r.y).all()
        assert y is above or not r.note                            # (above: the post line climbs the second edge)


def test_far_from_the_tabulated_edge_gives_a_calm_note():
    E = np.linspace(640, 740, 501)
    s = Spectrum("a", "x", E, _edge(E, 670.0))
    r = process(s, TraceRequest("a", "x", e0_mode="fixed", e0=670.0), edge_ev=696.7)
    assert "from the tabulated edge" in r.e0_note and "pre-edge window width (15 eV)" in r.e0_note
    ok = process(s, TraceRequest("a", "x", e0_mode="fixed", e0=690.0), edge_ev=696.7)
    assert ok.e0_note == ""


def test_row_mode_gives_identical_windows():
    from aurora.app.library import Library
    E = np.linspace(670, 730, 301)
    lib = Library()
    lib.add_spectra([Spectrum("A", "x", E, _edge(E, 694.0), "a"), Spectrum("B", "x", E, _edge(E, 697.5), "b"),
                     Spectrum("C", "x", E, _edge(E, 699.0), "c")], show_signals=("x",))
    for r in lib.requests[1:]:
        r.e0_mode = "row:A [x]"
    out = lib.processed()
    e0s = {r.label: nr.e0 for r, _s, nr in out}
    wins = {(nr.pre_window, nr.post_window) for _r, _s, nr in out}
    assert len(set(e0s.values())) == 1 and abs(e0s["A"] - 694) < 0.5 and len(wins) == 1
    # a rename follows the lock; a loop falls back to the derivative instead of recursing
    lib.rename("A", "A2")
    assert lib.requests[1].e0_mode == "row:A2 [x]"
    lib.requests[0].e0_mode = "row:B [x]"
    res = {r.label: nr for r, _s, nr in lib.processed()}
    assert np.isfinite(res["A2"].e0) and np.isfinite(res["B"].e0)


def test_mode_and_value_round_trip(tmp_path):
    reqs = [TraceRequest("A", "x", e0_mode="fixed", e0=692.5, e0_smooth=2.0),
            TraceRequest("B", "x", e0_mode="row:A [x]"), TraceRequest("C", "x", e0_mode="half")]
    cfg = tmp_path / "cfg.json"
    save_config(cfg, reqs, {})
    back, _src, _extra = load_config(cfg)
    assert [(r.e0_mode, r.e0, r.e0_smooth) for r in back] == [(r.e0_mode, r.e0, r.e0_smooth) for r in reqs]


def test_e0_in_the_table_and_the_diagnostics(qapp_e0, tmp_path):
    from demo_window import build_demo_window, pump
    from aurora.app import mode
    win = build_demo_window(qapp_e0, tmp_path)
    mode.set_advanced(True)
    c = win.compare
    win.show_perspective("compare")
    c.table.clearSelection()
    c.table.selectRow(0)
    pump(qapp_e0)
    assert c.selected_rows() == [0]
    r = win.library.requests[0]
    nr = win.library.process_row(r)
    assert c.table.item(0, c.col("E0 eV")).text() == f"{nr.e0:.2f}"
    assert f"E0 {nr.e0:.2f} eV" in c.diag.plotItem.titleLabel.text and "derivative" in c.diag.plotItem.titleLabel.text
    assert "tabulated F K edge 696.7 eV" in c.diag_note.text()        # the demo beamtime is an F K project
    # typing a value fixes E0; emptying it returns to the derivative
    c.table.item(0, c.col("E0 eV")).setText("691")
    assert r.e0_mode == "fixed" and r.e0 == 691.0 and c.table.item(0, c.col("E0")).text() == "fixed"
    c.table.item(0, c.col("E0 eV")).setText("")
    assert r.e0_mode == "deriv" and r.e0 is None
    c.table.item(0, c.col("E0")).setText("table")
    assert r.e0_mode == "table" and c.table.item(0, c.col("E0 eV")).text() == "696.70"
    mode.set_advanced(False)
    win.session._save_timer.stop()
    win.close()


@pytest.fixture(scope="module")
def qapp_e0():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])
