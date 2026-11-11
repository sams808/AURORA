"""A working folder for a beamtime AURORA may not write into (plain PL-18): Project.work_root holds the session, the
checks, the caches, the saved spectra and the reports; the choice is remembered per beamtime, the read-only banner
offers it, and nothing is ever written into the beamtime folder."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path

import pytest

pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


def _demo(path: Path) -> Path:
    from aurora.cli import cmd_demo
    with contextlib.redirect_stdout(io.StringIO()):
        assert cmd_demo(path) == 0
    return path


def _files(root: Path) -> list:
    return sorted((str(q), q.stat().st_mtime_ns, q.stat().st_size) for q in root.rglob("*") if q.is_file())


def _protect(monkeypatch, path: Path) -> None:
    monkeypatch.setenv("AURORA_PROTECT_DIRS", os.pathsep.join(filter(None, [os.environ.get("AURORA_PROTECT_DIRS", ""),
                                                                              str(path)])))


def test_the_work_root_of_a_project(tmp_path):
    from aurora.core import export, qc
    from aurora.core.flags import qc_path, qc_read_path
    from aurora.core.io.config import session_ui_value
    from aurora.core.model import Project, work_folder_for
    demo = _demo(tmp_path / "demo")
    proj = Project.discover(demo)
    assert proj.work is None and proj.work_root == proj.root and proj.aurora_dir == demo / ".aurora"
    assert proj.aurora_dirs() == [demo / ".aurora"] and export.default_folder(proj, "a b").parent == demo / "processed" / "reduced"
    work = tmp_path / "mine" / "demo"
    proj = Project.discover(demo, work_folders={str(demo): str(work)})
    assert proj.work == work and proj.work_root == work and proj.output_root == work
    assert proj.aurora_dirs() == [work / ".aurora", demo / ".aurora"]
    assert export.default_folder(proj, "s").parent == work / "processed" / "reduced"
    assert qc_path(proj) == work / ".aurora" / "qc.json"
    (demo / ".aurora").mkdir(exist_ok=True)
    (demo / ".aurora" / "qc.json").write_text("{}", encoding="utf-8")
    assert qc_read_path(proj) == demo / ".aurora" / "qc.json"        # read from the beamtime until the work has one
    qc.save_qc(proj, {})
    assert (work / ".aurora" / "qc.json").is_file() and qc_read_path(proj) == work / ".aurora" / "qc.json"
    (demo / ".aurora" / "session.json").write_text('{"ui": {"sample_sheet": "a.csv"}}', encoding="utf-8")
    assert session_ui_value(demo, "sample_sheet", work=work) == "a.csv"     # the beamtime's, none in the work yet
    (work / ".aurora" / "session.json").write_text('{"ui": {"sample_sheet": "b.csv"}}', encoding="utf-8")
    assert session_ui_value(demo, "sample_sheet", work=work) == "b.csv"
    assert work_folder_for(demo, {str(demo / "."): str(work)}) == work and work_folder_for(demo, {}) is None


def test_a_working_folder_keeps_the_whole_easy_job(qapp, tmp_path, monkeypatch, error_dialogs):
    """A protected beamtime: the banner offers Choose a working folder…; with one, zone → Check → Save ticked →
    Compare → Report complete, every file lands under the working folder, nothing changes in the beamtime, and
    opening the beamtime again restores the zones from the working folder."""
    from PySide6.QtWidgets import QMessageBox
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.app.settings import work_folders
    from aurora.core import testdata
    from aurora.core.model import Project
    from aurora.core.report import ReportBuilder
    demo = _demo(tmp_path / "ro")
    _protect(monkeypatch, demo)
    boxes = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: boxes.append(a) or QMessageBox.Ok)
    before = _files(demo)
    mode.set_advanced(False)
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    work = tmp_path / "AURORA work" / demo.name
    try:
        win.home.set_project(Project.discover(demo))
        pump(qapp)
        win.refresh_guide()
        assert win.banner.isVisible() and win.banner_work.isVisible() and win.session.read_only
        assert win.next_step.actions and win.next_step.actions[0][1] == "workfolder"
        ses = win.session
        sd = ses.stack(ses.stack_id(ses.project.find(testdata.STACK)))
        ses.select_stack(sd.id)
        with ses.edit("draw before the working folder"):
            z = ses.new_zone("1 alpha")
            x0, x1, y0, y1 = sd.bbox
            z.rects.append([x0, (x0 + x1) / 2, y0, y1])
        assert win.set_work_folder(demo / "sub") is False and boxes             # inside the beamtime: refused
        boxes.clear()
        assert win.set_work_folder(work), boxes
        pump(qapp)
        win.refresh_guide()
        assert not win.banner.isVisible() and not ses.read_only and not win.read_only_beamtime()
        assert ses.session_file == work / ".aurora" / "session.json" and ses.session_file.is_file()
        assert [z.label for z in ses.holder_zones().zones] == ["1 alpha"]          # the edit on screen was kept
        assert work_folders() and Path(next(iter(work_folders().values()))) == work
        assert "working folder" in win.home.path_label.text()
        sd = ses.stack(ses.stack_id(ses.project.find(testdata.STACK)))
        ses.select_stack(sd.id)
        ses.select_zone(ses.holder_zones().zones[0].id)
        win.show_perspective("reduce")
        win.reduce.scan_list.setCurrentRow(0)
        pump(qapp)
        assert win.reduce.add_to_check() == 1
        assert list((work / ".aurora" / "cache").rglob("_reduced_cache.npz"))       # the caches go to the work
        win.show_perspective("check")
        assert win.jobs.wait_idle(60000)
        pump(qapp)
        ck = win.check
        ck.ask_overwrite = lambda paths: "yes"
        ck.save_ticked()
        assert win.jobs.wait_idle(60000)
        pump(qapp)
        assert "added to Compare" in ck.queue_note.text(), ck.queue_note.text()
        assert list((work / "processed" / "reduced").rglob("*.csv"))
        win.library.flush()
        assert (work / ".aurora" / "library.json").is_file() and win.library.requests
        win.show_perspective("report")
        pump(qapp)
        out = Path(win.report.out_dir.text())
        assert out == work / "reports" / "aurora" and win.report.build_btn.isEnabled()
        res = ReportBuilder(ses.project, ses.zones, out, preset="draft", png_dpi=40, page_png_dpi=0).build()
        assert res.pdf is not None and Path(res.pdf).is_file() and work in Path(res.pdf).parents
        ses.flush()
        assert _files(demo) == before                                         # nothing written into the beamtime
        assert not error_dialogs and not boxes, (error_dialogs, boxes)
    finally:
        win.jobs.wait_idle(30000)
        win.session._save_timer.stop()
        win.session.release_lock()
        win.close()
    win2 = MainWindow()                                     # the beamtime again: the working folder is remembered
    try:
        win2.home.set_project(Project.discover(demo))
        pump(qapp)
        assert win2.session.project.work == work and not win2.session.read_only
        assert [z.label for z in win2.session.holder_zones().zones] == ["1 alpha"]
        assert not win2.read_only_beamtime()
    finally:
        win2.session._save_timer.stop()
        win2.session.release_lock()
        win2.close()
    assert _files(demo) == before


def test_a_session_saved_on_another_computer_says_so(qapp, tmp_path):
    """Plain PL-22: session.json carries a hash of the computer that saved it (never its name); opening a session
    saved on another computer says so in the status line, one saved here says nothing."""
    import json
    import platform
    from aurora.app.main import MainWindow
    from aurora.app.session import OTHER_COMPUTER_TEXT, host_id
    from aurora.core.model import Project
    demo = _demo(tmp_path / "demo")
    win = MainWindow()
    try:
        win.home.set_project(Project.discover(demo))
        ses = win.session
        ses.new_zone("1 alpha")
        ses.flush()
        d = json.loads(ses.session_file.read_text(encoding="utf-8"))
        assert d["host"] == host_id() and len(d["host"]) == 12
        assert platform.node().casefold() not in ses.session_file.read_text(encoding="utf-8").casefold()
        said: list[str] = []
        ses.status.connect(said.append)
        ses._open(ses.project)                                  # saved here: no note
        assert not any("another computer" in t for t in said)
        d["host"], d["exported"] = "0123456789ab", "2026-11-03T14:05:00"
        ses.release_lock()
        ses.session_file.write_text(json.dumps(d), encoding="utf-8")
        ses._open(ses.project)
        assert OTHER_COMPUTER_TEXT.format(when="on 2026-11-03 14:05") in said, said
        assert [z.label for z in ses.holder_zones().zones] == ["1 alpha"]
    finally:
        win.session._save_timer.stop()
        win.session.release_lock()
        win.close()
