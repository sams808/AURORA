"""Every write path: nothing lands in a beamline raw/ folder or a protected folder (the refusal comes before any file
or folder is made), `aurora demo` only writes into a new or empty folder, AURORA's own caches are read without pickle,
a John-format save replaces all four files of a sample or none and never merges two samples whose labels differ only
in letter case, an ordinary write failure is one calm message (never the error dialog), and Compare's CSV holds the
quantity its figure shows."""
import os
import sys
from pathlib import Path

import numpy as np
import pytest

from aurora.core import testdata
from aurora.core.io.config import ScanSettings, Zone
from aurora.core.model import Project


def _tree(root: Path) -> dict:
    """{relative path: bytes} of every file under root."""
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ----------------------------------------------------------------------------- the emission cache (no pickle)
def test_the_emission_cache_is_read_without_pickle(fake_project, tmp_path):
    from aurora.core import xrf_windows as xw
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    marker = tmp_path / "PICKLE_RAN.txt"

    class Payload:
        def __reduce__(self):
            return (open, (str(marker), "w"))           # harmless: an empty marker file when the pickle runs

    cdir = xw.cache_dir_of(scan, fake_project)
    cdir.mkdir(parents=True, exist_ok=True)
    np.savez(cdir / xw.EMISSION_FILE, signature=np.array([Payload()], dtype=object))
    xw.clear_memory()
    em = xw.read_emission(scan, root=fake_project)
    assert not marker.exists()                          # the crafted member was never unpickled
    assert em.per_pix.shape[1:] == (4, 256) and em.per_pix.sum() > 0       # read again from raw
    with np.load(cdir / xw.EMISSION_FILE, allow_pickle=False) as z:          # and the cache rebuilt without pickle
        assert z["per_pix"].shape == em.per_pix.shape
    xw.clear_memory()


# ----------------------------------------------------------------------------- aurora demo <folder>
def _beamtime(tmp_path: Path) -> Path:
    """A mock beamtime whose raw scan has the demo's own folder name (the demo's names copy the real beamtime's)."""
    b = tmp_path / "beamtime"
    scan = b / "raw" / testdata.DAY / testdata.SURVEY
    scan.mkdir(parents=True)
    (scan / "sdd1_0.bin").write_bytes(b"REAL RAW BYTES")
    (b / "config").mkdir()
    (b / "config" / "roi_bins.json").write_text('{"_note": "ORIGINAL BEAMTIME BINS"}', encoding="utf-8")
    return b


def test_demo_refuses_an_existing_beamtime(tmp_path, capsys):
    from aurora.cli import main
    b = _beamtime(tmp_path)
    before = _tree(b)
    assert main(["demo", str(b)]) == 1
    assert _tree(b) == before                           # not one file added or changed
    assert "new or empty folder" in capsys.readouterr().err
    # the scan writers refuse a beamline raw/ folder on their own too
    with pytest.raises(PermissionError):
        testdata.make_fake_project(b)
    assert _tree(b) == before


def test_demo_refuses_a_protected_folder_and_writes_into_a_new_or_empty_one(tmp_path, monkeypatch, capsys):
    from aurora.cli import main
    shared = tmp_path / "share"
    shared.mkdir()
    monkeypatch.setenv("AURORA_PROTECT_DIRS", os.pathsep.join([os.environ.get("AURORA_PROTECT_DIRS", ""), str(shared)]))
    assert main(["demo", str(shared)]) == 1 and not any(shared.iterdir())
    assert main(["demo", str(shared / "demo")]) == 1 and not (shared / "demo").exists()
    assert "protected" in capsys.readouterr().err
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["demo", str(empty)]) == 0
    assert sorted(s.status for s in Project.discover(empty).scans) == ["aborted", "complete", "survey", "test"]
    assert main(["demo", str(empty)]) == 1               # a second run on the same folder refuses as well


# ----------------------------------------------------------------------------- John-format saves
@pytest.fixture
def john(fake_project):
    from aurora.core.stack import StackData
    proj = Project.discover(fake_project)
    scan = proj.find(testdata.STACK)
    sd = StackData.load(proj, scan)
    zone = Zone(id=1, label="S1", group="Minerals", rects=[[-0.5, -0.25, 5.0, 5.25]])
    st = ScanSettings(stack=scan.name, line=sd.main)
    return proj, scan, sd, zone, st, f"{scan.day}_{scan.name}"


