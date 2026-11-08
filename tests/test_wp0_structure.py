"""WP0 structural prep, the core side (no Qt): bundled files through aurora.resources_dir, the zones / spectra split,
cancel tokens, UTF-8 and atomic text IO, stack-id helpers, the stubs later packages fill, the new per-scan and
per-zone fields of the session file, the synthetic beamtime variants and the demo's spectra."""
import ast
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
import pytest

import aurora
from aurora.core import testdata
from aurora.core.io.config import HolderZones, ScanSettings, Zone, ZonesFile
from aurora.core.model import Project

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "aurora"


# ----------------------------------------------------------------------------- resources_dir
def test_resources_dir_source_frozen_and_app_bundle(tmp_path, monkeypatch):
    src = aurora.resources_dir()
    assert src == PKG.resolve() / "resources"
    assert (src / "docs" / "MANUAL.md").is_file() and (src / "ui" / "check.svg").is_file()
    assert (src / "aurora_mark_256.png").is_file()
    # frozen Windows onedir: <exe folder>/_internal = sys._MEIPASS, aurora/resources bundled inside it
    meipass = tmp_path / "AURORA" / "_internal"
    (meipass / "aurora" / "resources").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "AURORA" / "AURORA.exe"))
    assert aurora.resources_dir().resolve() == (meipass / "aurora" / "resources").resolve()
    # macOS .app: sys._MEIPASS = Contents/Frameworks (its link to the data missing here), the copy in Contents/Resources
    contents = tmp_path / "AURORA.app" / "Contents"
    (contents / "Resources" / "aurora" / "resources").mkdir(parents=True)
    (contents / "Frameworks").mkdir()
    (contents / "MacOS").mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(contents / "Frameworks"))
    monkeypatch.setattr(sys, "executable", str(contents / "MacOS" / "AURORA"))
    assert aurora.resources_dir().resolve() == (contents / "Resources" / "aurora" / "resources").resolve()
    # frozen without any bundled copy (never expected): the package folder
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "nowhere"))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "nowhere" / "AURORA.exe"))
    assert aurora.resources_dir() == src


def test_no_module_locates_bundled_files_on_its_own():
    """resources_dir() is the only way to the bundled files: no other module joins __file__ and 'resources'."""
    bad = []
    for p in PKG.rglob("*.py"):
        if p == PKG / "__init__.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "__file__" in line and "resources" in line:
                bad.append(f"{p.relative_to(REPO)}:{i}")
    assert not bad, bad
    from aurora.app.help import docs_dir
    assert docs_dir() == aurora.resources_dir() / "docs"


# ----------------------------------------------------------------------------- zones / spectra split
def test_spectra_split_reexports_and_same_numbers(fake_project):
    from aurora.core import spectra, zones
    for name in ("NORM_MODES", "run_median", "despike", "Normalised", "normalise", "interp", "good_i0_curve", "I0Score",
                 "i0_score", "ZoneTraces", "build_traces"):
        assert getattr(zones, name) is getattr(spectra, name), name
    from aurora.core.stack import StackData
    proj = Project.discover(fake_project)
    sd = StackData.load(proj, proj.find(testdata.STACK))
    z = Zone(id=1, label="a", rects=[list(sd.bbox)])
    pix = zones.members(z, sd.x, sd.y, sd.pitch)
    st = ScanSettings(line="total XRF", sdd=[True, False, True, True], fluo_norm="max")
    sums = zones.zone_sums(sd, pix, st)
    assert isinstance(sums, zones.ZoneSums) and sums.n_pix == 12 and sums.main == sd.main and sums.sid == sd.id
    assert sums.E is sd.E and set(sums.det) == set(sd.labels) and sums.det["total XRF"].shape == (sd.n_energies, 4)
    a = zones.zone_traces(sd, pix, st)
    b = spectra.build_traces(sums, st)
    for f in ("sum", "sum_raw", "tey", "tey_raw", "i0", "i0_used", "i0_raw"):
        assert np.array_equal(getattr(a, f), getattr(b, f), equal_nan=True), f
    assert all(np.array_equal(x, y, equal_nan=True) for x, y in zip(a.det, b.det))
    # the new fields: empty by default, never shared between results
    assert a.err == {} and a.notes == [] and a.masked == {}
    a.notes.append("x")
    assert b.notes == []
    # a zone without pixels gives no traces, from both entry points
    assert zones.zone_traces(sd, [], st) is None
    assert spectra.build_traces(zones.zone_sums(sd, [], st), st) is None


