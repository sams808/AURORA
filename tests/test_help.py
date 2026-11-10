"""In-app manuals and help: the four documents open from the frozen-resource path and from the source tree, every
image resolves, every anchor used exists, F1 opens the section of the current perspective or tab, the cards' '?'
buttons open their own section, search finds a known word, the Help menu holds every entry in order, and the
manual screenshots are written into the bundled docs."""
import re
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402

from aurora.app import help as help_mod  # noqa: E402

LINK = re.compile(r"\]\(([^)\s]+)\)")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _anchors(doc: str) -> set[str]:
    return set(help_mod.headings(doc))


def _links():
    for name, _t, _tip in help_mod.DOCS:
        text = (help_mod.docs_dir() / name).read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for target in LINK.findall(text):
            yield name, target


def test_docs_open_from_the_source_tree_and_the_frozen_bundle(qapp, tmp_path, monkeypatch):
    import aurora
    from aurora.app.help import DOCS, HelpWindow, docs_dir
    src = docs_dir()
    assert src == Path(aurora.__file__).resolve().parent / "resources" / "docs"
    for frozen in (False, True):
        if frozen:
            bundle = tmp_path / "_internal"
            shutil.copytree(Path(aurora.__file__).resolve().parent / "resources", bundle / "aurora" / "resources")
            monkeypatch.setattr(sys, "frozen", True, raising=False)
            monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
            assert docs_dir() == bundle / "aurora" / "resources" / "docs"
        h = HelpWindow()
        for name, _title, _tip in DOCS:
            h.show_doc(name)
            qapp.processEvents()
            assert h.current_doc() == name
            assert len(h.browser.toPlainText()) > 500 and "AURORA" in h.browser.toPlainText()
            assert Path(h.browser.source().toLocalFile()).parent == docs_dir()
        h.close()
        h.deleteLater()


def test_every_image_resolves(qapp):
    from PySide6.QtGui import QImage
    images = [(doc, t) for doc, t in _links() if t.lower().endswith((".png", ".jpg", ".svg"))]
    assert len(images) >= 10
    for doc, target in images:
        p = help_mod.docs_dir() / target
        assert p.is_file(), (doc, target)
        assert not QImage(str(p)).isNull(), (doc, target)


def test_every_anchor_used_exists():
    docs = {name for name, _t, _tip in help_mod.DOCS}
    checked = 0
    for doc, target in _links():
        if target.startswith(("http://", "https://", "mailto:")) or target.lower().endswith((".png", ".jpg", ".svg")):
            continue
        path, _, frag = target.partition("#")
        other = Path(path).name if path else doc
        if path and other not in docs:
            continue                                     # a repository file (LICENSE, CREDITS.md): not a manual link
        assert other in docs, (doc, target)
        if frag:
            assert frag in _anchors(other), f"{doc}: '{target}' has no heading '{frag}' in {other}"
            checked += 1
    assert checked >= 10


def test_every_help_anchor_of_the_code_exists(qapp):
    from aurora.app.help_text import HELP
    from aurora.app.main import MainWindow
    from aurora.app.widgets import Card
    manual = _anchors("MANUAL.md")
    assert set(help_mod.PERSPECTIVE_HELP.values()) <= manual
    assert set(help_mod.TAB_HELP.values()) <= manual
    assert {a for _t, _n, a in HELP.values()} <= manual
    win = MainWindow()
    cards = [c for c in win.findChildren(Card) if c.help_anchor]
    assert len(cards) >= 20
    missing = sorted({c.help_anchor for c in cards} - manual)
    assert not missing, missing
    assert help_mod.section_of("45-one-card-per-attached-scan") == "4.5" and help_mod.section_of("10-settings") == "10"
    win.close()


def _press_f1(qapp, win):
    from PySide6.QtTest import QTest
    QTest.keySequence(win, win.page_help_action.shortcut())
    qapp.processEvents()


def test_f1_opens_the_section_of_the_page(qapp):
    from aurora.app import mode
    from aurora.app.main import MainWindow
    mode.set_advanced(True)                             # every perspective and tab can be shown
    win = MainWindow()
    win.show()
    win.activateWindow()
    qapp.processEvents()
    assert win.page_help_action.shortcut().matches(win.page_help_action.shortcut())
    for key, anchor in help_mod.PERSPECTIVE_HELP.items():
        if key == "settings":
            continue
        win.show_perspective(key)
        qapp.processEvents()
        _press_f1(qapp, win)
        h = win._help
        assert h is not None and h.isVisible(), key
        expected = help_mod.TAB_HELP.get((key, win.pages[key].tabs.tabText(win.pages[key].tabs.currentIndex())), anchor) \
            if key in ("fit", "maps") else anchor
        assert h.current_doc() == "MANUAL.md" and h.current_anchor() == expected, (key, h.current_anchor())
        win.activateWindow()
    win.show_perspective("fit")
    for i in range(win.fit.tabs.count()):
        win.fit.tabs.setCurrentIndex(i)
        qapp.processEvents()
        _press_f1(qapp, win)
        assert win._help.current_anchor() == help_mod.TAB_HELP[("fit", win.fit.tabs.tabText(i))]
        win.activateWindow()
    # Help > Help on this page does the same
    win.show_perspective("check")
    win.page_help_action.trigger()
    assert win._help.current_anchor() == "5-check"
    # the Settings dialog's Help button
    from aurora.app.settings_dialog import SettingsDialog
    d = SettingsDialog(win)
    d.help_btn.click()
    assert win._help.current_anchor() == "10-settings"
    d.deleteLater()
    win._help.close()
    win.close()


