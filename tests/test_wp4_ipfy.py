"""I26: IPFY computed when asked (yields.ipfy), one definition everywhere; lines.default_signal; files without 'sig'
read as PFY."""
import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.io.config import ScanSettings, Zone, ZonesFile
from aurora.core.lines import default_signal, line_element
from aurora.core.model import Project
from aurora.core.spectra import NORM_MODES, build_traces
from aurora.core.stack import StackData
from aurora.core.yields import CLS_OFFSET, ipfy
from aurora.core.zones import members, zone_sums, zone_traces


def test_ratio_is_affine_in_the_total_absorption():
    """Thick sample, non-resonant line: S_nr = c I0 w / (mu_tot(E) + mu_f g). I0 / S_nr = (mu_tot + mu_f g) / (c w)."""
    E = np.linspace(680, 730, 201)
    mu_tot = 1.0 + 2.0 / (1 + np.exp(-(E - 696.7) / 1.2)) + 0.6 * np.exp(-((E - 699) / 1.5) ** 2)   # known step
    I0 = 2.5 * (1 - 0.002 * (E - 680)) + 0.05 * np.sin(E / 3)              # a drifting incident flux
    c, w, mu_f, g = 1e4, 0.02, 0.8, 1.3
    S = c * I0 * w / (mu_tot + mu_f * g)
    r = ipfy(S, I0, "ratio")
    assert r.n_nonpos == 0
    assert np.corrcoef(r.y, mu_tot)[0, 1] > 0.9999
    assert np.allclose(r.y, (mu_tot + mu_f * g) / (c * w), rtol=1e-12)
    # points without counts are no ratio: NaN, counted
    S2 = S.copy(); S2[[3, 7]] = 0.0; S2[9] = -1.0
    r2 = ipfy(S2, I0, "ratio")
    assert r2.n_nonpos == 3 and np.isnan(r2.y[[3, 7, 9]]).all() and np.isfinite(np.delete(r2.y, [3, 7, 9])).all()


def _cls_dashboard(raw, mcc1):
    """plot_sgm_bsky_data.py (SGM_Beamline_Analysis), 'Update Normalized SDD Lines' with ipfy_mode on, verbatim."""
    active_i0 = np.abs(mcc1.copy())
    i0_safe = np.where(active_i0 <= 0, 1.0, active_i0)
    norm_data = raw / i0_safe
    norm_data = -norm_data
    offset = np.abs(np.min(norm_data)) + 500
    norm_data += offset
    return norm_data


def test_cls_and_neg_are_the_compatibility_forms():
    S = np.array([120.0, 150.0, 180.0, 400.0, 390.0, 385.0])
    I0 = np.array([2.0, 2.1, 0.0, -2.2, 2.3, 2.4])
    assert np.array_equal(ipfy(S, I0, "cls").y, _cls_dashboard(S, I0)) and CLS_OFFSET == 500
    with np.errstate(all="ignore"):
        assert np.array_equal(ipfy(S, I0, "neg").y, -np.where(I0 > 0, S / I0, np.nan), equal_nan=True)
    with pytest.raises(ValueError):
        ipfy(S, I0, "whatever")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    from aurora.cli import cmd_demo
    root = tmp_path_factory.mktemp("ipfy") / "demo"
    cmd_demo(root)
    proj = Project.discover(root)
    sd = StackData.load(proj, proj.find(testdata.STACK))
    z = Zone(id=1, label="a", rects=[list(sd.bbox)])
    return sd, members(z, sd.x, sd.y, sd.pitch)


def test_ipfy_traces_on_the_demo_are_i0_over_the_counts(demo):
    sd, pix = demo
    for line in ("O Kα", "F Kα"):
        t = zone_traces(sd, pix, ScanSettings(line=line, fluo_norm="raw", signal="ipfy", despike=False))
        assert t.signal == "ipfy" and np.allclose(t.sum, t.i0_used / t.sum_raw, rtol=1e-12)
        assert f"signal: IPFY (I0 / {line}, ratio)" in t.notes
        assert t.trace_names()["sum"] == "ipfy_sum_norm" and t.trace_names()["sdd2"] == "ipfy_sdd2_norm"
        # equal detectors give the sum's trace: per-SDD IPFY uses n_ticked x S_k, on the sum's scale
        mean_det = np.mean([1 / d for d in t.det], axis=0)
        assert np.allclose(1 / mean_det, t.sum, rtol=0.25)
        tj = zone_traces(sd, pix, ScanSettings(line=line, fluo_norm="i0jump", signal="ipfy"))
        post = (tj.E >= tj.post[0]) & (tj.E <= tj.post[1])
        assert np.isclose(np.nanmean(tj.sum[post]), 1.0)              # edge jump = 1 after the pre-divided path
    pfy = zone_traces(sd, pix, ScanSettings(line="O Kα"))
    assert pfy.signal == "pfy" and pfy.trace_names()["sum"] == "fluo_sum_norm" and not any("IPFY" in n for n in pfy.notes)


