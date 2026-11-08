"""Report perspective: build the group-organised PDF from the session's zones, preview its pages, open the folder."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QProgressBar, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from aurora.app.mode import mark_advanced, mark_easy
from aurora.app.session import Session
from aurora.app.widgets import Card, Worker
from aurora.core.report import GROUP_ORDER, UNGROUPED, ReportBuilder, ReportResult


class ReportPerspective(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._worker: Worker | None = None
        self._result: ReportResult | None = None
        self._page = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Report"); h1.setObjectName("h1")
        sub = QLabel("the final report, organised by sample group, from the zones and their settings"); sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub); head.addStretch(1)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        left = Card("Options")
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("title"))
        self.title = QLineEdit(); self.title.setPlaceholderText("AURORA report · <project>")
        r1.addWidget(self.title, 1)
        left.body.addLayout(r1)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("output"))
        self.out_dir = QLineEdit(); r2.addWidget(self.out_dir, 1)
        b = QPushButton("…"); b.setMaximumWidth(36); b.clicked.connect(self._choose_out); r2.addWidget(b)
        left.body.addLayout(r2)
        self.scope_box = QWidget()                     # appendices and groups: Advanced
        sl = QVBoxLayout(self.scope_box); sl.setContentsMargins(0, 0, 0, 0)
        self.appendix = QCheckBox("Appendix A: the four detectors per zone"); self.appendix.setChecked(True)
        self.maps = QCheckBox("Appendix B: plate maps with the zones"); self.maps.setChecked(True)
        sl.addWidget(self.appendix); sl.addWidget(self.maps)
        sl.addWidget(QLabel("Groups to include (sample-sheet groups found in the zones):"))
        self.groups = QListWidget()
        sl.addWidget(self.groups, 1)
        gb = QHBoxLayout()
        self.all_btn = QPushButton("All"); self.all_btn.clicked.connect(lambda: self._tick(True))
        self.none_btn = QPushButton("None"); self.none_btn.clicked.connect(lambda: self._tick(False))
        gb.addWidget(self.all_btn); gb.addWidget(self.none_btn); gb.addStretch(1)
        sl.addLayout(gb)
        left.body.addWidget(self.scope_box, 1)
        self.scope_line = QLabel(""); self.scope_line.setObjectName("muted"); self.scope_line.setWordWrap(True)
        left.body.addWidget(self.scope_line)          # Easy mode: what the report contains, read-only
        self.build_btn = QPushButton("Build the report"); self.build_btn.setObjectName("primary")
        self.build_btn.clicked.connect(self.build)
        left.body.addWidget(self.build_btn)
        self.progress = QProgressBar(); self.progress.setVisible(False)
        left.body.addWidget(self.progress)
        self.status = QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        left.body.addWidget(self.status)
        left.body.addStretch(0)                        # takes the room of the hidden options in Easy mode only
        split.addWidget(left)

        right = Card("Pages")
        nav = QHBoxLayout()
        self.prev_btn = QPushButton("◀"); self.prev_btn.clicked.connect(lambda: self._show_page(self._page - 1))
        self.next_btn = QPushButton("▶"); self.next_btn.clicked.connect(lambda: self._show_page(self._page + 1))
        self.page_label = QLabel("no report built yet"); self.page_label.setObjectName("muted")
        self.open_btn = QPushButton("Open folder"); self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_folder)
        self.pdf_btn = QPushButton("Open PDF"); self.pdf_btn.setEnabled(False)
        self.pdf_btn.clicked.connect(self._open_pdf)
        for w in (self.prev_btn, self.next_btn, self.page_label):
            nav.addWidget(w)
        nav.addStretch(1); nav.addWidget(self.open_btn); nav.addWidget(self.pdf_btn)
        right.body.addLayout(nav)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setObjectName("pageview")
        self.page_view = QLabel(); self.page_view.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.scroll.setWidget(self.page_view)
        right.body.addWidget(self.scroll, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 1); split.setStretchFactor(1, 2)
        split.setSizes([460, 900])
        root.addWidget(split, 1)
        session.project_changed.connect(self._project_changed)
        session.zones_changed.connect(self._fill_groups)
        self.groups.itemChanged.connect(self._scope_text)
        self.appendix.toggled.connect(self._scope_text)
        self.maps.toggled.connect(self._scope_text)
        # Easy mode: all groups and both appendices, stated read-only; the choices are Advanced
        mark_advanced(self.scope_box)
        mark_easy(self.scope_line)
        self._scope_text()

    def _scope_text(self, *_) -> None:
        """What the next build contains, from the (possibly hidden) options in force."""
        n = self.groups.count()
        ticked = len(self.selected_groups())
        groups = "all groups" if ticked == n else f"{ticked} of {n} groups (chosen in Advanced mode)"
        apps = [name for name, box in (("Appendix A (the four detectors)", self.appendix), ("Appendix B (plate maps)", self.maps))
                if box.isChecked()]
        self.scope_line.setText(f"The report covers {groups}" + (", with " + " and ".join(apps) if apps else ", without appendices") + ".")

    # ------------------------------------------------------------------ options
    def _project_changed(self, project) -> None:
        self.out_dir.setText(str(project.root / "reports" / "aurora"))
        self._fill_groups()

    def _fill_groups(self) -> None:
        ticked = {self.groups.item(i).text(): self.groups.item(i).checkState() == Qt.Checked for i in range(self.groups.count())}
        found = {(z.group.strip() or UNGROUPED) for h in self.session.zones.holders.values() for z in h.zones}
        self.groups.clear()
        for g in sorted(found, key=ReportBuilder.group_sort_key):
            it = QListWidgetItem(g)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if ticked.get(g, True) else Qt.Unchecked)
            self.groups.addItem(it)
        self._scope_text()

    def _tick(self, on: bool) -> None:
        for i in range(self.groups.count()):
            self.groups.item(i).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def selected_groups(self) -> list[str]:
        return [self.groups.item(i).text() for i in range(self.groups.count()) if self.groups.item(i).checkState() == Qt.Checked]

    def _choose_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Report output folder", self.out_dir.text())
        if d:
            self.out_dir.setText(d)

    # ------------------------------------------------------------------ build
    def build(self) -> None:
        if self.session.project is None or (self._worker is not None and self._worker.isRunning()):
            return
        groups = self.selected_groups()
        if not groups:
            self.status.setText("tick at least one group")
            return
        from aurora.app.settings_dialog import get
        from aurora.core.report import AUTHOR
        builder = ReportBuilder(self.session.project, self.session.zones, Path(self.out_dir.text()),
                                title=self.title.text().strip() or None, groups=groups,
                                author=str(get("report_author") or AUTHOR),
                                stacks=dict(self.session._stacks), appendix=self.appendix.isChecked(), maps=self.maps.isChecked())
        self.progress.setVisible(True); self.progress.setRange(0, 0)
        self.build_btn.setEnabled(False)
        self.status.setText("building …")
        self._worker = Worker(lambda cb: builder.build(progress=cb), self)
        self._worker.progress.connect(self._on_progress, Qt.QueuedConnection)
        self._worker.done.connect(self._on_done, Qt.QueuedConnection)
        self._worker.failed.connect(self._on_failed, Qt.QueuedConnection)
        self._worker.start()

    def _on_progress(self, i: int, n: int, msg: str) -> None:
        self.progress.setRange(0, max(n, 1)); self.progress.setValue(i)
        self.status.setText(msg)

    def _on_done(self, result: ReportResult) -> None:
        self._worker.wait(30000)
        self._result = result
        self.progress.setVisible(False); self.build_btn.setEnabled(True)
        self.status.setText(f"{result.pdf.name}: {result.pages} pages, {result.entries} zone spectra, {len(result.csvs)} CSV, "
                            f"{len(result.figures)} figures" + (f"; {len(result.skipped)} skipped" if result.skipped else ""))
        self.open_btn.setEnabled(True); self.pdf_btn.setEnabled(True)
        self._show_page(0)

    def _on_failed(self, text: str) -> None:
        self._worker.wait(30000)
        self.progress.setVisible(False); self.build_btn.setEnabled(True)
        self.status.setText("report failed: " + text.strip().splitlines()[-1])

    # ------------------------------------------------------------------ preview (the group figures)
    def _show_page(self, i: int) -> None:
        pages = self._result.page_pngs if self._result else []
        if not pages:
            self.page_label.setText("no pages"); return
        i = max(0, min(i, len(pages) - 1))
        self._page = i
        pm = QPixmap(str(pages[i]))
        if not pm.isNull():
            self.page_view.setPixmap(pm.scaledToWidth(min(820, pm.width()), Qt.SmoothTransformation))
        self.page_label.setText(f"page {i + 1} of {len(pages)}")

    def _open_folder(self) -> None:
        if self._result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.pdf.parent)))

    def _open_pdf(self) -> None:
        if self._result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.pdf)))
