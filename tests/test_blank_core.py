"""Blank and standalone (core): every configuration file optional and validated, other labs' raw layouts and naming,
a beamtime with only raw/<day>/<scan> working through the command line. Items BL-08 to BL-16, BL-19 to BL-24 of the
blank audit; the window's side is in test_blank_app.py."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import h5py
import numpy as np
import pytest

from aurora import cli
from aurora.core import overview, testdata
from aurora.core.constants import DETS
from aurora.core.io.config import (Calibration, RoiBins, ScanSettings, Zone, ZonesFile, check_plate_layout,
                                   find_sample_sheet, load_plate_layout_checked, parse_sample_sheet)
from aurora.core.io.safety import ProtectedPathError, assert_writable, is_protected
from aurora.core.model import Project
from aurora.core.names import (edge_for_range, holder_from_survey, is_test_name, label_from_name, pretty_edge,
                               resolve_edge, target_edge)
from aurora.core.propose import layout_group, layout_groups
from aurora.core.references import guess_edge
from aurora.core.report import ReportBuilder, plan

HOLDER = holder_from_survey(testdata.SURVEY)


# ----------------------------------------------------------------------------- helpers
def demo(tmp_path, name="beamtime") -> Path:
    return testdata.make_fake_project(tmp_path / name)


def raw_only(tmp_path, name="raw_only") -> Path:
    """A beamtime with only raw/<day>/<scan>: no config/, processed/, sheet or session."""
    root = demo(tmp_path, name)
    for c in list(root.iterdir()):
        if c.name != "raw":
            shutil.rmtree(c) if c.is_dir() else c.unlink()
    return root


def zones_with_line(proj, line: str) -> ZonesFile:
    """The demo zones plus a zone 4 whose settings use `line` on the complete stack."""
    zf = testdata.make_fake_zones(proj)
    key = next(iter(zf.holders))
    scan = proj.find(testdata.STACK)
    sid = f"{scan.day}_{scan.name}"
    z = zf.holders[key].zones[0]
    z4 = Zone(id=4, label="4 delta", group="Group 2", rects=[list(r) for r in z.rects])
    z4.settings[sid] = ScanSettings(stack=scan.name, line=line, n=6)
    zf.holders[key].zones.append(z4)
    return zf


def write(p: Path, text) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return p


def set_scan_name(scan_dir: Path, name: str) -> None:
    h5 = next(scan_dir.glob("*.h5"))
    with h5py.File(h5, "a") as h:
        g = h["scan_metadata"] if "scan_metadata" in h else h["stack_metadata"]
        g.attrs["scan_name"] = name


# ----------------------------------------------------------------------------- BL-08 a line that is not defined
def test_a_zone_line_missing_from_roi_bins_is_a_finding_not_a_silent_nan(tmp_path):
    """BL-08: the demo with a zone on line 'F Kα' and no config/roi_bins.json. plan() has one line_not_defined
    finding (a warning), the build leaves that entry's fluorescence out and says so, and no CSV carries 'TODO'."""
    root = demo(tmp_path)
    proj = Project.discover(root)
    zf = zones_with_line(proj, "F Kα")
    assert not proj.roi_bins.has("F Kα")              # the fake project has no roi_bins.json
    found = [f for f in plan(proj, zf).findings if f.kind == "line_not_defined"]
    assert len(found) == 1 and found[0].tone == "warn" and found[0].zone_id == 4
    assert "'F Kα'" in found[0].text and "Save as a fixed line…" in found[0].text and testdata.STACK.split("_")[0][:2]
    res = ReportBuilder(proj, zf, tmp_path / "rep", preset="draft", png_dpi=40, page_png_dpi=0).build()
    for csv in res.csvs:
        assert "TODO" not in csv.read_text(encoding="utf-8")
    man = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert any("fluorescence skipped: line 'F Kα' is not defined in config/roi_bins.json" in s for s in man["skipped"])
    assert any(p["kind"] == "line_not_defined" for p in man["preflight"])
    four = next(c for c in res.csvs if "_z4_" in c.name)
    head = four.read_text(encoding="utf-8")
    assert "line 'F Kα' is not defined in config/roi_bins.json: fluorescence left out" in head


