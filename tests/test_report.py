"""The group-organised report, on the synthetic project and on the real zones file."""
import json

import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.io.config import HolderZones, ScanSettings, Zone, ZonesFile
from aurora.core.model import Project
from aurora.core.report import ReportBuilder, slug
from aurora.core.stack import StackData


def _fake_zones(proj: Project) -> ZonesFile:
    return testdata.make_fake_zones(proj)


def test_report_on_fake_project(fake_project, tmp_path):
    proj = Project.discover(fake_project)
    zf = _fake_zones(proj)
    out = tmp_path / "report"
    res = ReportBuilder(proj, zf, out, title="Test report").build()
    assert res.pdf.exists() and res.pdf.stat().st_size > 10_000
    assert res.entries == 2 and res.pages >= 5                # cover, 2 group figures, appendix, map, QC
    assert len(res.figures) == 2 and all(f.exists() for f in res.figures)
    assert {f.stem for f in res.figures} == {"Group_1_F_K_p1", "Ungrouped_F_K_p1"}
    assert res.folder.parent == out and res.pdf.parent == res.folder        # one folder per build (I39)
    assert all((res.folder / "figures" / f"{f.stem}.svg").exists() for f in res.figures)
    assert len(res.csvs) == 2
    csv = next(c for c in res.csvs if "1_alpha" in c.name)
    header = [l for l in csv.read_text(encoding="utf-8").splitlines() if l.startswith("#")]
    assert any("zone: 1 alpha" in l for l in header) and any("edge jump = 1" in l for l in header)
    data = np.loadtxt(csv, delimiter=",", skiprows=len(header) + 1)
    assert data.shape == (5, 15) and np.allclose(data[:, 0], testdata.ENERGIES)
    man = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert man["pages"] == res.pages and man["not_included"] == [["holderA", "3 gamma", testdata.STACK]]
    assert man["groups"]["Group 1"] == [f"1 alpha · holderA · 09:27 F K"]
    # group filter
    res2 = ReportBuilder(proj, zf, tmp_path / "r2", groups=["Group 1"], appendix=False, maps=False).build()
    assert res2.entries == 1 and len(res2.figures) == 1 and res2.pages == 3


def test_slug():
    assert slug("F Kα / 13 alpha_beta") == "F_Ka_13_alpha_beta" and slug("") == "x"


def test_report_on_real_zones(real_project, real_expect, tmp_path):
    zpath = real_project / "zones_html" / "zones_all.json"
    if not zpath.exists():
        pytest.skip("zones_all.json not exported yet")
    ex = real_expect["report"]
    day = real_expect["day"]
    proj = Project.discover(real_project / "raw" / day)
    zf = ZonesFile.load(zpath)
    for key in list(zf.holders):
        if zf.holders[key].day != day:
            del zf.holders[key]
    res = ReportBuilder(proj, zf, tmp_path / "real", groups=ex["groups"], png_dpi=150).build()
    assert res.pdf.exists() and res.entries >= ex["min_entries"] and res.pages >= ex["min_pages"]
    assert not [s for s in res.skipped if "not in the project" in s]
    for word in ex["figure_words"]:
        assert any(word in f.stem for f in res.figures), word
