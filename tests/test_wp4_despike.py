"""I25: the coherent de-spike keeps a real white line on a coarse scan and removes a single-detector spike; 'classic'
stays the zone tool's rule; spike energies are reported in eV; spikes next to the edge get a calm note."""
import numpy as np
import pytest

from aurora.core.constants import DETS
from aurora.core.io.config import ScanSettings, ZonesFile
from aurora.core.spectra import (coherent_despike, despike, despike_coherent, despike_hampel, hampel)
from aurora.core.stack import StackData
from aurora.core.zones import zone_traces

GAINS = (1.0, 0.8, 1.25, 0.6)          # detector sensitivities: the ratio method must not care
E = np.arange(680.0, 731.0, 1.0)        # coarse 1 eV scan
WL, SPIKE = 700, 715                    # white line energy, SDD2 spike energy (eV)


def _model() -> np.ndarray:
    """Noise-free counts per pixel: edge step at 696, a one-point white line at 700 (2.5x the step)."""
    step = 1.0 / (1 + np.exp(-(E - 696.0) / 0.8))
    y = 300 + 900 * step
    y[E == WL] *= 2.5
    return y


def coarse_stack(seed: int = 4, spike_det: int = 1, spike_at: float = SPIKE, tey_white: bool = True) -> StackData:
    """4 pixels, one line, Poisson counts; a 3x spike in one detector at one energy."""
    rng = np.random.default_rng(seed)
    n_pix, nE = 4, len(E)
    mu = _model()
    rpd = np.zeros((nE, n_pix, 1, 4), np.float32)
    for k, g in enumerate(GAINS):
        lam = np.repeat((g * mu)[:, None], n_pix, axis=1)
        if k == spike_det:
            lam[E == spike_at] *= 3.0
        rpd[:, :, 0, k] = rng.poisson(lam)
    i0 = 2.5 + 0.005 * rng.standard_normal((nE, n_pix))
    tey_mu = mu / mu.max() if tey_white else 0.3 + 0.9 / (1 + np.exp(-(E - 696.0) / 0.8))
    tey = tey_mu[:, None] * (1 + 0.002 * rng.standard_normal((nE, n_pix)))
    x = np.array([0.0, 0.1, 0.0, 0.1]); y = np.array([0.0, 0.0, 0.1, 0.1])
    return StackData(id="2025-03-14_120000_coarse_stack", name="120000_coarse_stack", day="2025-03-14", E=E.copy(), x=x,
                     y=y, i0=i0, tey=tey, roi_pix_det=rpd, valid=np.ones(nE, bool), labels=["F Kα"], main="F Kα",
                     edge=("F", "K"), edge_ev=696.7, pitch=(0.1, 0.1))


def test_coherent_keeps_the_white_line_and_removes_the_sdd2_spike():
    sd = coarse_stack()
    pix = np.arange(4)
    measured = sd.roi_pix_det[:, :, 0, :].sum(axis=1).astype(float)          # (nE, 4) zone sums
    t = zone_traces(sd, pix, ScanSettings(line="F Kα", despike_mode="coherent", fluo_norm="raw", tey_norm="raw"))
    iw, isp = int(np.where(E == WL)[0][0]), int(np.where(E == SPIKE)[0][0])
    # only the SDD2 spike is removed
    assert t.spikes["sdd2"] == [isp]
    assert all(t.spikes[d] == [] for d in ("sdd1", "sdd3", "sdd4")) and t.spikes["sum"] == [] and t.spikes["tey"] == []
    # the white line is unchanged (not interpolated), and within Poisson noise of the model in every detector
    for k, g in enumerate(GAINS):
        assert t.det_raw[k][iw] == measured[iw, k]
        expect = 4 * g * _model()[iw]
        assert abs(t.det_raw[k][iw] - expect) < 5 * np.sqrt(expect)
    assert t.sum_raw[iw] == measured[iw].sum() and t.tey[iw] == sd.tey[iw].sum()
    # the spike is gone: interpolated between its neighbours, close to the model again
    expect2 = 4 * GAINS[1] * _model()[isp]
    assert measured[isp, 1] > 2 * expect2 and abs(t.det_raw[1][isp] - expect2) < 6 * np.sqrt(expect2)
    # spikes in eV
    assert t.spikes_ev == {"sdd2": [float(SPIKE)]}
    assert any("SDD2 at 715.00 eV" in n for n in t.notes) and t.notes[0].startswith("de-spike (coherent, k = 6)")


