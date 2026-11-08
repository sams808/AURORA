"""I40: check before building. report.plan / preflight findings from h5 coordinates, the 'Check before building'
list with jump and ignore, the Contents tree, the totals line, and the findings in the manifest and Appendix C."""
import json

import pytest

from aurora.core import testdata
from aurora.core.io.config import ScanSettings, SheetRow, Zone, ZonesFile
from aurora.core.report import ReportBuilder, plan, preflight

from wp7_support import bbox, close, project, pump, quick, report_window, two_holder_zones


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _sheet(*rows):
    return [SheetRow(plate=p, group=g, sample_id=s, composition="", f_k_edge="", other_edges="", notes="")
            for p, g, s in rows]


def test_exactly_the_three_findings(tmp_path):
    """One ungrouped zone, one zone whose settings are all include=False, one complete stack covered by no zone."""
    proj = project(tmp_path, n_holders=2)
    zf = testdata.make_fake_zones(proj)
    found = preflight(proj, zf)
    sid2 = f"{testdata.DAY}_{testdata.STACK2}"
    assert sorted((f.kind, f.holder, f.zone_id, f.sid) for f in found) == sorted([
        ("ungrouped", "N5", 2, None), ("no_included", "N5", 3, None), ("uncovered_stack", "N6", None, sid2)])
    assert [f.id for f in preflight(proj, zf)] == [f.id for f in found]                  # stable ids
    texts = {f.kind: f.text for f in found}
    assert texts["ungrouped"] == "'2 beta' (N5) has no group: it goes under 'Ungrouped'."
    assert texts["no_included"] == "'3 gamma' (N5) has no included scan, so it is left out."
    assert "covered by no zone" in texts["uncovered_stack"]
    p = plan(proj, zf)
    assert (p.n_zones, p.n_spectra) == (2, 2) and p.totals() == "2 zones · 2 spectra · 3 warnings"
    assert p.totals(ignored={found[0].id}) == "2 zones · 2 spectra · 2 warnings"
    skipped = {z.label: z.skip for z in p.zones}
    assert skipped == {"1 alpha": "", "2 beta": "", "3 gamma": "no included scan"}
    assert list(p.by_group()) == ["Minerals", "Ungrouped"]