# ----------------------------------------------------------------------------- cancel
def test_cancel_token_and_progress_wrapper():
    from aurora.core.cancel import CancelToken, Cancelled, cancellable
    seen = []
    tok = CancelToken()
    cb = cancellable(lambda i, n, m="": seen.append((i, n, m)), tok)
    cb(0, 3, "a")
    assert seen == [(0, 3, "a")] and not tok.cancelled
    tok.cancel()
    assert tok.cancelled
    with pytest.raises(Cancelled):
        cb(1, 3, "b")
    assert seen == [(0, 3, "a")]
    assert cancellable(None, CancelToken())(0, 1) is None          # no inner callback: only the check


# ----------------------------------------------------------------------------- textio
def test_textio_read_and_atomic_write(tmp_path, monkeypatch):
    from aurora.core.io import textio
    p = tmp_path / "sub" / "a.json"
    assert textio.read_json(p, default={"d": 1}) == {"d": 1}
    textio.write_json_atomic(p, {"line": "F Kα", "n": 3})
    assert json.loads(p.read_text(encoding="utf-8")) == {"line": "F Kα", "n": 3}
    assert "F Kα" in p.read_text(encoding="utf-8")                # UTF-8, not \\u escapes
    assert textio.read_json(p) == {"line": "F Kα", "n": 3}
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert textio.read_json(tmp_path / "bad.json", default="fallback") == "fallback"
    before = p.read_bytes()
    real_replace = os.replace
    # os.replace keeps failing: the old file stays byte-identical, the error reaches the caller, no tmp file is left
    monkeypatch.setattr(textio, "REPLACE_WAIT_S", 0.0)

    def refuse(src, dst):
        raise PermissionError(13, "held by another process", str(dst))
    monkeypatch.setattr(textio.os, "replace", refuse)
    with pytest.raises(PermissionError):
        textio.write_text_atomic(p, "new content")
    assert p.read_bytes() == before
    assert sorted(x.name for x in p.parent.iterdir()) == ["a.json"]
    # two refusals, then it works: retried
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "held", str(dst))
        return real_replace(src, dst)
    monkeypatch.setattr(textio.os, "replace", flaky)
    textio.write_text_atomic(p, "third time")
    assert p.read_text(encoding="utf-8") == "third time" and calls["n"] == 3


def test_textio_refuses_a_raw_folder(tmp_path):
    from aurora.core.io import textio
    from aurora.core.io.safety import ProtectedPathError
    day = tmp_path / "raw" / "2026-11-05"
    day.mkdir(parents=True)
    with pytest.raises(ProtectedPathError):
        textio.write_text_atomic(day / "x.json", "{}")
    assert not (day / "x.json").exists()


def test_no_text_io_without_an_encoding():
    """Every read_text, write_text and text-mode open() under aurora/ names its encoding (cp1252 is the Windows default)."""
    bad = []
    for p in PKG.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else ""
            kws = {k.arg for k in node.keywords}
            if name in ("read_text", "write_text") and "encoding" not in kws:
                bad.append(f"{p.relative_to(REPO)}:{node.lineno} {name}")
            elif name == "open" and (isinstance(f, ast.Name) or (isinstance(f, ast.Attribute) and f.attr == "open")):
                mode = next((k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)),
                            None)
                if mode is None and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = node.args[1].value
                if isinstance(f, ast.Attribute) and not isinstance(mode, str):
                    continue                                # obj.open(...) of other kinds (h5py, Qt) without a mode
                if isinstance(mode, str) and "b" in mode:
                    continue
                if "encoding" not in kws:
                    bad.append(f"{p.relative_to(REPO)}:{node.lineno} open({mode!r})")
    assert not bad, bad


# ----------------------------------------------------------------------------- stack ids
@pytest.mark.parametrize("sid,day,scan,hhmm", [
    ("2026-11-05_092758_alpha_beta_f_k_stack", "2026-11-05", "092758_alpha_beta_f_k_stack", "09:27"),
    ("_092758_x_stack", "", "092758_x_stack", "09:27"),
    ("CLS GUI_150255_UF4_u_n45coarse_1_stack", "CLS GUI", "150255_UF4_u_n45coarse_1_stack", "15:02"),
    ("2026_run_b_000512_y_stack", "2026_run_b", "000512_y_stack", "00:05"),
    ("stitched_2026-11-05_092758_x_stack", "", "stitched_2026-11-05_092758_x_stack", ""),
    ("plainname", "", "plainname", ""),
])
def test_split_stack_id_and_short_time(sid, day, scan, hhmm):
    from aurora.core.names import short_time, split_stack_id
    assert split_stack_id(sid) == (day, scan)
    assert short_time(sid) == hhmm


def test_no_fixed_offset_stack_id_slices():
    """'<day>_<scan>' ids are split with names.split_stack_id, never with sid[11:] (day keys of any length)."""
    bad = [f"{p.relative_to(REPO)}:{i}" for p in PKG.rglob("*.py")
           for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if re.search(r"\[11\s*:", line)]
    assert not bad, bad


