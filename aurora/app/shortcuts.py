"""Window actions and menu entries added from anywhere, without editing main.py.

    shortcuts.add(self, "Undo", QKeySequence.Undo, self.session_undo, menu="edit", section="Edit")
    shortcuts.add(self, "Rectangle tool", "R", lambda: self.set_tool("rect+"), section="Reduce")
    recent = shortcuts.add_menu(self, "Open recent", "file")

menu is one of the main window's menus ('file', 'edit', 'view', 'help'). Without a menu, the action belongs to `owner`
and its shortcut works while `owner` is visible in the active window (Qt.WindowShortcut). The QActions are children of
`owner`, so they live exactly as long as it does; nothing is kept at module level. An owner that is not inside the
main window yet (a perspective built before the window adds it) is placed when the window calls adopt(). Every
action added here is listed by registered(window), which the Keyboard shortcuts dialog reads, grouped by `section`.
"""
from __future__ import annotations

import re
import sys
from typing import Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu, QWidget

MENUS = ("file", "edit", "view", "help")
PROP_REGISTERED = "aurora_shortcut"         # set on every QAction made by add()
PROP_SECTION = "aurora_section"             # its group in the shortcuts list
PROP_MENU = "aurora_menu"                   # the main-window menu it belongs in ('' = none)
PROP_PLACED = "aurora_placed"               # already inserted into that menu

# a modifier chord written in a text: 'Ctrl+Shift+A', 'Ctrl+PgUp', 'Alt+Up', 'Ctrl+/', 'Shift+Enter' (not 'Shift+click')
_CHORD = re.compile(r"\b((?:(?:Ctrl|Shift|Alt|Meta)\+)+(?:F\d{1,2}\b|PgUp\b|PgDown\b|Return\b|Enter\b|Up\b|Down\b|Left\b|"
                    r"Right\b|Del\b|Delete\b|Esc\b|Tab\b|Space\b|[A-Z0-9](?![A-Za-z])|[/\[\]+\-](?!\w)))")


def native_text(text: str) -> str:
    """The shortcuts named in a user-visible text, written the way this platform writes them. Unchanged on Windows and
    Linux; on macOS, where Qt maps Ctrl to Command, 'Ctrl+Shift+A' reads '⇧⌘A' and 'Alt+Up' reads '⌥↑', as in the
    menus. Texts and manuals are written with the Windows names; every place that shows one passes it through here."""
    if sys.platform != "darwin" or not text or "+" not in text:
        return text
    return _CHORD.sub(lambda m: _native_chord(m.group(1)) or m.group(1), text)


def _native_chord(chord: str) -> str:
    return QKeySequence(chord).toString(QKeySequence.NativeText)


def delete_keys() -> list[QKeySequence]:
    """Delete, plus Backspace on macOS, whose 'delete' key is Backspace (Fn+delete is the forward Delete)."""
    keys = [QKeySequence("Delete")]
    if sys.platform == "darwin":
        keys.append(QKeySequence("Backspace"))
    return keys


def fullscreen_keys() -> list[QKeySequence]:
    """Full screen: F11 on Windows and Linux; Ctrl+Cmd+F on macOS, where F11 shows the desktop (Mission Control) and
    is a volume key without fn. Qt writes Command as 'Ctrl' and Control as 'Meta' on macOS."""
    return [QKeySequence("Ctrl+Meta+F" if sys.platform == "darwin" else "F11")]


def holder_keys(step: int) -> list[QKeySequence]:
    """Previous (step < 0) or next holder: Ctrl+PgUp / Ctrl+PgDown, plus Ctrl+Alt+Up / Ctrl+Alt+Down on macOS
    (Option-Command-arrow), whose laptop keyboards have no PgUp / PgDown keys. Not on Windows and Linux, where
    Ctrl+Alt+arrow rotates the screen or switches the workspace."""
    keys = [QKeySequence("Ctrl+PgUp" if step < 0 else "Ctrl+PgDown")]
    if sys.platform == "darwin":
        keys.append(QKeySequence("Ctrl+Alt+Up" if step < 0 else "Ctrl+Alt+Down"))
    return keys


