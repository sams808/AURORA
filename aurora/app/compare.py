"""Compare perspective (John Bussey's tab 3): the library table, the stacked plot, normalisation diagnostics, peaks."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from aurora.app.help_text import explain, header_tips, labelled
from aurora.app.library import Library
from aurora.app.mode import mark_advanced, mark_advanced_column
from aurora.app.theme import curve, dash, label_for, make_plot, on_theme_changed, role_pen, swatch_icon
from aurora.app.vocab import plural, unit_suffix
from aurora.app.widgets import (Card, ElidedLabel, EmptyState, FlowLayout, default_dir, default_path, guarded_write,
                                notify_saved, overlay_host, pair, remember_dir, set_enabled, set_plot_hint)
from aurora.core.compare import NORMS, derivative, difference, export_wide_csv, local_peaks, stack_offsets

COLS = ("Show", "Label", "Signal", "Shift eV", "Norm", "Pre lo", "Pre hi", "Post lo", "Post hi", "Flatten", "Spacing", "Scale", "Fit", "Diag")
COL_HELP = ("show", "label", "signal", "shift", "norm", "pre_lo", "pre_hi", "post_lo", "post_hi", "flatten", "spacing",
            "scale", "fit", "diag")
BOOL_COLS = (0, 9, 12, 13)
ADVANCED_COLS = (4, 5, 6, 7, 8, 9, 10, 11, 13)      # Norm, Pre / Post windows, Flatten, Spacing, Scale, Diag


class ComparePerspective(QWidget):
    def __init__(self, library: Library, parent: QWidget | None = None):
        super().__init__(parent)
        self.lib = library
        self._filling = False
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Compare"); h1.setObjectName("h1")
        sub = ElidedLabel("normalise, shift, stack and compare reduced spectra", Qt.ElideRight); sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub, 1)
        self.head_buttons: dict[str, QPushButton] = {}
        for text, fn, key in (("Add saved spectra…", self._add_folder, "add_folder"), ("Add CSV…", self._add_csv, "add_csv"),
                              ("References…", self._references, "references"),
                              ("Save config…", self._save_config, "save_config"),
                              ("Load config…", self._load_config, "load_config"), ("Export…", self._export, "export")):
            b = QPushButton(text); b.clicked.connect(fn); head.addWidget(b)
            explain(b, f"compare.{key}")
            self.head_buttons[text] = b
        self.export_btn = self.head_buttons["Export…"]
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        self.library_card = left = Card("Library", help_anchor="62-the-table")
        ops_box = QWidget()
        ops = FlowLayout(ops_box, spacing=6)
        self.op_buttons: dict[str, QPushButton] = {}
        for text, fn, key in (("Remove", self._remove_rows, "remove"), ("Remove sample", self._remove_sample, "remove_sample"),
                              ("Rename", self._rename, "rename"), ("Duplicate", lambda: self._row_op(self.lib.duplicate), "duplicate"),
                              ("▲", lambda: self._row_op(lambda r: self.lib.move(r, -1)), "up"),
                              ("▼", lambda: self._row_op(lambda r: self.lib.move(r, 1)), "down"),
                              ("Show all", lambda: self.lib.set_all("show", True), "show_all"),
                              ("Hide all", lambda: self.lib.set_all("show", False), "hide_all")):
            b = QPushButton(text); b.clicked.connect(fn)
            explain(b, f"compare.{key}")
            ops.addWidget(b)
            self.op_buttons[text] = b
        left.body.addWidget(ops_box)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setAccessibleName("library of spectra")
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemSelectionChanged.connect(self._draw_diagnostics)
        self.table.itemSelectionChanged.connect(self.update_actions)
        left.body.addWidget(self.table, 1)
        self.range_row = QWidget()                     # set pre / post on the selection: Advanced
        rng = FlowLayout(self.range_row, spacing=6)
        rng.addWidget(QLabel("windows of the selected rows, eV from E0:"))
        self.pre_lo = QDoubleSpinBox(); self.pre_lo.setRange(-200, 200); self.pre_lo.setValue(-20)
        self.pre_hi = QDoubleSpinBox(); self.pre_hi.setRange(-200, 200); self.pre_hi.setValue(-5)
        self.post_lo = QDoubleSpinBox(); self.post_lo.setRange(-200, 400); self.post_lo.setValue(15)
        self.post_hi = QDoubleSpinBox(); self.post_hi.setRange(-200, 400); self.post_hi.setValue(35)
        for w, key in ((self.pre_lo, "pre_lo"), (self.pre_hi, "pre_hi"), (self.post_lo, "post_lo"), (self.post_hi, "post_hi")):
            w.setSuffix(unit_suffix("eV"))
            explain(w, f"compare.{key}")
        pre_label, post_label = QLabel("pre"), QLabel("post")
        pre_label.setBuddy(self.pre_lo); post_label.setBuddy(self.post_lo)
        rng.addWidget(pair(pre_label, self.pre_lo, self.pre_hi))
        rng.addWidget(pair(post_label, self.post_lo, self.post_hi))
        self.apply_btn = b = QPushButton("Apply"); b.clicked.connect(self._apply_ranges); rng.addWidget(b)
        explain(b, "compare.apply")
        left.body.addWidget(self.range_row)
        split.addWidget(left)

        right = QSplitter(Qt.Vertical)
        pc = Card("Stacked plot", help_anchor="63-stacked-plot")
        opts_box = QWidget()
        opts = FlowLayout(opts_box, spacing=6)
        self.x_lo = QLineEdit(); self.x_lo.setPlaceholderText("auto"); self.x_lo.setMaximumWidth(80)
        self.x_hi = QLineEdit(); self.x_hi.setPlaceholderText("auto"); self.x_hi.setMaximumWidth(80)
        opts.addWidget(pair(labelled("x range (eV)", self.x_lo, "compare.x_lo"), self.x_lo, self.x_hi))
        explain(self.x_hi, "compare.x_hi")
        # one wrapping row for every plot option: the x range (Easy), then the Advanced ones, each marked
        self.spacing = QDoubleSpinBox(); self.spacing.setRange(0, 100); self.spacing.setSingleStep(0.1); self.spacing.setValue(0.6)
        spacing_pair = pair(labelled("spacing (signal units)", self.spacing, "compare.spacing"), self.spacing)
        self.labels_on = QCheckBox("labels"); self.labels_on.setChecked(True)
        self.signal_in_label = QCheckBox("signal in label")
        self.deriv = QCheckBox("first derivative")
        self.deriv_smooth = QDoubleSpinBox(); self.deriv_smooth.setRange(1, 51); self.deriv_smooth.setDecimals(0); self.deriv_smooth.setValue(3)
        self.deriv_smooth.setPrefix("smooth "); self.deriv_smooth.setSuffix(unit_suffix("points"))
        self.diff_btn = QPushButton("Difference of the two selected rows"); self.diff_btn.clicked.connect(self._difference)
        self.show_peaks = QCheckBox("mark peaks")
        self.prominence = QDoubleSpinBox(); self.prominence.setRange(0, 100); self.prominence.setDecimals(3); self.prominence.setSingleStep(0.01); self.prominence.setValue(0.05)
        prom_pair = pair(labelled("min prominence (signal units)", self.prominence, "compare.prominence"), self.prominence)
        self.distance = QDoubleSpinBox(); self.distance.setRange(1, 500); self.distance.setDecimals(0); self.distance.setValue(5)
        self.distance.setSuffix(unit_suffix("points"))
        dist_pair = pair(labelled("min distance", self.distance, "compare.distance"), self.distance)
        # Advanced: spacing, labels, derivative, difference; mark peaks with prominence and distance
        self.plot_opts = (spacing_pair, self.labels_on, self.signal_in_label, self.deriv, self.deriv_smooth, self.diff_btn)
        self.peaks_row = (self.show_peaks, prom_pair, dist_pair)
        for w in self.plot_opts + self.peaks_row:
            opts.addWidget(w)
        pc.body.addWidget(opts_box)
        self.plot = make_plot(x="incident energy (eV)", y="normalised, offset")
        self.plot.getViewBox().disableAutoRange()
        pc.body.addWidget(self.plot, 1)
        self.status = QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        pc.body.addWidget(self.status)
        right.addWidget(pc)
        dc = Card("Diagnostics of the selected row", help_anchor="64-diagnostics-and-export")
        self.diag = make_plot(x="incident energy (eV)", y="raw signal", legend="inside")
        self.diag.getViewBox().disableAutoRange()
        dc.body.addWidget(self.diag, 1)
        self.peaks_label = QLabel(""); self.peaks_label.setObjectName("muted"); self.peaks_label.setWordWrap(True)
        dc.body.addWidget(self.peaks_label)
        right.addWidget(dc)
        right.setSizes([560, 260])
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        dc.setMinimumHeight(200)
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([600, 900])
        self.body = overlay_host(split)
        root.addWidget(self.body, 1)
        self.empty_state = EmptyState("The library is empty: add the spectra saved by Check, pick references, or send a "
                                      "zone from Reduce.",
                                      [("Add saved spectra…", self._add_folder), ("References…", self._references, True)],
                                      icon="compare", parent=self.body)
        for w, key in ((self.labels_on, "labels"), (self.signal_in_label, "signal_in_label"), (self.deriv, "deriv"),
                       (self.deriv_smooth, "smooth"), (self.diff_btn, "difference"), (self.show_peaks, "peaks")):
            explain(w, f"compare.{key}")
        header_tips(self.table, {i: f"compare.col.{k}" for i, k in enumerate(COL_HELP)})
        for w in (self.x_lo, self.x_hi):
            w.editingFinished.connect(self.replot)
        self.spacing.valueChanged.connect(self.replot)
        for w in (self.labels_on, self.signal_in_label, self.show_peaks, self.deriv):
            w.toggled.connect(self.replot)
        self.prominence.valueChanged.connect(self.replot); self.distance.valueChanged.connect(self.replot)
        self.deriv_smooth.valueChanged.connect(self.replot)
        library.changed.connect(self._fill_table)
        library.changed.connect(self.replot)
        library.changed.connect(self.update_actions)
        on_theme_changed(self, lambda _s: (self._fill_table(), self.replot(), self._draw_diagnostics()))
        # Easy mode: John Bussey's Compare (saved spectra, show / fit / label / signal / shift, x range, export)
        h, o = self.head_buttons, self.op_buttons
        mark_advanced(h["Add CSV…"], h["References…"], h["Save config…"], h["Load config…"], o["Remove sample"],
                      o["Rename"], o["Duplicate"], self.range_row, *self.plot_opts, *self.peaks_row)
        mark_advanced_column(self.table, *ADVANCED_COLS)
        self.replot()
        self._draw_diagnostics()
        self.update_actions()

    def update_actions(self) -> None:
        has = bool(self.lib.requests)
        sel = self.selected_rows()
        shown = any(r.show for r in self.lib.requests)
        set_enabled(self.export_btn, shown, "add spectra and tick Show first")
        set_enabled(self.head_buttons["Save config…"], has, "the library is empty")
        for text in ("Remove", "Remove sample", "Rename", "Duplicate", "▲", "▼"):
            set_enabled(self.op_buttons[text], bool(sel), "select a row first" if has else "the library is empty")
        for text in ("Show all", "Hide all"):
            set_enabled(self.op_buttons[text], has, "the library is empty")
        set_enabled(self.diff_btn, len(sel) == 2, "select exactly two rows (Ctrl-click)")
        set_enabled(self.apply_btn, has, "the library is empty")
        self.empty_state.set_shown(not has)

    # ------------------------------------------------------------------ table
    def _fill_table(self) -> None:
        self._filling = True
        self.table.setRowCount(len(self.lib.requests))
        for i, r in enumerate(self.lib.requests):
            vals = [r.show, r.label, r.signal, f"{r.shift:g}", r.norm, f"{r.pre[0]:g}", f"{r.pre[1]:g}", f"{r.post[0]:g}",
                    f"{r.post[1]:g}", r.flatten, "" if r.spacing_after is None else f"{r.spacing_after:g}", f"{r.scale:g}", r.fit, r.diagnostics]
            for c, v in enumerate(vals):
                it = QTableWidgetItem("" if c in BOOL_COLS else str(v))
                if c in BOOL_COLS:
                    it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    it.setCheckState(Qt.Checked if v else Qt.Unchecked)
                if c == 1:                                 # ink text plus the series swatch (never coloured text)
                    it.setIcon(swatch_icon(r.colour or curve(i), dash(i)))
                self.table.setItem(i, c, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._filling = False

    def _item_changed(self, it: QTableWidgetItem) -> None:
        if self._filling:
            return
        row, col = it.row(), it.column()
        if row >= len(self.lib.requests):
            return
        r = self.lib.requests[row]
        txt = it.text().strip()
        try:
            if col == 0:
                r.show = it.checkState() == Qt.Checked
            elif col == 1 and txt and txt != r.label:
                if not self.lib.rename(r.label, txt):
                    self.status.setText(f"a sample named {txt} already exists")
                return
            elif col == 2 and txt in self.lib.signals_of(r.label):
                r.signal = txt
            elif col == 3:
                r.shift = float(txt or 0)
            elif col == 4 and txt in NORMS:
                r.norm = txt
            elif col in (5, 6):
                r.pre = (float(txt), r.pre[1]) if col == 5 else (r.pre[0], float(txt))
            elif col in (7, 8):
                r.post = (float(txt), r.post[1]) if col == 7 else (r.post[0], float(txt))
            elif col == 9:
                r.flatten = it.checkState() == Qt.Checked
            elif col == 10:
                r.spacing_after = float(txt) if txt else None
            elif col == 11:
                r.scale = float(txt or 1)
            elif col == 12:
                r.fit = it.checkState() == Qt.Checked
            elif col == 13:
                r.diagnostics = it.checkState() == Qt.Checked
        except ValueError:
            self.status.setText(f"could not read '{txt}'")
        self.replot()
        self._draw_diagnostics()
        self.update_actions()

    def selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def _row_op(self, fn) -> None:
        rows = self.selected_rows()
        if rows:
            fn(rows[0])

    def _remove_rows(self) -> None:
        rows = self.selected_rows()
        if rows:
            self.lib.remove(rows)

    def _remove_sample(self) -> None:
        for lab in {self.lib.requests[i].label for i in self.selected_rows() if i < len(self.lib.requests)}:
            self.lib.remove_label(lab)

    def _rename(self) -> None:
        rows = self.selected_rows()
        if not rows:
            return
        old = self.lib.requests[rows[0]].label
        new, ok = QInputDialog.getText(self, "Rename sample", "New label:", text=old)
        if ok and new.strip() and not self.lib.rename(old, new.strip()):
            QMessageBox.information(self, "Rename", f"A sample named {new} already exists.")

    def _apply_ranges(self) -> None:
        rows = self.selected_rows() or None
        self.lib.set_all("pre", (self.pre_lo.value(), self.pre_hi.value()), rows)
        self.lib.set_all("post", (self.post_lo.value(), self.post_hi.value()), rows)

    # ------------------------------------------------------------------ files
    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder of saved spectra (*_full_reduced.csv, *_xanes.csv, AURORA zone CSVs)",
                                             default_dir("reduced", self.lib))
        if d:
            remember_dir("reduced", Path(d))
            n = len(self.lib.add_folder(Path(d)))
            self.status.setText(f"{plural(n, 'trace')} added from {d}" if n else f"no reduced CSVs found in {d}")

    def _add_csv(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add CSV / text spectra", default_dir("csv", self.lib),
                                                "Spectra (*.csv *.txt *.dat *.nor);;All (*.*)")
        n = 0
        for f in files:
            try:
                n += len(self.lib.add_file(Path(f)))
            except ValueError as err:
                self.status.setText(str(err))
        if files:
            remember_dir("csv", files[0])
        if n:
            self.status.setText(f"{plural(n, 'trace')} added")

    def _references(self) -> None:
        from aurora.app.dialogs import ReferencesDialog
        from aurora.app.settings_dialog import get as setting
        d = ReferencesDialog(setting("reference_dir"), "", self)
        if d.exec():
            n = 0
            for p, label in d.chosen():
                try:
                    n += len(self.lib.add_file(p, label))
                except ValueError as err:
                    self.status.setText(str(err))
            self.status.setText(f"{plural(n, 'reference trace')} added")

    def _save_config(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Save compare config", default_path("config", "compare_config.json", self.lib),
                                           "JSON (*.json)")
        if p:
            remember_dir("config", p)
            with guarded_write(self, Path(p).name) as g:
                self.lib.save(Path(p), {"x_range": self._xrange(), "spacing": self.spacing.value()})
            if g.ok:
                notify_saved(self.status, [Path(p)])

    def _load_config(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Load compare config", default_path("config", "compare_config.json", self.lib),
                                           "JSON (*.json)")
        if p:
            remember_dir("config", p)
            try:
                missing, extra = self.lib.load(Path(p))
            except (OSError, ValueError) as err:
                QMessageBox.warning(self, "Load config", f"Could not read {Path(p).name}: {err}")
                return
            if "spacing" in extra:
                self.spacing.setValue(float(extra["spacing"]))
            if missing:
                QMessageBox.information(self, "Missing files", "Not found:\n" + "\n".join(missing))

    def _export(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Export comparison (.csv, .png, .svg next to it)",
                                           default_path("compare", "comparison.csv", self.lib), "CSV (*.csv)")
        if not p:
            return
        remember_dir("compare", p)
        with guarded_write(self, Path(p).name) as g:
            out = self.export(Path(p))
        if g.ok:
            notify_saved(self.status, out)

    def export(self, path: Path) -> list[Path]:
        traces = [(r, nr.y * 0 + s.E + r.shift, nr.y) for r, s, nr in self.lib.processed(lambda r: r.show)]
        export_wide_csv(path, [(r, E, y) for r, E, y in traces])
        out = [path]
        try:
            from pyqtgraph.exporters import ImageExporter, SVGExporter
            ImageExporter(self.plot.plotItem).export(str(path.with_suffix(".png")))
            SVGExporter(self.plot.plotItem).export(str(path.with_suffix(".svg")))
            out += [path.with_suffix(".png"), path.with_suffix(".svg")]
        except Exception as err:
            self.status.setText(f"figure export failed: {err}")
        cfg = path.with_name(path.stem + "_config.json")
        self.lib.save(cfg, {"x_range": self._xrange(), "spacing": self.spacing.value()})
        out.append(cfg)
        self.status.setText("exported " + ", ".join(p.name for p in out))
        return out

    # ------------------------------------------------------------------ plot
    def _xrange(self):
        try:
            return [float(self.x_lo.text()), float(self.x_hi.text())]
        except ValueError:
            return None

    def shown(self):
        return self.lib.processed(lambda r: r.show)

    def replot(self) -> None:
        self.plot.clear()
        items = self.shown()
        if not items:
            self.status.setText("add saved spectra, or send a zone from Reduce")
            set_plot_hint(self.plot, "tick Show on a row to plot it" if self.lib.requests else "no spectra yet")
            self.update_actions()
            return
        set_plot_hint(self.plot, None)
        offsets = stack_offsets([r for r, _, _ in items], self.spacing.value())
        xr = self._xrange()
        xs, ys, failed, peaks_txt = [], [], [], []
        rows = {id(r): k for k, r in enumerate(self.lib.requests)}
        for (r, s, nr), off in zip(items, offsets):
            i = rows.get(id(r), 0)                      # the table row: same colour and dash as its swatch
            E = s.E + r.shift
            base = derivative(E, nr.y, int(self.deriv_smooth.value())) if self.deriv.isChecked() else nr.y
            y = base + off
            if not np.isfinite(base).any():
                failed.append(r.label); continue
            colour = r.colour or curve(i)
            pen = pg.mkPen(colour, width=1.4, style=dash(i))
            self.plot.plot(E, y, pen=pen, connect="finite")
            sel = np.isfinite(y) & ((E >= xr[0]) & (E <= xr[1]) if xr else True)
            if sel.any():
                xs += [float(E[sel].min()), float(E[sel].max())]; ys.append(y[sel])
                if self.labels_on.isChecked():
                    t = pg.TextItem(r.label + (f" ({r.signal})" if self.signal_in_label.isChecked() else ""), color=label_for(colour), anchor=(1, 0.5))
                    t.setPos(float(E[sel].max()), float(y[sel][-1]))
                    self.plot.addItem(t, ignoreBounds=True)
            if self.show_peaks.isChecked():
                pk = local_peaks(E, base, self.prominence.value(), int(self.distance.value()))
                if pk:
                    self.plot.addItem(pg.ScatterPlotItem([p[0] for p in pk], [p[1] + off for p in pk], size=7, brush=pg.mkBrush(colour), pen=None), ignoreBounds=True)
                    peaks_txt.append(f"{r.label}: " + ", ".join(f"{p[0]:.1f}" for p in pk))
        if xs and ys:
            v = np.concatenate(ys)
            self.plot.getViewBox().setRange(xRange=(xr[0], xr[1]) if xr else (min(xs), max(xs)),
                                            yRange=(float(np.nanmin(v)), float(np.nanmax(v))), padding=0.05, disableAutoRange=True)
        self.plot.setLabel("left", "d/dE of the normalised signal, offset" if self.deriv.isChecked() else "normalised, offset")
        self.status.setText(f"{plural(len(items) - len(failed), 'trace')} plotted" + (f"; normalisation failed (all NaN) for: {', '.join(failed)} — adjust the ranges" if failed else ""))
        self.peaks_label.setText("peak maxima (shifted energies): " + " · ".join(peaks_txt) if peaks_txt else "")
        self.update_actions()

    def _difference(self) -> None:
        rows = self.selected_rows()
        if len(rows) != 2:
            self.status.setText("select exactly two rows (Ctrl-click) for a difference"); return
        ra, rb = self.lib.requests[rows[0]], self.lib.requests[rows[1]]
        sa, sb = self.lib.spectrum_of(ra), self.lib.spectrum_of(rb)
        if sa is None or sb is None:
            return
        from aurora.core.compare import process
        d = difference(sa, sb, process(sa, ra).y, process(sb, rb).y, ra.shift, rb.shift)
        self.lib.add_spectra([d], show_signals=("difference",), norm="none")
        self.status.setText(f"added {d.label} (normalised A minus normalised B, no further normalisation)")

    def _draw_diagnostics(self) -> None:
        self.diag.clear()
        if self.diag.plotItem.legend is not None:
            self.diag.plotItem.legend.clear()
        rows = self.selected_rows()
        r = self.lib.requests[rows[0]] if rows and rows[0] < len(self.lib.requests) else None
        s = self.lib.spectrum_of(r) if r is not None else None
        if s is None:
            self.diag.setTitle("")
            set_plot_hint(self.diag, "select a row to see its raw signal and fitted lines")
            return
        set_plot_hint(self.diag, None)
        from aurora.core.compare import process
        nr = process(s, r)
        E = s.E + r.shift
        self.diag.plot(E, s.y, pen=role_pen("raw", 1.4), connect="finite", name="raw signal")
        if nr.pre_line is not None:
            self.diag.plot(E, nr.pre_line, pen=role_pen("pre", 1.2), name="pre-edge line")
        if nr.post_line is not None:
            self.diag.plot(E, nr.post_line, pen=role_pen("post", 1.2), name="post-edge line")
        if np.isfinite(nr.e0):
            self.diag.addItem(pg.InfiniteLine(nr.e0, angle=90, pen=role_pen("e0", 1.2)), ignoreBounds=True)
        fin = np.isfinite(s.y)
        if fin.any():
            self.diag.getViewBox().setRange(xRange=(float(E[fin].min()), float(E[fin].max())),
                                            yRange=(float(s.y[fin].min()), float(s.y[fin].max())), padding=0.05, disableAutoRange=True)
        self.diag.setTitle(f"{r.label} [{r.signal}] · {r.norm} · E0 {nr.e0:.2f} eV · step {nr.step:.3g}" + (f" · {nr.note}" if nr.note else ""))
