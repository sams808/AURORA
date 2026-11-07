"""Checks against the Nov 2026 beamtime folder (skipped where it is not mounted)."""
import numpy as np
import pytest

from aurora.core.io.cache import ReducedCache, load_count_rate
from aurora.core.io.config import read_sample_sheet
from aurora.core.io.h5 import find_h5, xy
from aurora.core.io.raw import read_point_dir
from aurora.core.model import Project

DAY = "2026-11-05"
SBOF = "092758_alpha_beta_f_k_stack"
# Zone "13 alpha_beta" of sgm_report v2.5 (layout cell row 1 col 1, 18 px, centre x = 3.73 y = 5.86 mm). The pixel list is not
# stored in summary.json; it was identified on 2026-11-06 by a non-negative least-squares fit of the CSV's I0 and RAW
# traces against the cache's per-pixel traces (weights came out exactly 0 or 1, residual 1e-11). Two of the 18 are not
# among the 18 pixels nearest the centroid: the zone follows the plate layout grid, not a disc.
SBOF_ZONE_13_PIXELS = [81, 82, 83, 90, 91, 92, 93, 94, 103, 104, 105, 106, 107, 111, 112, 113, 115, 116]
SBOF_ZONE_13_CENTRE = (3.7252661975295487, 5.856076761314025)


@pytest.fixture(scope="module")
def project(real_project):
    return Project.discover(real_project / "raw" / DAY)


def _read_zone_csv(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = [l for l in lines if l.startswith("#")]
    cols = next(l for l in lines if not l.startswith("#")).split(",")
    data = np.genfromtxt(path, delimiter=",", comments="#", skip_header=len(header) + 1)
    return header, cols, data


def test_day_discovery(project):
    day = project.days[0]
    names = {h.name for h in day.holders}
    assert {"N5", "Se", "N6"} <= names
    n5 = next(h for h in day.holders if h.name == "N5")
    assert n5.survey.name == "010013_holderA_survey"          # the three earlier surveys were superseded
    assert len(n5.superseded_surveys) == 3
    assert any(s.name == SBOF for s in n5.stacks)
    sbof = project.find(SBOF)
    assert sbof.status == "complete" and sbof.n_energies == 311 and sbof.n_points == 121
    assert sbof.edge == ("F", "K") and (sbof.e_min, sbof.e_max) == (680.0, 730.0)
    assert project.find("143504_photodiode_f_k_stack").status == "test"
    assert project.find("154516_ZnSe_coarse_se_l3l2_coarse_stack").edge == ("Se", "L3L2")
    assert project.calibration.calibrated
    assert "F Kα" in project.roi_bins.labels


def test_summary_flags(project):
    se = project.find("155838_sampleD_coarse_se_l3l2_coarse_stack")
    assert se is not None and se.summary, "sgm_report summary expected for the Se scans"
    assert se.summary.get("i0_bad") is True and "I0 invalid" in se.flags


def test_reduced_cache_reproduces_sgm_report_zone_csv(real_project):
    """Acceptance check of docs/HANDOFF.md section 3.3: zone '13 alpha_beta' from the per-pixel cache equals the CSV."""
    proc = real_project / "processed" / DAY / SBOF
    rc = ReducedCache.load(proc)
    assert rc is not None and rc.n_energies == 311 and rc.n_pix == 121
    assert rc.roi_labels == ["O Kα", "Sb Mα", "F Kα", "total XRF"]
    assert (~rc.valid).sum() == 1 and rc.skipped[0][0] == 696.8         # one incomplete map (119/121 points)
    x, y = xy(find_h5(real_project / "raw" / DAY / SBOF))
    idx = np.array(SBOF_ZONE_13_PIXELS)
    assert np.allclose((x[idx].mean(), y[idx].mean()), SBOF_ZONE_13_CENTRE, atol=1e-9)
    header, cols, data = _read_zone_csv(proc / "13_alpha_beta_xanes.csv")
    assert header[0].startswith(f"# scan: {SBOF}   zone 1/4: 13 alpha_beta (18 pixels")
    assert data.shape[0] == 311
    ok = rc.valid & np.isfinite(data[:, cols.index("RAW_FKa")])
    assert ok.sum() == 310
    j = rc.roi_labels.index("F Kα")
    i0 = rc.i0[:, idx].sum(axis=1)
    raw = rc.roi_pix_det[:, idx, j, :].sum(axis=(1, 2))
    # the CSV carries 6 significant digits (54.6009 for 54.600889): counts are integers and match exactly
    assert np.allclose(i0[ok], data[ok, cols.index("I0_sum_V")], rtol=2e-6, atol=0)
    assert np.allclose(raw[ok], data[ok, cols.index("RAW_FKa")], rtol=0, atol=1e-6)
    for k in range(4):
        per_det = rc.roi_pix_det[:, idx, j, k].sum(axis=1)
        assert np.allclose(per_det[ok], data[ok, cols.index(f"RAW_FKa_SDD{k + 1}")], rtol=0, atol=1e-6)
    assert np.allclose(raw[ok] / i0[ok], data[ok, cols.index("PFY_FKa_over_I0")], rtol=1e-5, atol=0)
    assert np.isnan(data[~rc.valid, cols.index("PFY_FKa_over_I0")]).all()


def test_count_rate_cache(real_project):
    d = load_count_rate(real_project / "processed" / DAY / SBOF)
    assert d and len(d["rows"]) == 7 and d["time_per_map_s"] > 0


def test_sample_sheet_real(real_project):
    sheets = list(real_project.glob("samples*.csv"))
    if not sheets:
        pytest.skip("sample sheet not found")
    rows = read_sample_sheet(sheets[0])
    assert len(rows) > 100
    assert "Group 7" in {r.group for r in rows}


def test_first_energy_read(project):
    s = project.find(SBOF)
    e, p = s.energy_dirs[0]
    pd = read_point_dir(p)
    assert e == 680.0 and pd.n_pix == 121 and pd.complete and pd.mcc.shape[1] == 8