def _main_window(owner: QObject) -> QWidget | None:
    """The window with the `menus` dict (MainWindow) that owns `owner`, None while `owner` is not inside one yet."""
    w = owner if isinstance(owner, QWidget) else None
    while w is not None:
        top = w.window()
        if isinstance(getattr(top, "menus", None), dict):
            return top
        w = top.parentWidget()
    return None


def _menu_of(window: QWidget, name: str) -> QMenu | None:
    m = window.menus.get(name) if window is not None else None
    return m if isinstance(m, QMenu) else None


def _insert(window: QWidget, name: str, item) -> bool:
    """Put a QAction or a QMenu into the window's menu `name`, before its anchor (window.menu_anchors) when it has one."""
    menu = _menu_of(window, name)
    if menu is None:
        return False
    anchor = getattr(window, "menu_anchors", {}).get(name)
    if isinstance(item, QMenu):
        if anchor is not None:
            menu.insertMenu(anchor, item)
        else:
            menu.addMenu(item)
    else:
        if anchor is not None:
            menu.insertAction(anchor, item)
        else:
            menu.addAction(item)
    item.setProperty(PROP_PLACED, True)
    return True


def add(owner: QObject, text: str, key, slot: Callable, menu: str | None = None, section: str | None = None,
        context=Qt.WindowShortcut) -> QAction:
    """A QAction with a shortcut (`key`: a str, a QKeySequence, a StandardKey or None) that calls `slot`.

    With `menu`, it is an entry of that main-window menu; otherwise the owner carries it."""
    if menu is not None and menu not in MENUS:
        raise ValueError(f"menu must be one of {MENUS}, not {menu!r}")
    a = QAction(text, owner)
    if key is not None and key != "":
        a.setShortcut(key if isinstance(key, QKeySequence) else QKeySequence(key))
    a.setShortcutContext(context)
    a.setMenuRole(QAction.NoRole)
    a.triggered.connect(lambda _checked=False: slot())
    a.setProperty(PROP_REGISTERED, True)
    a.setProperty(PROP_SECTION, section or (menu.capitalize() if menu else "General"))
    a.setProperty(PROP_MENU, menu or "")
    if menu is None:
        if isinstance(owner, QWidget):
            owner.addAction(a)
    else:
        win = _main_window(owner)
        if win is not None:
            _insert(win, menu, a)
    return a


def add_menu(owner: QObject, title: str, menu: str) -> QMenu:
    """A submenu `title` in the main-window menu `menu` (for example File > Open recent), owned by `owner`."""
    if menu not in MENUS:
        raise ValueError(f"menu must be one of {MENUS}, not {menu!r}")
    m = QMenu(title, owner if isinstance(owner, QWidget) else None)
    m.setProperty(PROP_MENU, menu)
    win = _main_window(owner)
    if win is not None:
        _insert(win, menu, m)
    return m


def adopt(window: QWidget) -> int:
    """Place the menu entries and submenus added before their owner was inside `window`. Returns how many."""
    n = 0
    items = [a for a in window.findChildren(QAction) if a.property(PROP_REGISTERED)]
    items += [m for m in window.findChildren(QMenu) if m.property(PROP_MENU)]
    for item in items:
        name = item.property(PROP_MENU)
        if name and not item.property(PROP_PLACED) and _insert(window, name, item):
            n += 1
    return n


def registered(window: QWidget) -> list[QAction]:
    """Every action add() made for this window (its owners are inside it), in creation order."""
    return [a for a in window.findChildren(QAction) if a.property(PROP_REGISTERED)]


def section_of(action: QAction) -> str:
    return str(action.property(PROP_SECTION) or "General")
