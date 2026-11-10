"""Checks against a real beamtime folder, read-only (skipped where none is configured: tests/local_paths.py).

The structure checks hold for any beamtime. The checks of one beamtime's facts (its holders, scans, counts, the zone
an sgm_report CSV was made from) read them from its aurora_expectations.json, kept with the beamtime and never in
this repository (tests/real_expectations.example.json lists the keys)."""
import numpy as np
import pytest

from aurora.core.io.cache import ReducedCache, load_count_rate
from aurora.core.io.config import read_sample_sheet
from aurora.core.io.h5 import find_h5, xy
from aurora.core.io.raw import read_point_dir
from aurora.core.model import Project


@pytest.fixture(scope="module")
def whole(real_project):
    return Project.discover(real_project)


@pytest.fixture(scope="module")
def project(real_project, real_expect):
    return Project.discover(real_project / "raw" / real_expect["day"])


def _read_zone_csv(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = [l for l in lines if l.startswith("#")]
    cols = next(l for l in lines if not l.startswith("#")).split(",")
    data = np.genfromtxt(path, delimiter=",", comments="#", skip_header=len(header) + 1)
    return header, cols, data


# ----------------------------------------------------------------------------- any beamtime
def test_structure(whole):
    """Every day has holders, every stack belongs to one, every complete stack has its energy folders."""
    assert whole.days and whole.scans
    for day in whole.days:
        for h in day.holders:
            assert h.name, day.day
            for s in h.stacks:
                assert s.kind == "stack" and (s.status != "complete" or s.n_energies > 0), s.name
    stacks = [s for s in whole.scans if s.kind == "stack"]
    held = {id(s) for d in whole.days for h in d.holders for s in (*h.stacks, *h.dropped)}
    assert all(id(s) in held for s in stacks), [s.name for s in stacks if id(s) not in held]


def test_any_sample_sheet_parses(real_project):
    sheets = [p for p in sorted(real_project.glob("*.csv")) if read_sample_sheet(p)]
    if not sheets:
        pytest.skip("no sample sheet in the beamtime folder")
    rows = read_sample_sheet(sheets[0])
    assert all(r.sample_id for r in rows)


# ----------------------------------------------------------------------------- this beamtime (aurora_expectations.json)
def test_day_discovery(project, real_expect):
    ex = real_expect
    day = project.days[0]
    names = {h.name for h in day.holders}
    assert set(ex["holders"]) <= names
    h = next(h for h in day.holders if h.name == ex["holder"])
    assert h.survey.name == ex["holder_survey"]                  # the earlier surveys of the holder were superseded
    assert len(h.superseded_surveys) == ex["superseded_surveys"]
    assert any(s.name == ex["stack"] for s in h.stacks)
    st = project.find(ex["stack"])
    assert st.status == "complete" and st.n_energies == ex["stack_energies"] and st.n_points == ex["stack_points"]
    assert st.edge == tuple(ex["stack_edge"]) and [st.e_min, st.e_max] == ex["stack_range_ev"]
    assert project.find(ex["test_scan"]).status == "test"
    for name, edge in ex["edge_scans"].items():
        assert project.find(name).edge == tuple(edge), name
    assert project.calibration.calibrated
    assert ex["line"] in project.roi_bins.labels


def test_summary_flags(project, real_expect):
    s = project.find(real_expect["i0_bad_scan"])
    assert s is not None and s.summary, "sgm_report summary expected for this scan"
    assert s.summary.get("i0_bad") is True and "I0 invalid" in s.flags


def test_reduced_cache_reproduces_sgm_report_zone_csv(real_project, real_expect):
    """An sgm_report zone CSV, recomputed from the per-pixel cache: the zone's pixels are not stored in summary.json;
    they were identified once by a non-negative least-squares fit of the CSV's I0 and RAW traces against the cache's
    per-pixel traces (weights exactly 0 or 1) and kept in the expectations file."""
    ex, zx = real_expect, real_expect["zone"]
    proc = real_project / "processed" / ex["day"] / ex["stack"]
    rc = ReducedCache.load(proc)
    assert rc is not None and rc.n_energies == ex["stack_energies"] and rc.n_pix == ex["stack_points"]
    assert rc.roi_labels == ex["cache_lines"]
    assert (~rc.valid).sum() == 1 and rc.skipped[0][0] == ex["cache_skipped_ev"]      # one incomplete map
    x, y = xy(find_h5(real_project / "raw" / ex["day"] / ex["stack"]))
    idx = np.array(zx["pixels"])
    assert np.allclose((x[idx].mean(), y[idx].mean()), zx["centre"], atol=1e-9)
    header, cols, data = _read_zone_csv(proc / zx["csv"])
    assert header[0].startswith(zx["header_start"])
    assert data.shape[0] == ex["stack_energies"]
    tag = zx["column_tag"]                                           # 'FKa' for F Kα
    ok = rc.valid & np.isfinite(data[:, cols.index(f"RAW_{tag}")])
    assert ok.sum() == ex["stack_energies"] - 1
    j = rc.roi_labels.index(ex["line"])
    i0 = rc.i0[:, idx].sum(axis=1)
    raw = rc.roi_pix_det[:, idx, j, :].sum(axis=(1, 2))
    # the CSV carries 6 significant digits (54.6009 for 54.600889): counts are integers and match exactly
    assert np.allclose(i0[ok], data[ok, cols.index("I0_sum_V")], rtol=2e-6, atol=0)
    assert np.allclose(raw[ok], data[ok, cols.index(f"RAW_{tag}")], rtol=0, atol=1e-6)
    for k in range(4):
        per_det = rc.roi_pix_det[:, idx, j, k].sum(axis=1)
        assert np.allclose(per_det[ok], data[ok, cols.index(f"RAW_{tag}_SDD{k + 1}")], rtol=0, atol=1e-6)
    assert np.allclose(raw[ok] / i0[ok], data[ok, cols.index(f"PFY_{tag}_over_I0")], rtol=1e-5, atol=0)
    assert np.isnan(data[~rc.valid, cols.index(f"PFY_{tag}_over_I0")]).all()


def test_count_rate_cache(real_project, real_expect):
    d = load_count_rate(real_project / "processed" / real_expect["day"] / real_expect["stack"])
    assert d and len(d["rows"]) == real_expect["count_rate_rows"] and d["time_per_map_s"] > 0


def test_sample_sheet_real(real_project, real_expect):
    sheets = list(real_project.glob(real_expect["sheet_glob"]))
    if not sheets:
        pytest.skip("sample sheet not found")
    rows = read_sample_sheet(sheets[0])
    assert len(rows) > real_expect["sheet_min_rows"]
    assert real_expect["sheet_group"] in {r.group for r in rows}


def test_first_energy_read(project, real_expect):
    s = project.find(real_expect["stack"])
    e, p = s.energy_dirs[0]
    pd = read_point_dir(p)
    assert e == real_expect["stack_range_ev"][0] and pd.n_pix == real_expect["stack_points"] and pd.complete
    assert pd.mcc.shape[1] == 8
