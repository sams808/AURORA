"""Spectrum readers, normalisations, peaks and the fits (LCF, PCA, MCR-ALS, ITFA) on synthetic spectra."""
import numpy as np
import pytest

from aurora.core.compare import (TraceRequest, derivative_e0, export_wide_csv, find_reduced_csvs, load_config,
                                 load_spectra, local_peaks, normalise_larch, normalise_legacy, normalise_tey_line,
                                 process, save_config, stack_offsets)
from aurora.core.fit import itfa, lcf, mcr_als, pca, prepare_matrix
from local_paths import john_gui

JOHN = john_gui()                   # John Bussey's GUI folder (tests/local_paths.py), None when absent


def _edge(E, e0=690.0, width=1.0, white=0.6, slope=0.002):
    return 0.2 + slope * (E - E[0]) + 1.0 / (1 + np.exp(-(E - e0) / width)) + white * np.exp(-((E - e0 - 2) / 1.5) ** 2)


def test_readers(tmp_path):
    E = np.linspace(680, 730, 101)
    # AURORA zone CSV
    p = tmp_path / "N5_1_alpha_scan.csv"
    p.write_text("# AURORA zone spectrum\n# zone: 1 alpha (holder N5)\nenergy_eV,I0_zone_sum,I0_used,TEY_raw,TEY_norm,fluo_sum_raw,fluo_sum_norm\n"
                 + "\n".join(f"{e},2,2,{0.1 * i},{0.2 * i},{i},{_edge(E)[i]}" for i, e in enumerate(E)), encoding="utf-8")
    s = load_spectra(p)
    assert [x.signal for x in s] == ["fluo_sum_norm", "fluo_sum_raw", "TEY_norm", "TEY_raw"] and s[0].label == "1 alpha"
    assert np.allclose(s[0].E, E) and np.allclose(s[0].y, _edge(E))
    # John's layout
    q = tmp_path / "X_full_reduced.csv"
    q.write_text("# sample_label: X\nenergy,sdd1,sdd1_roi_lo,sdd1_roi_hi,sdd2,sdd2_roi_lo,sdd2_roi_hi,sdd3,sdd3_roi_lo,sdd3_roi_hi,sdd4,sdd4_roi_lo,sdd4_roi_hi,I0,TEY,sdd_total,sdd_mean,bad_point\n"
                 + "\n".join(f"{e},{10 * v},60,83,{20 * v},60,83,{5 * v},60,83,{5 * v},60,83,2.0,{v},{40 * v},{10 * v},False" for e, v in zip(E, _edge(E))), encoding="utf-8")
    s = load_spectra(q)
    assert [x.signal for x in s] == ["sdd-scaled", "sdd_total", "TEY"] and s[0].label == "X"
    assert np.allclose(s[2].y, _edge(E) / 2.0)                 # TEY / I0
    assert np.isclose(np.nanmax(s[0].y) * 2.0, 4.0)             # four min-max scaled detectors sum to 4 at the maximum (before / I0)
    # generic two-column text
    g = tmp_path / "ref.txt"
    g.write_text("\n".join(f"{e}\t{v}" for e, v in zip(E, _edge(E))))
    s = load_spectra(g)
    assert len(s) == 1 and s[0].signal == "col1" and s[0].label == "ref"
    # folder search prefers full_reduced over absorption
    (tmp_path / "X_absorption.csv").write_text("energy,sdd_total,TEY,I0,bad_point\n680,1,1,1,False\n")
    found = find_reduced_csvs(tmp_path)
    assert q in found and (tmp_path / "X_absorption.csv") not in found


def test_john_reduced_csv_from_legacy_folder():
    folder = JOHN / "gui_outputs" / "reduced" / "BO3_LiSO3F_LR_fluorine_k" if JOHN else None
    if folder is None or not folder.exists():
        pytest.skip("John's legacy outputs not present")
    specs = load_spectra(next(folder.glob("*_full_reduced.csv")))
    assert {s.signal for s in specs} == {"sdd-scaled", "sdd_total", "TEY"}
    assert specs[0].label == "BO3_LiSO3F_LR_fluorine_k" and len(specs[0].E) == 311
    r = normalise_larch(specs[0].E, specs[0].y)
    assert 684 < r.e0 < 695 and np.isfinite(r.y).sum() > 300


def test_normalisations_and_peaks():
    E = np.linspace(660, 740, 401)
    y = 3.0 * _edge(E)
    r = normalise_larch(E, y)
    assert abs(r.e0 - 690) < 1.0 and r.step > 0
    post = (E > r.e0 + 15) & (E < r.e0 + 35)
    assert abs(np.mean(r.y[post]) - 1.0) < 0.05                 # flattened post-edge sits at 1
    assert abs(np.mean(r.y[E < r.e0 - 10])) < 0.05
    r2 = normalise_larch(E, y, flatten=False)
    assert np.allclose(r2.y[E < r.e0], r.y[E < r.e0])
    l = normalise_legacy(E, y, (660, 675), (720, 740))
    assert abs(np.mean(l.y[(E >= 720)]) - 1.0) < 1e-9
    t = normalise_tey_line(E, y)
    assert abs(t.e0 - 690) < 1.0 and np.isfinite(t.y).all()
    bad = normalise_larch(E[:10], y[:10])
    assert np.isnan(bad.y).all() and bad.note
    pk = local_peaks(E, r.y, min_prominence=0.1, min_distance_points=5)
    assert pk and abs(pk[0][0] - 692) < 1.0
    assert derivative_e0(E, np.full(len(E), np.nan)) != derivative_e0(E, y)


