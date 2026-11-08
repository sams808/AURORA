"""I21: batch settings. Copy one card to many (three targets, the fields that travel, Ctrl+Shift+C / V), and rules
(scope, scan filter, patch) with a dry run, pinned fields, defaults for scans attached later, and the Rules dialog."""
import copy

import pytest

from aurora.core import rules as rl
from aurora.core import testdata
from aurora.core.io.config import HolderZones, ScanSettings, Zone, ZonesFile
from aurora.core.model import Project
from wp3_helpers import add_stack

RULE = {"name": "TEY stacks", "filter": {"edge": "F K", "plan": "_TEY"}, "patch": {"sdd": [False] * 4, "tn": "i0pre"}}
TEY1 = "093500_alpha_beta_f_k_TEY_stack"
TEY2 = "152000_CaF2_group_f_k_TEY_stack"


@pytest.fixture
def two_by_two(tmp_path):
    """2 holders x 2 complete stacks (one of each named *_TEY_stack)."""
    root = testdata.make_fake_project(tmp_path / "bt", n_holders=2)
    add_stack(root, TEY1, "alpha_beta_f_k_TEY", created="Thu Nov  5 09:35:00 2026")
    add_stack(root, TEY2, "CaF2_group_f_k_TEY", created="Thu Nov  5 15:20:00 2026", seed=12)
    proj = Project.discover(root)
    assert [s.name for s in proj.days[0].holders[0].stacks] == [testdata.STACK, TEY1]
    assert [s.name for s in proj.days[0].holders[1].stacks] == [testdata.STACK2, TEY2]
    return proj


def _sid(proj, name):
    s = proj.find(name)
    return f"{s.day}_{s.name}"


def _three_zones(proj) -> ZonesFile:
    zf = ZonesFile(project="T")
    n5 = [_sid(proj, testdata.STACK), _sid(proj, TEY1)]
    n6 = [_sid(proj, testdata.STACK2), _sid(proj, TEY2)]
    z1 = Zone(id=1, label="a", group="Minerals", settings={s: ScanSettings(stack=s[11:], n=6) for s in n5})
    z2 = Zone(id=2, label="b", group="Glass", settings={s: ScanSettings(stack=s[11:], n=4) for s in n5})
    z3 = Zone(id=1, label="c", group="Minerals", settings={s: ScanSettings(stack=s[11:], n=5) for s in n6})
    zf.holders["N5"] = HolderZones(holder="N5", day=testdata.DAY, survey=testdata.SURVEY, zones=[z1, z2])
    zf.holders["N6"] = HolderZones(holder="N6", day=testdata.DAY, survey=testdata.SURVEY2, zones=[z3])
    return zf


def test_rule_dry_run_and_apply(two_by_two):
    proj = two_by_two
    zf = _three_zones(proj)
    rule = rl.Rule.from_json(RULE)
    rows = rule.dry_run(zf, proj)
    tey = {_sid(proj, TEY1), _sid(proj, TEY2)}
    assert {(r.holder, r.zone, r.sid) for r in rows} == {("N5", 1, _sid(proj, TEY1)), ("N5", 2, _sid(proj, TEY1)),
                                                         ("N6", 1, _sid(proj, TEY2))}
    assert sorted({r.field for r in rows}) == ["sdd", "tn"] and len(rows) == 6
    assert all(r.old in ([True] * 4, "i0jump") and r.new in ([False] * 4, "i0pre") for r in rows)
    before = copy.deepcopy(zf)
    done = rule.apply(zf, proj)
    assert done == rows
    for hk, hz in zf.holders.items():
        for z, zb in zip(hz.zones, before.holders[hk].zones):
            for sid, st in z.settings.items():
                old = zb.settings[sid]
                if sid in tey:
                    assert st.sdd == [False] * 4 and st.tey_norm == "i0pre"
                    assert st.differs_from(old) == ["sdd", "tey_norm"] and st.include == old.include and st.n == old.n
                else:
                    assert st == old
    assert rule.dry_run(zf, proj) == []                     # applied: nothing left to change
    # a pinned 'tn' survives re-applying; override replaces it
    st = zf.holders["N5"].zones[0].settings[_sid(proj, TEY1)]
    st.tey_norm, st.pinned = "area", ["tn"]
    assert rule.apply(zf, proj) == [] and st.tey_norm == "area"
    assert [r.field for r in rule.apply(zf, proj, override=True)] == ["tn"] and st.tey_norm == "i0pre"
    assert "tn" not in st.pinned


