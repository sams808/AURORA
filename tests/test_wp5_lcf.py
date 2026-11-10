"""WP5 I33: weighted LCF with chi-square, reduced chi-square and the R-factor, uncertainties that hold on correlated
residuals (moving-block bootstrap), Hamilton's F-test, bound flags, and LCF / MCR-ALS off the GUI thread."""
import time

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d

from aurora.core.fit import autocorrelation_length, estimate_noise, f_test, lcf

El = np.linspace(680, 730, 401)
A = 1 / (1 + np.exp(-(El - 694) / 1.0)) + 0.6 * np.exp(-0.5 * ((El - 696) / 1.2) ** 2)
B = 1 / (1 + np.exp(-(El - 697) / 1.5)) + 0.3 * np.exp(-0.5 * ((El - 701) / 2.0) ** 2)
C = 1 / (1 + np.exp(-(El - 700) / 1.2)) + 0.8 * np.exp(-0.5 * ((El - 703) / 1.0) ** 2)


def test_correlated_noise_probe_uncertainty():
    """The scratch probe: residuals correlated over ~20 points. The iid bootstrap understated the scatter of the
    coefficient about 3.8 times; the moving-block bootstrap (the default) is within a factor of 1.5 of it."""
    fits, reported, iid = [], [], []
    for s in range(60):
        noise = gaussian_filter1d(np.random.default_rng(100 + s).normal(0, 0.02, El.size), 6) * 4
        t = 0.3 * A + 0.7 * B + noise
        r = lcf((El, t), [(El, A), (El, B)], bootstrap=200, seed=s)
        assert r.method == "block bootstrap" and r.block_length > 5
        fits.append(r.coefficients[0]); reported.append(r.uncertainties[0])
        if s < 20:
            iid.append(lcf((El, t), [(El, A), (El, B)], bootstrap=200, seed=s, resample="iid").uncertainties[0])
    true = np.std(fits)
    ratio = np.median(reported) / true
    assert 1 / 1.5 < ratio < 1.5, ratio
    assert np.median(iid) / true < 0.5                             # the old way, for the record


def test_reduced_chi_square_with_sigma_supplied():
    rc = []
    for s in range(100):
        t = 0.3 * A + 0.7 * B + np.random.default_rng(s).normal(0, 0.01, El.size)
        r = lcf((El, t), [(El, A), (El, B)], bootstrap=0, sigma=0.01)
        assert r.n_points == El.size and r.n_params == 1 and np.isclose(r.redchi, r.chi2 / (r.n_points - r.n_params))
        rc.append(r.redchi)
    rc = np.array(rc)
    assert abs(rc.mean() - 1.0) < 0.1 and np.mean(np.abs(rc - 1) < 0.2) >= 0.9
    # sigma from Spectrum.meta 'err' (the third array of the target) is used and said
    t = 0.3 * A + 0.7 * B + np.random.default_rng(1).normal(0, 0.01, El.size)
    r = lcf((El, t, np.full(El.size, 0.01)), [(El, A), (El, B)], bootstrap=0)
    assert r.sigma_source == "the target's own uncertainties" and abs(r.redchi - 1) < 0.25
    # without sigma it is estimated from the point-to-point scatter, and the result says so
    r = lcf((El, t), [(El, A), (El, B)], bootstrap=0)
    assert "estimated" in r.sigma_source and abs(np.median(r.sigma) - 0.01) < 0.002
    assert abs(estimate_noise(np.random.default_rng(3).normal(0, 0.05, 4000)) - 0.05) < 0.004


def test_covariance_uncertainty_is_the_larch_convention():
    t = 0.3 * A + 0.7 * B + np.random.default_rng(5).normal(0, 0.01, El.size)
    r = lcf((El, t), [(El, A), (El, B)], bootstrap=0, sigma=0.01)
    r2 = lcf((El, t), [(El, A), (El, B)], bootstrap=0, sigma=0.02)   # sigma doubled: chi2 / 4, same uncertainty
    assert r.method == "covariance" and np.allclose(r.uncertainties, r2.uncertainties, rtol=1e-6)
    assert np.isclose(r.uncertainties[0], r.uncertainties[1])       # sum to 1: one free parameter
    assert np.isclose(r2.redchi * 4, r.redchi)


