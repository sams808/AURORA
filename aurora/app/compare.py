"""Compare perspective (John Bussey's tab 3): the library table, the stacked plot, normalisation diagnostics, peaks."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from aurora.app.library import Library
from aurora.app.preview import DASH
from aurora.app.widgets import Card
from aurora.core.compare import NORMS, derivative, difference, export_wide_csv, local_peaks, stack_offsets

COLS = ("Show", "Label", "Signal", "Shift eV", "Norm", "Pre lo", "Pre hi", "Post lo", "Post hi", "Flatten", "Spacing", "Scale", "Fit", "Diag")
BOOL_COLS = (0, 9, 12, 13)


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
        sub = QLabel("normalise, shift, stack and compare reduced spectra"); sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub); head.addStretch(1)
        for text, fn in (("Add reduced folder…", self._add_folder), ("Add CSV…", self._add_csv),
                         ("Save config…", self._save_config), ("Load config…", self._load_config), ("Export…", self._export)):
            b = QPushButton(text); b.clicked.connect(fn); head.addWidget(b)
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        left = Card("Library")
        ops = QHBoxLayout()
        for text, fn in (("Remove row", self._remove_rows), ("Remove sample", self._remove_sample), ("Rename", self._rename),
                         ("Duplicate", lambda: self._row_op(self.lib.duplicate)), ("▲", lambda: self._row_op(lambda r: self.lib.move(r, -1))),
                         ("▼", lambda: self._row_op(lambda r: self.lib.move(r, 1))), ("Show all", lambda: self.lib.set_all("show", True)),
                         ("Hide all", lambda: self.lib.set_all("show", False))):
            b = QPushButton(text); b.clicked.connect(fn)
            if len(text) == 1:
                b.setMaximumWidth(34)
            ops.addWidget(b)
        ops.addStretch(1)
        left.body.addLayout(ops)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemSelectionChanged.connect(self._draw_diagnostics)
        left.body.addWidget(self.table, 1)
        rng = QHBoxLayout()
        rng.addWidget(QLabel("set pre/post (eV from E0) on selected rows:"))
        self.pre_lo = QDoubleSpinBox(); self.pre_lo.setRange(-200, 200); self.pre_lo.setValue(-20)
        self.pre_hi = QDoubleSpinBox(); self.pre_hi.setRange(-200, 200); self.pre_hi.setValue(-5)
        self.post_lo = QDoubleSpinBox(); self.post_lo.setRange(-200, 400); self.post_lo.setValue(15)
        self.post_hi = QDoubleSpinBox(); self.post_hi.setRange(-200, 400); self.post_hi.setValue(35)
        for w in (self.pre_lo, self.pre_hi, self.post_lo, self.post_hi):
            rng.addWidget(w)
        b = QPushButton("Apply"); b.clicked.connect(self._apply_ranges); rng.addWidget(b)
        rng.addStretch(1)
        left.body.addLayout(rng)
        split.addWidget(left)

        right = QSplitter(Qt.Vertical)
        pc = Card("Stacked plot")
        opts = QHBoxLayout()
        opts.addWidget(QLabel("x range"))
        self.x_lo = QLineEdit(); self.x_lo.setPlaceholderText("auto"); self.x_lo.setMaximumWidth(70)
        self.x_hi = QLineEdit(); self.x_hi.setPlaceholderText("auto"); self.x_hi.setMaximumWidth(70)
        opts.addWidget(self.x_lo); opts.addWidget(self.x_hi)
        opts.addWidget(QLabel("spacing"))
        self.spacing = QDoubleSpinBox(); self.spacing.setRange(0, 100); self.spacing.setSingleStep(0.1); self.spacing.setValue(0.6)
        opts.addWidget(self.spacing)
        self.labels_on = QCheckBox("labels"); self.labels_on.setChecked(True); opts.addWidget(self.labels_on)
        self.signal_in_label = QCheckBox("signal in label"); opts.addWidget(self.signal_in_label)
        self.deriv = QCheckBox("first derivative"); opts.addWidget(self.deriv)
        self.deriv_smooth = QDoubleSpinBox(); self.deriv_smooth.setRange(1, 51); self.deriv_smooth.setDecimals(0); self.deriv_smooth.setValue(3)
        self.deriv_smooth.setPrefix("smooth "); self.deriv_smooth.setSuffix(" pts"); opts.addWidget(self.deriv_smooth)
        self.diff_btn = QPushButton("Difference of the two selected rows"); self.diff_btn.clicked.connect(self._difference); opts.addWidget(self.diff_btn)
        opts.addStretch(1)
        pc.body.addLayout(opts)
        pk = QHBoxLayout()
        self.show_peaks = QCheckBox("mark peaks"); pk.addWidget(self.show_peaks)
        pk.addWidget(QLabel("min prominence"))
        self.prominence = QDoubleSpinBox(); self.prominence.setRange(0, 100); self.prominence.setDecimals(3); self.prominence.setSingleStep(0.01); self.prominence.setValue(0.05)
        pk.addWidget(self.prominence)
        pk.addWidget(QLabel("min distance (points)"))
        self.distance = QDoubleSpinBox(); self.distance.setRange(1, 500); self.distance.setDecimals(0); self.distance.setValue(5)
        pk.addWidget(self.distance)
        pk.addStretch(1)
        pc.body.addLayout(pk)
        self.plot = pg.PlotWidget(background="w")
        self.plot.setLabel("bottom", "incident energy (eV)"); self.plot.setLabel("left", "normalised, offset")
        self.plot.getViewBox().disableAutoRange()
        pc.body.addWidget(self.plot, 1)
        self.status = QLabel(""); self.status.setObjectName("muted"); self.status.setWordWrap(True)
        pc.body.addWidget(self.status)
        right.addWidget(pc)
        dc = Card("Diagnostics of the selected row")
        self.diag = pg.PlotWidget(background="w")
        self.diag.setLabel("bottom", "incident energy (eV)"); self.diag.setLabel("left", "raw signal")
        self.diag.addLegend(offset=(10, 10)); self.diag.getViewBox().disableAutoRange()
        dc.body.addWidget(self.diag, 1)
        self.peaks_label = QLabel(""); self.peaks_label.setObjectName("muted"); self.peaks_label.setWordWrap(True)
        dc.body.addWidget(self.peaks_label)
        right.addWidget(dc)
        right.setSizes([520, 300])
        split.addWidget(right)
        split.setSizes([760, 760])
        root.addWidget(split, 1)
        for w in (self.x_lo, self.x_hi):
            w.editingFinished.connect(self.replot)
        self.spacing.valueChanged.connect(self.replot)
        for w in (self.labels_on, self.signal_in_label, self.show_peaks, self.deriv):
            w.toggled.connect(self.replot)
        self.prominence.valueChanged.connect(self.replot); self.distance.valueChanged.connect(self.replot)
        self.deriv_smooth.valueChanged.connect(self.replot)
        library.changed.connect(self._fill_table)
        library.changed.connect(self.replot)

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
                if c == 1:
                    from PySide6.QtGui import QBrush, QColor
                    it.setForeground(QBrush(QColor(r.colour or "#000000")))
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
        d = QFileDialog.getExistingDirectory(self, "Folder with reduced CSVs (*_full_reduced.csv, *_xanes.csv, AURORA zone CSVs)")
        if d:
            n = len(self.lib.add_folder(Path(d)))
            self.status.setText(f"{n} traces added from {d}" if n else f"no reduced CSVs found in {d}")

    def _add_csv(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add CSV / text spectra", "", "Spectra (*.csv *.txt *.dat *.nor);;All (*.*)")
        n = 0
        for f in files:
            try:
                n += len(self.lib.add_file(Path(f)))
            except ValueError as err:
                self.status.setText(str(err))
        if n:
            self.status.setText(f"{n} traces added")

    def _save_config(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Save compare config", "compare_config.json", "JSON (*.json)")
        if p:
            self.lib.save(Path(p), {"x_range": self._xrange(), "spacing": self.spacing.value()})

    def _load_config(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Load compare config", "", "JSON (*.json)")
        if p:
            missing, extra = self.lib.load(Path(p))
            if "spacing" in extra:
                self.spacing.setValue(float(extra["spacing"]))
            if missing:
                QMessageBox.information(self, "Missing files", "Not found:\n" + "\n".join(missing))

    def _export(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Export comparison (.csv, .png, .svg next to it)", "comparison.csv", "CSV (*.csv)")
        if not p:
            return
        self.export(Path(p))

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
        self.lib.save(path.with_name(path.stem + "_config.json"), {"x_range": self._xrange(), "spacing": self.spacing.value()})
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
            self.status.setText("add reduced CSVs, or send a zone from Reduce")
            return
        offsets = stack_offsets([r for r, _, _ in items], self.spacing.value())
        xr = self._xrange()
        xs, ys, failed, peaks_txt = [], [], [], []
        for i, ((r, s, nr), off) in enumerate(zip(items, offsets)):
            E = s.E + r.shift
            base = derivative(E, nr.y, int(self.deriv_smooth.value())) if self.deriv.isChecked() else nr.y
            y = base + off
            if not np.isfinite(base).any():
                failed.append(r.label); continue
            pen = pg.mkPen(r.colour or "#000000", width=1.4, style=DASH[i % len(DASH)])
            self.plot.plot(E, y, pen=pen, connect="finite")
            sel = np.isfinite(y) & ((E >= xr[0]) & (E <= xr[1]) if xr else True)
            if sel.any():
                xs += [float(E[sel].min()), float(E[sel].max())]; ys.append(y[sel])
                if self.labels_on.isChecked():
                    t = pg.TextItem(r.label + (f" ({r.signal})" if self.signal_in_label.isChecked() else ""), color=r.colour or "#000000", anchor=(1, 0.5))
                    t.setPos(float(E[sel].max()), float(y[sel][-1]))
                    self.plot.addItem(t, ignoreBounds=True)
            if self.show_peaks.isChecked():
                pk = local_peaks(E, base, self.prominence.value(), int(self.distance.value()))
                if pk:
                    self.plot.addItem(pg.ScatterPlotItem([p[0] for p in pk], [p[1] + off for p in pk], size=7, brush=pg.mkBrush(r.colour or "#000"), pen=None), ignoreBounds=True)
                    peaks_txt.append(f"{r.label}: " + ", ".join(f"{p[0]:.1f}" for p in pk))
        if xs and ys:
            v = np.concatenate(ys)
            self.plot.getViewBox().setRange(xRange=(xr[0], xr[1]) if xr else (min(xs), max(xs)),
                                            yRange=(float(np.nanmin(v)), float(np.nanmax(v))), padding=0.05, disableAutoRange=True)
        self.plot.setLabel("left", "d/dE of the normalised signal, offset" if self.deriv.isChecked() else "normalised, offset")
        self.status.setText(f"{len(items) - len(failed)} traces plotted" + (f"; normalisation failed (all NaN) for: {', '.join(failed)} — adjust the ranges" if failed else ""))
        self.peaks_label.setText("peak maxima (shifted energies): " + " · ".join(peaks_txt) if peaks_txt else "")

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
        self.peaks_label.setText("peak maxima (shifted energies): " + " · ".join(peaks_txt) if peaks_txt else "")

    def _draw_diagnostics(self) -> None:
        self.diag.clear()
        if self.diag.plotItem.legend is not None:
            self.diag.plotItem.legend.clear()
        rows = self.selected_rows()
        if not rows or rows[0] >= len(self.lib.requests):
            return
        r = self.lib.requests[rows[0]]
        s = self.lib.spectrum_of(r)
        if s is None:
            return
        from aurora.core.compare import process
        nr = process(s, r)
        E = s.E + r.shift
        self.diag.plot(E, s.y, pen=pg.mkPen("#15213B", width=1.4), connect="finite", name="raw signal")
        if nr.pre_line is not None:
            self.diag.plot(E, nr.pre_line, pen=pg.mkPen("#E69F00", style=Qt.DashLine), name="pre-edge line")
        if nr.post_line is not None:
            self.diag.plot(E, nr.post_line, pen=pg.mkPen("#0072B2", style=Qt.DashLine), name="post-edge line")
        if np.isfinite(nr.e0):
            self.diag.addItem(pg.InfiniteLine(nr.e0, angle=90, pen=pg.mkPen("#A78BFA", style=Qt.DotLine)), ignoreBounds=True)
        fin = np.isfinite(s.y)
        if fin.any():
            self.diag.getViewBox().setRange(xRange=(float(E[fin].min()), float(E[fin].max())),
                                            yRange=(float(s.y[fin].min()), float(s.y[fin].max())), padding=0.05, disableAutoRange=True)
        self.diag.setTitle(f"{r.label} [{r.signal}] · {r.norm} · E0 {nr.e0:.2f} eV · step {nr.step:.3g}" + (f" · {nr.note}" if nr.note else ""))
