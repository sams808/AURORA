"""Quality checks: synthetic count-rate rows, I0 levels and traces; the probe on the fake project."""
import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.io.config import ScanSettings
from aurora.core.model import Project
from aurora.core.qc import (CountRate, count_rate_probe, day_reference, i0_candidates, i0_check, saturation_check,
                            sdd_consistency, spike_summary)
from aurora.core.stack import StackData
from aurora.core.zones import zone_traces


def _rate(name, i0, tops, dwell=0.05):
    rows = [dict(E_eV=680 + 5 * i, i0=float(v), dwell_s=dwell, mean=[t / 2 for t in tp], top=list(tp)) for i, (v, tp) in enumerate(zip(i0, tops))]
    return CountRate(scan=name, key="k", time_per_map_s=6.0, rows=rows)


def test_saturation_detected_and_not():
    i0 = np.array([2.0, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6])          # I0 varies by 30 %
    flat = [[10000, 10000, 10000, 10000]] * 7                      # saturated: same total every time, fast
    following = [[1000 * v, 1000 * v, 1000 * v, 1000 * v] for v in i0]
    sat = _rate("sat", i0, flat)
    normal = _rate("ok", i0, following)
    day_kcps, day_i0 = day_reference([normal, normal])
    assert np.allclose(day_kcps, 1000 * i0.mean() / 0.05 / 1e3, rtol=0.01) and np.isclose(day_i0, 2.3)
    r = saturation_check(sat, day_kcps, n_pix=100)
    assert r.saturated == list(("sdd1", "sdd2", "sdd3", "sdd4")) and r.notes and "not usable" in r.notes[0]
    r2 = saturation_check(normal, day_kcps)
    assert r2.ok and r2.detail["sdd1"]["slope_vs_i0"] == pytest.approx(1.0, abs=0.05)
    # high rate without the flat signature: 3x the day's rate but following I0 only weakly
    # slope vs I0 = 0.174 * 2.3 = 0.4 (< 0.5), spread 3.5 % (> SAT_TOP_CV): not the flat signature
    weak = [[7000 * (1 + 0.174 * (v - i0.mean()))] * 4 for v in i0]      # 7000 / (1000 x 2.3) = 3x the day's rate
    r3 = saturation_check(_rate("hi", i0, weak), day_kcps)
    assert r3.high_rate == ["sdd1", "sdd2", "sdd3", "sdd4"] and not r3.saturated and "pile-up" in r3.notes[0]
    assert saturation_check(_rate("short", i0[:3], flat[:3]), day_kcps).ok           # too few probes: no verdict


def test_i0_check():
    assert not i0_check(2.3, 2.4).bad
    r = i0_check(0.02, 2.4)
    assert r.bad and "I0 invalid" in r.note
    assert i0_check(-0.1, float("nan")).bad
    assert not i0_check(float("nan"), 2.0).bad


def test_sdd_consistency_flags_a_deviating_detector(fake_project):
    proj = Project.discover(fake_project)
    sd = StackData.load(proj, proj.find(testdata.STACK))
    # make a longer synthetic stack trace set: use the real arrays but build traces by hand
    E = np.linspace(680, 730, 101)
    base = 1 + 2 / (1 + np.exp(-(E - 695) / 1.5)) + 0.3 * np.exp(-((E - 700) / 3) ** 2)
    rng = np.random.default_rng(1)
    det = [base * s + rng.normal(0, 0.01, E.size) for s in (1.0, 0.9, 1.1, 1.0)]
    det[2] = det[2] + 1.5 * np.exp(-((E - 715) / 4) ** 2)            # SDD3 has an extra feature (75 % of the step)
    from aurora.core.zones import ZoneTraces
    i0 = np.full(E.size, 2.0)
    t = ZoneTraces(E=E, n_pix=4, line="F Kα", det=[None] * 4, det_raw=det, sum=None, sum_raw=None, tey=np.zeros(E.size),
                   tey_raw=np.zeros(E.size), i0=i0, i0_used=i0, i0_raw=i0, spikes={"sdd1": [3, 7], "tey": []})
    c = sdd_consistency(t)
    assert [d for d, _ in c.flags] == ["sdd3"] and "differs" in c.flags[0][1]
    assert c.normalised.shape == (101, 4)
    c2 = sdd_consistency(t, saturated=["sdd1"])
    assert c2.flags == [("sdd1", "saturated")] and c2.normalised is None
    assert spike_summary(t) == {"sdd1": 2}
    del sd


def test_probe_and_candidates_on_fake_project(fake_project):
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    cr = count_rate_probe(proj, scan)
    assert len(cr.rows) == 5 and cr.time_per_map_s == 6.0 and cr.rows[0]["dwell_s"] == pytest.approx(0.5)
    assert np.isfinite(cr.top_kcps).all() and cr.i0_level == pytest.approx(2.5, abs=0.05)
    assert (fake_project / ".aurora" / "cache" / testdata.DAY / testdata.STACK / "_count_rate.json").exists()
    cr2 = count_rate_probe(proj, scan)
    assert cr2.rows == cr.rows                                         # cached
    sd = StackData.load(proj, scan)
    cands = i0_candidates({"x": sd}, {"x": "stack"})
    assert len(cands) == 1 and cands[0].looks_good and cands[0].nonpos == 0
    t = zone_traces(sd, np.arange(12), ScanSettings(line="total XRF"))
    c = sdd_consistency(t)
    assert c.note.startswith("only") or c.normalised is not None       # 5 energies: too few for a verdict
