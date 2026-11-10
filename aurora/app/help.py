"""In-app manuals (Help menu): the Markdown documents bundled under aurora/resources/docs, shown in a browser window
with a document list, search, back / forward, heading anchors and cross-document links. Works in the frozen app
(the folder is bundled by packaging/aurora.spec) and from a source checkout."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import (QColor, QDesktopServices, QFontDatabase, QKeySequence, QShortcut, QTextBlockFormat,
                           QTextCharFormat, QTextCursor, QTextDocument, QTextFormat, QTextFrameFormat, QTextImageFormat,
                           QTextLength, QTextTableFormat)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
                               QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from aurora import APP_NAME, resources_dir
from aurora.app.shortcuts import native_text
from aurora.app.theme import TOKENS, on_theme_changed, px
from aurora.app.widgets import fit_screen

DOCS = [
    ("USER_GUIDE.md", "User guide", "the short guide, one section per perspective"),
    ("MANUAL.md", "Manual", "every control, the files read and written, troubleshooting"),
    ("TUTORIALS.md", "Tutorials", "walkthroughs on the demo beamtime, then the real-beamtime recipe"),
    ("INSTALL.md", "Installation and sharing", "installers, macOS and Windows notes, where things are stored"),
]


# F1 and Help > Help on this page: the MANUAL.md section of each perspective, of the Settings dialog and of the tabs
PERSPECTIVE_HELP: dict[str, str] = {
    "home": "2-home", "survey": "3-survey", "reduce": "4-reduce", "check": "5-check", "compare": "6-compare",
    "fit": "7-fit", "maps": "8-maps", "report": "9-report", "settings": "10-settings",
}
# (perspective, tab title) -> anchor: each Fit and Maps tab opens its own MANUAL subsection.
TAB_HELP: dict[tuple[str, str], str] = {
    ("fit", "LCF"): "71-lcf-linear-combination-fit", ("fit", "PCA / MCR-ALS"): "72-pca--mcr-als",
    ("fit", "ITFA"): "73-itfa", ("fit", "Peaks"): "74-peaks",
    ("maps", "Explore"): "81-explore", ("maps", "Elemental maps"): "82-elemental-maps", ("maps", "RGB"): "83-rgb",
    ("maps", "Clusters"): "84-clusters",
}
HELP_ANCHOR_PROP = "aurora_help_anchor"


def set_help_anchor(widget: QWidget, anchor: str | None) -> None:
    """The MANUAL.md section F1 opens while `widget` (a page, a tab, a dialog) is the current one."""
    widget.setProperty(HELP_ANCHOR_PROP, anchor or "")


def help_anchor_of(widget: QWidget | None) -> str | None:
    """The help anchor set on the widget or the nearest ancestor that has one."""
    w = widget
    while w is not None:
        a = w.property(HELP_ANCHOR_PROP)
        if a:
            return str(a)
        w = w.parentWidget()
    return None


def page_anchor(key: str, page: QWidget | None = None) -> str:
    """The anchor F1 opens for perspective `key`: its current tab's section (TAB_HELP), else the page's own anchor,
    else PERSPECTIVE_HELP."""
    tabs = getattr(page, "tabs", None) if page is not None else None
    if tabs is not None and hasattr(tabs, "currentWidget") and tabs.count():
        cur = tabs.currentWidget()
        a = cur.property(HELP_ANCHOR_PROP) if cur is not None else None
        if a:
            return str(a)
        t = TAB_HELP.get((key, tabs.tabText(tabs.currentIndex())))
        if t:
            return t
    a = page.property(HELP_ANCHOR_PROP) if page is not None else None
    return str(a) if a else PERSPECTIVE_HELP.get(key, "1-the-window")


_HEADINGS: dict[str, dict[str, str]] = {}           # doc -> {anchor: section number}; plain strings only


def headings(doc: str = "MANUAL.md") -> dict[str, str]:
    """{anchor: section number ('4.5', '10', '' when unnumbered)} of a bundled document's headings."""
    if doc not in _HEADINGS:
        out: dict[str, str] = {}
        p = docs_dir() / doc
        in_code = False
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for ln in lines:
            if ln.lstrip().startswith("```"):
                in_code = not in_code
                continue
            m = None if in_code else re.match(r"^(#{1,6})\s+(.*?)\s*$", ln)
            if m:
                title = m.group(2)
                num = re.match(r"^(\d+(?:\.\d+)*)\.?\s", title)
                out[slug(title)] = num.group(1) if num else ""
        _HEADINGS[doc] = out
    return _HEADINGS[doc]