def test_every_other_kind(tmp_path):
    proj = project(tmp_path, n_holders=2)
    zf = two_holder_zones(proj)                              # '1 alpha' in Minerals on N5 and on N6
    sid, (x0, x1, y0, y1) = bbox(proj, testdata.STACK)
    hz = zf.holders["N5"]
    z1, z2, z3 = hz.zones
    z1.settings[sid].i0_source = "good"                      # no good-I0 scan ticked
    proj.find(testdata.STACK).summary = {"sdd_saturated": ["sdd2"]}       # flagged; z1 still sums SDD2
    z2.settings.pop(sid)                                     # covers the stack, no settings
    far = Zone(id=7, label="far away", group="Minerals", rects=[[50.0, 51.0, 50.0, 51.0]])
    far.settings[sid] = ScanSettings(stack=testdata.STACK, line="total XRF")
    gone = Zone(id=8, label="1 alpha", group="Minerals", rects=[[60.0, 61.0, 60.0, 61.0]])     # covers no stack
    gone.settings[f"{testdata.DAY}_093000_nowhere_f_k_stack"] = ScanSettings(stack="093000_nowhere_f_k_stack")
    hz.zones += [far, gone]
    sheet = _sheet(("N5", "Minerals", "1 alpha"), ("N5", "Minerals", "2 betta"), ("N5/N7", "Minerals", "delta"),
                   ("N6", "Minerals", "1 alpha"))
    found = preflight(proj, zf, sheet_rows=sheet)
    kinds = {}
    for f in found:
        kinds.setdefault(f.kind, []).append(f)
    by = lambda k: [(f.holder, f.zone_id, f.sid) for f in kinds.get(k, [])]       # noqa: E731
    assert by("good_i0_empty") == [("N5", 1, None)]
    assert by("saturated_ticked") == [("N5", 1, sid)] and "SDD2, flagged saturated (sgm_report)" in kinds["saturated_ticked"][0].text
    assert by("no_settings") == [("N5", 2, sid)]
    assert "select the scan in Reduce" in kinds["no_settings"][0].text
    assert ("N5", 2, None) in by("no_included") and "no scan attached yet" in kinds["no_included"][0].text
    assert by("no_pixels") == [("N5", 7, sid)]
    assert by("scan_missing") == [("N5", 8, f"{testdata.DAY}_093000_nowhere_f_k_stack")]
    nis = {f.zone_id: f.text for f in kinds["label_not_in_sheet"]}
    assert "closest: '2 betta'" in nis[2] and 1 not in nis
    assert {f.text for f in kinds["sheet_row_no_zone"]} >= {"Sample 'delta' of N5 (sample sheet) has no zone.",
                                                            "Sample '2 betta' of N5 (sample sheet) has no zone."}
    dup = kinds["duplicate_label"]
    assert len(dup) == 1 and dup[0].text == "'1 alpha' appears in group Minerals on holders N5 and N6."
    assert "uncovered_stack" not in kinds                    # both complete stacks are covered
    # scope: the N6 findings go when only N5 is built; the group filter keeps zone findings of the groups built
    only5 = preflight(proj, zf, sheet_rows=sheet, holders=["N5"])
    assert {f.holder for f in only5} == {"N5"} and not [f for f in only5 if f.kind == "duplicate_label"]
    assert not [f for f in preflight(proj, zf, sheet_rows=sheet, groups=["Ungrouped"]) if f.zone_id == 7]


def test_loaded_stacks_give_the_same_coverage_as_the_h5(tmp_path):
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    from aurora.core.stack import StackData
    sid = f"{testdata.DAY}_{testdata.STACK}"
    loaded = {sid: StackData.load(proj, proj.find(testdata.STACK))}
    a, b = plan(proj, zf), plan(proj, zf, stacks=loaded)
    assert [(z.label, z.spectra) for z in a.zones] == [(z.label, z.spectra) for z in b.zones]


def test_the_manifest_and_appendix_c_carry_the_scans_without_settings(tmp_path):
    proj = project(tmp_path)
    zf = testdata.make_fake_zones(proj)
    sid = f"{testdata.DAY}_{testdata.STACK}"
    zf.holders["N5"].zones[1].settings.pop(sid)
    zf.ui["preflight_ignored"] = [f"ungrouped|N5|2"]
    b = ReportBuilder(proj, zf, tmp_path / "reports", png_dpi=40, page_png_dpi=0)
    res = b.build()
    man = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert len(man["scans_without_settings"]) == 1 and "'2 beta'" in man["scans_without_settings"][0]
    assert {f["kind"] for f in man["preflight"]} >= {"no_settings", "ungrouped", "no_included"}
    assert [f["ignored"] for f in man["preflight"] if f["kind"] == "ungrouped"] == [True]
    text = "\n".join(b.appendix_c)
    assert "Scans covering a zone without settings" in text and "'2 beta'" in text
    assert "has no group" in text and "[ignored]" in text
    assert res.plan is not None and man["totals"] == res.plan.totals(zf.ui["preflight_ignored"])


