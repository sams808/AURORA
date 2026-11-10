"""aurora.core on the synthetic project: readers, names, grid, status, discovery, configs."""
import datetime as dt
import json

import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.constants import DETS, N_CH, sdd_geometry
from aurora.core.grid import to_grid
from aurora.core.io.config import Calibration, RoiBins, ScanSettings, Zone, ZonesFile, read_sample_sheet, summary_by_scan
from aurora.core.io.raw import energy_from_dirname, read_point_dir, stack_energy_dirs, stack_status
from aurora.core.model import Project, ScanInfo
from aurora.core.names import (day_from_path, holder_from_survey, pretty_edge, strip_scan_suffix, target_edge,
                               time_label)


# ----------------------------------------------------------------------------- names
@pytest.mark.parametrize("name, label", [
    ("095242_sampleA_f_k_u_n45_stack", "sampleA"),
    ("150255_sampleB_u_n45coarse_1_stack", "sampleB"),
    ("092758_alpha_beta_f_k_stack", "alpha_beta"),
    ("101456_gamma_f_k_stack", "gamma"),
    ("154516_sampleC_coarse_se_l3l2_coarse_stack", "sampleC"),
    ("010013_holderA_survey", "holderA"),
])
def test_strip_scan_suffix(name, label):
    assert strip_scan_suffix(name) == label


@pytest.mark.parametrize("name, holder", [
    ("010013_holderA_survey", "holderA"), ("011600_holderC_plate_survey", "holderC"), ("133400_R2_survey_survey", "R2"),
    ("004900_tape_mount_plate_survey", "tape_mount"),
])
def test_holder_from_survey(name, holder):
    assert holder_from_survey(name) == holder


@pytest.mark.parametrize("name, edge", [
    ("092758_alpha_beta_f_k_stack", ("F", "K")),
    ("095242_sampleA_f_k_u_n45_stack", ("F", "K")),
    ("150255_sampleB_u_n45coarse_1_stack", ("U", "N45")),
    ("154516_sampleC_coarse_se_l3l2_coarse_stack", ("Se", "L3L2")),
    ("101456_gamma_f_k_stack", ("F", "K")),
    ("210000_holderD_BM-series_f_k_stack", ("F", "K")),
    ("011600_holderC_plate_survey", None),
])
def test_target_edge(name, edge):
    assert target_edge(name) == edge


def test_bm_series_is_not_boron_m():
    # the glued form needs a digit: 'BM' must not read as a boron M edge
    assert target_edge("200000_holderD_BM-series_o_k_stack") == ("O", "K")


def test_pretty_edge_and_time():
    assert pretty_edge(("U", "N45")) == "U N4,5"
    assert pretty_edge(("Se", "L3L2")) == "Se L3,2"
    assert pretty_edge(None) == ""
    assert time_label("092758_alpha_beta_f_k_stack") == "09:27"
    assert day_from_path(r"X:\raw\2025-03-14\092758_x_stack") == dt.date(2025, 3, 14)


def test_sdd_geometry_cutoff():
    assert sdd_geometry(dt.date(2026, 8, 12))["sdd1"] == "90 deg, top"
    assert sdd_geometry(dt.date(2026, 8, 13))["sdd1"] == "90 deg, side"
    assert sdd_geometry(None) == sdd_geometry(dt.date(2026, 11, 5))


# ----------------------------------------------------------------------------- raw readers
def test_energy_from_dirname():
    assert energy_from_dirname("alpha_beta_f_k_685_25eV") == 685.25
    assert energy_from_dirname("gamma_f_k_700_00eV") == 700.0
    assert energy_from_dirname("2025-03-14_092800_stack_data.h5") is None