def test_a_line_of_roi_bins_is_no_finding(tmp_path):
    root = demo(tmp_path)
    write(root / "config" / "roi_bins.json", json.dumps({"F Kα": {d: [60, 74] for d in DETS}}))
    proj = Project.discover(root)
    assert not [f for f in plan(proj, zones_with_line(proj, "F Kα")).findings if f.kind == "line_not_defined"]


def test_no_user_visible_todo_in_the_code():
    import re
    src = Path(__file__).resolve().parent.parent / "aurora"
    hits = []
    for p in list((src / "core").rglob("*.py")) + list((src / "app").rglob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"TODO", code):
                hits.append(f"{p.name}:{i}")
    assert not hits, hits


# ----------------------------------------------------------------------------- BL-09 / BL-19 the SDD calibration
BAD_CALIBRATIONS = [
    ({"sdd1": 5}, ["sdd1: not a JSON object (5)", "sdd2–sdd4 missing"]),
    ([], ["the file is not a JSON object (a list)"]),
    ({"sdd1": {"gain": "x", "offset": 0}}, ["sdd1: gain is not a number ('x')", "sdd2–sdd4 missing"]),
    ({"sdd1": {"gain": -1, "offset": 0}}, ["sdd1: gain must be above 0 eV per channel (it is -1)", "sdd2–sdd4 missing"]),
    ({"sdd1": {"gain": 9.9, "offset": 1.0}}, ["sdd2–sdd4 missing"]),
]


@pytest.mark.parametrize("data, problems", BAD_CALIBRATIONS)
def test_a_malformed_calibration_never_raises_and_is_listed(tmp_path, capsys, data, problems):
    """BL-09: each shape opens, prints and reports, with the problems in Home's rows and `aurora project`."""
    root = demo(tmp_path)
    write(root / "config" / "sdd_calibration.json", json.dumps(data))
    proj = Project.discover(root)
    cal = proj.calibration
    assert cal.problems == problems and not cal.calibrated
    assert np.isfinite(cal.ev("sdd1", 60)) and cal.bounds("sdd2", 600, 700)[1] > 0
    rows = dict(overview.project_rows(proj, overview.project_facts(proj)))
    assert "incomplete" in rows["calibration"] or "not usable" in rows["calibration"]
    assert problems[0] in rows["calibration"]                  # said once, in the calibration row
    assert cli.cmd_project(root) == 0
    out = capsys.readouterr().out
    assert problems[0] in out and "Traceback" not in out
    zf = testdata.make_fake_zones(proj)
    b = ReportBuilder(proj, zf, tmp_path / "rep", preset="draft", png_dpi=40, page_png_dpi=0)
    res = b.build()
    cover = " ".join(x.strip() for x in b.cover_lines)
    assert res.pdf.exists() and "SDD calibration: config/sdd_calibration.json" in cover and "(?)" not in cover
    assert "SDD calibration: config/sdd_calibration.json" in res.csvs[0].read_text(encoding="utf-8")


def test_calibration_in_words(tmp_path):
    """BL-19: none / incomplete / complete, the date only when the file has one; never '(?)'."""
    none = Calibration(tmp_path / "config" / "sdd_calibration.json")
    assert none.summary(tmp_path) == "none (no config/sdd_calibration.json), nominal 10 eV per channel"
    p = write(tmp_path / "config" / "sdd_calibration.json", json.dumps({"sdd1": {"gain": 9.9, "offset": 1.0}}))
    part = Calibration(p)
    assert part.summary(tmp_path) == "config/sdd_calibration.json, incomplete (sdd2–sdd4 missing: nominal)"
    full = {d: {"gain": 10.0, "offset": 0.0} for d in DETS}
    write(p, json.dumps(full))
    assert Calibration(p).summary(tmp_path) == "config/sdd_calibration.json" and Calibration(p).calibrated
    write(p, json.dumps({**full, "_metadata": {"last_updated": "2025-03-14 10:00:00"}}))
    assert Calibration(p).summary(tmp_path) == "config/sdd_calibration.json (updated 2025-03-14 10:00:00)"
    write(p, "{not json")
    bad = Calibration(p)
    assert bad.problems[0].startswith("the file is not valid JSON (line 1, column 2") and "(?)" not in bad.describe()