def test_requests_offsets_config_export(tmp_path):
    E = np.linspace(680, 730, 51)
    from aurora.core.compare import Spectrum
    sp = Spectrum("a", "fluo", E, _edge(E))
    req = TraceRequest("a", "fluo", shift=0.5, norm="none", scale=2.0)
    r = process(sp, req)
    assert np.allclose(r.y, 2 * sp.y)
    reqs = [TraceRequest("a", "x"), TraceRequest("b", "x", spacing_after=0.3), TraceRequest("c", "x")]
    assert np.allclose(stack_offsets(reqs, 0.6), [0.0, 0.6, 0.9])
    cfg = tmp_path / "compare_config.json"
    save_config(cfg, reqs, {"a": "a.csv"}, {"x_range": [680, 730]})
    back, src, extra = load_config(cfg)
    assert [r.label for r in back] == ["a", "b", "c"] and back[1].spacing_after == 0.3 and src == {"a": "a.csv"} and extra["x_range"] == [680, 730]
    out = tmp_path / "wide.csv"
    export_wide_csv(out, [(reqs[0], E, sp.y), (reqs[1], E[:10], sp.y[:10])])
    lines = out.read_text().splitlines()
    assert lines[0] == "a-energy (eV),a-x,b-energy (eV),b-x" and len(lines) == 52 and lines[-1].endswith(",,")


def test_lcf_recovers_mixture():
    E = np.linspace(680, 730, 201)
    a = _edge(E, 690, 1.0, 0.6); b = _edge(E, 697, 2.0, 0.2); c = _edge(E, 705, 1.5, 0.9)
    rng = np.random.default_rng(0)
    target = 0.6 * a + 0.3 * b + 0.1 * c + rng.normal(0, 0.003, E.size)
    r = lcf((E, target), [(E, a), (E, b), (E, c)], sum_to_one=True, bootstrap=50)
    assert np.allclose(r.coefficients, [0.6, 0.3, 0.1], atol=0.02) and abs(r.total - 1) < 1e-6
    assert r.rmse < 0.01 and r.r_factor < 1e-3 and np.all(r.uncertainties < 0.05)
    r2 = lcf((E, target), [(E, a), (E, b), (E, c)], sum_to_one=False, allow_negative=False, bootstrap=0)
    assert np.allclose(r2.coefficients, [0.6, 0.3, 0.1], atol=0.02) and r2.method == "covariance"
    assert np.all(np.isfinite(r2.uncertainties)) and np.allclose(r2.uncertainties, r2.cov_uncertainties)   # Larch: covariance x sqrt(redchi)
    r3 = lcf((E, target), [(E, a), (E, b)], fit_range=(685, 720), sum_to_one=False, allow_negative=True, bootstrap=0)
    assert r3.E[0] >= 685 and r3.E[-1] <= 720


def test_pca_mcr_itfa():
    E = np.linspace(680, 730, 151)
    s1 = _edge(E, 690, 1.0, 0.6); s2 = _edge(E, 700, 1.5, 0.3)
    fractions = np.array([[1.0, 0.0], [0.7, 0.3], [0.5, 0.5], [0.2, 0.8], [0.0, 1.0], [0.9, 0.1]])
    D = fractions @ np.vstack([s1, s2])
    grid, M = prepare_matrix([(E, d) for d in D])
    assert M.shape == (6, 151)
    p = pca(M)
    assert p.explained[0] > 0.9 and p.cumulative[1] > 0.999 and p.components.shape[1] == 151
    m = mcr_als(M, 2, max_iter=200)
    assert m.converged and m.rmse < 1e-3 and m.C.shape == (6, 2) and m.ST.shape == (2, 151)
    rec = m.C @ m.ST
    assert np.allclose(rec, M, atol=5e-3)
    props = m.proportions
    assert props.shape == (6, 2) and np.allclose(props.sum(axis=1), 100)
    # the pure samples are recovered as pure (one component near 100 %)
    assert props[0].max() > 95 and props[4].max() > 95
    anchored = itfa(M, m, {0: np.array([m.C[0].sum(), 0.0]) if m.C[0, 0] > m.C[0, 1] else np.array([0.0, m.C[0].sum()])})
    assert anchored.rmse < 2e-3 and anchored.C.shape == (6, 2)


def test_derivative_and_difference():
    from aurora.core.compare import Spectrum, derivative, difference
    E = np.linspace(680, 730, 251)
    y = _edge(E)
    d = derivative(E, y)
    assert np.isfinite(d).all() and abs(E[int(np.argmax(d))] - 690) < 1.0        # steepest at the edge (white line nearby)
    d3 = derivative(E, y, smooth_points=5)
    assert np.isfinite(d3).all() and np.nanmax(d3) < np.nanmax(d) + 1e-9
    yn = y.copy(); yn[100] = np.nan
    assert np.isnan(derivative(E, yn)[100]) and np.isfinite(derivative(E, yn)[99])
    a = Spectrum("A", "x", E, y); b = Spectrum("B", "x", E[::2] + 0.3, _edge(E[::2] + 0.3, 695))
    diff = difference(a, b, a.y, b.y)
    assert diff.label == "A − B" and diff.signal == "difference" and len(diff.E) == 251
    assert np.isnan(diff.y[0]) and np.isfinite(diff.y[10:-10]).all()
    assert abs(diff.y[-10] - (a.y[-10] - np.interp(E[-10], b.E, b.y))) < 1e-9
