"""The Easy-visible texts of a window, for the plain-language guards (tests/test_plain.py).

plain_texts(win) shows each Easy perspective and collects, from every visible widget: label texts (full text of an
ElidedLabel, plain text of a TermLabel with the term keys it links) and their tooltips; button texts and tooltips;
combo items and the combo tooltip; table and tree headers with their tooltips; the cells (and their tooltips) of the
Home tree, the Check queue and the Compare table; the Home summary panel; EmptyState texts; the status bar message and
the top bar. The About and Licences dialogs and the help window are not part of it (credits live there).
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

EASY_PAGES = ("home", "survey", "reduce", "check", "compare", "fit", "report")
INTERNALS = re.compile(r"Errno|Traceback|aurora\.core|aurora\.app|ProtectedPathError|PermissionError|\\\\|_reduced_cache"
                       r"|\.npz|\bnan\b|NaN|num_maps|fixed bins|nominal|i0jump|SDD1234|sdd-scaled|spectrum\(s\)"
                       r"|\b1 spectra\b")
NAMES = ("John Bussey", "Dynes", "sgm_report", "CLS GUI")


def owner_names() -> list[str]:
    """Names no shipped text may state, found at run time so that no test file writes one down (tests/test_blank.py
    scans the tests too): this machine's account, the author's first name (pyproject.toml) and the tokens of the
    local deny-list (AURORA_BLANK_DENYLIST, when one is configured)."""
    import tomllib
    from pathlib import Path

    from test_blank import _accounts, denylist
    root = Path(__file__).resolve().parent.parent
    author = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["authors"][0]["name"]
    return sorted(_accounts() | {author.split()[0]} | set(denylist() or []))


def names_in(text: str) -> list[str]:
    """The person and pipeline names of NAMES, the owner names (whole words, any case) and a CLS beamtime folder name
    found in a text that ships (the glossary, the welcome panel). Not for window texts: a folder path shown in the
    window may hold the account name of the machine."""
    from test_blank import BEAMTIME_FOLDER, _word
    hits = [n for n in NAMES if n in text]
    hits += [n for n in owner_names() if _word(n).search(text)]
    if BEAMTIME_FOLDER.search(text):
        hits.append("a beamtime folder name")
    return hits


@dataclass
class Text:
    where: str
    text: str
    tooltip: str = ""
    linked: tuple = field(default_factory=tuple)

    def __str__(self) -> str:
        return f"[{self.where}] {self.text!r}"


def _plain(t: str) -> str:
    t = str(t or "")
    if "<" in t and ">" in t:
        t = re.sub(r"<br\s*/?>", "\n", t)
        t = re.sub(r"<[^>]+>", "", t)
        t = html.unescape(t)
    return t.replace("​", "").strip()


def _shown(w, page) -> bool:
    return w.isVisible() and (page is None or page.isAncestorOf(w) or w is page)


def collect_widget_texts(root, where: str, page=None, cells_of=()) -> list[Text]:
    from PySide6.QtCore import QModelIndex, Qt
    from PySide6.QtWidgets import (QAbstractButton, QComboBox, QLabel, QTableWidget, QTextBrowser, QTreeWidget,
                                   QWidget)
    out: list[Text] = []
    for w in [root] + root.findChildren(QWidget):
        if not _shown(w, page):
            continue
        tip = _plain(w.toolTip())
        name = f"{where}:{type(w).__name__}:{w.objectName()}"
        if isinstance(w, QLabel):
            if hasattr(w, "plain_text"):
                t, linked = w.plain_text(), tuple(w.linked_keys())
            else:
                t, linked = (w.full_text() if hasattr(w, "full_text") else w.text()), ()
            t = _plain(t)
            if t or tip:
                out.append(Text(name, t, tip, linked))
        elif isinstance(w, QAbstractButton):
            if w.property("aurora_help_button") and not w.property("aurora_term"):
                continue
            t = _plain(w.text())
            if t or tip:
                out.append(Text(name, t, tip))
        elif isinstance(w, QComboBox):
            for i in range(w.count()):
                out.append(Text(name + f"[{i}]", _plain(w.itemText(i)), tip))
            if tip:
                out.append(Text(name + ":tip", "", tip))
        elif isinstance(w, QTextBrowser):
            out.append(Text(name, _plain(w.toPlainText()), tip))
        if isinstance(w, QTreeWidget):
            hi = w.headerItem()
            for c in range(w.columnCount()):
                if not w.isColumnHidden(c):
                    out.append(Text(name + f":head{c}", _plain(hi.text(c)), _plain(hi.toolTip(c))))
            if w in cells_of:
                stack = [w.topLevelItem(i) for i in range(w.topLevelItemCount())]
                while stack:
                    it = stack.pop()
                    for c in range(w.columnCount()):
                        if not w.isColumnHidden(c):
                            out.append(Text(name + f":cell{c}", _plain(it.text(c)), _plain(it.toolTip(c))))
                    stack.extend(it.child(i) for i in range(it.childCount()))
        elif isinstance(w, QTableWidget):
            for c in range(w.columnCount()):
                if w.isColumnHidden(c):
                    continue
                h = w.horizontalHeaderItem(c)
                if h is not None:
                    out.append(Text(name + f":head{c}", _plain(h.text()), _plain(h.toolTip())))
            if w in cells_of:
                for r in range(w.rowCount()):
                    for c in range(w.columnCount()):
                        if w.isColumnHidden(c):
                            continue
                        it = w.item(r, c)
                        if it is not None:
                            out.append(Text(name + f":cell{r},{c}", _plain(it.text()), _plain(it.toolTip())))
                        cw = w.cellWidget(r, c)
                        if cw is not None:
                            out.extend(collect_widget_texts(cw, name + f":cellw{r},{c}", None))
        elif hasattr(w, "model") and not isinstance(w, (QComboBox,)) and hasattr(w, "horizontalHeader"):
            m = w.model()
            if m is not None:
                try:
                    n = m.columnCount(QModelIndex())
                except TypeError:
                    n = 0
                for c in range(n):
                    if w.isColumnHidden(c):
                        continue
                    out.append(Text(name + f":head{c}", _plain(m.headerData(c, Qt.Horizontal, Qt.DisplayRole) or ""),
                                    _plain(m.headerData(c, Qt.Horizontal, Qt.ToolTipRole) or "")))
    return out


def plain_texts(win, app=None, pages=EASY_PAGES) -> list[Text]:
    """Every Easy-visible text of the window, page by page (the window must be in Easy mode)."""
    from demo_window import pump
    out: list[Text] = []
    cur = win.current_key()
    top = win.findChild(type(win.stack.parentWidget()), "topbar")
    for key in pages:
        win.show_perspective(key)
        if app is not None:
            pump(app, 6)
        page = win.pages[key]
        cells = []
        if key == "home":
            cells = [page.tree]
        elif key == "check":
            cells = [page.queue]
        elif key == "compare":
            cells = [getattr(page, "table", None)]
        out.extend(collect_widget_texts(page, key, page, [c for c in cells if c is not None]))
        if top is not None:
            out.extend(collect_widget_texts(top, f"{key}:topbar", None))
        if win.banner.isVisible():
            out.extend(collect_widget_texts(win.banner, f"{key}:banner", None))
        msg = win.statusBar().currentMessage()
        if msg:
            out.append(Text(f"{key}:status", msg, ""))
    win.show_perspective(cur)
    return out


def violations(texts, check) -> list[str]:
    return [f"{t}  tip={t.tooltip[:160]!r} -> {r}" for t in texts for r in [check(t)] if r]


def internals(t: Text):
    hits = [m.group(0) for s in (t.text, t.tooltip) for m in INTERNALS.finditer(s)]
    return hits or None


def names(t: Text):
    hits = [n for n in NAMES if n in t.text or n in t.tooltip]
    return hits or None


def jargon(t: Text):
    from aurora.glossary import unexplained
    # a text is explained by its own words, its linked terms or a 'What is …?' line of its tooltip; a tooltip may
    # carry the exact settings in expert words (Reduce step 4's tooltip is the technical line on purpose)
    return unexplained(t.text, t.tooltip, t.linked) or None