def test_read_point_dir_shapes(fake_project):
    stack = fake_project / "raw" / testdata.DAY / testdata.STACK
    edirs = stack_energy_dirs(stack)
    assert [e for e, _ in edirs] == list(testdata.ENERGIES)
    pd = read_point_dir(edirs[0][1])
    assert pd.mcc.shape == (12, 8)
    assert [a.shape for a in pd.sdd] == [(12, N_CH)] * 4
    assert pd.complete and pd.n_pix == 12
    assert all(a.dtype == np.uint32 for a in pd.sdd)
    # the synthetic F K alpha peak sits near channel 65 + k
    for k, a in enumerate(pd.sdd):
        assert abs(int(a.sum(0).argmax()) - (65 + k)) <= 1


def test_read_point_dir_truncates_incomplete(fake_project):
    stack = fake_project / "raw" / testdata.DAY / testdata.STACK
    d = stack_energy_dirs(stack)[1][1]
    raw = (d / "sdd3_0.bin").read_bytes()
    (d / "sdd3_0.bin").write_bytes(raw[: 7 * N_CH * 4 + 100])      # 7 pixels and a partial one
    pd = read_point_dir(d)
    assert pd.n_pix == 7 and not pd.complete and pd.n_mcc == 12


def test_stack_status(fake_project):
    day = fake_project / "raw" / testdata.DAY
    assert stack_status(day / testdata.STACK)[0] == "complete"
    assert stack_status(day / testdata.ABORTED)[0] == "aborted"
    assert stack_status(day / testdata.TEST)[0] == "test"
    # the same incomplete stack touched a minute ago is still in progress
    import time
    now = time.time() - 119 * 60
    assert stack_status(day / testdata.ABORTED, now=now)[0] == "in_progress"


# ----------------------------------------------------------------------------- grid
def test_to_grid_snake():
    x, y = testdata.snake_grid(nx=4, ny=3, pitch=0.25)
    vals = np.arange(12, dtype=float)
    g = to_grid(x, y, vals)
    assert g.img.shape == (3, 4)
    assert g.n_dropped == 0
    assert np.isclose(g.dx, 0.25) and np.isclose(g.dy, 0.25)
    # row 1 was acquired right to left: its values must land in the right columns
    assert g.img[1, 3] == 4 and g.img[1, 0] == 7
    assert g.img[0, 0] == 0 and g.img[2, 3] == 11


def test_to_grid_drops_turnaround_points():
    x, y = testdata.snake_grid(nx=5, ny=4, pitch=0.2)
    # insert a transition point between row 0 and row 1 (y half-way)
    x = np.insert(x, 5, x[4]); y = np.insert(y, 5, y[4] + 0.1)
    g = to_grid(x, y, np.ones(len(x)))
    assert g.img.shape == (4, 5)
    assert g.n_dropped == 1 and g.iy[5] == -1
    assert np.isfinite(g.img).all()


# ----------------------------------------------------------------------------- model
def test_project_discovery(fake_project):
    proj = Project.discover(fake_project)
    assert proj.root == fake_project.resolve() and proj.raw == (fake_project / "raw").resolve()
    assert [d.date for d in proj.days] == [testdata.DAY]
    day = proj.days[0]
    assert len(day.holders) == 1
    h = day.holders[0]
    assert h.name == "holderA" and h.survey is not None and h.survey.n_points == 48
    assert [s.name for s in h.stacks] == [testdata.STACK]
    assert sorted(s.name for s in h.dropped) == sorted([testdata.ABORTED, testdata.TEST])
    st = proj.find(testdata.STACK)
    assert st.status == "complete" and st.n_energies == 5 and st.n_planned == 5 and st.n_points == 12
    assert st.edge == ("F", "K") and st.e_min == 680.0 and st.e_max == 692.0
    ab = proj.find(testdata.ABORTED)
    assert ab.status == "aborted" and ab.n_energies == 3 and ab.n_planned == 5
    assert st.md["time_per_map"] == "6.0"
    sv = proj.find(testdata.SURVEY)
    assert sv.kind == "survey" and sv.e_min == 695.0 and sv.holder == "holderA"
    assert not proj.calibration.calibrated and proj.roi_bins.labels == []


