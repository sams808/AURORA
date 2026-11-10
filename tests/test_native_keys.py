"""Shortcuts named in texts follow the platform: 'Ctrl+Z' on Windows and Linux, '⌘Z' on macOS (Qt maps Ctrl to
Command there), in tooltips, status lines, dialogs and the manuals shown in the Help window."""
import sys

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtGui import QKeySequence  # noqa: E402

from aurora.app import shortcuts  # noqa: E402
from aurora.app.shortcuts import native_text  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _marked(chord):
    """Stands in for Qt's native text: marks what was matched, '<Ctrl+Z>'."""
    return f"<{chord}>"


@pytest.mark.parametrize("text, expected", [
    ("Undo (Ctrl+Z).", "Undo (<Ctrl+Z>)."),
    ("Ctrl+Shift+A switches.", "<Ctrl+Shift+A> switches."),
    ("with Ctrl+PgUp / Ctrl+PgDown.", "with <Ctrl+PgUp> / <Ctrl+PgDown>."),
    ("Ctrl+Shift+C / Ctrl+Shift+V copy", "<Ctrl+Shift+C> / <Ctrl+Shift+V> copy"),
    ("**Ctrl+1** … **Ctrl+8**", "**<Ctrl+1>** … **<Ctrl+8>**"),
    ("**Ctrl+/** keyboard shortcuts, **Shift+F1** what's this", "**<Ctrl+/>** keyboard shortcuts, **<Shift+F1>** what's this"),
    ("**Alt+Up** / **Alt+Down**", "**<Alt+Up>** / **<Alt+Down>**"),
    ("(Ctrl+Return).", "(<Ctrl+Return>)."),
    ("Enter: next, Shift+Enter: previous", "Enter: next, <Shift+Enter>: previous"),
    ("a Shift+click adds a pixel", "a Shift+click adds a pixel"),          # a click, not a key
    ("Ctrl+Zoom", "Ctrl+Zoom"),
    ("no shortcut here", "no shortcut here"),
])
def test_the_chords_found_in_a_text(monkeypatch, text, expected):
    monkeypatch.setattr(shortcuts.sys, "platform", "darwin")
    monkeypatch.setattr(shortcuts, "_native_chord", _marked)
    assert native_text(text) == expected


def test_windows_and_linux_keep_the_text(monkeypatch):
    for plat in ("win32", "linux"):
        monkeypatch.setattr(shortcuts.sys, "platform", plat)
        assert native_text("Undo (Ctrl+Z), Ctrl+Shift+A, Alt+Up") == "Undo (Ctrl+Z), Ctrl+Shift+A, Alt+Up"


@pytest.mark.skipif(sys.platform != "darwin", reason="Qt writes ⌘ only on macOS")
def test_macos_writes_command_keys(qapp):
    assert native_text("Undo (Ctrl+Z)") == f"Undo ({QKeySequence('Ctrl+Z').toString(QKeySequence.NativeText)})"
    assert "⌘" in native_text("Ctrl+Shift+A") and "Ctrl" not in native_text("Ctrl+Shift+A")


def test_the_window_texts_go_through_it(qapp, monkeypatch):
    """With macOS key names forced, the tooltips, the Advanced hint and the Delete keys follow."""
    from aurora.app.help_text import HELP
    from aurora.app.main import MainWindow, PERSPECTIVES
    monkeypatch.setattr(shortcuts.sys, "platform", "darwin")
    monkeypatch.setattr(shortcuts, "_native_chord", _marked)
    win = MainWindow()
    try:
        title = dict(PERSPECTIVES)["reduce"]
        assert win.rail_buttons["reduce"].toolTip() == f"{title} (<Ctrl+3>)"
        assert win.perspective_hint("maps").endswith("(<Ctrl+Shift+A>)")
        assert "Ctrl+Shift+A" in HELP["main.mode"][0] and "<Ctrl+Shift+A>" in win.findChild(type(win.mode_btn),
                                                                                             "modebtn").toolTip()
    finally:
        win.close()


def test_delete_also_answers_backspace_on_macos(monkeypatch):
    monkeypatch.setattr(shortcuts.sys, "platform", "darwin")
    assert [k.toString() for k in shortcuts.delete_keys()] == ["Del", "Backspace"]
    monkeypatch.setattr(shortcuts.sys, "platform", "win32")
    assert [k.toString() for k in shortcuts.delete_keys()] == ["Del"]


def test_the_help_window_shows_macos_keys_on_a_mac(qapp, monkeypatch):
    from aurora.app import help as help_mod
    monkeypatch.setattr(help_mod.sys, "platform", "darwin")
    monkeypatch.setattr(shortcuts.sys, "platform", "darwin")
    monkeypatch.setattr(shortcuts, "_native_chord", _marked)
    b = help_mod.DocBrowser()
    b.setSource(help_mod.QUrl.fromLocalFile(str(help_mod.docs_dir() / "MANUAL.md")))
    text = b.toPlainText()
    assert "<Ctrl+Shift+A>" in text and "Ctrl+Shift+A" not in text.replace("<Ctrl+Shift+A>", "")
    b.deleteLater()


def test_full_screen_and_holder_keys_work_on_a_mac(qapp, monkeypatch):
    """F11 is Mission Control's 'Show Desktop' on macOS and MacBooks have no PgUp / PgDown: the native keys there."""
    monkeypatch.setattr(shortcuts.sys, "platform", "darwin")
    assert [k.toString() for k in shortcuts.fullscreen_keys()] == [QKeySequence("Ctrl+Meta+F").toString()]
    assert [k.toString() for k in shortcuts.holder_keys(-1)] == ["Ctrl+PgUp", "Ctrl+Alt+Up"]
    assert [k.toString() for k in shortcuts.holder_keys(1)] == ["Ctrl+PgDown", "Ctrl+Alt+Down"]
    monkeypatch.setattr(shortcuts.sys, "platform", "win32")
    assert [k.toString() for k in shortcuts.fullscreen_keys()] == ["F11"]
    assert [k.toString() for k in shortcuts.holder_keys(1)] == ["Ctrl+PgDown"]       # Ctrl+Alt+arrow rotates screens
    monkeypatch.undo()
    from aurora.app.main import MainWindow
    win = MainWindow()
    try:
        assert win.fullscreen_action.shortcuts() == shortcuts.fullscreen_keys()
        assert win.next_holder_action.shortcuts() == shortcuts.holder_keys(1)
    finally:
        win.close()


def test_no_missing_font_family_comes_first(qapp):
    """A missing family at the head of a font list costs a font-alias scan at every start (macOS: 80-250 ms)."""
    from PySide6.QtGui import QFontDatabase
    from aurora.app import theme
    assert "Inter" not in qapp.font().families() and "JetBrains" not in theme.MONO_FONT
    installed = set(QFontDatabase.families())
    if sys.platform == "win32" and installed:
        assert qapp.font().families()[0] in installed
        assert theme.MONO_FONT.split(",")[0].strip("' ") in installed