def test_an_overwrite_removes_the_other_roi_file(john):
    from aurora.core.export import write_john
    proj, scan, sd, zone, st, sid = john
    first = write_john(zone, sid, sd, st, project=proj, scan=scan)
    folder = first[0].parent
    assert (folder / "S1_polygon_roi.json").is_file()
    zone.rects.append([0.0, 0.25, 5.0, 5.25])           # two rectangles: no longer one polygon, so _zone.json
    with pytest.raises(FileExistsError):
        write_john(zone, sid, sd, st, project=proj, scan=scan)
    second = write_john(zone, sid, sd, st, project=proj, scan=scan, overwrite=True)
    assert [p.name for p in second][2] == "S1_zone.json"
    assert sorted(p.name for p in folder.iterdir()) == sorted(p.name for p in second)   # one zone described, not two
    zone.rects.pop()                                    # and back: the polygon replaces the _zone.json
    third = write_john(zone, sid, sd, st, project=proj, scan=scan, overwrite=True)
    assert sorted(p.name for p in folder.iterdir()) == sorted(p.name for p in third)


def _replace_refused_for(monkeypatch, name: str) -> None:
    """os.replace refuses to move anything to or from a file called `name`, as Windows does while another program
    (Excel) holds it."""
    real = os.replace

    def replace(src, dst, *a, **k):
        if Path(src).name == name or Path(dst).name == name:
            raise PermissionError(13, "Access is denied", str(src), None, str(dst))
        return real(src, dst, *a, **k)
    monkeypatch.setattr(os, "replace", replace)


def test_a_held_file_leaves_every_file_of_the_sample_as_it_was(john, monkeypatch):
    from aurora.core import export
    from aurora.core.io import textio
    monkeypatch.setattr(textio, "REPLACE_WAIT_S", 0.001)
    proj, scan, sd, zone, st, sid = john
    first = export.write_john(zone, sid, sd, st, project=proj, scan=scan)
    folder = first[0].parent
    before = _tree(folder)
    st.sdd = [True, False, True, False]                 # other settings: every file of the second save differs
    _replace_refused_for(monkeypatch, "S1_absorption.csv")
    with pytest.raises(PermissionError) as info:
        export.write_john(zone, sid, sd, st, project=proj, scan=scan, overwrite=True)
    assert Path(info.value.filename).name == "S1_absorption.csv"          # the file that refused, not a temporary
    assert _tree(folder) == before                      # all four old files, nothing new, no temporary left
    monkeypatch.undo()
    export.write_john(zone, sid, sd, st, project=proj, scan=scan, overwrite=True)
    after = _tree(folder)
    assert set(after) == set(before)
    assert all(after[k] != before[k] for k in ("S1_full_reduced.csv", "S1_absorption.csv", "S1_gui_settings.json"))


@pytest.mark.skipif(sys.platform != "win32", reason="a file held without sharing exists on Windows only")
def test_a_file_held_by_another_program_on_windows(john):
    import ctypes
    from ctypes import wintypes
    from aurora.core import export
    proj, scan, sd, zone, st, sid = john
    first = export.write_john(zone, sid, sd, st, project=proj, scan=scan)
    folder = first[0].parent
    before = _tree(folder)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                wintypes.DWORD, wintypes.HANDLE]
    # GENERIC_READ, FILE_SHARE_READ only (as Excel holds a workbook), OPEN_EXISTING
    h = k32.CreateFileW(str(folder / "S1_gui_settings.json"), 0x80000000, 0x1, None, 3, 0x80, None)
    assert h not in (None, wintypes.HANDLE(-1).value)
    try:
        st.sdd = [True, False, True, False]
        with pytest.raises(PermissionError):
            export.write_john(zone, sid, sd, st, project=proj, scan=scan, overwrite=True)
    finally:
        k32.CloseHandle(h)
    assert _tree(folder) == before


# ----------------------------------------------------------------------------- the window: exports, Save ticked, report
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def demo(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    yield win
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


@pytest.fixture
def warnings_shown(monkeypatch):
    """The text of every QMessageBox.warning (none opens)."""
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a, **k: shown.append(text)))
    return shown


def _protect(monkeypatch, *folders) -> None:
    monkeypatch.setenv("AURORA_PROTECT_DIRS", os.pathsep.join([os.environ.get("AURORA_PROTECT_DIRS", ""),
                                                               *map(str, folders)]))