def test_f_test_power_and_size():
    real, spurious = 0, 0
    for s in range(50):
        rng = np.random.default_rng(1000 + s)
        t = 0.6 * A + 0.3 * B + 0.1 * C + rng.normal(0, 0.01, El.size)
        r = lcf((El, t), [(El, A), (El, B), (El, C)], bootstrap=0)
        real += r.f_tests[2].p_value < 0.05
        t0 = 0.6 * A + 0.4 * B + rng.normal(0, 0.01, El.size)
        r0 = lcf((El, t0), [(El, A), (El, B), (El, C)], bootstrap=0)
        spurious += r0.f_tests[2].p_value > 0.05
    assert real >= 45 and spurious >= 45, (real, spurious)
    ft = f_test(110.0, 1, 100.0, 2, 401)
    assert ft.F > 0 and 0 < ft.p_value < 0.01 and ft.dof1 == 1
    assert f_test(110.0, 1, 100.0, 2, 401, tau=20).p_value > ft.p_value      # correlated residuals: less evidence
    assert autocorrelation_length(np.random.default_rng(0).normal(size=2000)) < 1.3


def test_bound_and_sum_flags():
    t = 0.6 * A + 0.4 * B + np.random.default_rng(7).normal(0, 0.01, El.size)
    r = lcf((El, t), [(El, A), (El, B), (El, -C + 2.0)], bootstrap=0)
    assert r.at_bound[2] and "component 3 at the 0 bound" in r.flags and np.isnan(r.cov_uncertainties[2])
    r = lcf((El, 1.3 * t), [(El, A), (El, B)], bootstrap=0, sum_to_one=False)
    assert any("outside 1 ± 3σ" in f for f in r.flags) and np.isfinite(r.sum_sigma)


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_bound_coefficient_is_flagged_in_the_table(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from demo_window import build_demo_window
    from aurora.app.theme import TOKENS
    from aurora.core.compare import Spectrum
    win = build_demo_window(qapp, tmp_path)
    lib, fit = win.library, win.fit
    E = np.linspace(660, 740, 161)
    lib.add_spectra([Spectrum("flat", "x", E, 0.5 + 0.0 * E, "flat")], show_signals=("x",), norm="none")
    lib.requests[-1].fit = True
    lib.changed.emit()
    fit.refresh()
    fit.target.setCurrentText("glass [sdd-scaled]")
    for i in range(fit.components.count()):
        it = fit.components.item(i)
        it.setCheckState(Qt.Checked if it.text() in ("NaF [sdd-scaled]", "CaF2 [sdd-scaled]", "flat [x]") else Qt.Unchecked)
    fit.bootstrap.setValue(20)
    job = fit.run_lcf()
    assert job is not None and win.jobs.wait_idle(30000)
    r = fit.lcf_result
    names = [fit.lcf_table.item(i, 0).text() for i in range(fit.lcf_table.rowCount())]
    k = names.index("flat [x]")
    assert r.at_bound[k] and fit.lcf_table.item(k, 3).text() == "at the 0 bound"
    assert fit.lcf_table.item(k, 1).background().color().name().upper() == TOKENS["warn_bg"].upper()
    assert "0 bound" in fit.lcf_table.item(k, 1).toolTip()
    assert "χ²ν" in fit.lcf_info.text() and f"N {r.n_points} − p {r.n_params}" in fit.lcf_info.text()
    assert "F-test (Hamilton)" in fit.lcf_detail.text()
    assert fit.lcf_chip.text().startswith("converged")
    # the export header carries everything
    text = fit.export_lcf(tmp_path / "lcf.csv")[0].read_text(encoding="utf-8")
    for word in ("reduced chi-square", "N = ", "p = ", "R-factor", "uncertainty:", "sigma:", "F-test (Hamilton)",
                 "at the 0 bound"):
        assert word in text, word
    win.session._save_timer.stop()
    win.close()


def test_easy_reads_200_bootstrap_draws(qapp, tmp_path):
    from demo_window import build_demo_window, pump
    from aurora.app import mode
    win = build_demo_window(qapp, tmp_path)
    assert not mode.is_advanced()
    win.show_perspective("fit")
    pump(qapp)
    fit = win.fit
    assert "± from 200 bootstrap draws" in fit.lcf_fixed.text() and fit.lcf_fixed.isVisible()
    assert not fit.lcf_detail.isVisible() and not fit.lcf_options.isVisible()
    fit.run_lcf()
    assert win.jobs.wait_idle(60000)
    assert fit.lcf_result.n_draws == 200 and fit.lcf_table.horizontalHeaderItem(2).text() == "± (200 bootstrap draws)"
    win.session._save_timer.stop()
    win.close()


def test_mcr_keeps_the_event_loop_responsive(qapp):
    from PySide6.QtCore import QElapsedTimer, QTimer
    from aurora.app.library import Library
    from aurora.app.fit import FitPerspective
    from aurora.app.jobs import of
    from aurora.core.compare import Spectrum
    E = np.linspace(670, 740, 281)
    rng = np.random.default_rng(4)
    lib = Library()
    specs = []
    for i in range(50):
        f = rng.uniform(0, 1, 3)
        f /= f.sum()
        y = f[0] * np.interp(E, El, A) + f[1] * np.interp(E, El, B) + f[2] * np.interp(E, El, C) + rng.normal(0, 0.005, E.size)
        specs.append(Spectrum(f"s{i:02d}", "x", E, y, f"s{i}"))
    lib.add_spectra(specs, show_signals=("x",), norm="none")
    for r in lib.requests:
        r.fit = True
    fit = FitPerspective(lib)
    fit.refresh()
    fit.n_comp.setValue(3)
    fit.max_iter.setValue(400)
    fit.tol.setValue(0.0)                                          # run every iteration: a long job
    # the new perspective's first layout (pyqtgraph axes and labels, ~0.4 s on CI) runs at the first event-loop pass:
    # let it finish, so the gaps measure the job and not the widget's construction
    settle = time.monotonic()
    while time.monotonic() - settle < 0.5:
        qapp.processEvents()
        time.sleep(0.01)
    gaps, clock = [], QElapsedTimer()
    clock.start()
    last = [clock.elapsed()]

    def tick():
        now = clock.elapsed()
        gaps.append(now - last[0])
        last[0] = now
    timer = QTimer()
    timer.setInterval(50)
    timer.timeout.connect(tick)
    timer.start()
    t0 = time.monotonic()
    job = fit.run_mcr()
    assert job is not None
    job.ended.connect(lambda _state: timer.stop())                 # the gaps while it runs, not the drawing after
    assert of(fit).wait_idle(120000)
    timer.stop()
    took = time.monotonic() - t0
    assert fit.mcr_result is not None and fit.mcr_result.n_iter == 400 and took > 0.5
    assert len(gaps) >= 5 and max(gaps[1:]) <= 150, (max(gaps), len(gaps), took)
    assert fit.mcr_chip.text().startswith("not converged") and "400" in fit.mcr_chip.text()
    fit.deleteLater()


def test_cancel_stops_the_mcr_job(qapp, error_dialogs):
    from aurora.app.library import Library
    from aurora.app.fit import FitPerspective
    from aurora.app.jobs import of
    from aurora.core.compare import Spectrum
    E = np.linspace(670, 740, 281)
    lib = Library()
    rng = np.random.default_rng(9)
    lib.add_spectra([Spectrum(f"s{i}", "x", E, rng.uniform() * np.interp(E, El, A) + np.interp(E, El, B), f"s{i}")
                     for i in range(30)], show_signals=("x",), norm="none")
    for r in lib.requests:
        r.fit = True
    fit = FitPerspective(lib)
    fit.refresh()
    fit.max_iter.setValue(5000)
    fit.tol.setValue(0.0)
    job = fit.run_mcr()
    job.cancel()
    assert of(fit).wait_idle(30000) and job.state == "cancelled"
    assert fit.mcr_result is None and not error_dialogs
    fit.deleteLater()


def test_fit_status_lines_after_cancel_and_failure(qapp, error_dialogs, monkeypatch):
    """After a Cancel the status line says 'LCF cancelled' / 'MCR-ALS cancelled', after a failure the short error;
    before, it kept 'fitting …' and 'MCR-ALS on 30 spectra …' for good (threads-10)."""
    from PySide6.QtCore import Qt
    from aurora.app import fit as fit_mod
    from aurora.app.library import Library
    from aurora.app.fit import FitPerspective
    from aurora.app.jobs import of
    from aurora.core.compare import Spectrum
    E = np.linspace(670, 740, 281)
    lib = Library()
    rng = np.random.default_rng(9)
    lib.add_spectra([Spectrum(f"s{i}", "x", E, rng.uniform() * np.interp(E, El, A) + np.interp(E, El, B), f"s{i}")
                     for i in range(30)], show_signals=("x",), norm="none")
    for r in lib.requests:
        r.fit = True
    fit = FitPerspective(lib)
    fit.refresh()
    fit.target.setCurrentIndex(0)
    for i in range(fit.components.count()):
        fit.components.item(i).setCheckState(Qt.Checked if 0 < i < 4 else Qt.Unchecked)
    fit.bootstrap.setValue(100000)
    job = fit.run_lcf()
    assert job is not None and fit.lcf_info.text() == "fitting …"
    job.cancel()
    assert of(fit).wait_idle(30000) and job.state == "cancelled" and fit.lcf_info.text() == "LCF cancelled"
    fit.max_iter.setValue(5000)
    fit.tol.setValue(0.0)
    job = fit.run_mcr()
    job.cancel()
    assert of(fit).wait_idle(30000) and fit.mcr_status.text() == "MCR-ALS cancelled" and not error_dialogs

    def boom(*_a, **_k):
        raise ZeroDivisionError("no spectra left")
    monkeypatch.setattr(fit_mod, "mcr_als", boom)
    fit.run_mcr()
    assert of(fit).wait_idle(30000)
    assert fit.mcr_status.text() == "MCR-ALS failed: ZeroDivisionError: no spectra left" and len(error_dialogs) == 1
    fit.deleteLater()


def test_fit_range_defaults_to_the_compare_x_range(qapp, tmp_path):
    from demo_window import build_demo_window, pump
    win = build_demo_window(qapp, tmp_path)
    c, fit = win.compare, win.fit
    c.x_lo.setText("670"); c.x_hi.setText("720"); c.x_lo.editingFinished.emit()
    win.show_perspective("fit")
    pump(qapp)
    assert fit.lcf_lo.placeholderText() == "670 (Compare)" and fit.lcf_hi.placeholderText() == "720 (Compare)"
    assert fit.lcf_range() == ((670.0, 720.0), "the Compare x range")
    fit.bootstrap.setValue(0)
    fit.run_lcf()
    assert win.jobs.wait_idle(30000)
    r = fit.lcf_result
    assert r.E[0] >= 670 and r.E[-1] <= 720 and "(the Compare x range)" in fit.lcf_info.text()
    assert r.method == "covariance" and fit.lcf_table.horizontalHeaderItem(2).text() == "± (covariance)"
    fit.lcf_lo.setText("680"); fit.lcf_hi.setText("700")              # a typed range wins
    assert fit.lcf_range() == ((680.0, 700.0), "typed")
    win.session._save_timer.stop()
    win.close()