def section_of(anchor: str, doc: str = "MANUAL.md") -> str:
    """'45-one-card-per-attached-scan' -> '4.5' (the section number shown in What's This texts)."""
    return headings(doc).get(anchor, "")


def docs_dir() -> Path:
    """The bundled manuals (aurora/resources/docs): in the frozen app, the .app bundle and a source checkout."""
    return resources_dir() / "docs"


def slug(text: str) -> str:
    """GitHub-style heading anchor: lower case, punctuation dropped, spaces to hyphens."""
    s = text.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s, flags=re.UNICODE)
    return s.replace(" ", "-")


class DocBrowser(QTextBrowser):
    """QTextBrowser that adds heading anchors, readable tables / code blocks and fitted images to every page."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setOpenExternalLinks(True)
        self.setOpenLinks(True)
        self.setSearchPaths([str(docs_dir())])
        self._loaded = ""
        self.anchor = ""                        # the section the page was opened at
        self._fit = QTimer(self)
        self._fit.setSingleShot(True)
        self._fit.setInterval(120)
        self._fit.timeout.connect(self._fit_images)

    def loadResource(self, type_: int, name: QUrl):
        data = super().loadResource(type_, name)
        if sys.platform == "darwin" and type_ == QTextDocument.MarkdownResource and data is not None:
            raw = data.data() if hasattr(data, "data") else data
            text = bytes(raw).decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray, memoryview)) else str(raw)
            return native_text(text)
        return data

    def doSetSource(self, name: QUrl, type_=QTextDocument.UnknownResource) -> None:
        if name.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(name)
            return
        base = QUrl.fromLocalFile(name.toLocalFile()) if name.isLocalFile() else QUrl(name.toString().split("#", 1)[0])
        target = base.toString() if name.toString().split("#", 1)[0] else ""
        if target and target != self._loaded:
            local = Path(name.toLocalFile()) if name.isLocalFile() else None
            if local is not None and not local.exists():
                self.setMarkdown(f"# Not available\n\n`{local.name}` is not part of the bundled manuals.")
                self._loaded = target
                return
            super().doSetSource(base, QTextDocument.MarkdownResource)
            self._loaded = target
            self.polish_document()
        frag = name.fragment()
        self.anchor = frag
        if frag:
            self.scrollToAnchor(frag)
        elif target:
            self.verticalScrollBar().setValue(0)

    def polish_document(self) -> None:
        doc = self.document()
        doc.setDocumentMargin(18)
        t = TOKENS
        cur = QTextCursor(doc)
        cur.beginEditBlock()
        block = doc.begin()
        # code blocks: the platform's fixed font (Consolas on Windows) a little smaller than the body, following Text
        # size (a bare QFont('Consolas') was a fixed 12 pt, larger than the 13 px body and blind to Text size)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        if sys.platform == "win32":
            mono.setFamilies(["Consolas", mono.family()])
        mono.setPixelSize(px(12))
        while block.isValid():
            bf = block.blockFormat()
            level = bf.headingLevel()
            if level > 0:
                c = QTextCursor(block)
                c.movePosition(QTextCursor.StartOfBlock)
                c.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                fmt = QTextCharFormat()
                fmt.setAnchor(True)
                fmt.setAnchorNames([slug(block.text())])
                fmt.setForeground(QColor(t["ink"]))
                c.mergeCharFormat(fmt)
                nb = QTextBlockFormat(bf)
                nb.setTopMargin(16 if level <= 2 else 10)
                nb.setBottomMargin(6)
                QTextCursor(block).setBlockFormat(nb)
            elif bf.hasProperty(QTextFormat.BlockCodeFence) or bf.nonBreakableLines():
                nb = QTextBlockFormat(bf)
                nb.setBackground(QColor(t["alt"]))
                nb.setLeftMargin(12)
                nb.setNonBreakableLines(False)          # long command lines wrap inside the page (no sideways scroll)
                QTextCursor(block).setBlockFormat(nb)
                c = QTextCursor(block)
                c.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                cf = QTextCharFormat()
                cf.setFont(mono)
                cf.setForeground(QColor(t["ink"]))
                c.mergeCharFormat(cf)
            elif block.textList() is None and QTextCursor(block).currentTable() is None and block.text().strip():
                nb = QTextBlockFormat(bf)                # a paragraph: a gap before the next one
                nb.setBottomMargin(max(bf.bottomMargin(), px(8)))
                QTextCursor(block).setBlockFormat(nb)
            block = block.next()
        # tables: visible borders, padding, header shading
        frames = [doc.rootFrame()]
        while frames:
            fr = frames.pop()
            for child in fr.childFrames():
                frames.append(child)
                table = child if hasattr(child, "cellAt") else None
                if table is None:
                    continue
                tf = QTextTableFormat(table.format())
                tf.setBorder(1)
                tf.setBorderBrush(QColor(t["line"]))
                tf.setBorderStyle(QTextFrameFormat.BorderStyle_Solid)
                tf.setCellPadding(5)
                tf.setCellSpacing(0)
                tf.setWidth(QTextLength(QTextLength.PercentageLength, 100))
                table.setFormat(tf)
                for col in range(table.columns()):
                    cell = table.cellAt(0, col)
                    cf = cell.format().toTableCellFormat()
                    cf.setBackground(QColor(t["header"]))
                    cell.setFormat(cf)
        cur.endEditBlock()
        self._fit_images()

    def _fit_images(self) -> None:
        doc = self.document()
        avail = max(200, self.viewport().width() - 60)
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid() and frag.charFormat().isImageFormat():
                    img = frag.charFormat().toImageFormat()
                    natural = doc.resource(QTextDocument.ImageResource, QUrl(img.name()))
                    w = getattr(natural, "width", lambda: 0)() if natural is not None else 0
                    if w:
                        nf = QTextImageFormat(img)
                        nf.setWidth(min(w, avail))
                        nf.setHeight(natural.height() * min(w, avail) / w)
                        c = QTextCursor(doc)
                        c.setPosition(frag.position())
                        c.setPosition(frag.position() + frag.length(), QTextCursor.KeepAnchor)
                        c.setCharFormat(nf)
                it += 1
            block = block.next()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit.start()


class HelpWindow(QMainWindow):
    """The manuals window. MainWindow keeps one instance; `show_doc(name, anchor)` brings it up at a section."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} manuals")
        fit_screen(self, 1080, 820)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(10, 10, 10, 10)
        bar = QHBoxLayout()
        self.back_btn = QPushButton("◀  Back")
        self.fwd_btn = QPushButton("Forward  ▶")
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText(native_text("search in this document (Enter: next, Shift+Enter: previous)"))
        self.find_edit.setClearButtonEnabled(True)
        self.find_info = QLabel("")
        self.find_info.setObjectName("muted")
        bar.addWidget(self.back_btn)
        bar.addWidget(self.fwd_btn)
        bar.addSpacing(12)
        bar.addWidget(self.find_edit, 1)
        bar.addWidget(self.find_info)
        lay.addLayout(bar)
        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.setMaximumWidth(260)
        for name, title, tip in DOCS:
            it = QListWidgetItem(title)
            it.setToolTip(tip)
            it.setData(Qt.UserRole, name)
            self.list.addItem(it)
        split.addWidget(self.list)
        self.browser = DocBrowser()
        split.addWidget(self.browser)
        split.setStretchFactor(1, 1)
        lay.addWidget(split, 1)
        self.setCentralWidget(central)
        self.list.currentItemChanged.connect(self._doc_picked)
        self.back_btn.clicked.connect(self.browser.backward)
        self.fwd_btn.clicked.connect(self.browser.forward)
        self.browser.backwardAvailable.connect(self.back_btn.setEnabled)
        self.browser.forwardAvailable.connect(self.fwd_btn.setEnabled)
        self.browser.sourceChanged.connect(self._source_changed)
        self.back_btn.setEnabled(False)
        self.fwd_btn.setEnabled(False)
        self.find_edit.returnPressed.connect(lambda: self.find(backward=False))
        QShortcut(QKeySequence("Shift+Return"), self.find_edit, activated=lambda: self.find(backward=True))
        QShortcut(QKeySequence.Find, self, activated=lambda: (self.find_edit.setFocus(), self.find_edit.selectAll()))
        QShortcut(QKeySequence.Close, self, activated=self.close)
        QShortcut(QKeySequence.Cancel, self, activated=self.close)      # Esc, as in every other window of the app
        on_theme_changed(self, lambda _s: self._reload())

    def show_doc(self, name: str = "USER_GUIDE.md", anchor: str | None = None) -> None:
        url = QUrl.fromLocalFile(str(docs_dir() / name))
        if anchor:
            url.setFragment(anchor)
        self.browser.setSource(url)
        self.show()
        self.raise_()
        self.activateWindow()

    def current_doc(self) -> str:
        return Path(self.browser.source().toLocalFile()).name

    def current_anchor(self) -> str:
        """The section the window was last opened at ('' for the top of a document)."""
        return self.browser.anchor

    def find(self, backward: bool = False) -> bool:
        text = self.find_edit.text()
        if not text:
            return False
        flags = QTextDocument.FindBackward if backward else QTextDocument.FindFlag(0)
        ok = self.browser.find(text, flags)
        if not ok:                                   # wrap around
            c = self.browser.textCursor()
            c.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            self.browser.setTextCursor(c)
            ok = self.browser.find(text, flags)
        self.find_info.setText("" if ok else "not found")
        return ok

    def _doc_picked(self, item, _prev=None) -> None:
        if item is not None and item.data(Qt.UserRole) != self.current_doc():
            self.show_doc(item.data(Qt.UserRole))

    def _source_changed(self, url: QUrl) -> None:
        name = Path(url.toLocalFile()).name
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == name:
                self.list.blockSignals(True)
                self.list.setCurrentRow(i)
                self.list.blockSignals(False)
                break
        self.setWindowTitle(f"{APP_NAME} manuals · {dict((d[0], d[1]) for d in DOCS).get(name, name)}")

    def _reload(self) -> None:
        if self.browser._loaded:
            src = self.browser.source()
            self.browser._loaded = ""
            self.browser.setSource(src)


def open_help(widget: QWidget, doc: str = "USER_GUIDE.md", anchor: str | None = None) -> None:
    """From any widget: open the manuals window of its main window at `doc#anchor` (F1, the cards' '?' buttons, the
    Settings dialog's Help button). A widget outside the main window (a dialog) finds it through its parents."""
    from aurora.app.nav import window_of
    win = window_of(widget)
    if win is not None and hasattr(win, "show_help"):
        win.show_help(doc, anchor)
        return
    top = QWidget.window(widget) if widget is not None else None
    h = top.findChild(HelpWindow) if top is not None else None
    if h is None:
        h = HelpWindow(top)
    h.show_doc(doc, anchor)