def test_coherent_over_many_noise_draws_and_unequal_white_lines():
    """Not one lucky seed: over 60 Poisson draws the white line is never touched and the spike is found; a white line
    of a different height in each detector (geometry, self-absorption) stays too, where the classic rule removes it."""
    iw, isp = int(np.where(E == WL)[0][0]), int(np.where(E == SPIKE)[0][0])
    touched = missed = 0
    for seed in range(60):
        t = zone_traces(coarse_stack(seed=seed), np.arange(4), ScanSettings(line="F Kα", despike_mode="coherent"))
        touched += any(iw in (t.spikes.get(k) or []) for k in (*DETS, "sum", "tey"))
        missed += isp not in t.spikes["sdd2"]
    assert touched == 0 and missed <= 1
    step = 300 + 900 / (1 + np.exp(-(E - 696.0) / 0.8))
    removed = classic = 0
    for seed in range(60):
        rng = np.random.default_rng(seed)
        dets = []
        for j, (g, h) in enumerate(zip(GAINS, (2.5, 1.6, 3.0, 1.3))):
            mu = 4 * g * step.copy()
            mu[E == WL] *= h
            dets.append(rng.poisson(mu).astype(float))
        removed += any(iw in s for s in despike_coherent(dets, 6)[1])
        classic += any(iw in despike(d, 6)[1] for d in dets)
    assert removed == 0 and classic == 60


def test_classic_removes_the_white_line_on_the_coarse_scan():
    """Why the coherent mode exists: the zone tool's rule takes a one-point white line for a spike."""
    sd = coarse_stack()
    t = zone_traces(sd, np.arange(4), ScanSettings(line="F Kα", fluo_norm="raw", tey_norm="raw"))
    iw = int(np.where(E == WL)[0][0])
    assert any(iw in t.spikes[d] for d in DETS) and iw in t.spikes["tey"]
    assert t.notes[0].startswith("de-spike (classic, k = 6)") and "700.00" in t.notes[0]


def test_classic_is_the_zone_tool_rule_trace_by_trace():
    """'classic' (and every settings file without 'dsm') is despike() applied to each trace, as before."""
    sd = coarse_stack(seed=9)
    st = ZonesFile.from_json({"holders": {"holderA": {"zones": [{"id": 1, "label": "a", "st": {
        sd.id: {"inc": True, "sdd": [1, 1, 1, 1], "line": "F Kα", "fn": "i0jump", "ds": True, "k": 6}}}]}}}
    ).holders["holderA"].zones[0].settings[sd.id]
    assert st.despike_mode == "classic"
    t = zone_traces(sd, np.arange(4), st)
    sums = sd.sum_over(np.arange(4))
    for k in range(4):
        y, sp = despike(sums["det"]["F Kα"][:, k].astype(float), 6)
        assert np.array_equal(t.det_raw[k], y, equal_nan=True) and t.spikes[f"sdd{k + 1}"] == sp
    ty, sp = despike(sums["tey"], 6)
    assert t.spikes["tey"] == sp and "sum" not in t.spikes
    i0, sp = despike(sums["i0"], 6)
    assert np.array_equal(t.i0, i0) and t.spikes["i0"] == sp


def test_spike_next_to_the_edge_gets_a_calm_note():
    sd = coarse_stack(spike_det=2, spike_at=698.0)          # 1.3 eV above the F K edge (696.7 eV)
    t = zone_traces(sd, np.arange(4), ScanSettings(line="F Kα", despike_mode="coherent"))
    assert t.spikes_ev.get("sdd3") == [698.0]
    assert "1 point near the edge was treated as a spike, check the preview" in t.notes


