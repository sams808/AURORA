"""Opening a project, the command line and the CI checks (0.4.0 review): a file that is no project says what it is and
offers Choose another only; the day folders without raw/ open as a beamtime; a day, scan or .h5 path says it opened
one day, and Show every day opens the rest; a failed open is said once (no raw exception in the progress strip); the
last project at start fails in one sentence; Open project… promises folders only; a folder macOS sends (Finder's Open
With, the Dock icon) opens. Command line: a user error is one line, a non-scan path is an error, the help says what
AURORA is. Release notes print in UTF-8; CI checks stop their step, the portable zip is tested, the suite has time; the
self-test removes its temporary folder."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aurora import cli
from aurora.core import overview, testdata
from aurora.core.model import NotAProjectError, Project

ROOT = Path(__file__).resolve().parents[1]
DAY2 = "2025-03-15"


def two_days(root: Path) -> Path:
    """The synthetic beamtime with its day copied to a second day: raw/2025-03-14 and raw/2025-03-15."""
    testdata.make_fake_project(root)
    shutil.copytree(root / "raw" / testdata.DAY, root / "raw" / DAY2)
    return root


# ----------------------------------------------------------------------------- Project.resolve and discover
def test_a_file_that_is_no_project_says_what_it_is(tmp_path):
    bt = testdata.make_fake_project(tmp_path / "beamtime")
    day = bt / "raw" / testdata.DAY
    in_survey = next((day / testdata.SURVEY).glob("sdd1_0.bin"))
    in_energy = next((day / testdata.STACK).glob("*/sdd1_0.bin"))            # a stack's energy folder
    zipped = tmp_path / "beamtime.zip"
    zipped.write_bytes(b"PK\x05\x06" + bytes(18))
    notes = tmp_path / "notes.txt"
    notes.write_text("x", encoding="utf-8")
    cases = [(in_survey, f"sdd1_0.bin is one file of the scan {testdata.SURVEY}"),
             (in_energy, f"sdd1_0.bin is one file of the scan {testdata.STACK}"),
             (zipped, "beamtime.zip is an archive, which AURORA does not read: extract it first"),
             (notes, "notes.txt is a file, not a folder: open the beamtime folder")]
    for path, words in cases:
        with pytest.raises(NotAProjectError) as e:
            Project.resolve(path)
        assert str(e.value).startswith(words) and str(tmp_path) not in str(e.value), (path, str(e.value))
    assert issubclass(NotAProjectError, FileNotFoundError)               # callers that catch FileNotFoundError still do
    assert Project.discover(next((day / testdata.STACK).glob("*.h5"))).root == bt.resolve()      # an .h5 still opens


def test_day_folders_without_raw_are_a_beamtime(tmp_path):
    src = two_days(tmp_path / "src")
    mine = tmp_path / "MyBeamtime"
    for d in (testdata.DAY, DAY2):
        shutil.copytree(src / "raw" / d, mine / d)
    proj = Project.discover(mine)
    assert proj.root == proj.raw == mine.resolve() and not proj.loose and not proj.days_left_out
    assert [d.date for d in proj.days] == [testdata.DAY, DAY2] and len(proj.scans) == 8
    assert dict(overview.project_rows(proj))["layout"] == "<day>/<scan>, the day folders without raw/"
    one = Project.discover(mine / DAY2)                                   # one of its days: that day alone
    assert one.root == mine.resolve() and [d.date for d in one.days] == [DAY2] and one.days_left_out == [testdata.DAY]
    (tmp_path / "Photos" / testdata.DAY / "holiday").mkdir(parents=True)   # dated folders without scans: no beamtime
    with pytest.raises(FileNotFoundError, match="not a project folder"):
        Project.resolve(tmp_path / "Photos")


def test_a_day_scan_or_h5_path_names_the_days_left_out(tmp_path):
    bt = two_days(tmp_path / "bt")
    assert Project.discover(bt).days_left_out == [] and Project.discover(bt / "raw").days_left_out == []
    day = bt / "raw" / testdata.DAY
    for path in (day, day / testdata.STACK, next((day / testdata.STACK).glob("*.h5"))):
        p = Project.discover(path)
        assert [d.date for d in p.days] == [testdata.DAY] and p.days_left_out == [DAY2], path
    assert dict(overview.project_rows(p))["days"] == f"1: {testdata.DAY} (opened alone; left out: {DAY2})"
    single = testdata.make_fake_project(tmp_path / "single")
    assert Project.discover(single / "raw" / testdata.DAY).days_left_out == []


# ----------------------------------------------------------------------------- Home
@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


def _window(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.resize(1300, 820)
    win.show()
    pump(qapp)
    return win


def _open(qapp, win, path, quiet=False):
    win.home.open_path(path, quiet=quiet)
    win.jobs.wait_idle(30000)
    pump(qapp, 10)


def _close(win):
    win.jobs.wait_idle(10000)
    if getattr(win.session, "_save_timer", None) is not None:
        win.session._save_timer.stop()
    win.close()


def test_a_file_gets_its_own_sentence_and_choose_another_only(qapp, tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("x", encoding="utf-8")
    win = _window(qapp)
    home, fs = win.home, win.home.failure_state
    _open(qapp, win, notes)
    assert fs.isVisible() and [b.text() for b in fs.buttons] == ["Choose another…"]
    assert fs.buttons[0].objectName() == "primary"                       # the one action that can help
    text = fs.text().replace("​", "")
    assert "notes.txt is a file, not a folder" in text and "does not exist" not in text
    assert not home.taskbar.isVisible()                                  # said once: no 'failed: NotADirectoryError …'
    assert "Retry" not in home.details.toPlainText()
    _open(qapp, win, tmp_path / "gone")                                  # a missing folder: Retry stays first
    assert [b.text() for b in fs.buttons] == ["Retry", "Choose another…"] and not home.taskbar.isVisible()
    assert "The folder does not exist" in fs.text()
    _close(win)


def test_one_day_opened_says_so_and_show_every_day_opens_them_all(qapp, tmp_path):
    bt = two_days(tmp_path / "bt")
    win = _window(qapp)
    home = win.home
    btn = home.all_days_btn
    _open(qapp, win, bt / "raw" / testdata.DAY / testdata.STACK)
    assert [d.date for d in home.project.days] == [testdata.DAY]
    assert f"{testdata.DAY} only (1 of 2 days), 1 holder, 4 scans" in home.path_label.full_text()
    assert btn.isVisible() and btn.isEnabled() and "opened alone" in home.details.toPlainText()
    home.reload_btn.click()                                              # Reload keeps the day opened alone
    win.jobs.wait_idle(30000)
    pump(qapp, 10)
    assert [d.date for d in home.project.days] == [testdata.DAY] and btn.isVisible()
    btn.click()
    win.jobs.wait_idle(30000)
    pump(qapp, 10)
    assert [d.date for d in home.project.days] == [testdata.DAY, DAY2] and not btn.isVisible()
    assert "2 days, 2 holders, 8 scans" in home.path_label.full_text()
    _close(win)


def test_the_last_project_at_start_fails_in_one_sentence(qapp, tmp_path):
    win = _window(qapp)
    _open(qapp, win, tmp_path / "p11_B", quiet=True)
    line = win.home.path_label.full_text()
    assert line == ("the last project, p11_B, could not be opened: the folder does not exist, or the share that holds "
                    "it is not reachable now. Pick another one below."), line
    assert "Error" not in line and not win.home.taskbar.isVisible() and win.home.empty_state.isVisible()
    _close(win)


def test_open_project_promises_folders_only(qapp):
    """The folder dialog lists no files: no text says Open project… opens an .h5; the drop and the command line do."""
    from aurora.app import home
    from aurora.app.help_text import HELP
    win = _window(qapp)
    texts = [home.WELCOME_TEXT, HELP["home.open"][0], win.home.open_btn.toolTip()]
    texts += [a.statusTip() for a in win.menus["file"].actions() if a.text().replace("&", "") == "Open project…"]
    assert len(texts) == 4 and not [t for t in texts if "or one .h5" in t], texts
    assert "dropped on the window" in HELP["home.open"][0]
    assert "dropped on the window or given on the command line" in win.home._welcome_html()
    _close(win)


def test_a_folder_macos_sends_opens_like_the_command_line(qapp, tmp_path, monkeypatch):
    """Finder's Open With and a drop on the Dock icon arrive as a QFileOpenEvent on the application (not in argv)."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QFileOpenEvent
    from aurora.app import main as app_main
    from aurora.app.settings import remember_project, settings
    assert isinstance(qapp, app_main.AuroraApplication)
    bt = testdata.make_fake_project(tmp_path / "sent beamtime")
    win = _window(qapp)
    app_main.QApplication.sendEvent(qapp, QFileOpenEvent(QUrl.fromLocalFile(str(bt))))
    win.jobs.wait_idle(30000)
    pump(qapp, 10)
    assert win.home.project is not None and win.home.project.root == bt.resolve()
    _close(win)
    pump(qapp)
    # sent before the window is up: it waits, and wins over the last project at start
    other = testdata.make_fake_project(tmp_path / "last one")
    remember_project(other)
    s = settings()
    s.setValue("open_last_project", True)
    s.sync()
    app_main.QApplication.sendEvent(qapp, QFileOpenEvent(QUrl.fromLocalFile(str(bt))))
    assert qapp.pending == [bt]
    win = _window(qapp)
    app_main.open_at_start(win, None)
    win.jobs.wait_idle(30000)
    pump(qapp, 10)
    assert qapp.pending == [] and win.home.project is not None and win.home.project.root == bt.resolve()
    # sent while the last project is being read: it opens once that read ends
    seen = []
    real = win.home.open_path
    monkeypatch.setattr(win.home, "open_path", lambda p, quiet=False: (seen.append(Path(p)), real(p, quiet=quiet)))
    real(other, quiet=True)
    qapp.open_sent(bt)
    assert seen == []
    win.jobs.wait_idle(30000)
    pump(qapp, 10)
    win.jobs.wait_idle(30000)
    pump(qapp, 10)
    assert seen == [bt] and win.home.project.root == bt.resolve()
    _close(win)


