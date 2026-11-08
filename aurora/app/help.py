"""In-app manuals (Help menu): the Markdown documents bundled under aurora/resources/docs, shown in a browser window
with a document list, search, back / forward, heading anchors and cross-document links. Works in the frozen app
(the folder is bundled by packaging/aurora.spec) and from a source checkout."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import (QColor, QDesktopServices, QFont, QKeySequence, QShortcut, QTextBlockFormat, QTextCharFormat,
                           QTextCursor, QTextDocument, QTextFormat, QTextFrameFormat, QTextImageFormat, QTextLength,
                           QTextTableFormat)
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QPushButton,
                               QSplitter, QTextBrowser, QVBoxLayout, QWidget)

from aurora import APP_NAME
from aurora.app.theme import TOKENS, on_theme_changed

DOCS = [
    ("USER_GUIDE.md", "User guide", "the short guide, one section per perspective"),
    ("MANUAL.md", "Manual", "every control, the files read and written, troubleshooting"),
    ("TUTORIALS.md", "Tutorials", "walkthroughs on the demo beamtime, then the real-beamtime recipe"),
    ("INSTALL.md", "Installation and sharing", "installers, macOS and Windows notes, where things are stored"),
]


def docs_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "resources" / "docs"


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
        self._fit = QTimer(self)
        self._fit.setSingleShot(True)
        self._fit.setInterval(120)
        self._fit.timeout.connect(self._fit_images)

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
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
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
                QTextCursor(block).setBlockFormat(nb)
                c = QTextCursor(block)
                c.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                cf = QTextCharFormat()
                cf.setFont(mono)
                cf.setForeground(QColor(t["ink"]))
                c.mergeCharFormat(cf)
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
        self.resize(1080, 820)
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(10, 10, 10, 10)
        bar = QHBoxLayout()
        self.back_btn = QPushButton("◀  Back")
        self.fwd_btn = QPushButton("Forward  ▶")
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("search in this document (Enter: next, Shift+Enter: previous)")
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
    """From any widget: open the manuals window of its main window at `doc#anchor` (the perspectives' ? buttons)."""
    win = widget.window()
    if hasattr(win, "show_help"):
        win.show_help(doc, anchor)
    else:
        HelpWindow(win).show_doc(doc, anchor)