def test_tey_white_line_kept_only_when_the_detectors_show_it():
    sd = coarse_stack()
    sums = sd.sum_over(np.arange(4))
    dets = [sums["det"]["F Kα"][:, k].astype(float) for k in range(4)]
    iw = int(np.where(E == WL)[0][0])
    tey = sums["tey"].copy()
    _, kept = coherent_despike(tey, 6, dets)
    assert iw not in kept                                     # the detectors show the white line: real
    flat = [np.convolve(d, np.ones(3) / 3, mode="same") for d in dets]    # detectors without a local maximum there
    flat = [np.where(np.isfinite(f), f, d) for f, d in zip(flat, dets)]
    _, removed = coherent_despike(tey, 6, flat)
    assert iw in removed
    # with fewer than COHERENT_MIN_SDDS detector traces TEY is de-spiked on its own, with the noise floor: the white
    # line (no detector to vouch for it) goes, the classic rule's collapsed MAD would take ordinary noise as well
    y, sp = coherent_despike(tey, 6, [dets[0], None, None, None])
    assert sp == despike_hampel(tey, 6)[1] == [iw] and len(despike(tey, 6)[1]) > 3


def test_despike_coherent_edge_cases():
    rng = np.random.default_rng(2)
    base = 500 + 400 * (E > 696)
    dets = [rng.poisson(g * base).astype(float) for g in GAINS]
    dets[3][:] = 0.0                                          # a dead detector: no reference, nothing flagged in it
    out, sp = despike_coherent(dets, 6)
    assert sp[3] == [] and np.array_equal(out[3], dets[3])
    out, sp = despike_coherent([dets[0], None, None, dets[2]], 6)        # one reference only: on its own
    assert out[1] is None and sp[0] == despike_hampel(dets[0], 6)[1]
    # the Hampel window scale never makes it more sensitive than the scan-wide MAD; MAD = 0 removes nothing (classic)
    y = 1 + 0.01 * np.random.default_rng(5).standard_normal(20); y[7] = 5.0
    assert hampel(y, 6) == [7] == despike(y, 6)[1] and hampel(np.ones(20), 6) == []


# ----------------------------------------------------------------------------- smooth, high-S/N traces (whole pads)
FINE_E = np.round(np.r_[np.arange(680.0, 685.0, 0.5), np.arange(685.0, 700.0, 0.1), np.arange(700.0, 730.01, 0.2)], 2)


def _whole_pad_sums(seed: int, n_pix: int = 361, dip_at: float | None = None, i0_spike_at: float | None = None):
    """Zone sums of a whole pad on the beamtime's F K grid (311 energies, 0.5 / 0.1 / 0.2 eV steps): a smooth I0 that
    drifts (a slow random walk of 6e-4 per step on a falling curve) with white noise of 1e-3, which gives the
    statistics of the 2026 whole-pad I0 (UF4, 361 px: residual to the running median exactly 0 at 40 % of the
    energies, point-to-point noise 1.3e-3); SDD counts proportional to it (Poisson).
    dip_at: a 3 % beam dip seen by I0 and every SDD; i0_spike_at: a 3 % jump of I0 alone."""
    from aurora.core.zones import ZoneSums
    rng = np.random.default_rng(seed)
    E = FINE_E
    walk = np.exp(np.cumsum(6e-4 * rng.standard_normal(E.size)))
    beam = (1 + 0.03 * np.sin((E - 680.0) / 9.0) - 0.004 * (E - 680.0)) * walk
    if dip_at is not None:
        beam = np.where(np.isclose(E, dip_at), beam * 0.97, beam)
    i0 = n_pix * 2.4 * beam * (1 + 1e-3 * rng.standard_normal(E.size))
    if i0_spike_at is not None:
        i0 = np.where(np.isclose(E, i0_spike_at), i0 * 1.03, i0)
    mu = 3.0 + 9.0 / (1 + np.exp(-(E - 693.5) / 0.8))
    det = np.column_stack([rng.poisson(n_pix * g * mu * beam).astype(float) for g in GAINS])
    tey = n_pix * 0.4 * mu / mu.max() * beam * (1 + 1e-3 * rng.standard_normal(E.size))
    return ZoneSums(E=E, n_pix=n_pix, i0=i0, tey=tey, det={"F Kα": det}, main="F Kα", edge_ev=696.7)