# ----------------------------------------------------------------------------- command line
@pytest.fixture
def logs(tmp_path, monkeypatch):
    import logging
    monkeypatch.setenv("AURORA_LOG_DIR", str(tmp_path / "logs"))
    yield tmp_path / "logs"
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_aurora", False) or getattr(h, "_aurora_console", False):
            root.removeHandler(h)
            h.close()


def test_user_errors_are_one_line_and_a_non_scan_path_is_one(logs, tmp_path, capsys):
    demo = tmp_path / "demo1"
    assert cli.main(["demo", str(demo)]) == 0
    capsys.readouterr()
    (tmp_path / "empty").mkdir()
    cases = [(["scan", str(tmp_path / "empty")], "aurora scan: empty is not a scan folder"),
             (["scan", str(demo / "raw" / testdata.DAY)], f"aurora scan: {testdata.DAY} is not a scan folder"),
             (["scan", str(tmp_path / "nope")], "aurora scan: "),
             (["project", str(tmp_path / "empty")], "aurora project: "),
             (["report", str(demo), "--out", str(tmp_path / "rep")], "aurora report: no zones yet in demo1 (.aurora/"
                                                                     "session.json is missing): draw zones in AURORA"),
             (["report", str(demo), "--zones", str(tmp_path / "z.json")], "aurora report: the zones file "),
             (["report", str(tmp_path / "no such beamtime")], "aurora report: ")]
    for argv, start in cases:
        assert cli.main(argv) == 1, argv
        err = capsys.readouterr().err
        assert err.startswith(start) and "Traceback" not in err and len(err.strip().splitlines()) == 2, (argv, err)
        assert "details in" in err and str(logs) in err
    log = (logs / "aurora.log").read_text(encoding="utf-8")
    assert log.count("Traceback (most recent call last)") >= len(cases)          # the traceback is in the log
    # a scan's .h5 describes its scan folder, as `aurora <file>.h5` opens it
    h5 = next((demo / "raw" / testdata.DAY / testdata.STACK).glob("*.h5"))
    assert cli.main(["scan", str(h5)]) == 0 and capsys.readouterr().out.startswith(f"{testdata.STACK}: kind stack")


