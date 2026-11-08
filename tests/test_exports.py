"""Exports start in a sensible folder (the project's, else the home folder; never the install folder), say what they
wrote, and fail calmly."""
import builtins
import re
from pathlib import Path

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import QFileDialog, QMessageBox  # noqa: E402

from aurora.app import mode  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def captured(monkeypatch):
    """The directory argument of every file dialog, the dialog cancelled."""
    seen = []

    def grab(kind, ret):
        def f(*args, **kwargs):
            d = kwargs.get("dir") or kwargs.get("directory") or (args[2] if len(args) > 2 else "")
            seen.append((kind, str(d)))
            return ret
        return staticmethod(f)

    monkeypatch.setattr(QFileDialog, "getSaveFileName", grab("save", ("", "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", grab("open", ("", "")))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", grab("open", ([], "")))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", grab("dir", ""))
    return seen


def _all_exports(win):
    """Every action that opens a file dialog (export, import, add, save, load, choose a folder)."""
    c, f, m, s, r, h = win.compare, win.fit, win.maps, win.survey, win.report, win.home
    return [c._add_folder, c._add_csv, c._save_config, c._load_config, c._export, f._export_lcf, f._export_mcr,
            f._export_peaks, f._export_itfa, m._export_xanes, m._export_4d, s._import, s._export, r._choose_out,
            h.choose_project]


def _under(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def test_dialogs_start_in_the_project_or_the_home_folder(qapp, tmp_path, monkeypatch, captured):
    from demo_window import build_demo_window
    from aurora.app.main import MainWindow
    install = tmp_path / "Programs" / "AURORA" / "_internal"
    install.mkdir(parents=True)
    monkeypatch.chdir(install)                         # started from the installation folder
    mode.set_advanced(True)
    win = build_demo_window(qapp, tmp_path / "work")
    root = win.session.project.root
    for fn in _all_exports(win):
        fn()
    assert len(captured) >= 12
    outside = [(k, d) for k, d in captured if not _under(d, root) and not _under(d, root.parent)]
    assert not outside, outside
    assert not [d for _k, d in captured if _under(d, install)]
    win.session._save_timer.stop()
    win.close()
    # without a project: the home folder
    captured.clear()
    win = MainWindow()
    for fn in (win.compare._add_folder, win.compare._add_csv, win.compare._save_config, win.compare._load_config,
               win.survey._import, win.home.choose_project):
        fn()
    # Open… starts next to the most recent project (the demo opened above, under tmp_path: outside the home folder on
    # macOS and Linux, where the temporary folder is /tmp or /var/folders)
    assert captured and all((_under(d, Path.home()) or _under(d, tmp_path)) and not _under(d, install)
                            for _k, d in captured), captured
    mode.set_advanced(False)
    win.close()


def test_no_dialog_starts_at_a_bare_file_name():
    root = Path(__file__).resolve().parent.parent / "aurora"
    bad = []
    call = re.compile(r"QFileDialog\.get\w+\(\s*self\s*,\s*(\"[^\"]*\"|'[^']*')\s*,\s*(\"[^\"]*\"|'[^']*')")
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for m in call.finditer(text):
            bad.append(f"{p.name}: {m.group(0)[:80]}")          # a literal as the directory argument
    assert not bad, bad


def test_lcf_export_writes_csv_svg_png_and_says_so(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    fit = win.fit
    assert fit.lcf_result is not None and fit.lcf_export_btn.isEnabled()
    out = fit.export_lcf(tmp_path / "x.csv")
    assert [p.name for p in out] == ["x.csv", "x.svg", "x.png"]
    assert all((tmp_path / n).is_file() and (tmp_path / n).stat().st_size > 100 for n in ("x.csv", "x.svg", "x.png"))
    svg = (tmp_path / "x.svg").read_text(encoding="utf-8")
    assert " Z" in svg and not re.search(r"[Zz]-?\d", svg)        # closed paths survive the Qt 6.12 SVG fix (pgcompat)
    assert "x.csv" in fit.lcf_info.text() and "x.png" in fit.lcf_info.text() and "Open folder" in fit.lcf_info.text()
    assert "wrote x.csv, x.svg, x.png" in win.statusBar().currentMessage()
    win.session._save_timer.stop()
    win.close()


def test_export_buttons_wait_for_a_result(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    for b, reason in ((win.fit.lcf_export_btn, "run LCF first"), (win.fit.pk_export_btn, "run the decomposition first"),
                      (win.fit.mcr_export_btn, "run MCR-ALS first"), (win.maps.el_export, "open a project first")):
        assert not b.isEnabled() and reason in b.toolTip(), (b.text(), b.toolTip())
    win.close()


def test_a_write_error_is_one_calm_dialog(qapp, tmp_path, monkeypatch, excepthook_collector):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a, **k: shown.append(text)))
    target = tmp_path / "locked" / "lcf.csv"
    target.parent.mkdir()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(target), "CSV (*.csv)")))
    real_open = builtins.open

    def locked(path, *a, **k):
        if str(path).endswith("lcf.csv"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", locked)
    win.fit._export_lcf()
    monkeypatch.setattr(builtins, "open", real_open)
    assert len(shown) == 1 and "lcf.csv" in shown[0] and "Permission denied" in shown[0]
    assert "Is it open in another program?" in shown[0] and not excepthook_collector
    win.session._save_timer.stop()
    win.close()


def test_importing_broken_zones_changes_nothing(qapp, tmp_path, monkeypatch, fake_project):
    from aurora.app.main import MainWindow
    from aurora.core.model import Project
    win = MainWindow()
    win.home.set_project(Project.discover(fake_project))
    ses = win.session
    z = ses.new_zone("kept")
    before = [zz.label for zz in ses.zones.holders["N5"].zones]
    bad = tmp_path / "zones_all.json"
    bad.write_text("{ not json", encoding="utf-8")
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a, **k: shown.append(text)))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(bad), "JSON (*.json)")))
    win.survey._import()
    assert len(shown) == 1 and "zones_all.json" in shown[0] and "unchanged" in shown[0]
    assert [zz.label for zz in ses.zones.holders["N5"].zones] == before and z.label == "kept"
    ses._save_timer.stop()
    win.close()


def test_the_working_folder_never_stays_inside_the_bundle(tmp_path, monkeypatch):
    import sys
    from aurora.app.main import fix_working_dir
    bundle = tmp_path / "AURORA" / "_internal"
    (bundle / "aurora").mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.chdir(bundle / "aurora")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert fix_working_dir() == tmp_path and Path.cwd() == tmp_path
    app_dir = tmp_path / "AURORA.app" / "Contents" / "MacOS"
    app_dir.mkdir(parents=True)
    monkeypatch.chdir(app_dir)
    assert fix_working_dir() == tmp_path
    monkeypatch.chdir(tmp_path)
    assert fix_working_dir() is None                    # a normal folder is left alone
