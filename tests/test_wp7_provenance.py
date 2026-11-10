"""I41: provenance on every report CSV (header + JSON sidecar), a manifest that holds the settings, and verify()."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from aurora.core import provenance, testdata
from aurora.core.io.config import ScanSettings, Zone
from aurora.core.model import Project
from aurora.core.report import MANIFEST, ReportBuilder
from aurora.core.spectra import good_i0_curve
from aurora.core.stack import StackData
from aurora.core.zones import members, zone_traces


def test_header_round_trip():
    meta = {"zone": "13 alpha_beta (holder N5, zone id 13, group Minerals)", "plain": "a: b, c", "number_text": "13",
            "bool_text": "true", "null_text": "null", "list_text": "[1, 2]", "quoted": '"x"', "empty": "",
            "spaced": "  padded ", "multi": "line one\nline two", "int": 6, "float": 0.1, "nan": float("nan"),
            "none": None, "bool": False, "list": [680.0, 692.5], "dict": {"sdd1": [60, 74]},
            "nested": {"rects": [[-0.5, 0.25, 4.9, 5.6]], "polys": []}, "unicode": "F Kα · ±"}
    lines = provenance.header_block(meta)
    assert lines[0] == provenance.first_line() and lines[0].startswith("# AURORA ")
    assert "(version 2 of John Bussey's SGM XAS GUI)" in lines[0]
    assert all(ln.startswith("# ") and "\n" not in ln for ln in lines)
    back = provenance.parse_header(lines + ["energy_eV,a,b", "680,1,2"])
    assert set(back) == set(meta)
    for k, v in meta.items():
        if isinstance(v, float) and math.isnan(v):
            assert math.isnan(back[k])
        else:
            assert back[k] == v and type(back[k]) is type(v), k
    assert "# zone: 13 alpha_beta (holder N5, zone id 13, group Minerals)" in lines          # human lines stay plain
    with pytest.raises(ValueError):
        provenance.header_block({"bad: key": 1})
    assert provenance.sidecar_path(Path("a/b.csv")) == Path("a/b.json")


@pytest.fixture
def demo(tmp_path):
    """The demo beamtime (fixed ROI bins with F Kα, a sample sheet) with the three fake zones plus one zone on the
    F Kα line that uses the good-I0 mean of a ticked good scan."""
    from aurora.cli import cmd_demo
    root = tmp_path / "demo"
    cmd_demo(root)
    proj = Project.discover(root)
    zf = testdata.make_fake_zones(proj)
    scan = proj.find(testdata.STACK)
    sid = f"{scan.day}_{scan.name}"
    sd = StackData.load(proj, scan)
    x0, x1, y0, y1 = sd.bbox
    z4 = Zone(id=4, label="4 delta on F Ka", group="Minerals", rects=[[x0, x1, y0, (y0 + y1) / 2]],
              paint=[[float(sd.x[-1]), float(sd.y[-1]), sd.pitch[0], sd.pitch[1]]])
    z4.settings[sid] = ScanSettings(stack=scan.name, line="F Kα", n=0, i0_source="good", fluo_norm="area",
                                    sdd=[True, True, False, True])
    zf.holders["N5"].zones.append(z4)
    zf.good_i0[sid] = True
    scan.summary = {"sdd_saturated": ["sdd3"]}
    return proj, zf


def _recompute(proj, h: dict):
    """The traces of one CSV, recomputed from its parsed header alone (settings, geometry, good-I0 contributors)."""
    scan = proj.find(h["sid"].split("_", 1)[1])
    sd = StackData.load(proj, scan)
    g = h["zone_geometry"]
    z = Zone(id=h["zone_id"], label=h["zone_label"], rects=g["rects"], erects=g["erects"], paint=g["paint"],
             erase=g["erase"], polys=g["polys"])
    pix = members(z, sd.x, sd.y, sd.pitch)
    st = ScanSettings.from_json(h["settings"])
    good = None
    if st.i0_source == "good" and h["good_i0_contributors"]:
        curves = []
        for gsid in h["good_i0_contributors"]:
            gsd = StackData.load(proj, proj.find(gsid.split("_", 1)[1]))
            curves.append((gsd.E, gsd.i0_mean()))
        good = good_i0_curve(sd.E, curves)
    return pix, zone_traces(sd, pix, st, good)


def test_every_csv_carries_its_provenance_and_reproduces(demo, tmp_path):
    proj, zf = demo
    res = ReportBuilder(proj, zf, tmp_path / "reports", png_dpi=40, page_png_dpi=0).build()
    assert len(res.csvs) == 3
    zones = {z.id: z for z in zf.holders["N5"].zones}
    for csv in res.csvs:
        text = csv.read_text(encoding="utf-8")
        assert text.startswith("# AURORA ") and text.splitlines()[0] == provenance.first_line()
        h = provenance.read_header(csv)
        z = zones[h["zone_id"]]
        st = next(iter(z.settings.values()))
        assert ScanSettings.from_json(h["settings"]).to_json() == st.to_json()          # the settings dict, back
        assert h["data_source"] == "raw reduce" and h["data_source_file"].startswith(".aurora/cache/")
        assert h["signal"] == "PFY" and h["members"] > 0
        assert "SDD saturated: SDD3 (sgm_report)" in h["flags"]
        roi = h["sdd_roi_channels"]
        if h["line"] == "F Kα":
            assert roi == {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76], "sdd4": [63, 77]}
            assert h["sdd_roi_channels_from"].startswith("config/roi_bins.json")
            assert h["good_i0_contributors"] == [f"{testdata.DAY}_{testdata.STACK}"] and h["I0 source"] == "good-I0 mean"
        else:
            assert roi == {d: [5, 200] for d in ("sdd1", "sdd2", "sdd3", "sdd4")}
        assert set(h["spikes_eV"]) >= {"i0", "sdd1", "tey"} or not st.despike
        assert all(e in testdata.ENERGIES for v in h["spikes_eV"].values() for e in v)
        # the numbers, recomputed from the header alone
        pix, t = _recompute(proj, h)
        assert pix.size == h["members"]
        from aurora.core.compare import read_csv_table
        _hdr, cols, data = read_csv_table(csv)
        assert cols == h["columns"].split(", ")
        ref = {"energy_eV": t.E, "I0_zone_sum": t.i0_raw, "I0_used": t.i0_used, "TEY_raw": t.tey_raw, "TEY_norm": t.tey,
               "fluo_sum_raw": t.sum_raw, "fluo_sum_norm": t.sum}
        for k in range(4):
            ref[f"sdd{k + 1}_raw"], ref[f"sdd{k + 1}_norm"] = t.det_raw[k], t.det[k]
        for name, want in ref.items():
            got = data[:, cols.index(name)]
            assert np.allclose(got, np.asarray(want, float), rtol=1e-12, atol=1e-300, equal_nan=True), (csv.name, name)
        # the sidecar holds the same items
        side = json.loads(provenance.sidecar_path(csv).read_text(encoding="utf-8"))
        assert side["aurora"].startswith("AURORA ") and side["settings"] == h["settings"]
        assert side["member_indices"] == pix.tolist() and side["zone_geometry"] == h["zone_geometry"]


def test_every_manifest_entry_has_settings_members_flags_and_sha256(demo, tmp_path):
    proj, zf = demo
    res = ReportBuilder(proj, zf, tmp_path / "reports", png_dpi=40, page_png_dpi=0).build()
    man = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert len(man["entries"]) == res.entries == 3
    for e in man["entries"]:
        assert {"settings", "members", "flags", "sha256"} <= set(e)
        assert e["settings"]["fn"] and e["members"] and len(e["members"]) == e["n_members"]
        assert {"text": "SDD saturated: SDD3", "kind": "sdd_saturated", "source": "sgm_report", "dets": ["sdd3"]} in e["flags"]
        csv = res.folder / e["csv"]
        assert provenance.sha256(csv) == e["sha256"] and (res.folder / e["sidecar"]).exists()
    assert man["options"] == {"preset": "final", "groups": None, "holders": None, "days": None, "png_dpi": 40,
                              "appendix": True, "maps": True, "page_png_dpi": 0}


def test_verify_passes_unchanged_and_names_the_entry_that_changed(demo, tmp_path):
    proj, zf = demo
    res = ReportBuilder(proj, zf, tmp_path / "reports", png_dpi=40, page_png_dpi=0).build()
    v = ReportBuilder(proj, zf, tmp_path / "reports").verify(res.manifest)
    assert v.ok and bool(v) and v.checked == 3 and not v.differences and "reproduce" in v.text()
    assert ReportBuilder(proj, zf, tmp_path).verify(res.folder).ok                    # the folder works too
    z1 = zf.holders["N5"].zones[0]
    next(iter(z1.settings.values())).fluo_norm = "max"
    v = ReportBuilder(proj, zf, tmp_path / "reports").verify(res.manifest)
    assert not v.ok and len(v.differences) == 1
    assert v.differences[0].startswith("N5 · '1 alpha' (zone 1) · 09:27: the CSV differs") and "fluo_norm" in v.differences[0]
    assert "1 alpha" in v.text()
    # a zone removed and one included again are named as well
    zf.holders["N5"].zones.pop(1)
    next(iter(zf.holders["N5"].zones[1].settings.values())).include = True
    v = ReportBuilder(proj, zf, tmp_path / "reports").verify(res.manifest)
    assert any("'2 beta'" in d and "no longer in the report" in d for d in v.differences)
    assert any("'3 gamma'" in d and "not in that build" in d for d in v.differences)
    with pytest.raises(ValueError):
        ReportBuilder(proj, zf, tmp_path).verify(tmp_path / "nothing" / MANIFEST)


def test_notes_err_and_masked_energies_reach_the_csv(demo, tmp_path, monkeypatch):
    from aurora.core import report as core_report
    proj, zf = demo
    real = core_report.zone_traces

    def with_extras(sd, pix, st, good=None):
        t = real(sd, pix, st, good)
        t.notes = ["1 energy with I0 below 0.2 x its median left out (NaN)"]
        t.err = {"fluo_sum": np.full(len(t.E), 0.01)}
        t.masked = {"fluo_sum": np.array([False, True, False, False, False])}
        return t
    monkeypatch.setattr(core_report, "zone_traces", with_extras)
    res = ReportBuilder(proj, zf, tmp_path / "reports", png_dpi=40, page_png_dpi=0).build()
    h = provenance.read_header(res.csvs[0])
    assert "1 energy with I0 below 0.2 x its median left out (NaN)" in h["notes"]
    assert h["masked_energies_eV"] == {"fluo_sum": [testdata.ENERGIES[1]]}
    assert h["columns"].endswith("fluo_sum_err")
    from aurora.core.compare import read_csv_table
    _h, cols, data = read_csv_table(res.csvs[0])
    assert np.allclose(data[:, cols.index("fluo_sum_err")], 0.01)


def test_cover_and_csvs_name_the_calibration_and_groups_follow_the_sheet(tmp_path):
    """The cover prints the whole calibration line (wrapped, never cut at the page width, so sdd4 is there), every CSV
    names the calibration file, and the groups follow the sample sheet's order, not a list kept in the code."""
    from aurora.core.io.config import SheetRow
    root = testdata.make_fake_project(tmp_path / "beamtime")
    gains = {"sdd1": (10.058, 3.9), "sdd2": (9.398, -3.6), "sdd3": (9.852, -1.3), "sdd4": (10.090, 7.9)}
    (root / "config" / "sdd_calibration.json").write_text(json.dumps(
        {**{d: {"gain": g, "offset": o} for d, (g, o) in gains.items()},
         "_metadata": {"last_updated": "2026-11-04 14:06:43"}}), encoding="utf-8")
    proj = Project.discover(root)
    zf = testdata.make_fake_zones(proj)
    zf.holders["N5"].zones[1].group = "Zeta glass"                      # '1 alpha' stays in Minerals
    sheet = [SheetRow(plate="N5", group=g, sample_id=s, composition="", f_k_edge="", other_edges="", notes="")
             for g, s in (("Zeta glass", "2 beta"), ("Minerals", "1 alpha"))]
    b = ReportBuilder(proj, zf, tmp_path / "reports", sheet_rows=sheet, png_dpi=40, page_png_dpi=0)
    res = b.build()
    cover = " ".join(ln.strip() for ln in b.cover_lines)
    assert ("SDD calibration config/sdd_calibration.json (last updated 2026-11-04 14:06:43): sdd1 10.058 eV/ch +3.9; "
            "sdd2 9.398 eV/ch -3.6; sdd3 9.852 eV/ch -1.3; sdd4 10.090 eV/ch +7.9") in cover
    assert all(len(ln) <= 110 for ln in b.cover_lines)
    start = b.cover_lines.index("Contents") + 1
    assert [ln.split(":")[0].strip() for ln in b.cover_lines[start:start + 2]] == ["Zeta glass", "Minerals"]
    for csv in res.csvs:
        cal = provenance.read_header(csv)["calibration"]
        assert cal.startswith("SDD calibration config/sdd_calibration.json") and "sdd4 10.090 eV/ch +7.9" in cal
