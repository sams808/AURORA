"""I42: the report author is the person who builds it: Settings, else the OS account's full name, else a one-time
prompt in the Report perspective; blank gives no author line; never a default name."""
import json

import pytest

from aurora.core import report as core_report
from aurora.core import testdata
from aurora.core.report import AUTHOR, ReportBuilder, resolve_author

from wp7_support import close, project, pump, quick, report_window


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _pdf_meta(pdf) -> str:
    return pdf.read_bytes().decode("latin-1")


def _build(qapp, win):
    win.report.build()
    win.jobs.wait_idle(120000)
    pump(qapp)
    return win.report._result, win.report._builder


def test_core_default_and_resolution_order(monkeypatch):
    assert AUTHOR == ""
    monkeypatch.setattr(core_report, "os_full_name", lambda: "Test User")
    assert resolve_author("Jane Doe") == ("Jane Doe", "Settings")
    assert resolve_author("  ") == ("Test User", "the system account")
    assert resolve_author(None) == ("Test User", "the system account")
    monkeypatch.setattr(core_report, "os_full_name", lambda: "")
    assert resolve_author("") == ("", "")


def test_os_full_name_answers_calmly():
    name = core_report.os_full_name()
    assert isinstance(name, str) and name == name.strip()


def test_builder_author_goes_into_the_footer(tmp_path):
    proj = project(tmp_path)
    b = ReportBuilder(proj, testdata.make_fake_zones(proj), tmp_path / "r", author="A B", png_dpi=40, page_png_dpi=0)
    res = b.build()
    assert all(" · A B · " in f for f in b.footers) and "/Author (A B)" in _pdf_meta(res.pdf)
    assert json.loads(res.manifest.read_text(encoding="utf-8"))["author"] == "A B"
    b0 = ReportBuilder(proj, testdata.make_fake_zones(proj), tmp_path / "r0", png_dpi=40, page_png_dpi=0)
    res0 = b0.build()
    assert b0.author == "" and "/Author" not in _pdf_meta(res0.pdf) and all(" ·  · " not in f for f in b0.footers)


def test_the_os_account_name_from_the_perspective(qapp, tmp_path, monkeypatch):
    """Fresh AURORA_SETTINGS_DIR (conftest) and the OS name 'Test User': footer and /Author say Test User, and
    'Sami Soudani' is in neither (the cover credits name him as the author of the reporting pipeline, by design)."""
    from aurora.app.settings_dialog import get
    from PySide6.QtWidgets import QInputDialog
    asked = []
    # the first build asks once, the account's name filled in (a shared computer: persona S-10); OK keeps it
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: asked.append(k.get("text")) or
                                                              (k.get("text", ""), True)))
    monkeypatch.setattr(core_report, "os_full_name", lambda: "Test User")
    quick(monkeypatch)
    assert get("report_author") == ""
    proj = project(tmp_path)
    win = report_window(qapp, proj, testdata.make_fake_zones(proj), tmp_path)
    from aurora.app import mode
    assert win.report.author_line.text() == ("Author: Test User, from the system account" if mode.is_advanced() else
                                             "Author: asked at the first build (your computer account's name, Test "
                                             "User, is offered; blank prints none)")
    res, b = _build(qapp, win)
    assert asked == ["Test User"] and res is not None and get("report_author") == "Test User"
    _res2, _b2 = _build(qapp, win)
    assert asked == ["Test User"]                             # once
    assert b.footers and all(" · Test User · " in f for f in b.footers)
    raw = _pdf_meta(res.pdf)
    assert "/Author (Test User)" in raw
    assert not any("Sami Soudani" in f for f in b.footers) and "Sami Soudani" not in raw
    man = json.loads(res.manifest.read_text(encoding="utf-8"))
    assert man["author"] == "Test User"
    assert all("Sami Soudani" not in c.read_text(encoding="utf-8") for c in res.csvs)
    close(win)


def test_no_name_anywhere_and_the_prompt_cancelled(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from aurora.app.settings import settings
    asked = []
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: asked.append(a) or ("", False)))
    monkeypatch.setattr(core_report, "os_full_name", lambda: "")
    quick(monkeypatch)
    proj = project(tmp_path)
    win = report_window(qapp, proj, testdata.make_fake_zones(proj), tmp_path)
    assert win.report.author_line.text() == "No author line (Settings > report author)"
    res, b = _build(qapp, win)
    assert len(asked) == 1 and b.author == ""
    assert b.footers and all(" ·  · " not in f and f.count(" · ") == b.footers[0].count(" · ") for f in b.footers)
    assert "/Author" not in _pdf_meta(res.pdf)
    assert settings().value("report_author", "") in ("", None)
    _res2, b2 = _build(qapp, win)                         # one-time: no second prompt
    assert len(asked) == 1 and b2.author == ""
    close(win)


def test_a_name_typed_in_the_prompt_is_kept(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from aurora.app.settings_dialog import get
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Typed Name", True)))
    monkeypatch.setattr(core_report, "os_full_name", lambda: "")
    quick(monkeypatch)
    proj = project(tmp_path)
    win = report_window(qapp, proj, testdata.make_fake_zones(proj), tmp_path)
    res, b = _build(qapp, win)
    assert b.author == "Typed Name" and get("report_author") == "Typed Name"
    assert "/Author (Typed Name)" in _pdf_meta(res.pdf)
    from aurora.app import mode
    assert win.report.author_line.text() == ("Author: Typed Name, from Settings" if mode.is_advanced() else
                                             "Author: Typed Name (Settings > report author)")
    close(win)


def test_the_settings_value_wins(qapp, tmp_path, monkeypatch):
    from aurora.app.settings import settings
    s = settings()
    s.setValue("report_author", "Jane Doe")
    s.sync()
    monkeypatch.setattr(core_report, "os_full_name", lambda: "Test User")
    quick(monkeypatch)
    proj = project(tmp_path)
    win = report_window(qapp, proj, testdata.make_fake_zones(proj), tmp_path)
    _res, b = _build(qapp, win)
    assert b.author == "Jane Doe" and all(" · Jane Doe · " in f for f in b.footers)
    close(win)