# ----------------------------------------------------------------------------- stubs with their final signatures
def test_stubs_have_their_final_signatures():
    from aurora.core.lines import default_signal
    from aurora.core.zones import export_ready
    assert default_signal(None, {}) == "pfy" and default_signal(object(), {"mode": "PFY F Kα"}) == "pfy"
    z = Zone(id=4, label="d", rects=[[0.0, 1.0, 0.0, 1.0]], polys=[[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]])
    d = export_ready(z, {"2026-11-05_092758_x": (0.25, 0.25)})
    assert d == z.to_json()
    d["rects"][0][0] = 99.0
    assert z.rects[0][0] == 0.0                                  # a copy: the zone is untouched


# ----------------------------------------------------------------------------- session-file fields
def _settings_json(**extra) -> dict:
    d = {"inc": True, "n": 18, "sdd": [True, True, False, True], "line": "F Kα", "fn": "i0jump", "tey": True,
         "tn": "i0jump", "ds": True, "k": 6, "i0src": "own", "pre": None, "post": [700, 730], "stack": "092758_x_stack"}
    d.update(extra)
    return d


def test_scan_settings_new_fields_round_trip():
    old = _settings_json()
    st = ScanSettings.from_json(old)
    assert (st.roi, st.signal, st.ipfy_mode, st.despike_mode, st.pinned, st.extra) == (None, "pfy", "ratio", "classic", [], {})
    assert st.to_json() == old                                   # a file without the new keys writes exactly as today
    st.roi = {"sdd1": [60, 83], "sdd2": [61, 84], "sdd3": [60, 83], "sdd4": [62, 85]}
    st.signal, st.ipfy_mode, st.despike_mode, st.pinned = "ipfy", "cls", "coherent", ["tn", "sdd"]
    d = st.to_json()
    assert (d["roi"]["sdd2"], d["sig"], d["ipm"], d["dsm"], d["pin"]) == ([61, 84], "ipfy", "cls", "coherent", ["tn", "sdd"])
    back = ScanSettings.from_json(json.loads(json.dumps(d)))
    assert back == st and back.to_json() == d
    # keys given explicitly at their default value are kept; unknown keys are written back unchanged
    explicit = _settings_json(sig="pfy", dsm="classic", future_key={"a": 1}, other=[1, 2])
    st2 = ScanSettings.from_json(explicit)
    assert st2.extra == {"future_key": {"a": 1}, "other": [1, 2]}
    assert st2.to_json() == explicit


def test_scan_settings_differs_from():
    a, b = ScanSettings(), ScanSettings()
    assert a.differs_from(b) == [] and ScanSettings().differs_from(ScanSettings(k=6)) == []
    b.include, b.n, b.stack, b.pinned, b.extra = False, 40, "x", ["fn"], {"q": 1}      # bookkeeping never counts
    assert a.differs_from(b) == []
    b.fluo_norm, b.sdd, b.post, b.signal = "max", [True, True, False, True], [700.0, 730.0], "ipfy"
    assert a.differs_from(b) == ["sdd", "fluo_norm", "post", "signal"]
    assert ScanSettings(pre=[1.0, 2.0]).differs_from(ScanSettings(pre=(1.0, 2.0))) == []
    assert a.differs_from(b, fields=("fluo_norm", "tey")) == ["fluo_norm"]


def test_zones_file_unknown_keys_ui_rules_and_polygons(tmp_path):
    d = {"version": 1, "exported": "2026-11-06T20:00:00", "project": "P", "built": "x", "notes": "", "goodI0": {},
         "top_future": {"keep": True},
         "ui": {"preflight_ignored": ["a"]}, "rules": [{"scope": "project", "patch": {"tn": "i0pre"}}],
         "holders": {"N5": {"holder": "N5", "day": "2026-11-05", "survey": "s", "holder_future": 7, "zones": [
             {"id": 1, "label": "13 alpha_beta", "group": "", "comment": "", "rects": [], "erects": [], "paint": [], "erase": [],
              "polys": [[[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]]], "zone_future": "z",
              "st": {"2026-11-05_092758_x_stack": _settings_json(set_future=1.5)}, "members": {}}]}}}
    zf = ZonesFile.from_json(json.loads(json.dumps(d)))
    hz = zf.holders["N5"]
    z = hz.zones[0]
    assert isinstance(hz, HolderZones) and hz.extra == {"holder_future": 7}
    assert z.polys == [[[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]]] and z.extra == {"zone_future": "z"}
    assert z.settings["2026-11-05_092758_x_stack"].extra == {"set_future": 1.5}
    assert zf.extra == {"top_future": {"keep": True}} and zf.ui == {"preflight_ignored": ["a"]} and len(zf.rules) == 1
    assert zf.to_json() == d
    zf.save(tmp_path / "session.json")
    assert json.loads((tmp_path / "session.json").read_text(encoding="utf-8")) == d
    assert ZonesFile.load(tmp_path / "session.json").to_json() == d
    # the zone tool's export: no ui and no rules block, everything else as is
    ex = zf.to_json(export=True)
    assert "ui" not in ex and "rules" not in ex and ex["holders"] == d["holders"] and ex["top_future"] == {"keep": True}
    zf.save(tmp_path / "zones_all.json", export=True)
    assert set(json.loads((tmp_path / "zones_all.json").read_text(encoding="utf-8"))) == set(ex)
    # a zones_all.json of the tool (no new keys) writes back without them
    plain = ZonesFile.from_json({"version": 1, "holders": {"N5": {"holder": "N5", "day": "", "survey": "", "zones": []}}})
    assert plain.ui == {} and plain.rules == [] and plain.extra == {}
    assert set(plain.to_json()) == {"version", "exported", "project", "built", "notes", "goodI0", "holders"}
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError):
        ZonesFile.load(tmp_path / "list.json")