def test_card_question_marks_open_their_section(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.show()
    for card in (win.reduce.scans_card, win.reduce.zone_card, win.check.table_card, win.compare.library_card):
        assert card.help_btn.accessibleName() == f"Help on {card.title.text()}"
        card.help_btn.click()
        qapp.processEvents()
        assert win._help.isVisible() and win._help.current_doc() == "MANUAL.md"
        assert win._help.current_anchor() == card.help_anchor
    win._help.close()
    win.close()


def test_whats_this_and_tooltips_point_at_the_manual(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    assert win.whats_this_action.shortcut().toString() == "Shift+F1"
    assert win.reduce.load_all_btn.whatsThis().endswith("More: manual §4.1")
    from PySide6.QtWidgets import QCheckBox
    from aurora.app.help_text import explain
    box = explain(QCheckBox("include"), "reduce.include")
    assert box.whatsThis().endswith("… More: manual §4.5") and box.accessibleName() == "include the scan"
    assert box.toolTip() and box.statusTip()
    box.deleteLater()
    win.close()


def test_search_and_the_help_menu(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    win.show_help("MANUAL.md")
    qapp.processEvents()
    win._help.find_edit.setText("Richardson-Lucy")
    assert win._help.find() and win._help.find_info.text() == ""
    win._help.find_edit.setText("zzz-not-in-the-manual")
    assert not win._help.find() and win._help.find_info.text() == "not found"
    titles = [a.text().replace("&", "") for a in win.menus["help"].actions() if not a.isSeparator()]
    assert titles == ["User guide", "Glossary", "Manual", "Tutorials", "Installation and sharing", "Help on this page",
                      "Keyboard shortcuts", "What's this?", "Open the demo beamtime", "Run self-test…",
                      "Report a problem…", "Check for updates…", "Licences…", "About AURORA"]
    assert win.help_actions["USER_GUIDE.md"].shortcut().isEmpty()        # F1 belongs to Help on this page
    win._help.close()
    win.close()


def test_make_screenshots_writes_into_the_bundled_docs():
    import importlib.util
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("make_screenshots", root / "docs" / "make_screenshots.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.OUT.resolve() == (root / "aurora" / "resources" / "docs" / "img").resolve()


def test_run_selftest_report_a_problem_and_licences(qapp, monkeypatch, tmp_path):
    from PySide6.QtGui import QGuiApplication
    from aurora.app import diagnostics
    from aurora.app.main import MainWindow
    from aurora.credits import CREDITS_SHORT
    calls = []

    def quick(progress):
        progress(1, 2, "ok    imports")
        calls.append(1)
        return {"version": "0.0.0", "ok": True, "json": str(tmp_path / "selftest.json"),
                "checks": [{"name": "imports", "ok": True, "seconds": 0.1, "detail": ""},
                           {"name": "manuals", "ok": True, "seconds": 0.1, "detail": "4 documents"}]}

    monkeypatch.setattr(diagnostics, "run_selftest", quick)
    win = MainWindow()
    win.selftest_action.trigger()
    assert win.jobs.wait_idle(10000)
    qapp.processEvents()
    assert calls == [1] and "2 of 2 checks ok" in win.selftest_dialog.text()
    win.selftest_dialog.copy()
    assert "checks ok" in QGuiApplication.clipboard().text()
    win.selftest_dialog.close()
    # the command it would run, frozen or from source
    cmd = diagnostics.selftest_command(tmp_path / "x.json")
    assert cmd[-3:] == ["--selftest", "--out", str(tmp_path / "x.json")]
    failed = diagnostics.summary_text({"version": "1", "checks": [{"name": "report", "ok": False, "detail": "no svg"}]})
    assert "0 of 1 checks ok" in failed and "failed: report: no svg" in failed
    # Report a problem: what to send, Copy diagnostics, the issues address
    d = diagnostics.ReportProblemDialog(win)
    text = d.copy()
    assert CREDITS_SHORT in text and "Python" in text and "Qt" in text and "numpy" in text and "settings" in text
    assert QGuiApplication.clipboard().text() == text and diagnostics.ISSUES_URL == "https://github.com/sams808/AURORA/issues"
    d.deleteLater()
    # Licences: LICENSE (and the note on third-party notices in a source checkout)
    lic = diagnostics.LicencesDialog(win)
    root = Path(__file__).resolve().parent.parent
    assert root.joinpath("LICENSE").read_text(encoding="utf-8").strip()[:200] in lic.text()
    assert "Credits" in [t for t, _x in lic.texts] and "Third-party licences" in [t for t, _x in lic.texts]
    lic.deleteLater()
    win.close()
