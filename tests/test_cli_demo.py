"""`aurora demo <folder>` writes a beamtime the headless commands and the Project model accept: neutral names, a survey
that covers the stack, a stack across the F K edge, a sample sheet in the generic columns."""
import numpy as np

from aurora.cli import main
from aurora.core import testdata
from aurora.core.model import Project


def test_demo_command(tmp_path, capsys):
    assert main(["demo", str(tmp_path / "demo")]) == 0
    out = capsys.readouterr().out
    assert "demo beamtime written to" in out
    proj = Project.discover(tmp_path / "demo")
    assert len(proj.scans) == 4 and proj.roi_bins is not None and "F Kα" in proj.roi_bins.labels
    from aurora.core.io.config import read_sample_sheet
    rows = read_sample_sheet(tmp_path / "demo" / testdata.SHEET)
    assert [r.sample_id for r in rows] == ["1 alpha", "2 beta"]
    assert all(r.plate == proj.days[0].holders[0].name for r in rows)   # so Reduce offers the IDs for the holder
    assert [r.group for r in rows] == [testdata.GROUP1, testdata.GROUP2] and {r.other_edges for r in rows} == {"F K"}
    assert (tmp_path / "demo" / testdata.SHEET).read_text(encoding="utf-8").splitlines()[0] == \
        "Sample ID,Holder,Group,Composition,Edges,Notes"                  # the generic columns, no title line
    assert main(["project", str(tmp_path / "demo")]) == 0
    listing = capsys.readouterr().out
    assert "fixed ROI bins: F Kα, O Kα" in listing
    assert f"holder {testdata.HOLDER}: 1 complete, 2 dropped" in listing


def test_demo_holds_a_whole_easy_job(tmp_path):
    """PM-01 / PM-02 / PS-01: the stack spans the F K edge (xraydb 696.7 eV) with data before and after it, the survey
    holds the stack footprint with two bright pads under it, the aborted stack is aborted and the test stack a test."""
    import xraydb
    from aurora.cli import cmd_demo
    from aurora.core.stack import StackData
    from aurora.core.survey import SurveyData
    root = tmp_path / "demo"
    assert cmd_demo(root) == 0
    proj = Project.discover(root)
    h = proj.days[0].holders[0]
    assert h.name == testdata.HOLDER and [s.name for s in h.stacks] == [testdata.STACK]
    assert {s.name: s.status for s in h.dropped} == {testdata.ABORTED: "aborted", testdata.TEST: "test"}
    st = proj.find(testdata.STACK)
    edge = xraydb.xray_edge("F", "K").energy
    assert st.edge == ("F", "K") and st.e_min <= edge - 25 and st.e_max >= edge + 35      # the larch windows fit
    sd = StackData.load(proj, st)
    sv = SurveyData.load(proj.find(testdata.SURVEY), proj.roi_bins)
    sx0, sx1, sy0, sy1 = float(np.min(sv.x)), float(np.max(sv.x)), float(np.min(sv.y)), float(np.max(sv.y))
    x0, x1, y0, y1 = sd.bbox
    px, py = sv.pitch
    assert sx0 - px / 2 <= x0 and x1 <= sx1 + px / 2 and sy0 - py / 2 <= y0 and y1 <= sy1 + py / 2
    assert sv.energy > edge                                    # F K alpha is excited on the survey
    f = sv.values("F Kα", per_i0=True, log10=False)
    pads = np.array([testdata.pad_of(a, b) for a, b in zip(sv.x, sv.y)])
    plate, alpha, beta = (float(np.nanmean(f[pads == k])) for k in ("", "1 alpha", "2 beta"))
    assert alpha > 1.3 * beta and beta > 3 * plate             # two bright pads on a dark plate
    o = sv.values("O Kα", per_i0=True, log10=False)
    assert float(np.nanmean(o[pads == "2 beta"])) > 2 * float(np.nanmean(o[pads == "1 alpha"]))
    assert testdata.is_current_demo(root)


def test_demo_zones_normalise_in_compare(tmp_path):
    """The two halves of the demo stack (the tutorials' zones '1 alpha' and '2 beta') reduce to two spectra that the
    larch-style normalisation of Compare scales (finite everywhere, edge step about 1) with E0 near the F K edge."""
    from aurora.cli import cmd_demo
    from aurora.core import compare as cmp
    from aurora.core.io.config import ScanSettings, Zone
    from aurora.core.stack import StackData
    from aurora.core.zones import members, zone_traces
    root = tmp_path / "demo"
    assert cmd_demo(root) == 0
    proj = Project.discover(root)
    scan = proj.find(testdata.STACK)
    sd = StackData.load(proj, scan)
    x0, x1, y0, y1 = sd.bbox
    e0s = []
    for rect in ([x0, (x0 + x1) / 2, y0, y1], [(x0 + x1) / 2, x1, y0, y1]):
        pix = members(Zone(id=1, label="z", rects=[rect]), sd.x, sd.y, sd.pitch)
        t = zone_traces(sd, pix, ScanSettings(stack=scan.name, line="F Kα", n=len(pix)))
        r = cmp.normalise_larch(t.E, t.sum)
        assert len(pix) == 6 and np.isfinite(r.y).all() and 0.5 < r.step < 2.0, r.note
        assert abs(r.e0 - 696.7) < cmp.DEFAULT_PRE[1] - cmp.DEFAULT_PRE[0]      # no 'E0 far from the edge' note
        e0s.append(r.e0)
    assert abs(e0s[0] - e0s[1]) > 2                           # two different samples