def test_exports_are_refused_before_anything_is_written(qapp, demo, tmp_path, monkeypatch, warnings_shown):
    from aurora.app.widgets import guarded_write
    win = demo
    raw_day = win.session.project.root / "raw" / testdata.DAY
    share = tmp_path / "protected share"
    share.mkdir()
    _protect(monkeypatch, share)
    exports = [("lcf.csv", win.fit.export_lcf), ("peaks.csv", win.fit.export_peaks),
               ("mcr_out", lambda p: win.fit.export_mcr(p, win.fit.mcr_result, "MCR")),
               ("comparison.csv", win.compare.export)]
    for folder in (raw_day, share):
        before = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))
        for name, fn in exports:
            warnings_shown.clear()
            with guarded_write(win, name) as g:
                fn(folder / name)
            after = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*"))
            assert after == before, (name, sorted(set(after) - set(before)))     # not one file or folder made
            assert not g.ok and len(warnings_shown) == 1, (name, warnings_shown)
            assert "never writes here" in warnings_shown[0] and "open in another program" not in warnings_shown[0]


def test_the_compare_csv_holds_what_its_figure_shows(qapp, demo, tmp_path):
    from demo_window import pump
    c = demo.compare
    c.deriv.setChecked(True)
    c.deriv_smooth.setValue(1)                          # no running mean: the plain first derivative
    pump(qapp)
    assert c._ylabel(units=True).startswith("d/dE")
    traces = c.plot_traces()
    out = c.export(tmp_path / "cmp.csv")
    assert out[0].name == "cmp.csv"
    lines = (tmp_path / "cmp.csv").read_text(encoding="utf-8").splitlines()
    head = lines[0].split(",")
    t = traces[0]
    assert head[:2] == [f"{t.r.label}-energy (eV)", f"{t.r.label}-d/dE {t.r.signal}"]
    cells = [ln.split(",")[:2] for ln in lines[1:len(t.E) + 1]]
    E = np.array([float(a) for a, _b in cells])
    col = np.array([float(b) if b else np.nan for _a, b in cells])
    # the derivative by hand (central differences on the even demo grid) of the normalised values, not AURORA's code
    y = np.asarray(t.nr.y, float)
    hand = (y[2:] - y[:-2]) / (t.E[2:] - t.E[:-2])
    ok = np.isfinite(hand) & np.isfinite(col[1:-1])
    assert ok.sum() > 100 and np.allclose(E, t.E, rtol=1e-6)
    assert np.allclose(col[1:-1][ok], hand[ok], rtol=1e-4, atol=1e-6 * np.nanmax(np.abs(hand)))
    assert np.allclose(col[1:-1][ok], (t.y - t.off)[1:-1][ok], rtol=1e-4, atol=1e-9)   # the plotted curve, offset off
    c.deriv.setChecked(False)
    pump(qapp)
    c.export(tmp_path / "plain.csv")
    plain = (tmp_path / "plain.csv").read_text(encoding="utf-8").splitlines()
    assert plain[0].split(",")[1] == f"{t.r.label}-{t.r.signal}"
    assert np.isclose(float(plain[1].split(",")[1]), float(np.asarray(t.nr.y)[0]), rtol=1e-5)


def _queue_ready(qapp, win, n: int) -> list:
    import time
    from demo_window import pump
    ck = win.check
    t0 = time.monotonic()
    while time.monotonic() - t0 < 60:
        win.jobs.wait_idle(200)
        pump(qapp)
        rows = ck.queue_rows()
        if len(rows) == n and all(r["state"] == "ready" for r in rows):
            return rows
    raise AssertionError(ck.queue_rows())


def _queue_zone(qapp, win, z) -> None:
    from demo_window import pump
    win.show_perspective("reduce")
    win.session.select_zone(z.id)
    pump(qapp)
    assert win.reduce.add_to_check() == 1


def test_labels_that_differ_only_in_case_keep_their_own_files(qapp, demo):
    from demo_window import pump
    win = demo
    ses, ck = win.session, win.check
    z1 = ses.holder_zones().zones[0]
    assert z1.label == "1 alpha"
    x0, x1, y0, y1 = ses.stack(ses.stack_id(ses.project.find(testdata.STACK))).bbox
    z2 = ses.new_zone("1 ALPHA")
    z2.rects.append([x0, (x0 + x1) / 2, y0, y1])
    ses.touch()
    for z in (z1, z2):
        _queue_zone(qapp, win, z)
    win.show_perspective("check")
    _queue_ready(qapp, win, 2)
    asked = []
    ck.ask_overwrite = lambda paths: asked.append(paths) or "yes"
    ck.save_btn.click()
    assert win.jobs.wait_idle(60000)
    pump(qapp)
    red = ses.project.root / "processed" / "reduced"
    csvs = [p for p in red.rglob("*_full_reduced.csv") if "alpha" in p.name.casefold()]
    # two files whose paths differ beyond letter case (one folder and one file on Windows and macOS otherwise)
    assert len({str(p).casefold() for p in csvs}) == 2, csvs
    zones = sorted(next(h for h in p.read_text(encoding="utf-8").splitlines() if h.startswith("# zone: "))[8:15]
                   for p in csvs)
    assert zones == ["1 ALPHA", "1 alpha"] and not asked
    assert "2 spectrum(s) saved" in ck.queue_note.text()