def test_ipfy_edge_jump_positive_in_every_normalisation_mode(demo):
    """The demo's synthetic raw data has no non-resonant response (its 'O Kα' window holds the tail of the F Kα peak,
    which rises with the edge step at 682 eV, so I0 / S falls there, correctly). Here the demo stack's O Kα counts are
    replaced, in memory only, by the thick-sample model of a non-resonant line falling across that step."""
    sd, pix = demo
    k = sd.roi_index("O Kα")
    step = 1.0 / (1 + np.exp(-(sd.E - 682.0) / 0.8))
    mu_tot = 1.0 + 1.5 * step                                         # total absorption with the demo's step
    i0 = sd.i0                                                        # (nE, n_pix)
    model = sd.roi_pix_det.astype(float).copy()
    for j in range(4):
        model[:, :, k, j] = 400.0 * i0 / (mu_tot[:, None] + 0.5)
    sums = zone_sums(sd, pix)
    sums.det = {**sums.det, "O Kα": np.nansum(model[:, pix, k, :], axis=1)}
    for mode in NORM_MODES:
        t = build_traces(sums, ScanSettings(line="O Kα", fluo_norm=mode, signal="ipfy"))
        pre = (t.E >= t.pre[0]) & (t.E <= t.pre[1])
        post = (t.E >= t.post[0]) & (t.E <= t.post[1])
        jump = np.nanmean(t.sum[post]) - np.nanmean(t.sum[pre])
        assert jump > 0, (mode, jump)
        for d in t.det:
            assert np.nanmean(d[post]) - np.nanmean(d[pre]) > 0, mode
    # PFY of the same line falls across the step: the sign is the physics, not the normalisation
    t = build_traces(sums, ScanSettings(line="O Kα", fluo_norm="i0", signal="pfy"))
    assert np.nanmean(t.sum[-1:]) < np.nanmean(t.sum[:1])


def test_a_session_without_sig_loads_as_pfy(demo):
    sd, pix = demo
    zf = ZonesFile.from_json({"holders": {"holderA": {"holder": "holderA", "zones": [{"id": 1, "label": "a", "st": {
        sd.id: {"inc": True, "sdd": [1, 1, 1, 1], "line": "O Kα", "fn": "i0jump", "tey": True, "tn": "i0jump",
                "ds": True, "k": 6, "i0src": "own", "pre": None, "post": None, "stack": sd.name}}}]}}})
    st = zf.holders["holderA"].zones[0].settings[sd.id]
    assert st.signal == "pfy" and st.ipfy_mode == "ratio" and "sig" not in st.to_json()
    assert zone_traces(sd, pix, st).signal == "pfy"


class _Scan:
    def __init__(self, edge, mode=None):
        self.edge = edge
        self.summary = {"mode": mode} if mode else {}


def test_default_signal():
    assert default_signal(_Scan(("U", "N45")), {"mode": "IPFY O Kα"}) == "ipfy"
    assert default_signal(_Scan(("U", "N45"), "IPFY O Kα")) == "ipfy"                 # the scan's own summary
    assert default_signal(_Scan(("F", "K")), {"mode": "PFY F Kα"}) == "pfy"
    assert default_signal(_Scan(("F", "K")), {}, line="O Kα") == "ipfy"                # non-resonant line chosen
    assert default_signal(_Scan(("F", "K")), {}, line="F Kα") == "pfy"
    assert default_signal(_Scan(("F", "K")), {}, line="total XRF") == "pfy"
    assert default_signal(None, {}) == "pfy" and default_signal(object(), {"mode": "PFY F Kα"}) == "pfy"
    assert line_element("Fe Lα") == "Fe" and line_element("total XRF") is None and line_element("") is None


def test_easy_defaults_agree_with_the_check_defaults():
    """Reduce's 'advanced settings in use' chip compares a zone's settings with mode.easy_scan_settings; new settings
    also pass through check.science_defaults. Both must give the coherent de-spike and the same signal, or every new
    zone would show the chip in Easy."""
    import copy

    pytest.importorskip("pyqtgraph")
    from aurora.app import mode
    from aurora.app.check import science_defaults
    from aurora.app.main import create_app
    from aurora.app.reduce import easy_differences
    create_app(["aurora-tests"])
    cases = ((("F", "K"), "PFY F Kα", None), (("U", "N45"), "IPFY O Kα", None), (("F", "K"), None, ["O Kα", "total XRF"]),
             (("F", "K"), None, ["F Kα", "O Kα", "total XRF"]))
    for edge, mode_name, labels in cases:
        scan = _Scan(edge, mode_name)
        scan.name = "s"
        easy = mode.easy_scan_settings(scan, [], labels=labels)
        new = copy.deepcopy(easy)
        science_defaults(scan, new)
        assert easy.despike_mode == "coherent", (edge, mode_name, labels)
        assert easy_differences(new, easy) == [], (edge, mode_name, labels, easy_differences(new, easy))
    assert mode.EASY_DEFAULTS["zone_scan"]["despike_mode"] == "coherent"