def test_scopes_filters_and_relative_windows(two_by_two):
    proj = two_by_two
    zf = _three_zones(proj)
    by_holder = rl.Rule(scope={"kind": "holders", "holders": ["N6"]}, patch={"fn": "area"})
    assert {(r.holder, r.zone) for r in by_holder.dry_run(zf, proj)} == {("N6", 1)}
    by_group = rl.Rule(scope={"kind": "groups", "groups": ["glass"]}, patch={"ds": False})
    assert {(r.holder, r.zone) for r in by_group.dry_run(zf, proj)} == {("N5", 2)}
    by_zone = rl.Rule(scope={"kind": "zones", "zones": [["N5", 1]]}, patch={"k": 8})
    assert {(r.holder, r.zone) for r in by_zone.dry_run(zf, proj)} == {("N5", 1)}
    other_edge = rl.Rule(scan_filter={"edge": "O K"}, patch={"fn": "max"})
    assert other_edge.dry_run(zf, proj) == []
    span = rl.Rule(scan_filter={"span": [679, 693]}, patch={"fn": "max"})
    assert len(span.dry_run(zf, proj)) == 6
    assert rl.Rule(scan_filter={"span": [681, 693]}, patch={"fn": "max"}).dry_run(zf, proj) == []
    flagged = rl.Rule(scan_filter={"flags": ["sdd_saturated"]}, patch={"fn": "max"})
    assert flagged.dry_run(zf, proj) == []
    proj.find(TEY1).summary["sdd_saturated"] = ["sdd2"]
    assert {r.sid for r in flagged.dry_run(zf, proj)} == {_sid(proj, TEY1)}
    # pre / post relative to E0 (xraydb's F K edge), in eV
    rel = rl.Rule(patch={"pre": "E0-15..E0-5", "post": "E0+15..E0+35"})
    e0 = rl.edge_ev(proj.find(testdata.STACK))
    row = next(r for r in rel.dry_run(zf, proj) if r.field == "pre")
    assert row.new == [e0 - 15, e0 - 5]
    with pytest.raises(ValueError):
        rl.parse_range("E0-15..", e0)
    assert rl.parse_range("680-684") == [680.0, 684.0] and rl.parse_range("auto") is None
    # JSON round trip of a rule list (ZonesFile.rules)
    rules = [rl.Rule.from_json(RULE), by_group]
    assert [r.to_json() for r in rl.rules_from_json(rl.rules_to_json(rules))] == [r.to_json() for r in rules]


def test_copy_settings_core():
    src = ScanSettings(sdd=[True, True, False, True], fluo_norm="area", include=False, n=3)
    dst = ScanSettings(n=9)
    changes = rl.copy_settings(src, [dst], rl.DEFAULT_COPY)
    assert dst.sdd == src.sdd and dst.fluo_norm == "area" and dst.include is True and dst.n == 9
    assert {c.field for c in changes} == {"sdd", "fn"} and set(dst.pinned) == {"sdd", "fn"}
    assert "inc" not in rl.DEFAULT_COPY and "n" not in rl.DEFAULT_COPY
    with pytest.raises(KeyError):
        rl.key_of("nonsense")


# ----------------------------------------------------------------------------- in the application
_pg = pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_copy_to_this_scan_in_every_zone(qapp, tmp_path):
    from aurora.app import mode
    from wp3_helpers import close, open_window, pump
    root = testdata.make_fake_project(tmp_path / "bt")
    add_stack(root, TEY1, "alpha_beta_f_k_TEY", dx=3.0)                # S2: a footprint 3 mm to the right
    proj = Project.discover(root)
    win, s1 = open_window(qapp, proj)
    try:
        ses, rd = win.session, win.reduce
        s2 = _sid(proj, TEY1)
        ses.stack(s2)
        rd._attach(ses.stack_loaded(s2))
        a1, a2 = ses.stack_loaded(s1).bbox, ses.stack_loaded(s2).bbox
        with ses.edit("zones A and B"):
            za = ses.new_zone("A"); za.rects.append([a1[0], a2[1], a1[2], a1[3]])    # covers S1 and S2
            zb = ses.new_zone("B"); zb.rects.append(list(a1))                         # covers S1 only
        ses.select_zone(za.id)
        pump(qapp)
        assert set(za.settings) == {s1, s2} and set(zb.settings) == {s1}
        incs = {(z.id, s): (st.include, st.n) for z in (za, zb) for s, st in z.settings.items()}
        s2_before = copy.deepcopy(za.settings[s2])
        mode.set_advanced(True)
        pump(qapp)
        card = next(c for c in rd._cards if c.sid == s1)
        card.sdd[2].setChecked(False)
        card.fn.setCurrentIndex(card.fn.findData("area"))
        pump(qapp)
        card = next(c for c in rd._cards if c.sid == s1)
        menu = card.copy_menu()
        assert [a.text() for a in menu.actions()[:3]] == [f"Copy to {w}" for w in rl.COPY_TARGETS.values()]
        changes = rd.copy_card_to(card, "scan")
        assert zb.settings[s1].sdd == [True, True, False, True] and zb.settings[s1].fluo_norm == "area"
        assert za.settings[s2] == s2_before                               # S2 unchanged
        assert {(z.id, s): (st.include, st.n) for z in (za, zb) for s, st in z.settings.items()} == incs
        assert rd.status_label.text() == "settings copied to 1 card in 1 zone" and len(changes) == 2
        # 'all scans of this zone' and copy / paste between cards
        card = next(c for c in rd._cards if c.sid == s1)
        rd.copy_card_to(card, "zone")
        assert za.settings[s2].fluo_norm == "area" and za.settings[s2].include == s2_before.include
        za.settings[s2].fluo_norm = "max"
        rd._rebuild_cards()
        rd.copy_card(next(c for c in rd._cards if c.sid == s1))
        rd.paste_card(next(c for c in rd._cards if c.sid == s2))
        assert za.settings[s2].fluo_norm == "area"
        # the field checklist: untick 'fluorescence normalisation' and it no longer travels
        rd.set_copy_field("fn", False)
        assert "fn" not in rd.copy_keys() and "sdd" in rd.copy_keys()
        zb.settings[s1].fluo_norm = "raw"
        rd.copy_card_to(next(c for c in rd._cards if c.sid == s1), "scan")
        assert zb.settings[s1].fluo_norm == "raw"
        rd.set_copy_field("fn", True)
    finally:
        mode.set_advanced(False)
        close(win)


