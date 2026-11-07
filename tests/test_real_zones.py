"""The manual zones Sami exported from the HTML tool (zones_all.json) against AURORA's geometry and loaders."""
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
    assert checked >= 200
    assert mismatches == 0


def test_stack_and_traces_for_a_manual_zone(project, zones):
    h = zones.holders["N5"]
    z = next(zz for zz in h.zones if zz.label == "13 alpha_beta")
    sid = next(iter(z.members))
    scan = project.find(sid[11:])
    sd = StackData.load(project, scan, allow_reduce=False)
    assert sd.source.startswith("sgm_report cache") and sd.main == "F Kα" and sd.edge == ("F", "K")
    assert sd.n_energies == 311 and sd.n_pix == 121
    pix = members(z, sd.x, sd.y, sd.pitch)
    assert pix.tolist() == z.members[sid] and len(pix) == z.settings[sid].n
    t = zone_traces(sd, pix, z.settings[sid])
    assert t.line == "F Kα" and t.n_pix == len(pix)
    ok = np.isfinite(t.sum)
    assert ok.sum() >= 300 and np.isclose(np.nanmean(t.sum[(t.E >= t.post[0]) & ok]), 1.0, atol=1e-9)
    assert np.nanmean(t.sum[(t.E < 685) & ok]) < 0.1 and 1.0 < np.nanmax(t.sum) < 2.0     # pre-edge at 0, white line above the jump
    assert all(d is not None for d in t.det)
    # ticked detectors average to the sum (per-SDD scale = sum scale / n ticked)
    mean_det = np.nanmean([d for d, on in zip(t.det, z.settings[sid].sdd) if on], axis=0)
    i = ok & (t.E > 700)
    assert np.allclose(mean_det[i], t.sum[i], rtol=1e-6, atol=1e-9)
    assert "sdd1" in t.spikes and "tey" in t.spikes


def test_survey_of_a_holder(project, zones):
    h = zones.holders["N5"]
    sv = SurveyData.load(project.find(h.survey), project.roi_bins)
    assert sv.n > 4000 and "F Kα" in sv.lines and sv.i0_usable
    g = sv.grid(sv.values("F Kα", per_i0=True, log10=True))
    assert g.img.shape[0] > 50 and g.img.shape[1] > 90
    assert 0.19 < sv.pitch[0] < 0.21 and 0.19 < sv.pitch[1] < 0.21
    se = zones.holders["Se"]
    sv2 = SurveyData.load(project.find(se.survey), project.roi_bins)
    assert not sv2.i0_usable                                         # the Se survey at 1995 eV: I0 unusable