def test_the_check_list_jump_ignore_contents_and_totals(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QToolButton
    from aurora.app.report import IGNORED_KEY
    from aurora.core import report as core_report
    monkeypatch.setattr(core_report, "os_full_name", lambda: "Test User")
    quick(monkeypatch)
    proj = project(tmp_path, n_holders=2)
    win = report_window(qapp, proj, testdata.make_fake_zones(proj), tmp_path)
    rp = win.report
    lst = rp.preflight_list
    assert lst.topLevelItemCount() == 3 and rp.totals.text() == "2 zones · 2 spectra · 3 warnings"
    assert rp.check_tabs.tabText(0) == "Findings (3)" and rp.check_tabs.tabText(1) == "Contents (2)"
    # the Contents tree: group ▸ holder ▸ zone, a skipped zone with the warning icon and the reason
    groups = [rp.contents.topLevelItem(i) for i in range(rp.contents.topLevelItemCount())]
    assert [g.text(0) for g in groups] == ["Minerals", "Ungrouped"]
    minerals = groups[0].child(0)
    assert minerals.text(0) == f"N5 · {testdata.DAY}"
    zones = {minerals.child(j).text(0): minerals.child(j) for j in range(minerals.childCount())}
    assert zones["1 alpha"].text(1) == "F K" and zones["1 alpha"].text(2) == "1"
    assert zones["3 gamma"].text(2) == "0" and not zones["3 gamma"].icon(0).isNull()
    assert "left out: no included scan" in zones["3 gamma"].toolTip(0) and zones["1 alpha"].icon(0).isNull()
    # jump: the Show button of the ungrouped zone selects its holder and zone and shows Reduce
    item = next(lst.topLevelItem(i) for i in range(lst.topLevelItemCount()) if "has no group" in lst.topLevelItem(i).text(0))
    win.session.set_holder("N6")
    lst.itemWidget(item, 2).findChild(QToolButton).click()
    pump(qapp)
    assert win.session.current_holder == "N5" and win.session.current_zone == 2
    assert win.stack.currentWidget() is win.reduce
    win.show_perspective("report")
    pump(qapp)
    # ignore: ticked, remembered with the session, not counted
    fid = item.data(0, Qt.UserRole)
    item = next(lst.topLevelItem(i) for i in range(lst.topLevelItemCount()) if lst.topLevelItem(i).data(0, Qt.UserRole) == fid)
    item.setCheckState(1, Qt.Checked)
    pump(qapp)
    assert win.session.ui_value(IGNORED_KEY) == [fid] and rp.totals.text().endswith("2 warnings")
    last = lst.topLevelItem(lst.topLevelItemCount() - 1)
    assert last.data(0, Qt.UserRole) == fid and last.checkState(1) == Qt.Checked           # sorted last
    win.session.save()
    # build: the totals line equals the entry count of collect()
    rp.build()
    win.jobs.wait_idle(120000)
    pump(qapp)
    res = rp._result
    n = len(ReportBuilder(proj, win.session.zones, tmp_path / "x").collect())
    assert res.entries == n == 2 and rp.totals.text().startswith(f"2 zones · {n} spectra")
    close(win)
    # reopen: the ignored finding stays ignored
    from aurora.core.model import Project
    win2 = report_window(qapp, Project.discover(proj.root), ZonesFile(), tmp_path / "again")
    assert win2.session.ui_value(IGNORED_KEY) == [fid]
    lst2 = win2.report.preflight_list
    states = {lst2.topLevelItem(i).data(0, Qt.UserRole): lst2.topLevelItem(i).checkState(1) for i in range(lst2.topLevelItemCount())}
    assert states[fid] == Qt.Checked and win2.report.totals.text().endswith("2 warnings")
    close(win2)


def test_the_check_runs_again_after_an_edit(qapp, tmp_path, monkeypatch):
    from aurora.core import report as core_report
    monkeypatch.setattr(core_report, "os_full_name", lambda: "Test User")
    proj = project(tmp_path)
    win = report_window(qapp, proj, testdata.make_fake_zones(proj), tmp_path)
    rp = win.report
    assert rp.totals.text().endswith("2 warnings")
    ses = win.session
    with ses.edit("give zone 2 a group"):
        ses.holder_zones("N5").zones[1].group = "Minerals"
    from PySide6.QtTest import QTest
    QTest.qWait(600)
    win.jobs.wait_idle(60000)
    pump(qapp)
    assert rp.totals.text().endswith("1 warning")
    close(win)