# ----------------------------------------------------------------------------- BL-10 the fixed ROI bins
BAD_BINS = [
    (["a"], [], "the file is not a JSON object (a list)"),
    ({"x": 5}, [], "x: not a JSON object (5)"),
    ("text", [], "the file is not a JSON object (a str)"),
    ({"F Kα": {"sdd1": [60, 74], "sdd2": [61, 75], "sdd3": [62, 76]}}, [], "F Kα: sdd4 missing"),
    ({"F Kα": {d: [60, 74] for d in DETS}, "O Kα": {d: [46.5, 58] for d in DETS}}, ["F Kα"],
     "O Kα: sdd1 must be two whole channel numbers, not [46.5, 58]"),
]


@pytest.mark.parametrize("data, labels, problem", BAD_BINS)
def test_malformed_roi_bins_never_raise(tmp_path, capsys, data, labels, problem):
    """BL-10: the project opens, stacks load with the valid lines plus the total, the report builds, and the problem
    is listed in Home's rows and `aurora project`."""
    from aurora.core.stack import StackData
    root = demo(tmp_path)
    write(root / "config" / "roi_bins.json", json.dumps(data))
    proj = Project.discover(root)
    assert proj.roi_bins.labels == labels and problem in proj.roi_bins.problems
    assert proj.roi_bins.describe("F Kα")                   # never raises, also for a line it does not have
    sd = StackData.load(proj, proj.find(testdata.STACK))
    assert sd.labels == labels + ["total XRF"]
    assert any(problem in v for k, v in overview.project_rows(proj) if k == "config problem")
    assert cli.cmd_project(root) == 0 and problem in capsys.readouterr().out
    res = ReportBuilder(proj, testdata.make_fake_zones(proj), tmp_path / "rep", preset="draft", png_dpi=40,
                        page_png_dpi=0).build()
    assert res.pdf.exists()


def test_add_line_creates_the_file_keeps_the_rest_and_a_backup(tmp_path):
    p = tmp_path / "config" / "roi_bins.json"
    RoiBins(p).add_line("Fe Lα", {d: [68, 75] for d in DETS}, extra={"line_ev": 705.0})
    b = RoiBins(p)
    assert b.labels == ["Fe Lα"] and "_meta" in b.data and b.line_ev("Fe Lα") == 705.0
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["odd"] = {"sdd1": [1, 2]}                          # an entry another tool wrote: kept as it is
    p.write_text(json.dumps(raw), encoding="utf-8")
    RoiBins(p).add_line("O Kα", {d: [46, 58] for d in DETS})
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert raw["odd"] == {"sdd1": [1, 2]} and set(RoiBins(p).labels) == {"Fe Lα", "O Kα"}
    assert list(p.parent.glob("roi_bins.backup-*.json"))
    with pytest.raises(ValueError):
        RoiBins(p).add_line("bad", {d: [80, 70] for d in DETS})


# ----------------------------------------------------------------------------- BL-11 the project's calibration first
def _cal(path: Path, gain: float) -> None:
    write(path, json.dumps({d: {"gain": gain, "offset": 0.0} for d in DETS}))


def test_the_project_calibration_wins_over_a_sibling_file(tmp_path):
    root = demo(tmp_path)
    _cal(root / "config" / "sdd_calibration.json", 10.5)
    _cal(tmp_path / "SGM_Beamline_Analysis-main" / "sdd_calibration.json", 9.0)
    proj = Project.discover(root)
    assert proj.calibration.data["sdd1"]["gain"] == 10.5 and not proj.calibration.sibling
    assert dict(overview.project_rows(proj))["calibration"] == "config/sdd_calibration.json"


def test_a_sibling_calibration_alone_is_named(tmp_path):
    root = demo(tmp_path)
    _cal(tmp_path / "SGM_Beamline_Analysis-main" / "sdd_calibration.json", 9.0)
    proj = Project.discover(root)
    assert proj.calibration.sibling and proj.calibration.data["sdd1"]["gain"] == 9.0
    row = dict(overview.project_rows(proj))["calibration"]
    assert row.startswith("../SGM_Beamline_Analysis-main/sdd_calibration.json") and \
        "the project has no config/sdd_calibration.json" in row