def test_project_resolve_from_scan_and_day(fake_project):
    day = fake_project / "raw" / testdata.DAY
    root, raw, days = Project.resolve(day / testdata.STACK)
    assert root == fake_project.resolve() and days == [day.resolve()]
    root, raw, days = Project.resolve(day)
    assert root == fake_project.resolve()
    with pytest.raises(FileNotFoundError):
        Project.resolve(fake_project / "processed")


def test_summary_merge(fake_project):
    proc = fake_project / "processed" / testdata.DAY
    proc.mkdir(parents=True)
    (proc / "summary.json").write_text(json.dumps({"day": testdata.DAY, "holders": [{
        "holder": "holderA", "survey": testdata.SURVEY, "pads": 3,
        "scans": [{"scan": testdata.STACK, "label": "alpha_beta", "mode": "PFY F Kα", "zones": [{"id": 1}],
                   "sdd_saturated": ["sdd1"], "i0_bad": True, "notes": ["a note"]}],
        "dropped": [[testdata.ABORTED, "aborted: 2/5"]]}]}))
    proj = Project.discover(fake_project)
    st = proj.find(testdata.STACK)
    assert st.flags == ["saturated SDD1", "I0 invalid"]
    assert proj.find(testdata.ABORTED).flags == ["dropped"]
    by = summary_by_scan(proj.days[0].summary)
    assert by[testdata.ABORTED]["dropped"] == "aborted: 2/5"


def test_scaninfo_unreadable_folder(tmp_path):
    d = tmp_path / "123456_broken_stack"
    d.mkdir()
    (d / "x_680_00eV").mkdir()
    (d / "bad.h5").write_bytes(b"not an hdf5 file")
    s = ScanInfo.from_dir(d)
    assert s.kind == "stack" and s.error and s.n_energies == 1


# ----------------------------------------------------------------------------- configs
def test_calibration_default_and_file(tmp_path):
    c = Calibration(tmp_path / "none.json")
    assert not c.calibrated
    assert np.isclose(c.ev("sdd1", 10), 100.0)
    data = {d: {"gain": 10.0 + i * 0.1, "offset": -1.0} for i, d in enumerate(DETS)}
    data["_metadata"] = {"last_updated": "2025-03-13 14:06:43"}
    p = tmp_path / "sdd_calibration.json"
    p.write_text(json.dumps(data))
    c = Calibration(p)
    assert c.calibrated and c.last_updated.startswith("2025-03-13")
    assert c.bounds("sdd1", 100, 200) == (10, 21)
    assert np.isclose(c.ch("sdd2", c.ev("sdd2", 42.0)), 42.0)


def test_calibration_describe_names_the_file_or_says_not_calibrated(tmp_path, capsys):
    """The provenance line (aurora project, the report cover, every report CSV): the file used, relative to the project
    ('../' for the sibling SGM_Beamline_Analysis-main/), its date and the gains; without a file 'not calibrated', never
    'SDD calibration (?)'."""
    from aurora.cli import cmd_project
    root = testdata.make_fake_project(tmp_path / "beamtime")
    proj = Project.discover(root)
    text = proj.calibration.describe(proj.root)
    assert text == "SDD calibration: none found (config/sdd_calibration.json), the default 10 eV/ch is used, not calibrated"
    gains = {"sdd1": (10.058, 3.9), "sdd2": (9.398, -3.6), "sdd3": (9.852, -1.3), "sdd4": (10.090, 7.9)}
    lib = tmp_path / "SGM_Beamline_Analysis-main"
    lib.mkdir()
    (lib / "sdd_calibration.json").write_text(json.dumps({**{d: {"gain": g, "offset": o} for d, (g, o) in gains.items()},
                                                          "_metadata": {"last_updated": "2025-03-13 14:06:43"}}))
    proj = Project.discover(root)
    text = proj.calibration.describe(proj.root)
    assert text.startswith("SDD calibration ../SGM_Beamline_Analysis-main/sdd_calibration.json (last updated "
                           "2025-03-13 14:06:43): sdd1 10.058 eV/ch +3.9;") and text.endswith("sdd4 10.090 eV/ch +7.9")
    assert cmd_project(root) == 0 and text in capsys.readouterr().out
    part = Calibration(data={"sdd1": {"gain": 10.1, "offset": 0.0}}).describe()      # a file without three detectors
    assert "(?)" not in part and "sdd2 10.000 eV/ch +0.0 (not in the file: default, not calibrated)" in part


