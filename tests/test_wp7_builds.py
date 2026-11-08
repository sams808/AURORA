"""I39: report builds that never overwrite or race: scope (holders, days), Draft / Final presets, a folder per build,
latest.json, unique file names, locked targets, a snapshot of the zones and stacks."""
import json
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.io.config import ScanSettings, Zone, ZonesFile
from aurora.core.report import (BUILDING_PREFIX, KEEP_BUILDS, LATEST, MANIFEST, PRESETS, ReportBuilder, slug_name)

from wp7_support import bbox, project, two_holder_zones


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _tree(folder: Path) -> dict:
    """Every file under folder -> (size, mtime_ns, sha-free bytes digest), to prove a folder untouched."""
    import hashlib
    return {str(p.relative_to(folder)): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha1(p.read_bytes()).hexdigest())
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _quick(proj, zf, out, **kw):
    kw.setdefault("png_dpi", 40)
    kw.setdefault("page_png_dpi", 0)
    return ReportBuilder(proj, zf, out, **kw)


def test_build_rename_build_again(tmp_path):
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    out = tmp_path / "reports"
    r1 = _quick(proj, zf, out, title="T").build()
    before = _tree(r1.folder)
    assert r1.folder.parent == out and r1.folder.name.endswith("_all") and r1.pdf.parent == r1.folder
    assert not list(out.glob(BUILDING_PREFIX + "*"))
    zf.holders["N5"].zones[0].label = "1 alpha renamed"
    r2 = _quick(proj, zf, out, title="T").build()
    assert r2.folder != r1.folder and r2.folder.parent == out
    old_name = next(p.name for p in r1.csvs if p.name.startswith("N5_z1_"))
    names2 = sorted(p.name for p in r2.csvs)
    assert old_name.startswith("N5_z1_1_alpha_0") and old_name not in names2
    assert any(n.startswith("N5_z1_1_alpha_renamed_") for n in names2)
    assert sorted(p.name for p in (r2.folder / "csv").rglob("*.csv")) == names2          # only the new name
    assert _tree(r1.folder) == before                                                    # the first build untouched
    man = json.loads(r2.manifest.read_text(encoding="utf-8"))
    ch = man["changes_since"]
    assert ch["previous"] == r1.folder.name
    assert ch["zones_renamed"] == ["N5 zone 1: '1 alpha' → '1 alpha renamed'"]
    assert not ch["zones_added"] and not ch["zones_removed"] and not ch["settings_changed"]
    assert any("renamed" in ln for ln in ReportBuilder.changes_lines(ch))
    lat = json.loads((out / LATEST).read_text(encoding="utf-8"))
    assert lat["latest"] == r2.folder.name and [b["folder"] for b in lat["builds"]] == [r2.folder.name, r1.folder.name]


def test_changes_since_settings_zones_and_scans(tmp_path):
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    out = tmp_path / "reports"
    _quick(proj, zf, out).build()
    hz = zf.holders["N5"]
    sid = next(iter(hz.zones[0].settings))
    hz.zones[0].settings[sid].fluo_norm = "max"
    hz.zones[2].settings[sid].include = True
    hz.zones.pop(1)
    hz.zones.append(Zone(id=9, label="new one", group="Minerals", rects=list(hz.zones[0].rects)))
    r = _quick(proj, zf, out).build()
    ch = json.loads(r.manifest.read_text(encoding="utf-8"))["changes_since"]
    assert ch["zones_added"] == ["N5 · 'new one' (zone 9)"] and ch["zones_removed"] == ["N5 · '2 beta' (zone 2)"]
    assert any("'1 alpha'" in s and "fluo_norm" in s for s in ch["settings_changed"])
    assert any("'3 gamma'" in s and "include" in s for s in ch["settings_changed"])
    assert ch["scans_added"] == []


def test_holders_scope_on_a_two_holder_project(tmp_path):
    proj = project(tmp_path, n_holders=2)
    zf = two_holder_zones(proj)
    out = tmp_path / "reports"
    both = _quick(proj, zf, out).build()
    assert {e["holder"] for e in json.loads(both.manifest.read_text(encoding="utf-8"))["entries"]} == {"N5", "N6"}
    b = _quick(proj, zf, out, holders=["N5"])
    entries = b.collect()
    assert entries and {e.holder for e in entries} == {"N5"}
    res = _quick(proj, zf, out, holders=["N5"]).build()
    man = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert {e["holder"] for e in man["entries"]} == {"N5"} and man["scope"]["holders"] == ["N5"]
    assert res.folder.name.endswith("_N5") and all(c.name.startswith("N5_") for c in res.csvs)
    n6 = _quick(proj, zf, out, holders=["N6"]).collect()
    assert {e.holder for e in n6} == {"N6"}
    assert _quick(proj, zf, out, days=["2026-01-01"]).collect() == []
    assert len(_quick(proj, zf, out, days=[testdata.DAY]).collect()) == len(entries) + len(n6)


