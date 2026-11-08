"""Report perspective: build the group-organised PDF from the session's zones, preview its pages, open the folder."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from aurora.app import jobs, nav
from aurora.app.help_text import explain_all, labelled
from aurora.app.mode import mark_advanced, mark_easy
from aurora.app.session import Session
from aurora.app.vocab import plural
from aurora.app.widgets import (Card, ElidedLabel, EmptyState, TaskBar, default_dir, overlay_host, remember_dir,
                                set_enabled)
from aurora.core.report import UNGROUPED, ReportBuilder, ReportResult

NO_PROJECT = "open a project first"


class ReportPerspective(QWidget):
    def __init__(self, session: Session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self._result: ReportResult | None = None
        self._page = 0
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Report"); h1.setObjectName("h1")
        sub = ElidedLabel("the final report, organised by sample group, from the zones and their settings", Qt.ElideRight)
        sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub, 1)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        left = Card("Options", help_anchor="9-report")
        r1 = QHBoxLayout()
        self.title = QLineEdit(); self.title.setPlaceholderText("AURORA report · <project>")
        r1.addWidget(labelled("title", self.title, "report.title")); r1.addWidget(self.title, 1)
        left.body.addLayout(r1)
        r2 = QHBoxLayout()
        self.out_dir = QLineEdit()
        r2.addWidget(labelled("output", self.out_dir, "report.out")); r2.addWidget(self.out_dir, 1)
        self.browse_btn = b = QPushButton("Browse…"); b.clicked.connect(self._choose_out); r2.addWidget(b)
        left.body.addLayout(r2)
        self.scope_box = QWidget()                     # appendices and groups: Advanced
        sl = QVBoxLayout(self.scope_box); sl.setContentsMargins(0, 0, 0, 0)
        self.appendix = QCheckBox("Appendix A: the four detectors per zone"); self.appendix.setChecked(True)
        self.maps = QCheckBox("Appendix B: plate maps with the zones"); self.maps.setChecked(True)
        sl.addWidget(self.appendix); sl.addWidget(self.maps)
        self.groups = QListWidget()
        gl = QLabel("Groups to include (sample-sheet groups found in the zones):"); gl.setWordWrap(True); gl.setBuddy(self.groups)
        sl.addWidget(gl)
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
        self.taskbar = TaskBar(self)
        left.body.addWidget(self.taskbar)
        self.status = QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        left.body.addWidget(self.status)
        left.body.addStretch(0)                        # takes the room of the hidden options in Easy mode only
        split.addWidget(left)

        right = Card("Pages", help_anchor="9-report")
        nav_row = QHBoxLayout()
        self.prev_btn = QPushButton("◀"); self.prev_btn.clicked.connect(lambda: self._show_page(self._page - 1))
        self.next_btn = QPushButton("▶"); self.next_btn.clicked.connect(lambda: self._show_page(self._page + 1))
        self.page_label = QLabel("no report built yet"); self.page_label.setObjectName("muted")
        self.open_btn = QPushButton("Open folder")
        self.open_btn.clicked.connect(self._open_folder)
        self.pdf_btn = QPushButton("Open PDF")
        self.pdf_btn.clicked.connect(self._open_pdf)
        for w in (self.prev_btn, self.next_btn, self.page_label):
            nav_row.addWidget(w)
        nav_row.addStretch(1); nav_row.addWidget(self.open_btn); nav_row.addWidget(self.pdf_btn)
        right.body.addLayout(nav_row)
        self.scroll = QScrollArea(); self.scroll.setWidgetResizable(True); self.scroll.setObjectName("pageview")
        self.page_view = QLabel(); self.page_view.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.page_view.setAccessibleName("page preview")
        self.scroll.setWidget(self.page_view)
        right.body.addWidget(self.scroll, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 1); split.setStretchFactor(1, 2)
        split.setSizes([460, 900])
        self.body = overlay_host(split)
        root.addWidget(self.body, 1)
        self.empty_state = EmptyState("No project open: the report is built from the zones drawn in Reduce.",
                                      [("Open project…", lambda: nav.open_project_dialog(self)),
                                       ("Open the demo beamtime", lambda: nav.open_demo(self))], icon="report",
                                      parent=self.body)
        self.zone_state = EmptyState("No zone yet: the report is built from the zones drawn in Reduce.",
                                     [("Go to Reduce", lambda: nav.go(self, "reduce"))], icon="report", parent=self.body)
        self.groups.setAccessibleName("groups to include")
        explain_all([(self.browse_btn, "report.browse"), (self.appendix, "report.appendix"), (self.maps, "report.maps"),
                     (self.groups, "report.groups"), (self.all_btn, "report.all"), (self.none_btn, "report.none"),
                     (self.build_btn, "report.build"), (self.prev_btn, "report.prev"), (self.next_btn, "report.next"),
                     (self.open_btn, "report.open_folder"), (self.pdf_btn, "report.open_pdf")])
        session.project_changed.connect(self._project_changed)
        session.zones_changed.connect(self._fill_groups)
        session.zones_changed.connect(self.update_actions)
        self.groups.itemChanged.connect(self._scope_text)
        self.appendix.toggled.connect(self._scope_text)
        self.maps.toggled.connect(self._scope_text)
        # Easy mode: all groups and both appendices, stated read-only; the choices are Advanced
        mark_advanced(self.scope_box)
        mark_easy(self.scope_line)
        self._scope_text()
        self.update_actions()

    def has_zones(self) -> bool:
        return any(h.zones for h in self.session.zones.holders.values())

    def update_actions(self) -> None:
        has = self.session.project is not None
        busy = jobs.of(self).job("report") is not None
        zones = self.has_zones()
        set_enabled(self.build_btn, has and zones and not busy,
                    NO_PROJECT if not has else ("the report is being built" if busy else "draw a zone in Reduce first"))
        built = self._result is not None
        pages = len(self._result.page_pngs) if built else 0
        set_enabled(self.prev_btn, built and self._page > 0, "build the report first" if not built else "this is the first page")
        set_enabled(self.next_btn, built and self._page < pages - 1, "build the report first" if not built else "this is the last page")
        for b in (self.open_btn, self.pdf_btn):
            set_enabled(b, built, "build the report first")
        self.empty_state.set_shown(not has)
        self.zone_state.set_shown(has and not zones)

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
        self._result = None
        self._fill_groups()
        self.update_actions()

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
        cur = Path(self.out_dir.text()) if self.out_dir.text().strip() else None
        start = str(cur) if cur is not None and cur.is_dir() else default_dir("report", self.session)
        d = QFileDialog.getExistingDirectory(self, "Report output folder", start)
        if d:
            remember_dir("report", Path(d))
            self.out_dir.setText(d)

    # ------------------------------------------------------------------ build
    def build(self) -> None:
        if self.session.project is None:
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
        self.status.setText("building …")
        jobs.of(self).submit("report", lambda cb: builder.build(progress=cb), on_done=self._on_done,
                             on_failed=self._on_failed, on_end=lambda _s: self.update_actions(),
                             label="building the report", taskbar=self.taskbar, pause_gc=True)
        self.update_actions()

    def _on_done(self, result: ReportResult) -> None:
        self._result = result
        self.status.setText(f"{result.pdf.name}: {plural(result.pages, 'page')}, {plural(result.entries, 'zone spectrum', 'zone spectra')}, "
                            f"{len(result.csvs)} CSV, {plural(len(result.figures), 'figure')}"
                            + (f"; {len(result.skipped)} skipped" if result.skipped else ""))
        self._show_page(0)

    def _on_failed(self, text: str) -> None:
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
        self.update_actions()

    def _open_folder(self) -> None:
        if self._result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.pdf.parent)))

    def _open_pdf(self) -> None:
        if self._result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._result.pdf)))