def test_saved_rules_reach_a_stack_attached_later_and_the_dialog(qapp, two_by_two, monkeypatch):
    from PySide6.QtWidgets import QDialog
    from aurora.app import mode
    from wp3_helpers import close, open_window, pump
    proj = two_by_two
    win, s1 = open_window(qapp, proj)
    try:
        ses, rd = win.session, win.reduce
        t1 = _sid(proj, TEY1)
        ses.stack(t1)
        rd._attach(ses.stack_loaded(t1))
        sd = ses.stack_loaded(s1)
        with ses.edit("a zone"):
            z = ses.new_zone("a"); z.rects.append(list(sd.bbox))
        pump(qapp)
        assert set(z.settings) == {s1, t1}
        # the dialog: a rule like RULE, live table rows == dry run, Save as default
        monkeypatch.setattr(QDialog, "exec", lambda self: 0)
        mode.set_advanced(True)
        d = rd.open_rules()
        d.add_rule()
        d.name.setText("TEY stacks")
        d.edge.setText("F K")
        d.plan.setText("_TEY")
        d.use["sdd"].setChecked(True)
        for c in d.sdd_boxes:
            c.setChecked(False)
        d.use["tn"].setChecked(True)
        d.tn.setCurrentIndex(d.tn.findData("i0pre"))
        n = d.refresh()
        assert n == len(d.dry_run()) == d.table.rowCount() == 2
        assert {d.table.item(i, 3).text() for i in range(n)} == {"detectors", "TEY normalisation"}
        d.save_default()
        assert rl.rules_from_json(ses.zones.rules)[0].patch == {"sdd": [False] * 4, "tn": "i0pre"}
        assert z.settings[t1].tey_norm == "i0jump"          # saving does not apply
        changes = d.apply_now()
        assert {c.field for c in changes} == {"sdd", "tn"} and z.settings[t1].tey_norm == "i0pre"
        assert z.settings[s1].tey_norm == "i0jump"
        assert d.refresh() == 0 and d.table.rowCount() == 0
        d.deleteLater()
        ses.flush(); ses.save()
        # a third stack written afterwards and attached receives the patch
        t3 = "094500_alpha_beta_f_k_TEY_stack"
        add_stack(proj.root, t3, "alpha_beta_f_k_TEY", created="Thu Nov  5 09:45:00 2026", seed=13)
        proj2 = Project.discover(proj.root)
        ses.set_project(proj2)
        pump(qapp)
        win.jobs.wait_idle(30000)
        sid3 = _sid(proj2, t3)
        ses.stack(sid3)
        ses.touch()
        pump(qapp)
        z2 = ses.holder_zones().zones[0]
        assert z2.settings[sid3].sdd == [False] * 4 and z2.settings[sid3].tey_norm == "i0pre"
        assert z2.settings[_sid(proj2, testdata.STACK)].sdd == [True] * 4
        # 'Remember these windows for F K' saves a rule the next attached scan inherits
        ses.select_stack(_sid(proj2, testdata.STACK))
        pump(qapp)
        rd.remember_windows({"line": "total XRF", "roi": {d_: [60, 74] for d_ in ("sdd1", "sdd2", "sdd3", "sdd4")}})
        names = [r["name"] for r in ses.zones.rules]
        assert "XRF window for F K" in names
    finally:
        mode.set_advanced(False)
        close(win)
