"""I24: AURORA's own Check results persist in .aurora/qc.json, drive new zones' defaults and merged_flags, are
re-checked when a scan changes; 'Run on every holder'; the documented I0 rules (I0 mask, energy-local reference)."""
import json
import os
import time

import numpy as np
import pytest

from aurora.core import flags as fl
from aurora.core import qc, testdata
from aurora.core.io.config import ScanSettings
from aurora.core.model import Project, ScanInfo
from aurora.core.qc import CountRate, SaturationResult, reference_pool, verdicts
from aurora.core.spectra import build_traces
from aurora.core.zones import ZoneSums


# ----------------------------------------------------------------------------- the I0 rules (core)
def test_an_i0_dip_is_left_out_of_every_i0_trace():
    E = np.linspace(680.0, 730.0, 51)
    n = 4
    i0 = np.full(len(E), 2.5 * n); i0[30] *= 0.05                     # a dip to 5 % of the median
    step = 1.0 + (E > 696)
    det = np.column_stack([1000 * g * step for g in (1.0, 0.9, 1.1, 0.8)])
    sums = ZoneSums(E=E, n_pix=n, i0=i0, tey=0.5 * n * step, det={"F Kα": det}, main="F Kα", edge_ev=696.7)
    for mode in ("i0", "i0pre", "i0jump", "area", "max"):
        t = build_traces(sums, ScanSettings(line="F Kα", fluo_norm=mode, tey_norm=mode, despike=False))
        assert np.isnan(t.sum[30]) and np.isnan(t.tey[30]) and all(np.isnan(d[30]) for d in t.det), mode
        assert np.isfinite(np.delete(t.sum, 30)).all(), mode
        assert t.masked["i0_low"].sum() == 1 and t.masked["i0_low"][30]
        assert "I0 below 20% of its median at 1 energy (710.00 eV): left out of the / I0 traces" in t.notes
    raw = build_traces(sums, ScanSettings(line="F Kα", fluo_norm="raw", tey_norm="raw", despike=False))
    assert np.isfinite(raw.sum[30]) and raw.sum_raw[30] == det[30].sum()          # raw counts are not divided
    # with de-spiking on the dip is still left out (it is judged before the de-spike interpolates I0)
    t = build_traces(sums, ScanSettings(line="F Kα", despike_mode="coherent"))
    assert np.isnan(t.sum[30]) and t.i0_raw[30] == i0[30]


def _rate(name, e0, e1, level, n=7):
    rows = [dict(E_eV=float(e), i0=float(level), dwell_s=0.05, mean=[100.0] * 4, top=[1000.0 * (1 + 0.01 * i)] * 4)
            for i, e in enumerate(np.linspace(e0, e1, n))]
    return CountRate(scan=name, key="k", time_per_map_s=6.0, rows=rows)


def _scan(name, e0, e1):
    s = ScanInfo(path=__import__("pathlib").Path(name), day="2025-03-14", kind="stack", name=name)
    s.e_min, s.e_max, s.n_points = e0, e1, 100
    return s


def test_the_i0_reference_is_energy_local():
    rates = {"fk1": _rate("fk1", 680, 730, 2.5), "fk2": _rate("fk2", 680, 730, 2.4), "fk3": _rate("fk3", 670, 740, 2.6),
             "se": _rate("se", 1990, 2100, 0.1)}
    scans = {k: _scan(k, r.rows[0]["E_eV"], r.rows[-1]["E_eV"]) for k, r in rates.items()}
    ranges = {k: (s.e_min, s.e_max) for k, s in scans.items()}
    assert reference_pool("se", ranges) == [] and sorted(reference_pool("fk1", ranges)) == ["fk2", "fk3"]
    res = verdicts(rates, scans, "holderA")
    assert not res["se"].i0_bad and res["se"].n_ref == 0 and res["se"].i0_ref is None
    assert not any(res[k].i0_bad for k in ("fk1", "fk2", "fk3"))
    # the old rule (every scan of the holder) would have called the Se scan invalid: 0.1 < 10 % of 2.45
    assert qc.i0_check(0.1, float(np.median([2.5, 2.4, 2.6]))).bad
    # an F K scan with a dropped I0 is flagged against its neighbours in energy
    rates["fk4"] = _rate("fk4", 685, 725, 0.01)
    scans["fk4"] = _scan("fk4", 685, 725)
    res = verdicts(rates, scans, "holderA")
    assert res["fk4"].i0_bad and "I0 invalid" in " ".join(res["fk4"].notes) and not res["se"].i0_bad
    # within +/- 150 eV still counts as a neighbour
    assert reference_pool("a", {"a": (680, 730), "b": (870, 900), "c": (881, 900)}) == ["b"]


