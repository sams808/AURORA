"""WP5 I34: fits that say what they did: a result chip on LCF, MCR-ALS and Peaks, undetermined parameters tinted with
the reason, a fit that did not converge not drawn, peak widths that are FWHM in eV for every shape, the pseudo-Voigt
fraction fitted, peak areas with errors, and ITFA bound to the matrix of its MCR run."""
from dataclasses import replace

import numpy as np
import pytest

from aurora.core import fit as core_fit
from aurora.core.fit import fit_status, peak_decomposition


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def demo(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    yield win
    win.session._save_timer.stop()
    win.close()


def test_status_words():
    assert fit_status(True) == ("converged", "ok")
    assert fit_status(False, "max iterations") == ("not converged: max iterations", "bad")
    text, tone = fit_status(True, "", 3)
    assert tone == "warn" and "3 parameters undetermined (σ > |value|)" in text


def test_two_peaks_on_the_demo_naf_trace(qapp, demo):
    from aurora.app.theme import TOKENS
    fit = demo.fit
    fit.select_trace("NaF", "sdd-scaled")
    fit.pk_n.setValue(2)
    fit.pk_centres.setText("")
    fit.run_peaks()
    assert "undetermined" in fit.pk_chip.text() and fit.pk_chip.property("chip") == "warn"
    rows = {fit.pk_table.item(i, 0).text(): i for i in range(fit.pk_table.rowCount())}
    amp = fit.pk_table.item(rows["p2_amp"], 1)
    assert amp.background().color().name().upper() == TOKENS["warn_bg"].upper()
    assert "σ > |value|" in amp.toolTip()
    ok = fit.pk_table.item(rows["p1_centre"], 1)
    assert ok.background().style() == 0 or ok.background().color().name().upper() != TOKENS["warn_bg"].upper()
    # the unit column
    assert fit.pk_table.horizontalHeaderItem(3).text() == "unit"
    assert fit.pk_table.item(rows["p1_centre"], 3).text() == "eV"
    assert fit.pk_table.item(rows["p1_fwhm"], 3).text() == "eV"
    assert fit.pk_table.item(rows["step_centre"], 3).text() == "eV"
    assert fit.pk_table.item(rows["step_width"], 3).text() == "eV"
    assert fit.pk_table.item(rows["p1_amp"], 3).text() == "a.u."
    assert "peak areas" in fit.pk_areas.text()


def test_a_fit_that_did_not_converge_is_not_drawn(qapp, demo, monkeypatch):
    real = core_fit.peak_decomposition

    def failing(*a, **k):
        return replace(real(*a, **k), converged=False, message="the optimiser stopped after 20000 evaluations")
    monkeypatch.setattr(core_fit, "peak_decomposition", failing)
    fit = demo.fit
    fit.select_trace("NaF", "sdd-scaled")
    fit.pk_n.setValue(1)
    fit.pk_centres.setText("692")
    fit.run_peaks()
    items = fit.pk_plot.plotItem.listDataItems()
    assert len(items) == 1 and np.allclose(items[0].yData, fit.peak_result.y)
    assert fit.pk_chip.text().startswith("not converged") and fit.pk_chip.property("chip") == "bad"
    import pyqtgraph as pg
    texts = [it for it in fit.pk_plot.plotItem.items if isinstance(it, pg.TextItem)]
    assert any("fit not shown: not converged" in t.toPlainText() for t in texts)
    lines = [it for it in fit.pk_plot.plotItem.items if isinstance(it, pg.InfiniteLine)]
    assert [round(l.value(), 3) for l in lines] == [692.0]       # the initial-guess marker


def _peak(shape, E, fwhm=1.5, eta=0.5, c=695.0, a=1.0):
    f = core_fit.SHAPES[shape]
    return f(E, a, c, fwhm, eta) if shape == "pseudo-voigt" else f(E, a, c, fwhm)


@pytest.mark.parametrize("shape", ["gaussian", "lorentzian"])
def test_fwhm_means_fwhm(shape):
    E = np.linspace(680, 710, 601)
    y = 0.1 + _peak(shape, E) + np.random.default_rng(1).normal(0, 0.002, E.size)
    r = peak_decomposition(E, y, 1, shape, step=False, background="constant", init_centres=[694.0])
    assert r.converged and abs(r.params["p1_fwhm"] - 1.5) < 0.05
    # the half maximum really sits at ± FWHM / 2
    half = _peak(shape, np.array([695.0 + 0.75]))[0]
    assert abs(half - 0.5) < 1e-9
    v, e = r.areas["p1_area"]
    expected = {"gaussian": 1.5 * np.sqrt(np.pi / (4 * np.log(2))), "lorentzian": 1.5 * np.pi / 2}[shape]
    assert abs(v - expected) < 0.02 and 0 < e < 0.05


def test_pseudo_voigt_fraction_is_fitted():
    E = np.linspace(670, 720, 1001)
    y = 0.05 + _peak("pseudo-voigt", E, fwhm=2.0, eta=0.3) + np.random.default_rng(2).normal(0, 0.002, E.size)
    r = peak_decomposition(E, y, 1, "pseudo-voigt", step=False, background="constant", init_centres=[694.0])
    assert r.converged and abs(r.params["p1_eta"] - 0.3) < 0.05 and abs(r.params["p1_fwhm"] - 2.0) < 0.05
    assert core_fit.unit_of("p1_eta") == "fraction" and core_fit.unit_of("p1_area") == "a.u.·eV"


def test_itfa_refuses_when_the_ticked_rows_changed(qapp, demo, excepthook_collector):
    fit = demo.fit
    assert fit.mcr_result is not None and fit.mcr_result.D is not None and fit.mcr_result.labels
    assert fit.mcr_chip.text() in ("converged",) or fit.mcr_chip.text().startswith("not converged")
    fit.select_key(fit.itfa_sample, fit.mcr_result.labels[0])
    fit.itfa_pct.setText("100, 0")
    fit._add_target()
    lib = demo.library
    ticked = [r for r in lib.requests if r.fit]
    ticked[-1].fit = False                                         # untick a row
    lib.changed.emit()
    out = fit.run_itfa()
    assert out is None and fit.itfa_result is None
    assert fit.itfa_status.text() == "the spectra differ from the MCR run; rerun MCR first"
    assert not excepthook_collector
    # rerun MCR on the new rows: ITFA runs
    fit.run_mcr()
    assert demo.jobs.wait_idle(30000)
    fit.targets.clear()
    fit.select_key(fit.itfa_sample, fit.mcr_result.labels[0])
    fit._add_target()
    assert fit.run_itfa() is not None and demo.jobs.wait_idle(30000)
    assert fit.itfa_result is not None and fit.itfa_result.labels == fit.mcr_result.labels
    assert fit.itfa_chip.text()


def test_lcf_chip_on_the_demo(qapp, demo):
    fit = demo.fit
    assert fit.lcf_result is not None
    assert fit.lcf_chip.text().startswith("converged") and fit.lcf_chip.property("chip") in ("ok", "warn")
