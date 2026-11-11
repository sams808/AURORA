"""Zones exported from the browser zone tool (zones_all.json) of a real beamtime against AURORA's geometry and loaders
(read-only; skipped without a real beamtime or its aurora_expectations.json, see tests/local_paths.py)."""
import numpy as np
import pytest

from aurora.core.io.config import ZonesFile
from aurora.core.io.h5 import find_h5, xy
from aurora.core.model import Project
from aurora.core.stack import StackData
from aurora.core.survey import SurveyData
from aurora.core.zones import grid_pitch, members, zone_traces


@pytest.fixture(scope="module")
def zones(real_project):
    p = real_project / "zones_html" / "zones_all.json"
    if not p.exists():
        pytest.skip("zones_all.json not exported yet")
    return ZonesFile.load(p)


@pytest.fixture(scope="module")
def project(real_project):
    return Project.discover(real_project)


def test_membership_reproduces_the_tool(real_project, zones):
    """Every (zone, stack) member list in the file must come out of the geometry alone."""
    xy_cache = {}
    checked = mismatches = 0
    for hname, h in zones.holders.items():
        for z in h.zones:
            for sid, mem in z.members.items():
                day, scan = sid[:10], sid[11:]
                if sid not in xy_cache:
                    h5 = find_h5(real_project / "raw" / day / scan)
                    x, y = xy(h5)
                    xy_cache[sid] = (x, y, grid_pitch(x, y))
                x, y, pitch = xy_cache[sid]
                # the tool's arrays are truncated to the cache's pixel count; member indices never exceed it
                got = members(z, x, y, pitch).tolist()
                checked += 1
                if got != mem:
                    mismatches += 1
                    print(f"MISMATCH {hname} zone {z.id} {z.label!r} {sid}: tool {len(mem)} px, aurora {len(got)} px")
    assert checked >= 1
    assert mismatches == 0


def test_membership_count(zones, real_expect):
    checked = sum(len(z.members) for h in zones.holders.values() for z in h.zones)
    assert checked >= real_expect["zones_checked_min"]


def test_stack_and_traces_for_a_manual_zone(project, zones, real_expect):
    ex = real_expect
    h = zones.holders[ex["holder"]]
    z = next(zz for zz in h.zones if zz.label == ex["zone"]["label"])
    sid = next(iter(z.members))
    scan = project.find(sid[11:])
    sd = StackData.load(project, scan, allow_reduce=False)
    assert sd.source.startswith("sgm_report cache") and sd.main == ex["line"] and sd.edge == tuple(ex["stack_edge"])
    assert sd.n_energies == ex["stack_energies"] and sd.n_pix == ex["stack_points"]
    pix = members(z, sd.x, sd.y, sd.pitch)
    assert pix.tolist() == z.members[sid] and len(pix) == z.settings[sid].n
    t = zone_traces(sd, pix, z.settings[sid])
    assert t.line == ex["line"] and t.n_pix == len(pix)
    ok = np.isfinite(t.sum)
    assert ok.sum() >= ex["stack_energies"] - 11 and np.isclose(np.nanmean(t.sum[(t.E >= t.post[0]) & ok]), 1.0, atol=1e-9)
    pre = (t.E < ex["zone"]["pre_edge_below_ev"]) & ok
    assert np.nanmean(t.sum[pre]) < 0.1 and 1.0 < np.nanmax(t.sum) < 2.0    # pre-edge at 0, white line above the jump
    assert all(d is not None for d in t.det)
    # ticked detectors average to the sum (per-SDD scale = sum scale / n ticked)
    mean_det = np.nanmean([d for d, on in zip(t.det, z.settings[sid].sdd) if on], axis=0)
    i = ok & (t.E > ex["zone"]["post_edge_above_ev"])
    assert np.allclose(mean_det[i], t.sum[i], rtol=1e-6, atol=1e-9)
    assert "sdd1" in t.spikes and "tey" in t.spikes


def test_survey_of_a_holder(project, zones, real_expect):
    ex = real_expect
    h = zones.holders[ex["holder"]]
    sv = SurveyData.load(project.find(h.survey), project.roi_bins)
    assert sv.n > ex["survey_min_points"] and ex["line"] in sv.lines and sv.i0_usable
    g = sv.grid(sv.values(ex["line"], per_i0=True, log10=True))
    rows, cols = ex["survey_grid_min"]
    assert g.img.shape[0] > rows and g.img.shape[1] > cols
    p = ex["survey_pitch_mm"]
    assert abs(sv.pitch[0] - p) < 0.01 and abs(sv.pitch[1] - p) < 0.01
    low = zones.holders[ex["low_i0_holder"]]
    sv2 = SurveyData.load(project.find(low.survey), project.roi_bins)
    assert not sv2.i0_usable                                         # a survey far above the I0 mesh's useful range