def test_roi_bins_read(tmp_path):
    p = tmp_path / "roi_bins.json"
    p.write_text(json.dumps({"_meta": {"gains": {"sdd1": 10.0}}, "F Kα": {"sdd1": [60, 74], "sdd2": [65, 80],
                                                                           "sdd3": [62, 76], "sdd4": [59, 74], "line_ev": 676.8}}))
    b = RoiBins(p)
    assert b.labels == ["F Kα"] and b.has("F Kα") and b.bounds("F Kα")["sdd2"] == (65, 80)
    assert b.line_ev("F Kα") == 676.8 and b.describe("F Kα").startswith("F Kα: 60-73/65-79")


def test_sample_sheet(tmp_path):
    p = tmp_path / "sheet.csv"
    p.write_bytes(("measured 5/2026,#REF!,,0\n"
                   "Paper,Final plate#,Sample group,Sample ID,Composition,F K edge,Other edges?,Notes, e.g. synthesis\n"
                   ",holderC,Group 7,sample7,\"Fe2O3, SiO2\",no,Fe L,caf\xe9\n"
                   ",,,,,,,\n").encode("cp1252"))
    rows = read_sample_sheet(p)
    assert len(rows) == 1
    r = rows[0]
    assert (r.plate, r.group, r.sample_id, r.other_edges) == ("holderC", "Group 7", "sample7", "Fe L")
    assert r.composition == "Fe2O3, SiO2" and r.notes == "café"


def test_zones_file_round_trip(tmp_path):
    d = {"version": 1, "exported": "2025-03-15T20:00:00", "project": "00G00000", "built": "x", "notes": "",
         "goodI0": {"2025-03-14_092758_alpha_beta_f_k_stack": True},
         "holders": {"holderA": {"holder": "holderA", "day": "2025-03-14", "survey": "010013_holderA_survey", "zones": [
             {"id": 1, "label": "13 alpha_beta", "group": "Group 3", "comment": "", "rects": [[3.5, 4.0, 5.6, 6.1]],
              "erects": [], "paint": [[3.7, 5.9, 0.25, 0.25]], "erase": [],
              "st": {"2025-03-14_092758_alpha_beta_f_k_stack": {"inc": True, "n": 18, "sdd": [True, True, False, True],
                     "line": "F Kα", "fn": "i0jump", "tey": True, "tn": "i0jump", "ds": True, "k": 6, "i0src": "own",
                     "pre": None, "post": [700, 730], "stack": "092758_alpha_beta_f_k_stack"}},
              "members": {"2025-03-14_092758_alpha_beta_f_k_stack": [1, 2, 3]}}]}}}
    p = tmp_path / "zones_all.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    zf = ZonesFile.load(p)
    z = zf.holders["holderA"].zones[0]
    assert isinstance(z, Zone) and z.label == "13 alpha_beta" and z.rects == [[3.5, 4.0, 5.6, 6.1]]
    st = z.settings["2025-03-14_092758_alpha_beta_f_k_stack"]
    assert isinstance(st, ScanSettings) and st.sdd == [True, True, False, True] and st.post == [700, 730]
    assert zf.to_json() == d
    zf.save(tmp_path / "out.json")
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8")) == d