def test_draft_preset_writes_figures_at_110_dpi(tmp_path):
    from PIL import Image
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    b = ReportBuilder(proj, zf, tmp_path / "reports", preset="draft", page_png_dpi=0)
    assert (b.png_dpi, b.appendix, b.maps) == (110, False, False) == tuple(PRESETS["draft"][k] for k in ("png_dpi", "appendix", "maps"))
    res = b.build()
    assert res.figures and res.folder.name.endswith("_all_draft")
    for f in res.figures:
        im = Image.open(f)
        assert round(im.info["dpi"][0]) == 110
        assert abs(im.size[0] - 8.27 * 110) <= 2 and abs(im.size[1] - 11.69 * 110) <= 2
    final = ReportBuilder(proj, zf, tmp_path / "reports", page_png_dpi=0)
    assert (final.png_dpi, final.appendix, final.maps) == (600, True, True)
    # explicit options override the preset; an unknown preset is refused
    assert ReportBuilder(proj, zf, tmp_path, preset="draft", png_dpi=200, maps=True).png_dpi == 200
    with pytest.raises(ValueError):
        ReportBuilder(proj, zf, tmp_path, preset="poster")
    man = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert man["options"]["preset"] == "draft" and man["options"]["png_dpi"] == 110
    assert res.pages < _quick(proj, zf, tmp_path / "r2", page_png_dpi=0).build().pages     # no Appendix A or B


def test_labels_that_slug_alike_give_distinct_files(tmp_path):
    proj = project(tmp_path)
    sid, (x0, x1, y0, y1) = bbox(proj, testdata.STACK)
    zf = testdata.make_fake_zones(proj)
    hz = zf.holders["N5"]
    a = Zone(id=13, label="13 alpha_beta", group="Minerals", rects=[[x0, (x0 + x1) / 2, y0, y1]])
    b = Zone(id=14, label="13_alpha_beta", group="Minerals", rects=[[(x0 + x1) / 2, x1, y0, y1]])
    for z in (a, b):
        z.settings[sid] = ScanSettings(stack=testdata.STACK, line="total XRF", n=6)
    hz.zones = [a, b]
    res = _quick(proj, zf, tmp_path / "reports").build()
    assert len(res.csvs) == 2 and len({c.name for c in res.csvs}) == 2
    from aurora.core.provenance import read_header
    heads = {read_header(c)["zone_label"]: c for c in res.csvs}
    assert set(heads) == {"13 alpha_beta", "13_alpha_beta"}
    from aurora.core.compare import read_csv_table
    _h, cols, da = read_csv_table(heads["13 alpha_beta"])
    _h, _c, db = read_csv_table(heads["13_alpha_beta"])
    k = cols.index("fluo_sum_raw")
    assert not np.allclose(da[:, k], db[:, k])                                    # each file its own content
    # a long label is cut, with a hash of the full text; two long labels with the same start stay distinct
    long1, long2 = "x" * 60 + " one", "x" * 60 + " two"
    assert slug_name(long1) != slug_name(long2) and len(slug_name(long1)) <= 47


def test_a_thread_renaming_zones_during_the_build_changes_nothing(tmp_path):
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    snapshot = ZonesFile.from_json(json.loads(json.dumps(zf.to_json())))
    stacks = {}
    builder = ReportBuilder(proj, zf, tmp_path / "live", title="Race", png_dpi=40, page_png_dpi=0, stacks=stacks)
    stop = threading.Event()
    renames = [0]

    def rename():
        while not stop.is_set():
            for z in zf.holders["N5"].zones:
                z.label = f"renamed {renames[0]}"
                z.group = "Elsewhere"
                for st in z.settings.values():
                    st.fluo_norm = "raw"
            zf.holders["N5"].zones.append(Zone(id=100 + renames[0], label="intruder"))
            renames[0] += 1
            time.sleep(0.001)
    t = threading.Thread(target=rename, daemon=True)
    t.start()
    try:
        live = builder.build()
    finally:
        stop.set()
        t.join(5)
    assert renames[0] > 0
    ref = ReportBuilder(proj, snapshot, tmp_path / "ref", title="Race", png_dpi=40, page_png_dpi=0).build()
    ml, mr = (json.loads(r.manifest.read_text(encoding="utf-8")) for r in (live, ref))
    for k in ("entries", "groups", "not_included", "skipped", "csv", "zones", "settings", "preflight", "totals"):
        assert ml[k] == mr[k], k
    assert [c.read_bytes() for c in live.csvs] == [c.read_bytes() for c in ref.csvs]
    assert {e["label"] for e in ml["entries"]} == {"1 alpha", "2 beta"}


def test_the_stacks_given_are_a_frozen_copy(tmp_path):
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    from aurora.core.stack import StackData
    sid = f"{testdata.DAY}_{testdata.STACK}"
    stacks = {sid: StackData.load(proj, proj.find(testdata.STACK))}
    b = ReportBuilder(proj, zf, tmp_path / "r", stacks=stacks)
    stacks.clear()
    assert b.stack(sid) is not None and sid in b._given
    with pytest.raises(TypeError):
        b._given["x"] = None