def test_coherent_flags_almost_nothing_on_smooth_whole_pad_traces():
    """A whole pad gives smooth traces: the point is the median of its own 5-point window at most energies, so the
    residual MAD collapses and the classic rule calls ordinary noise spikes (17-19 % of I0 on the 2026 pads). The
    coherent mode never goes below the point-to-point noise of the trace (second differences): on 40 draws of clean
    data it flags well under one point per scan in any trace, I0 included."""
    from aurora.core.spectra import build_traces
    classic = coherent = 0
    for seed in range(40):
        sums = _whole_pad_sums(seed)
        classic += len(despike(sums.i0, 6)[1])
        t = build_traces(sums, ScanSettings(line="F Kα", despike_mode="coherent"))
        coherent += sum(len(v) for k, v in t.spikes.items() if isinstance(v, list))
        assert not any("near the edge" in n for n in t.notes), t.notes
    assert classic > 40 * 10                    # why: the classic rule on the same I0, unchanged (zone tool)
    assert coherent <= 10, coherent             # < 0.25 per scan over all eight traces


def test_coherent_keeps_a_beam_dip_and_removes_an_i0_spike():
    """I0 in the coherent mode: a beam dip that every SDD shows too is divided out by I0 and stays measured; a jump
    of I0 alone is a spike, interpolated."""
    from aurora.core.spectra import build_traces
    dip, spike = 712.0, 720.0
    idip, isp = int(np.argmin(np.abs(FINE_E - dip))), int(np.argmin(np.abs(FINE_E - spike)))
    for seed in range(10):
        sums = _whole_pad_sums(seed, dip_at=dip, i0_spike_at=spike)
        t = build_traces(sums, ScanSettings(line="F Kα", despike_mode="coherent", fluo_norm="i0"))
        assert idip not in t.spikes["i0"] and t.i0[idip] == sums.i0[idip], (seed, t.spikes_ev)
        assert isp in t.spikes["i0"] and t.i0[isp] < sums.i0[isp] / 1.02, (seed, t.spikes_ev)
        # the dip leaves no mark on fluorescence / I0: within 4 sigma of its neighbours
        y = t.sum
        nb = 0.5 * (y[idip - 1] + y[idip + 1])
        assert abs(y[idip] - nb) < 4 * np.sqrt(sums.det["F Kα"][idip].sum()) / sums.i0[idip], seed


def test_near_edge_note_uses_the_measured_edge():
    """'n points near the edge were treated as spikes': the edge of the measured trace (maximum of the derivative),
    not the tabulated one, which a compound's edge lies eV away from (F K: 696.7 eV tabulated, 693.5 measured)."""
    from aurora.core.spectra import build_traces
    sums = _whole_pad_sums(3)
    k = int(np.argmin(np.abs(FINE_E - 693.6)))
    sums.det["F Kα"][k, 2] *= 1.5                       # a spike in SDD3 next to the measured edge
    t = build_traces(sums, ScanSettings(line="F Kα", despike_mode="coherent"))
    assert t.spikes_ev.get("sdd3") == [float(FINE_E[k])]
    assert "1 point near the edge was treated as a spike, check the preview" in t.notes


def test_coherent_on_real_whole_pads(real_project):
    """The 2026 beamtime's whole pads (UF4, UO2F2: 361 px): the coherent mode flagged 57 and 52 of 311 I0 points of
    ordinary noise (1-5 sigma) and Check warned '19 % of the points flagged bad'."""
    from aurora.core import qc
    from aurora.core.model import Project
    proj = Project.discover(real_project)
    for name in ("141456_UF4_f_k_stack", "181731_UO2F2_f_k_stack"):
        scan = proj.find(name)
        if scan is None:
            pytest.skip(f"{name} not in the beamtime folder")
        try:
            sd = StackData.load(proj, scan, allow_reduce=False)
        except FileNotFoundError:
            pytest.skip(f"{name}: no reduced cache")
        t = zone_traces(sd, np.arange(sd.n_pix), ScanSettings(line="F Kα", despike_mode="coherent"))
        assert len(t.spikes["i0"]) <= 2, (name, t.spikes_ev.get("i0"))
        msgs = qc.check_zone(t)
        assert not any("flagged bad" in m for _lvl, m in msgs) and not any("near the edge" in m for _l, m in msgs), msgs