# ----------------------------------------------------------------------------- BL-12 sample sheets of any lab
def test_the_old_lab_format_still_parses(tmp_path):
    p = write(tmp_path / "sheet.csv", ("measured 5/2026,#REF!,,0\n"
                                       "Paper,Final plate#,Sample group,Sample ID,Composition,F K edge,Other edges?,"
                                       "Notes, e.g. synthesis\n"
                                       ",H7,Series A,S1,\"CaF2, SiO2\",y,Cu L,caf\xe9\n"
                                       ",,,,,,,\n").encode("cp1252"))
    rows, cols, note = parse_sample_sheet(p)
    assert len(rows) == 1 and note == ""
    r = rows[0]
    assert (r.holder, r.plate, r.group, r.sample_id, r.composition, r.notes) == ("H7", "H7", "Series A", "S1",
                                                                                 "CaF2, SiO2", "café")
    assert r.edges == "F K; Cu L" and r.f_k_edge == r.edges                    # a yes-mark gives the header's edge
    assert cols["sample"] == "Sample ID" and cols["holder"] == "Final plate#" and cols["group"] == "Sample group"


def test_a_utf8_bom_sheet_with_other_characters_parses(tmp_path):
    p = write(tmp_path / "samples.csv", "﻿Sample ID,Holder,Group,Composition\n1 alpha,holderA,Glasses,"
                                        "Na₂O·Al₂O₃ → ζ-phase Ł\n")
    rows, cols, _ = parse_sample_sheet(p)
    assert rows[0].composition == "Na₂O·Al₂O₃ → ζ-phase Ł" and rows[0].holder == "holderA"


def test_other_column_names_and_a_semicolon_sheet(tmp_path):
    p = write(tmp_path / "a.csv", "Sample,Holder,Group,Composition\n1 alpha,holderA,Glasses,NaF\n")
    rows, cols, _ = parse_sample_sheet(p)
    assert cols == {"sample": "Sample", "holder": "Holder", "group": "Group", "composition": "Composition"}
    p = write(tmp_path / "b.csv", "Name;Plate #;Series;Formula;Comments\nquartz;2;S1;SiO2;ok\n")
    rows, cols, _ = parse_sample_sheet(p)
    assert (rows[0].sample_id, rows[0].holder, rows[0].group, rows[0].composition, rows[0].notes) == \
        ("quartz", "2", "S1", "SiO2", "ok")


def test_the_sheet_choice_and_what_is_skipped(tmp_path):
    root = tmp_path / "bt"
    write(root / "a_notes.csv", "Sample ID,whatever\nfoo,bar\n")
    write(root / "samples.csv", "Sample ID,Holder\nS1,holderA\n")
    write(root / "binary.csv", b"PK\x03\x04\x00\x00binary")
    write(root / "z_empty.csv", "Sample ID,Holder\n")
    ch = find_sample_sheet(root)
    assert ch.path.name == "samples.csv" and len(ch.rows) == 1
    skipped = {p.name: why for p, why in ch.skipped}
    assert skipped["a_notes.csv"] == "no holder column"
    assert skipped["binary.csv"].startswith("not a text CSV") and skipped["z_empty.csv"] == "a header but no sample row"
    chosen = find_sample_sheet(root, "a_notes.csv")                    # the session's choice wins
    assert chosen.path.name == "a_notes.csv" and chosen.chosen
    gone = find_sample_sheet(root, "moved.csv")
    assert gone.path.name == "samples.csv" and gone.skipped[0][1] == "chosen for this beamtime, but not found"


def test_home_rows_name_the_sheet_columns_and_the_skipped_files(tmp_path, capsys):
    root = demo(tmp_path)
    write(root / "a_notes.csv", "Sample ID,whatever\nfoo,bar\n")
    write(root / "samples.csv", f"Sample,Holder,Group\nS1,{HOLDER},Group 1\n")
    proj = Project.discover(root)
    rows = overview.project_rows(proj, overview.project_facts(proj))
    sheet = [v for k, v in rows if k == "sample sheet"]
    assert sheet[0].startswith(f"samples.csv, 1 sample on {HOLDER} (") and "sample = Sample, holder = Holder, group = Group" in sheet[0]
    assert ("CSV not used", "a_notes.csv: no holder column") in rows
    assert cli.cmd_project(root) == 0 and "CSV not used: a_notes.csv: no holder column" in capsys.readouterr().out