def test_two_builds_in_one_minute_and_the_latest_pointer(tmp_path):
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    out = tmp_path / "reports"
    folders = [_quick(proj, zf, out, appendix=False, maps=False).build().folder for _ in range(KEEP_BUILDS + 1)]
    assert len(set(folders)) == len(folders) and all(f.is_dir() for f in folders)       # nothing deleted
    lat = json.loads((out / LATEST).read_text(encoding="utf-8"))
    assert lat["latest"] == folders[-1].name
    assert [b["folder"] for b in lat["builds"]] == [f.name for f in reversed(folders)][:KEEP_BUILDS]
    assert all((out / b["pdf"]).exists() for b in lat["builds"])


def test_a_locked_target_is_written_as_name_2(tmp_path, monkeypatch):
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    real = ReportBuilder._locked
    seen = []

    def locked(path):
        if path.suffix == ".pdf" and not path.stem.endswith("_2"):
            seen.append(path)
            return True
        return real(path)
    monkeypatch.setattr(ReportBuilder, "_locked", staticmethod(locked))
    res = _quick(proj, zf, tmp_path / "reports").build()
    assert seen and res.pdf.name.endswith("_2.pdf") and res.pdf.exists()
    assert any("was locked" in n and res.pdf.name in n for n in res.notes)
    man = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert man["pdf"] == res.pdf.name and any("was locked" in n for n in man["notes"])


def test_a_locked_file_on_disk_is_detected(tmp_path):
    import sys
    if not sys.platform.startswith("win"):
        pytest.skip("share-mode locks are a Windows behaviour")
    import ctypes
    from ctypes import wintypes
    p = tmp_path / "open_in_a_viewer.pdf"
    p.write_bytes(b"%PDF")
    CreateFileW = ctypes.windll.kernel32.CreateFileW
    CreateFileW.restype = wintypes.HANDLE
    h = CreateFileW(str(p), 0x80000000, 0, None, 3, 0x80, None)        # GENERIC_READ, no sharing, OPEN_EXISTING
    try:
        assert ReportBuilder._locked(p) is True
        b = ReportBuilder(project(tmp_path / "x"), ZonesFile(), tmp_path)
        assert b._target(p).name == "open_in_a_viewer_2.pdf" and b.notes
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    assert ReportBuilder._locked(p) is False


def test_a_cancelled_build_leaves_nothing_behind(tmp_path):
    from aurora.core.cancel import Cancelled
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    out = tmp_path / "reports"
    calls = [0]

    def progress(i, n, msg):
        calls[0] += 1
        if msg.startswith("group"):
            raise Cancelled()
    with pytest.raises(Cancelled):
        _quick(proj, zf, out).build(progress=progress)
    assert calls[0] and not [p for p in out.iterdir() if p.is_dir()] and not (out / LATEST).exists()


def test_a_folder_that_cannot_be_renamed_is_copied(tmp_path, monkeypatch):
    import os
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    real = os.rename

    def refuse(src, dst, *a, **k):
        if BUILDING_PREFIX in str(src):
            raise PermissionError(13, "held by a scanner")
        return real(src, dst, *a, **k)
    monkeypatch.setattr("aurora.core.report.os.rename", refuse)
    monkeypatch.setattr("aurora.core.report.time.sleep", lambda s: None)
    res = _quick(proj, zf, tmp_path / "reports").build()
    assert res.folder.is_dir() and res.pdf.exists() and (res.folder / MANIFEST).exists()
    assert any("could not be renamed" in n for n in res.notes)
    assert not list((tmp_path / "reports").glob(BUILDING_PREFIX + "*"))


def test_draft_and_current_holder_from_the_perspective(qapp, tmp_path, monkeypatch):
    from wp7_support import close, quick, report_window, pump
    from aurora.app import mode
    from aurora.core import report as core_report
    monkeypatch.setattr(core_report, "os_full_name", lambda: "Test User")
    quick(monkeypatch)
    proj = project(tmp_path, n_holders=2)
    win = report_window(qapp, proj, two_holder_zones(proj), tmp_path)
    rp = win.report
    mode.set_advanced(True)
    pump(qapp)
    assert rp.selected_holders() is None and rp.check_tabs.isTabVisible(2)
    win.session.set_holder("N6")
    rp.current_holder_btn.click()                                  # 'current holder' in one click
    assert rp.selected_holders() == ["N6"]
    rp.all_holders_btn.click()
    assert rp.selected_holders() is None
    win.session.set_holder("N5")
    rp.preset.setCurrentIndex(rp.preset.findData("draft"))
    assert rp.selected_holders() == ["N5"] and rp.dpi.value() == 110
    assert not rp.appendix.isChecked() and not rp.maps.isChecked()
    rp.build()
    win.jobs.wait_idle(120000)
    pump(qapp)
    res = rp._result
    assert res is not None and res.folder.name.endswith("_N5_draft")
    man = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert man["options"]["holders"] == ["N5"] and man["options"]["preset"] == "draft"
    assert {e["holder"] for e in man["entries"]} == {"N5"}
    mode.set_advanced(False)
    pump(qapp)
    assert not rp.check_tabs.isTabVisible(2) and "1 holder (N5" in rp.scope_line.text()
    assert "Draft preset" in rp.scope_line.text()
    close(win)