def test_qc_json_round_trip(fake_project):
    proj = Project.discover(fake_project)
    r = qc.QcResult(sid="x", i0_level=2.5, i0_bad=True, kcps=[1.0, None, 3.0, 4.0], saturated=["sdd2"],
                    high_rate=["sdd4"], notes=["n"], probed_at="2026-11-08T10:00:00", aurora_version="0.4.0",
                    scan_mtime=12.5, n_energies=5, holder="holderA")
    p = qc.save_qc(proj, {"x": r})
    assert p == fake_project / ".aurora" / "qc.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    assert set(d["x"]) >= {"i0_level", "i0_bad", "kcps", "saturated", "high_rate", "notes", "probed_at",
                           "aurora_version", "scan_mtime"}
    back = qc.load_qc(proj)["x"]
    assert back == r
    qc.save_qc(proj, {"y": qc.QcResult(sid="y")})                    # merged: the other entry is kept
    assert set(qc.load_qc(proj)) == {"x", "y"}
    assert [str(f) for f in back.flags] == ["SDD saturated: SDD2 (AURORA check)", "high count rate: SDD4 (AURORA check)",
                                            "I0 invalid (AURORA check)"]


# ----------------------------------------------------------------------------- the perspective
pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _sat_sdd2(cr, day_kcps, n_pix=None):
    return SaturationResult(saturated=["sdd2"], notes=["SDD saturation: SDD2 (test)"])


def _open(qapp, root):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.home.set_project(Project.discover(root))
    qapp.processEvents()
    return win


def _close(win):
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


def test_checks_persist_and_drive_the_defaults(qapp, fake_project, monkeypatch):
    monkeypatch.setattr(qc, "saturation_check", _sat_sdd2)
    assert not list((fake_project / "processed").glob("*/summary.json"))          # no sgm_report on this beamtime
    win = _open(qapp, fake_project)
    ses = win.session
    scan = ses.project.find(testdata.STACK)
    sid = ses.stack_id(scan)
    win.check.run_checks()
    assert win.jobs.wait_idle(30000)
    qapp.processEvents()
    qcfile = fake_project / ".aurora" / "qc.json"
    entry = json.loads(qcfile.read_text(encoding="utf-8"))[sid]
    assert entry["saturated"] == ["sdd2"] and entry["holder"] == "holderA" and entry["aurora_version"]
    assert "SDD saturated: SDD2 (AURORA check)" in [str(f) for f in fl.merged_flags(ses.project, scan)]
    _close(win)
    # a new window fills the table from qc.json, without probing again
    calls = []
    monkeypatch.setattr(qc, "count_rate_probe", lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError))
    win2 = _open(qapp, fake_project)
    ck = win2.check
    assert ck.table.item(0, 10).text() == "SDD2" and ck.table.item(0, 5).text() == "ok"
    assert win2.jobs.wait_idle(30000) and calls == []
    # a newly drawn zone unticks the saturated SDD (flags of AURORA's check), with the coherent de-spike
    ses2 = win2.session
    sd = ses2.stack(sid)
    z = ses2.new_zone("new")
    with ses2.edit("draw"):
        z.rects.append(list(sd.bbox))
    st = z.settings[sid]
    assert st.sdd == [True, False, True, True] and st.despike_mode == "coherent" and st.signal == "pfy"
    _close(win2)


def test_a_changed_scan_is_checked_again(qapp, fake_project, monkeypatch):
    win = _open(qapp, fake_project)
    win.check.run_every_holder()
    assert win.jobs.wait_idle(30000)
    sid = win.session.stack_id(win.session.project.find(testdata.STACK))
    first = qc.load_qc(win.session.project)[sid].probed_at
    _close(win)
    # the scan's newest energy folder is touched (more data arrived): the next window re-checks it in the background
    edirs = win.session.project.find(testdata.STACK).energy_dirs
    later = time.time() + 5
    os.utime(edirs[-1][1], (later, later))
    calls = []
    real = qc.count_rate_probe
    monkeypatch.setattr(qc, "count_rate_probe", lambda *a, **k: calls.append(a[1].name) or real(*a, **k))
    time.sleep(1.1)                                                   # probed_at has a 1 s resolution
    win2 = _open(qapp, fake_project)
    assert win2.jobs.wait_idle(30000)
    assert testdata.STACK in calls
    r = qc.load_qc(win2.session.project)[sid]
    assert r.probed_at > first and abs(r.scan_mtime - later) < 1
    _close(win2)


def test_run_on_every_holder(qapp, tmp_path):
    root = testdata.make_fake_project(tmp_path / "two", n_holders=2)
    win = _open(qapp, root)
    ses = win.session
    assert list(ses.holder_keys) == ["holderA", "holderB"] and ses.current_holder == "holderA"
    win.check.run_every_holder()
    job = win.jobs.job("check:every")
    assert job is not None and win.check.taskbar.job is job        # one job, on the progress strip with Cancel
    assert win.jobs.wait_idle(30000)
    d = json.loads((root / ".aurora" / "qc.json").read_text(encoding="utf-8"))
    sids = {ses.stack_id(s): k for k in ses.holder_keys for s in ses.stacks_of(k)}
    assert set(d) == set(sids) and {d[s]["holder"] for s in d} == {"holderA", "holderB"}
    assert testdata.STACK2 in " ".join(d)
    _close(win)