def test_a_sheet_naming_other_holders_says_so(tmp_path):
    root = demo(tmp_path)
    write(root / "samples.csv", "Sample ID,Holder\nS1,elsewhere\n")
    proj = Project.discover(root)
    rows = [v for k, v in overview.project_rows(proj, overview.project_facts(proj)) if k == "sample sheet"]
    assert any("its Holder column names elsewhere, none of the holders of this beamtime" in v for v in rows)


# ----------------------------------------------------------------------------- BL-13 holder names keep their numbers
@pytest.mark.parametrize("name, scan_name, holder", [
    ("010013_plateB2_survey", None, "B2"), ("011600_C4_plate_survey", None, "C4"),
    ("133400_R2_survey_survey", "R2_survey_survey", "R2"), ("093834_Rad1_plate_survey", "Rad1_plate_survey", "Rad1"),
    ("093000_Plate_3_survey", None, "Plate_3"), ("150000_Plate_4_survey", None, "Plate_4"),
    ("093000_tape_mount_2_survey", None, "tape_mount_2"), ("101500_holderA_survey", None, "holderA"),
    ("093000_survey", None, "holder 09:30"), ("093000_x_survey", "Plate_3_survey", "Plate_3"),
])
def test_holder_names(name, scan_name, holder):
    assert holder_from_survey(name, scan_name) == holder


def test_two_numbered_plates_on_one_day_are_two_holders(tmp_path):
    root = demo(tmp_path)
    day = root / "raw" / testdata.DAY
    (day / testdata.SURVEY).rename(day / "000500_Plate_3_survey")
    set_scan_name(day / "000500_Plate_3_survey", "Plate_3_survey")
    shutil.copytree(day / "000500_Plate_3_survey", day / "150000_Plate_4_survey")
    set_scan_name(day / "150000_Plate_4_survey", "Plate_4_survey")
    proj = Project.discover(root)
    assert [h.name for h in proj.days[0].holders] == ["Plate_3", "Plate_4"]


# ----------------------------------------------------------------------------- BL-14 / BL-16 other layouts
def test_day_folders_with_other_names(tmp_path, capsys):
    """BL-14: raw/Nov05 is read as a day folder dated from the .h5 files, and raw/ is protected."""
    root = raw_only(tmp_path)
    (root / "raw" / testdata.DAY).rename(root / "raw" / "Nov05")
    proj = Project.discover(root)
    assert [d.date for d in proj.days] == [testdata.DAY] and proj.n_holders == 1 and len(proj.scans) == 4
    assert any(n.startswith("raw/Nov05 is not a YYYY-MM-DD folder: 4 of its 4 scan folders dated from their .h5")
               for n in proj.layout_notes)
    assert any(k == "layout note" and "raw/Nov05" in v for k, v in overview.project_rows(proj))
    assert is_protected(root / "raw" / "Nov05" / "x")
    with pytest.raises(ProtectedPathError):
        assert_writable(root / "raw" / "Nov05" / "x")
    assert cli.cmd_project(root) == 0 and "layout: raw/Nov05 is not a YYYY-MM-DD folder" in capsys.readouterr().out
    # the day folder itself opens as one day of the beamtime, not as a loose folder inside raw/
    r, raw, days = Project.resolve(root / "raw" / "Nov05")
    assert r == root.resolve() and days == [(root / "raw" / "Nov05").resolve()]


def test_a_raw_folder_with_scans_directly_is_protected(tmp_path):
    raw = tmp_path / "raw"
    (raw / "103000_sample1_f_k_stack").mkdir(parents=True)
    assert is_protected(raw / "out.csv")
    other = tmp_path / "raw2" / "raw"
    (other / "notes").mkdir(parents=True)
    assert not is_protected(other / "out.csv")              # a folder named raw without any scan is the user's


