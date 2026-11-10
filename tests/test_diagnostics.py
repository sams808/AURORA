"""Help > Report a problem sends people to the public GitHub issues: the dialog says so, the diagnostics write the home
folder as '~' (paths, the log tail), and the issues address is a link."""
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home folder of its own: Path.home() is tmp/Users/jdoe."""
    h = tmp_path / "Users" / "jdoe"
    h.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: cls(str(h))))
    return h


def test_the_home_folder_is_written_as_a_tilde(home):
    from aurora.app.diagnostics import tilde_home
    back, fwd = str(home), str(home).replace("\\", "/")
    text = f"project {back}\\beamtime\ncache {fwd}/beamtime/.aurora\nraw {back.upper()}\\x\n"
    out = tilde_home(text)
    assert back not in out and fwd not in out and "project ~" in out and "cache ~/beamtime" in out
    if Path(back.upper()) == Path(back):                 # Windows: the same folder in other letter case
        assert back.upper() not in out
    # a folder that only starts like the home folder is somebody else's
    assert tilde_home(f"{back}son\\x") == f"{back}son\\x"


def test_report_a_problem_warns_and_hides_the_home_folder(qapp, home, tmp_path, monkeypatch):
    from aurora import logsetup
    from aurora.app import diagnostics
    log = tmp_path / "aurora.log"
    log.write_text(f"INFO project {home / 'MyGroup_beamtime'} (raw {home / 'MyGroup_beamtime' / 'raw'})\n",
                   encoding="utf-8")
    monkeypatch.setattr(logsetup, "log_file", lambda: log)
    from PySide6.QtWidgets import QMainWindow
    win = QMainWindow()                                   # what the dialog reads of the main window: its session
    win.session = SimpleNamespace(project=SimpleNamespace(root=home / "MyGroup_beamtime"), zones=None)
    d = diagnostics.ReportProblemDialog(win)
    try:
        text = d.copy()
        assert str(home) not in text and "project ~" in text and "~" in d.preview.toPlainText()
        intro = d.intro.text()
        assert "public" in intro and "~" in intro and "before posting" in intro
        assert d.url.openExternalLinks() and f'href="{diagnostics.ISSUES_URL}"' in d.url.text()
    finally:
        d.deleteLater()
        win.deleteLater()