# ----------------------------------------------------------------------------- synthetic beamtimes
def test_fake_project_with_two_holders(tmp_path):
    one = Project.discover(testdata.make_fake_project(tmp_path / "one"))
    two = Project.discover(testdata.make_fake_project(tmp_path / "two", n_holders=2))
    assert [h.name for d in one.days for h in d.holders] == ["N5"]
    hs = [h for d in two.days for h in d.holders]
    assert [h.name for h in hs] == ["N5", "N6"]
    assert [s.name for s in hs[1].stacks] == [testdata.STACK2] and hs[1].survey.name == testdata.SURVEY2
    assert sorted(s.status for s in two.scans) == ["aborted", "complete", "complete", "survey", "survey", "test"]
    # holder N5 is the same data whatever the variant
    rel = Path("raw") / testdata.DAY / testdata.STACK / "alpha_beta_f_k_689_00eV" / "sdd2_0.bin"
    assert (tmp_path / "one" / rel).read_bytes() == (tmp_path / "two" / rel).read_bytes()
    with pytest.raises(ValueError):
        testdata.make_fake_project(tmp_path / "bad", n_holders=0)


def test_fake_project_loose_folder(tmp_path):
    root = testdata.make_fake_project(tmp_path / "CLS GUI", loose=True)
    assert root == tmp_path / "CLS GUI"
    assert sorted(p.name for p in root.iterdir()) == sorted([testdata.SURVEY, testdata.STACK, testdata.ABORTED, testdata.TEST])
    proj = Project.discover(root)
    assert sorted(s.status for s in proj.scans) == ["aborted", "complete", "survey", "test"]


def test_make_fake_zones_takes_a_project_or_its_root(fake_project):
    a = testdata.make_fake_zones(Project.discover(fake_project))
    b = testdata.make_fake_zones(fake_project)
    assert a.to_json() == b.to_json() and len(b.holders["N5"].zones) == 3


def test_demo_extras_and_cmd_demo(tmp_path, capsys):
    from aurora.cli import cmd_demo
    from aurora.core.compare import find_reduced_csvs, load_spectra
    from aurora.core.references import list_references
    root = tmp_path / "demo beamtime"
    assert cmd_demo(root) == 0
    reduced, refs = root / "processed" / "reduced", root / "references"
    found = find_reduced_csvs(reduced)
    assert sorted(p.parent.name for p in found) == sorted(testdata.DEMO_SPECTRA)
    sp = {s.signal: s for s in load_spectra(reduced / "NaF" / "NaF_full_reduced.csv")}
    assert set(sp) == {"sdd-scaled", "sdd_total", "TEY"} and sp["sdd_total"].E.size == testdata.DEMO_E[2]
    assert all(s.label == "NaF" for s in sp.values())
    entries = list_references(refs)
    assert sorted(e.label for e in entries) == sorted(f"{n} F K" for n in testdata.DEMO_REFERENCES)
    assert {e.edge for e in entries} == {"F K"}
    ref = load_spectra(refs / "NaF_F_K_ref.csv")
    assert [s.signal for s in ref] == ["mu_norm"] and abs(float(ref[0].y[-1]) - 1.0) < 0.05
    # deterministic: a second demo gives the same files
    extras = testdata.write_demo_extras(tmp_path / "again")
    assert (extras["spectra"][0]).read_bytes() == (reduced / "NaF" / "NaF_full_reduced.csv").read_bytes()
    # the raw data is still the four-scan beamtime
    proj = Project.discover(root)
    assert sorted(s.status for s in proj.scans) == ["aborted", "complete", "survey", "test"]
    assert "reduced spectra in" in capsys.readouterr().out