def test_layout_notes_name_what_was_not_recognised(tmp_path, capsys):
    """BL-16: names without _survey/_stack (recognised from the .h5), scans one level deeper, folders that are not
    scans, stacks before any survey, an empty raw/."""
    root = raw_only(tmp_path)
    day = root / "raw" / testdata.DAY
    (day / testdata.SURVEY).rename(day / "010013_holderA_map")
    (day / testdata.STACK).rename(day / "092758_sample1_Fe_L3_xanes")
    (day / "notes").mkdir()
    nested = day / "run2"
    nested.mkdir()
    shutil.copytree(day / testdata.TEST, nested / testdata.TEST)
    proj = Project.discover(root)
    notes = " | ".join(proj.layout_notes)
    assert "2 scan folders without _survey / _stack in the name recognised from the plan_name of the .h5" in notes
    assert f"raw/{testdata.DAY}/run2/ holds 1 scan folder one level deeper" in notes
    assert "1 folder is neither surveys nor stacks" in notes
    renamed = proj.find("092758_sample1_Fe_L3_xanes")
    assert renamed.kind == "stack" and renamed.status == "complete" and renamed.kind_from_h5
    assert proj.days[0].holders[0].survey.name == "010013_holderA_map"          # BL-23: the renamed survey starts it
    assert cli.cmd_project(root) == 0 and "one level deeper" in capsys.readouterr().out
    # stacks before any survey
    root2 = raw_only(tmp_path, "no_survey")
    shutil.rmtree(root2 / "raw" / testdata.DAY / testdata.SURVEY)
    proj2 = Project.discover(root2)
    assert any("recorded before any survey: they form holder 'unknown'" in n for n in proj2.layout_notes)
    # an empty raw/
    (tmp_path / "empty" / "raw").mkdir(parents=True)
    proj3 = Project.discover(tmp_path / "empty")
    assert proj3.layout_notes == ["raw/ is empty: copy the day folders of the beamtime into it"]
    rows = overview.project_rows(proj3)
    assert rows[1] == ("layout note", "raw/ is empty: copy the day folders of the beamtime into it")   # first