def test_an_unexpected_failure_keeps_its_traceback(logs, monkeypatch, capsys):
    def broken(_path):
        raise RuntimeError("a bug, not a user error")
    monkeypatch.setattr(cli, "cmd_project", broken)
    assert cli.main(["project", "x"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("aurora project failed: RuntimeError: a bug") and "Traceback (most recent call last)" in err


def test_help_says_what_aurora_is(capsys):
    text = cli.build_parser().format_help()
    assert "CLS SGM XAS and XRF map analysis" in text and "John Bussey" in text
    for example in ("aurora <beamtime folder>", "aurora demo <folder>", "aurora report <beamtime>"):
        assert example in text, example
    with pytest.raises(SystemExit):
        cli.main(["report", "--help"])
    out = re.sub(r"\s+", " ", capsys.readouterr().out)
    assert "a zones file (zones_all.json of the browser zone tool, or Survey > Export zones…; default: the session" in out


# ----------------------------------------------------------------------------- release notes, CI, self-test
def test_release_notes_print_utf8_on_any_console(tmp_path):
    """`release_notes.py <version>` on a cp1252 console (Windows): the notes hold '▸' and 'α'."""
    import aurora
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8",)}
    env["PYTHONIOENCODING"] = "cp1252"                                   # the Windows console, on any platform
    p = subprocess.run([sys.executable, "-B", str(ROOT / "packaging" / "release_notes.py"), aurora.__version__],
                       capture_output=True, env=env, cwd=str(tmp_path), timeout=120)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")[-600:]
    out = p.stdout.decode("utf-8")
    assert out.startswith(f"## {aurora.__version__}") and "### Downloads" in out


def _workflow(name: str) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def and_lists_that_stop_nothing(wf: dict) -> list[str]:
    """Lines of the bash steps that chain commands with && before the step's last command and without a || fallback:
    under bash -e only the last command of such a list can fail the step."""
    bad = []
    for name, job in wf["jobs"].items():
        for step in job.get("steps", []):
            run, shell = step.get("run", ""), step.get("shell", "bash")
            if not run or shell in ("pwsh", "powershell", "cmd"):
                continue
            lines = [ln.strip() for ln in run.splitlines() if ln.strip() and not ln.strip().startswith("#")]
            for ln in lines[:-1]:
                code = re.sub(r"'[^']*'|\"[^\"]*\"", "''", ln)             # && inside quotes is no list
                if "&&" in code and "||" not in code.split("&&")[-1]:
                    bad.append(f"{name} / {step.get('name')}: {ln}")
    return bad


def test_ci_checks_are_commands_of_their_own():
    wf = _workflow("ci.yml")
    jobs = wf["jobs"]
    assert not and_lists_that_stop_nothing(wf), and_lists_that_stop_nothing(wf)
    mount = next(s["run"] for s in jobs["test-macos"]["steps"] if s.get("name", "").startswith("Mount read-only"))
    for f in ("If macOS blocks AURORA.txt", "LICENSE", "CREDITS.md", "THIRD_PARTY_LICENSES.txt"):
        assert f in mount, f
    probe = {"jobs": {"j": {"steps": [{"run": 'test -f "$M/LICENSE" && test -f "$M/CREDITS.md"\necho go'}]}}}
    assert and_lists_that_stop_nothing(probe)                            # the check finds the 0.4.0 line


def test_the_portable_zip_is_unpacked_and_self_tested():
    steps = _workflow("ci.yml")["jobs"]["test-windows"]["steps"]
    step = next(s for s in steps if "zip" in s.get("name", "").lower())
    run = step["run"]
    assert "Expand-Archive" in run and "windows-x64.zip" in run and step.get("timeout-minutes", 99) <= 5
    assert "aurora-cli.exe\" --selftest" in run and "--selftest --platform native" in run
    assert "$LASTEXITCODE -ne 0" in run and "$p.ExitCode -ne 0" in run
    install = (ROOT / "aurora" / "resources" / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    assert "unzip the new version beside it or\nover it" not in install and "into a new folder" in install


def test_the_selftest_removes_its_temporary_folder(tmp_path, monkeypatch):
    """The hypercube check leaves memory-mapped .npy files open; on Windows they could not be deleted, and
    rmtree(ignore_errors=True) hid it: a folder per run stayed in %TEMP%."""
    import tempfile
    from aurora import selftest
    from aurora.core import xrf_windows
    for var in ("AURORA_SETTINGS_DIR", "AURORA_LOG_DIR", "QT_QPA_PLATFORM", "QT_QPA_FONTDIR"):
        if var in os.environ:
            monkeypatch.setenv(var, os.environ[var])
        else:
            monkeypatch.delenv(var, raising=False)
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    keep = {"demo beamtime and discovery", "hypercube streamed to disk, cancelled cleanly, k-means"}
    real_core = selftest._core_checks

    def only(r, ctx, base):
        check = r.check
        r.check = lambda name, fn: check(name, fn) if name in keep else None
        real_core(r, ctx, base)
    monkeypatch.setattr(selftest, "_core_checks", only)
    try:
        rc = selftest.run(out=tmp_path / "s.json", gui=False)
    finally:
        xrf_windows.clear_memory()
        xrf_windows.forget_projects()
    assert rc == 0
    assert list(temp.iterdir()) == [], [str(p) for p in temp.rglob("*") if p.is_file()]