def test_a_held_file_on_save_ticked_is_one_calm_message(qapp, demo, monkeypatch, warnings_shown, error_dialogs):
    from demo_window import pump
    from aurora.core.io import textio
    win = demo
    ck = win.check
    z = win.session.holder_zones().zones[0]
    _queue_zone(qapp, win, z)
    win.show_perspective("check")
    _queue_ready(qapp, win, 1)
    ck.ask_overwrite = lambda paths: "yes"
    ck.save_btn.click()
    assert win.jobs.wait_idle(60000)
    pump(qapp)
    folder = win.session.project.root / "processed" / "reduced" / z.label
    before = _tree(folder)
    assert len(before) == 4 and not warnings_shown
    with monkeypatch.context() as m:
        m.setattr(textio, "REPLACE_WAIT_S", 0.001)
        _replace_refused_for(m, f"{z.label}_full_reduced.csv")           # as Excel holds it
        ck.save_btn.click()
        assert win.jobs.wait_idle(60000)
        pump(qapp)
    assert error_dialogs == []                          # not the error dialog of an unexpected failure
    assert len(warnings_shown) == 1, warnings_shown
    text = warnings_shown[0]
    assert f"{z.label}_full_reduced.csv" in text and "open in another program" in text and "Save ticked" in text
    assert ".tmp" not in text and ".old" not in text and "PermissionError" not in text and "WinError" not in text
    note = ck.queue_note.text()
    assert "not saved" in note and ".tmp" not in note and "Error" not in note
    assert _tree(folder) == before
    # a protected (or read-only) project: the same calm message, no folder or file made
    warnings_shown.clear()
    _protect(monkeypatch, win.session.project.root / "processed")
    for f in folder.iterdir():
        f.unlink()
    folder.rmdir()
    ck.save_btn.click()
    assert win.jobs.wait_idle(60000)
    pump(qapp)
    assert error_dialogs == [] and len(warnings_shown) == 1 and "never writes here" in warnings_shown[0]
    assert not folder.exists()


def test_a_report_folder_that_cannot_be_written_is_one_calm_message(qapp, demo, tmp_path, monkeypatch, warnings_shown,
                                                                   error_dialogs):
    import pathlib
    from demo_window import pump
    win = demo
    rp = win.report
    win.show_perspective("report")
    pump(qapp)
    monkeypatch.setattr(rp, "resolve_author", lambda ask=True: "")
    share = tmp_path / "protected reports"
    share.mkdir()
    _protect(monkeypatch, share)
    rp.out_dir.setText(str(share / "aurora"))
    rp.build()
    assert win.jobs.wait_idle(60000)
    pump(qapp)
    assert error_dialogs == [] and len(warnings_shown) == 1, (error_dialogs, warnings_shown)
    assert "cannot be written" in warnings_shown[0] and "Browse" in warnings_shown[0]
    assert "ProtectedPathError" not in rp.status.text() and not any(share.iterdir())
    # a real refusal of the system (a read-only share): the staging folder cannot be made
    real_mkdir = pathlib.Path.mkdir

    def refuse(self, *a, **k):
        if self.name.startswith(".building-"):
            raise PermissionError(13, "Permission denied", str(self))
        return real_mkdir(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "mkdir", refuse)
    warnings_shown.clear()
    rp.out_dir.setText(str(tmp_path / "ro" / "aurora"))
    rp.build()
    assert win.jobs.wait_idle(60000)
    pump(qapp)
    assert error_dialogs == [] and len(warnings_shown) == 1, (error_dialogs, warnings_shown)
    assert str(tmp_path / "ro" / "aurora") in warnings_shown[0] and ".building-" not in warnings_shown[0]
    assert "Permission denied" in rp.status.text()