# ----------------------------------------------------------------------------- BL-15 aurora report without a session
def test_report_on_a_raw_only_beamtime_is_one_line(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AURORA_LOG_DIR", str(tmp_path / "logs"))
    root = raw_only(tmp_path)
    assert cli.main(["report", str(root), "--preset", "draft", "--author", ""]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"aurora report: no zones yet in {root.name}") and "Traceback" not in err
    assert cli.main(["project", str(root)]) == 0


def test_a_raw_only_beamtime_reports_with_a_session(tmp_path, capsys):
    """Every configuration file absent: the report builds from the session alone (total XRF), with the line finding."""
    root = raw_only(tmp_path)
    proj = Project.discover(root)
    zf = zones_with_line(proj, "F Kα")
    zf.save(root / ".aurora" / "session.json")
    assert cli.main(["report", str(root), "--preset", "draft", "--author", "", "--dpi", "40"]) == 0
    out = capsys.readouterr().out
    assert "skipped: " in out and "fluorescence skipped: line 'F Kα'" in out


# ----------------------------------------------------------------------------- BL-20 the plate layout
def test_plate_layout_problems_never_raise(tmp_path):
    lay, problems = check_plate_layout({"H1": {"groups": [{"id": "g1", "rows": 2, "cols": 3, "cells": 5},
                                                          {"id": "g2", "rows": "two", "cols": 3},
                                                          {"id": "g3", "rows": 2, "cols": 2, "match": ["a", 3]},
                                                          {"id": "ok", "rows": 1, "cols": 2, "cells": [[1, None]]}],
                                               "samples": {"1": "quartz"}},
                                        "H2": 5, "_help": "x"})
    assert [g["id"] for g in lay["H1"]["groups"]] == ["ok"] and "H2" not in lay
    assert problems == ["H1, group g1: cells must be a list of rows, not 5: left out",
                        "H1, group g2: rows must be a whole number of at least 1, not 'two': left out",
                        "H1, group g3: match must be a list of words, not ['a', 3]: left out",
                        "H2: not a JSON object"]
    assert layout_groups(lay, "H1")[0]["labels"] == [["quartz", None]]
    assert layout_group(lay, "H1", "quartz") is not None
    p = write(tmp_path / "config" / "plate_layout.json", "[1, 2")
    lay, problems = load_plate_layout_checked(p)
    assert lay == {} and problems[0].startswith("the file is not valid JSON (line 1")


def test_a_bad_plate_layout_is_a_config_problem_row(tmp_path):
    root = demo(tmp_path)
    write(root / "config" / "plate_layout.json", json.dumps({HOLDER: {"groups": [{"rows": 2, "cols": 2,
                                                                                  "cells": 5}]}}))
    proj = Project.discover(root)
    assert any(k == "config problem" and v.startswith("config/plate_layout.json:") for k, v in
               overview.project_rows(proj))
    assert not hasattr(proj, "sample_elements")


# ----------------------------------------------------------------------------- BL-21 / BL-22 / BL-24 names
@pytest.mark.parametrize("name, label, edge", [
    ("103000_sample1_f_k_stack", "sample1", ("F", "K")),
    ("103000_fine_powder_A_f_k_stack", "fine_powder_A", ("F", "K")),
    ("092758_soil_coarse_fraction_ca_l_stack", "soil_coarse_fraction", ("Ca", "L")),
    ("092758_MnO2_mn_l23_stack", "MnO2", ("Mn", "L23")),
    ("092758_calcite_Ca_L23_stack", "calcite", ("Ca", "L23")),
    ("092758_graphite_CK_stack", "graphite", ("C", "K")),
    ("092758_quartz_OK_stack", "quartz", ("O", "K")),
    ("092758_NiO_ni_l2,3_stack", "NiO", ("Ni", "L23")),
    ("092758_hematite_mnL23_stack", "hematite", ("Mn", "L23")),
    ("092758_carbon_k_stack", "carbon_k", ("C", "K")),                 # never the empty name
    ("092758_tey_test_sample_o_k_stack", "tey_test_sample", ("O", "K")),
    ("092758_film_fe_l3_fine_stack", "film", ("Fe", "L3")),
    ("203529_Se-std_se_l3l2_fine1_stack", "Se-std", ("Se", "L3L2")),   # a numbered step word
    ("092758_UO2_u_n45coarse_1_stack", "UO2", ("U", "N45")),
    ("092758_glass_2_stack", "glass_2", None),                         # a sample number is no re-take counter
    ("092758_BM_series_o_k_stack", "BM_series", ("O", "K")),           # BM is no boron M edge
    ("092758_sample_edge1_stack", "sample", None),
    ("092758_Mn-oxide_mn_l3_stack", "Mn-oxide", ("Mn", "L3")),
    ("101500_holderA_survey", "holderA", None),
])
def test_labels_and_edges_without_any_lab_words(name, label, edge):
    assert label_from_name(name) == label and target_edge(name) == edge


def test_naming_json_adds_a_beamtime_s_own_plan_words(tmp_path):
    root = demo(tmp_path)
    day = root / "raw" / testdata.DAY
    shutil.copytree(day / testdata.STACK, day / "120000_glassX_f_k_run_stack")
    write(root / "config" / "naming.json", json.dumps({"label_suffix_words": ["run"]}))
    proj = Project.discover(root)
    assert proj.naming_words == ["run"]
    s = proj.find("120000_glassX_f_k_run_stack")
    assert s.label == label_from_name(testdata.STACK, ["run"])          # the h5 scan_name is the copied stack's
    assert label_from_name("120000_glassX_f_k_run_stack", ["run"]) == "glassX"


def test_the_edge_comes_from_the_energies_when_the_name_disagrees():
    assert edge_for_range(680, 692) == ("F", "K") and edge_for_range(700, 730) == ("Fe", "L3")
    assert edge_for_range(630, 670) == ("Mn", "L3") and edge_for_range(525, 560) == ("O", "K")
    edge, note = resolve_edge(("O", "K"), [680.0, 692.0])
    assert edge == ("F", "K") and note == "the name says O K (543 eV), the energies cover 680–692 eV: F K used"
    assert resolve_edge(("F", "K"), [680.0, 692.0]) == (("F", "K"), "")
    assert resolve_edge(None, [])[0] is None
    # a scan above an edge without a name: the beamtime's other scans decide (F K, 3 eV below), else the range (Fe L3)
    assert resolve_edge(None, [700.0, 730.0]) == (("Fe", "L3"), "edge Fe L3 from the energies (700–730 eV): the name "
                                                                "states none")
    edge, note = resolve_edge(None, [700.0, 730.0], {("F", "K"): 40, ("U", "N45"): 3})
    assert edge == ("F", "K") and "the other scans of this beamtime" in note
    assert pretty_edge(("Fe", "L23")) == "Fe L2,3"


def test_a_stack_named_for_another_edge_is_noted(tmp_path):
    root = raw_only(tmp_path)
    day = root / "raw" / testdata.DAY
    (day / testdata.STACK).rename(day / "092758_quartz_o_k_stack")
    set_scan_name(day / "092758_quartz_o_k_stack", "quartz_o_k")
    proj = Project.discover(root)
    s = proj.find("092758_quartz_o_k_stack")
    assert s.edge == ("F", "K") and s.edge_note.startswith("the name says O K (543 eV)")
    assert any("an edge the energies do not cover" in n for n in proj.layout_notes)
    assert "the name says O K" in dict(overview.scan_rows(s, proj)).get("edge note", "")


@pytest.mark.parametrize("name, test", [
    ("143504_photodiode_f_k_mylab_stack", True), ("121500_photodiode_f_k_stack", True), ("121500_dark_stack", True),
    ("121500_dark_2_stack", True), ("121500_i0_test_stack", True), ("121500_beam_test_f_k_stack", True),
    ("121500_dark_glass_fe_l3_stack", False), ("121500_Darkite_stack", False), ("121500_my_diode_array_stack", False),
])
def test_the_test_scan_rule_matches_whole_words(name, test):
    assert is_test_name(name) is test


def test_a_kind_set_by_hand_survives_a_reload(tmp_path):
    """BL-22: the session's kind_override is read at discovery (the command line and the window agree)."""
    root = demo(tmp_path)
    proj = Project.discover(root)
    assert proj.find(testdata.TEST).status == "test"
    zf = ZonesFile(ui={"kind_override": {testdata.TEST: "sample", testdata.STACK: "test"}})
    zf.save(root / ".aurora" / "session.json")
    again = Project.discover(root)
    t, s = again.find(testdata.TEST), again.find(testdata.STACK)
    assert t.status == "complete" and "marked as a sample scan" in t.status_reason
    assert s.status == "test" and t in again.days[0].holders[0].stacks and s in again.days[0].holders[0].dropped
    again.apply_overrides({})
    assert again.find(testdata.TEST).status == "test" and again.find(testdata.STACK).status == "complete"


@pytest.mark.parametrize("name, edge", [
    ("manganese_L3", "Mn L3"), ("nickel_l2", "Ni L2"), ("titanium_l3_ref", "Ti L3"), ("cobalt-L3", "Co L3"),
    ("hematite_fe_l23", "Fe L2,3"), ("calcite_Ca_L23", "Ca L2,3"), ("sulphur_k", "S K"), ("aluminium_k", "Al K"),
    ("NaF_F_K_edge", "F K"), ("random", ""),
])
def test_reference_edges_for_every_element(name, edge):
    assert guess_edge(name) == edge


# ----------------------------------------------------------------------------- BL-31 a plate layout made in AURORA
def test_a_grid_saved_as_a_plate_layout_group(tmp_path):
    from aurora.core.propose import add_layout_group, layout_group_entry
    g, samples = layout_group_entry("row 1", 2, 3, ["quartz", "albite", "calcite", "NaF", "CaF2", None])
    assert g == {"id": "row 1", "rows": 2, "cols": 3, "cells": [[1, 2, 3], [4, 5, None]], "match": []}
    assert samples == {"1": "quartz", "2": "albite", "3": "calcite", "4": "NaF", "5": "CaF2"}
    p = tmp_path / "config" / "plate_layout.json"
    add_layout_group(p, "holderA", g, samples)
    g2, s2 = layout_group_entry("numbers", 1, 2, None, first_number=6)
    add_layout_group(p, "holderA", g2, s2)
    lay, problems = load_plate_layout_checked(p)
    assert not problems and [x["id"] for x in lay["holderA"]["groups"]] == ["row 1", "numbers"]
    assert layout_groups(lay, "holderA")[0]["labels"] == [["quartz", "albite", "calcite"], ["NaF", "CaF2", None]]
    assert lay["holderA"]["groups"][1]["cells"] == [[6, 7]] and list(p.parent.glob("plate_layout.backup-*.json"))
    p.write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError):
        add_layout_group(p, "holderA", g, samples)              # never written over a file of the wrong shape
