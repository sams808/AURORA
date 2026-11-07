"""Fit perspective (John Bussey's tabs 4-6): LCF, PCA + MCR-ALS, ITFA on the rows ticked 'Fit' in Compare."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from aurora.app.library import Library
from aurora.app.preview import DASH, PAL
from aurora.app.widgets import Card
from aurora.core.fit import LcfResult, McrResult, PcaResult, itfa, lcf, mcr_als, pca, prepare_matrix


def _set_range(plot, xs, ys) -> None:
    v = np.concatenate([np.asarray(y, float) for y in ys]) if ys else np.array([])
    v = v[np.isfinite(v)]
    if xs and v.size:
        plot.getViewBox().setRange(xRange=(min(xs), max(xs)), yRange=(float(v.min()), float(v.max()) if v.max() > v.min() else float(v.min()) + 1),
                                   padding=0.05, disableAutoRange=True)


class FitPerspective(QWidget):
    def __init__(self, library: Library, parent: QWidget | None = None):
        super().__init__(parent)
        self.lib = library
        self.lcf_result: LcfResult | None = None
        self.pca_result: PcaResult | None = None
        self.mcr_result: McrResult | None = None
        self.itfa_result: McrResult | None = None
        self._mcr_labels: list[str] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Fit"); h1.setObjectName("h1")
        sub = QLabel("rows ticked 'Fit' in Compare, with their shifts and normalisation"); sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub); head.addStretch(1)
        root.addLayout(head)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._lcf_tab(), "LCF")
        self.tabs.addTab(self._mcr_tab(), "PCA / MCR-ALS")
        self.tabs.addTab(self._itfa_tab(), "ITFA")
        self.tabs.addTab(self._peaks_tab(), "Peaks")
        root.addWidget(self.tabs, 1)
        library.changed.connect(self.refresh)

    # ------------------------------------------------------------------ data
    def fit_rows(self):
        return self.lib.processed(lambda r: r.fit)

    def curves(self):
        """[(label, E shifted, y normalised)] of the Fit rows."""
        return [(f"{r.label} [{r.signal}]", s.E + r.shift, nr.y) for r, s, nr in self.fit_rows() if np.isfinite(nr.y).any()]

    def _range(self, lo: QLineEdit, hi: QLineEdit):
        try:
            return (float(lo.text()), float(hi.text()))
        except ValueError:
            return None

    def refresh(self) -> None:
        names = [c[0] for c in self.curves()]
        cur = self.target.currentText()
        self.target.blockSignals(True); self.target.clear(); self.target.addItems(names)
        if cur in names:
            self.target.setCurrentText(cur)
        self.target.blockSignals(False)
        checked = {self.components.item(i).text() for i in range(self.components.count()) if self.components.item(i).checkState() == Qt.Checked}
        self.components.clear()
        for n in names:
            it = QListWidgetItem(n); it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if (n in checked or not checked) else Qt.Unchecked)
            self.components.addItem(it)
        self.mcr_info.setText(f"{len(names)} spectra ticked for the fits in Compare")
        cur = self.pk_trace.currentText(); self.pk_trace.blockSignals(True); self.pk_trace.clear(); self.pk_trace.addItems(names)
        if cur in names:
            self.pk_trace.setCurrentText(cur)
        self.pk_trace.blockSignals(False)
        self.itfa_sample.clear(); self.itfa_sample.addItems(names)

    # ------------------------------------------------------------------ LCF
    def _lcf_tab(self) -> QWidget:
        w = QWidget(); lay = QHBoxLayout(w)
        left = Card("Linear combination")
        left.body.addWidget(QLabel("target"))
        self.target = QComboBox(); left.body.addWidget(self.target)
        left.body.addWidget(QLabel("components (tick)"))
        self.components = QListWidget(); left.body.addWidget(self.components, 1)
        rng = QHBoxLayout(); rng.addWidget(QLabel("fit range (eV)"))
        self.lcf_lo = QLineEdit(); self.lcf_lo.setPlaceholderText("auto"); self.lcf_hi = QLineEdit(); self.lcf_hi.setPlaceholderText("auto")
        rng.addWidget(self.lcf_lo); rng.addWidget(self.lcf_hi); left.body.addLayout(rng)
        self.sum_one = QCheckBox("force coefficients to sum to 1"); self.sum_one.setChecked(True); left.body.addWidget(self.sum_one)
        self.allow_neg = QCheckBox("allow negative coefficients"); left.body.addWidget(self.allow_neg)
        bs = QHBoxLayout(); bs.addWidget(QLabel("bootstrap draws")); self.bootstrap = QSpinBox(); self.bootstrap.setRange(0, 5000); self.bootstrap.setValue(200); bs.addWidget(self.bootstrap); bs.addStretch(1)
        left.body.addLayout(bs)
        b = QPushButton("Run LCF"); b.setObjectName("primary"); b.clicked.connect(self.run_lcf); left.body.addWidget(b)
        self.lcf_table = QTableWidget(0, 3); self.lcf_table.setHorizontalHeaderLabels(["component", "coefficient", "± (bootstrap)"])
        left.body.addWidget(self.lcf_table, 1)
        self.lcf_info = QLabel(""); self.lcf_info.setObjectName("muted"); self.lcf_info.setWordWrap(True); left.body.addWidget(self.lcf_info)
        eb = QPushButton("Export figure + coefficients…"); eb.clicked.connect(self._export_lcf); left.body.addWidget(eb)
        lay.addWidget(left, 1)
        pc = Card("Target, fit and residual")
        self.lcf_plot = pg.PlotWidget(background="w"); self.lcf_plot.addLegend(offset=(10, 10)); self.lcf_plot.getViewBox().disableAutoRange()
        self.lcf_plot.setLabel("bottom", "energy (eV)")
        pc.body.addWidget(self.lcf_plot, 1)
        lay.addWidget(pc, 2)
        return w

    def run_lcf(self) -> None:
        curves = {n: (E, y) for n, E, y in self.curves()}
        tname = self.target.currentText()
        comps = [self.components.item(i).text() for i in range(self.components.count()) if self.components.item(i).checkState() == Qt.Checked and self.components.item(i).text() != tname]
        if tname not in curves:
            self.lcf_info.setText("add spectra in Compare and tick 'Fit' first"); return
        if not comps:
            self.lcf_info.setText("tick at least one component (other than the target)"); return
        try:
            r = lcf(curves[tname], [curves[c] for c in comps], self._range(self.lcf_lo, self.lcf_hi), self.sum_one.isChecked(),
                    self.allow_neg.isChecked(), self.bootstrap.value())
        except ValueError as err:
            self.lcf_info.setText(str(err)); return
        self.lcf_result = r; self._lcf_names = (tname, comps)
        self.lcf_table.setRowCount(len(comps))
        for i, c in enumerate(comps):
            self.lcf_table.setItem(i, 0, QTableWidgetItem(c)); self.lcf_table.setItem(i, 1, QTableWidgetItem(f"{r.coefficients[i]:.4f}"))
            self.lcf_table.setItem(i, 2, QTableWidgetItem(f"{r.uncertainties[i]:.4f}" if np.isfinite(r.uncertainties[i]) else "-"))
        self.lcf_table.resizeColumnsToContents()
        self.lcf_info.setText(f"RMSE {r.rmse:.4g} · R-factor {r.r_factor:.3g} · sum of coefficients {r.total:.3f} · {r.n_points} points · "
                              f"{r.E[0]:.1f}-{r.E[-1]:.1f} eV")
        self.lcf_plot.clear(); self.lcf_plot.plotItem.legend.clear()
        self.lcf_plot.plot(r.E, r.target, pen=pg.mkPen("#15213B", width=2), name=f"target {tname}")
        self.lcf_plot.plot(r.E, r.fit, pen=pg.mkPen("#D55E00", width=1.6, style=Qt.DashLine), name="linear combination")
        for i, c in enumerate(comps):
            self.lcf_plot.plot(r.E, r.coefficients[i] * r.components[i], pen=pg.mkPen(PAL[i % len(PAL)], width=1, style=DASH[i % len(DASH)]), name=f"{r.coefficients[i]:.2f} x {c}")
        resid = r.target - r.fit - 0.2 * (np.nanmax(r.target) - np.nanmin(r.target))
        self.lcf_plot.plot(r.E, resid, pen=pg.mkPen("#5B6B8C", width=1), name="residual (offset)")
        _set_range(self.lcf_plot, [float(r.E[0]), float(r.E[-1])], [r.target, r.fit, resid])

    def _export_lcf(self) -> None:
        if self.lcf_result is None:
            self.lcf_info.setText("run LCF first"); return
        p, _ = QFileDialog.getSaveFileName(self, "Export LCF", "lcf_coefficients.csv", "CSV (*.csv)")
        if p:
            self.export_lcf(Path(p))

    def export_lcf(self, path: Path) -> None:
        r = self.lcf_result; tname, comps = self._lcf_names
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"# LCF of {tname}; RMSE {r.rmse:.6g}; R-factor {r.r_factor:.6g}; sum {r.total:.6g}; range {r.E[0]:.2f}-{r.E[-1]:.2f} eV\n")
            f.write("component,coefficient,uncertainty\n")
            for c, v, u in zip(comps, r.coefficients, r.uncertainties):
                f.write(f"{c},{v:.6g},{u:.6g}\n")
            f.write("\nenergy_eV,target,fit,residual," + ",".join(comps) + "\n")
            for i in range(len(r.E)):
                f.write(f"{r.E[i]:.6g},{r.target[i]:.6g},{r.fit[i]:.6g},{r.target[i] - r.fit[i]:.6g}," + ",".join(f"{r.coefficients[k] * r.components[k][i]:.6g}" for k in range(len(comps))) + "\n")
        try:
            from pyqtgraph.exporters import SVGExporter
            SVGExporter(self.lcf_plot.plotItem).export(str(path.with_suffix(".svg")))
        except Exception:
            pass

    # ------------------------------------------------------------------ PCA / MCR-ALS
    def _mcr_tab(self) -> QWidget:
        w = QWidget(); lay = QHBoxLayout(w)
        left = Card("MCR-ALS")
        self.mcr_info = QLabel(""); self.mcr_info.setObjectName("muted"); left.body.addWidget(self.mcr_info)
        rng = QHBoxLayout(); rng.addWidget(QLabel("range (eV)"))
        self.mcr_lo = QLineEdit(); self.mcr_lo.setPlaceholderText("auto"); self.mcr_hi = QLineEdit(); self.mcr_hi.setPlaceholderText("auto")
        rng.addWidget(self.mcr_lo); rng.addWidget(self.mcr_hi); left.body.addLayout(rng)
        row = QHBoxLayout(); row.addWidget(QLabel("components"))
        self.n_comp = QSpinBox(); self.n_comp.setRange(1, 10); self.n_comp.setValue(2); row.addWidget(self.n_comp)
        row.addWidget(QLabel("max iterations")); self.max_iter = QSpinBox(); self.max_iter.setRange(1, 5000); self.max_iter.setValue(200); row.addWidget(self.max_iter)
        row.addWidget(QLabel("tol. err change")); self.tol = QDoubleSpinBox(); self.tol.setDecimals(8); self.tol.setRange(0, 1); self.tol.setValue(1e-6); row.addWidget(self.tol)
        left.body.addLayout(row)
        self.clip_neg = QCheckBox("clip negative values to 0 before the fit"); left.body.addWidget(self.clip_neg)
        bb = QHBoxLayout()
        b1 = QPushButton("Update PCA scree"); b1.clicked.connect(self.run_pca); bb.addWidget(b1)
        b2 = QPushButton("Run MCR-ALS"); b2.setObjectName("primary"); b2.clicked.connect(self.run_mcr); bb.addWidget(b2)
        b3 = QPushButton("Export…"); b3.clicked.connect(self._export_mcr); bb.addWidget(b3)
        left.body.addLayout(bb)
        self.scree = pg.PlotWidget(background="w"); self.scree.setLabel("bottom", "PCA component"); self.scree.setLabel("left", "explained variance (%)")
        self.scree.getViewBox().disableAutoRange()
        left.body.addWidget(self.scree, 1)
        self.prop_table = QTableWidget(0, 1); left.body.addWidget(self.prop_table, 1)
        self.mcr_status = QLabel(""); self.mcr_status.setObjectName("muted"); self.mcr_status.setWordWrap(True); left.body.addWidget(self.mcr_status)
        lay.addWidget(left, 1)
        pc = Card("Component spectra")
        self.comp_plot = pg.PlotWidget(background="w"); self.comp_plot.addLegend(offset=(10, 10)); self.comp_plot.getViewBox().disableAutoRange()
        self.comp_plot.setLabel("bottom", "energy (eV)")
        pc.body.addWidget(self.comp_plot, 1)
        lay.addWidget(pc, 2)
        return w

    def _matrix(self):
        cs = self.curves()
        if len(cs) < 2:
            raise ValueError("MCR-ALS needs at least two spectra ticked 'Fit' in Compare")
        E, D = prepare_matrix([(e, y) for _, e, y in cs], self._range(self.mcr_lo, self.mcr_hi))
        return [c[0] for c in cs], E, D

    def run_pca(self) -> None:
        try:
            labels, E, D = self._matrix()
        except ValueError as err:
            self.mcr_status.setText(str(err)); return
        p = pca(D)
        self.pca_result = p
        self.scree.clear()
        k = np.arange(1, len(p.explained) + 1)
        self.scree.plot(k, 100 * p.explained, pen=pg.mkPen("#0072B2", width=1.5), symbol="o", symbolBrush="#0072B2", symbolSize=7)
        self.scree.plot(k, 100 * p.cumulative, pen=pg.mkPen("#E69F00", width=1.2, style=Qt.DashLine), symbol="s", symbolBrush="#E69F00", symbolSize=6)
        self.scree.getViewBox().setRange(xRange=(0.5, len(k) + 0.5), yRange=(0, 100), padding=0.05, disableAutoRange=True)
        self.mcr_status.setText("PCA: " + ", ".join(f"PC{i + 1} {100 * v:.1f} %" for i, v in enumerate(p.explained[:6])))

    def run_mcr(self) -> None:
        try:
            labels, E, D = self._matrix()
        except ValueError as err:
            self.mcr_status.setText(str(err)); return
        k = min(self.n_comp.value(), len(labels))
        m = mcr_als(D, k, self.max_iter.value(), self.tol.value(), clip_negative=self.clip_neg.isChecked())
        self.mcr_result, self._mcr_E, self._mcr_labels = m, E, labels
        self._show_components(m, E, labels, "MCR-ALS")
        self.mcr_status.setText(f"MCR-ALS: {len(labels)} spectra, {k} components, RMSE {m.rmse:.4g}, {m.n_iter} iterations"
                                + ("" if m.converged else " (max iterations reached, not converged)"))

    def _show_components(self, m: McrResult, E, labels, name: str) -> None:
        self.comp_plot.clear(); self.comp_plot.plotItem.legend.clear()
        k = m.ST.shape[0]
        for i in range(k):
            self.comp_plot.plot(E, m.ST[i], pen=pg.mkPen(PAL[i % len(PAL)], width=1.6, style=DASH[i % len(DASH)]), name=f"{name} component {i + 1}")
        _set_range(self.comp_plot, [float(E[0]), float(E[-1])], [m.ST])
        props = m.proportions
        self.prop_table.setRowCount(len(labels)); self.prop_table.setColumnCount(k)
        self.prop_table.setHorizontalHeaderLabels([f"comp {i + 1} %" for i in range(k)])
        self.prop_table.setVerticalHeaderLabels(labels)
        for r in range(len(labels)):
            for c in range(k):
                self.prop_table.setItem(r, c, QTableWidgetItem(f"{props[r, c]:.1f}"))
        self.prop_table.resizeColumnsToContents()

    def _export_mcr(self) -> None:
        if self.mcr_result is None:
            self.mcr_status.setText("run MCR-ALS first"); return
        d = QFileDialog.getExistingDirectory(self, "Folder for the MCR_* files")
        if d:
            self.export_mcr(Path(d), self.mcr_result, "MCR")

    def export_mcr(self, folder: Path, m: McrResult, prefix: str) -> list[Path]:
        folder.mkdir(parents=True, exist_ok=True)
        E, labels = self._mcr_E, self._mcr_labels
        p1 = folder / f"{prefix}_component_spectra.csv"
        with open(p1, "w", encoding="utf-8", newline="\n") as f:
            f.write("energy_eV," + ",".join(f"component{i + 1}" for i in range(m.ST.shape[0])) + "\n")
            for i in range(len(E)):
                f.write(f"{E[i]:.6g}," + ",".join(f"{v:.6g}" for v in m.ST[:, i]) + "\n")
        p2 = folder / f"{prefix}_component_makeup.csv"
        props = m.proportions
        with open(p2, "w", encoding="utf-8", newline="\n") as f:
            f.write("sample," + ",".join(f"component{i + 1}_percent" for i in range(m.ST.shape[0])) + "\n")
            for lab, row in zip(labels, props):
                f.write(lab + "," + ",".join(f"{v:.3f}" for v in row) + "\n")
        self.mcr_status.setText(f"wrote {p1.name} and {p2.name} to {folder}")
        return [p1, p2]

    # ------------------------------------------------------------------ ITFA
    def _itfa_tab(self) -> QWidget:
        w = QWidget(); lay = QHBoxLayout(w)
        left = Card("ITFA: anchored targets")
        info = QLabel("Refines the MCR-ALS concentration profiles with some samples' component percentages held fixed. "
                      "Use only with independent chemical evidence.")
        info.setObjectName("muted"); info.setWordWrap(True); left.body.addWidget(info)
        row = QHBoxLayout(); row.addWidget(QLabel("sample"))
        self.itfa_sample = QComboBox(); row.addWidget(self.itfa_sample, 1)
        row.addWidget(QLabel("percentages (comma separated, one per component)"))
        self.itfa_pct = QLineEdit(); self.itfa_pct.setPlaceholderText("e.g. 100, 0"); row.addWidget(self.itfa_pct)
        b = QPushButton("Add target"); b.clicked.connect(self._add_target); row.addWidget(b)
        left.body.addLayout(row)
        self.targets = QListWidget(); left.body.addWidget(self.targets, 1)
        bb = QHBoxLayout()
        b1 = QPushButton("Remove target"); b1.clicked.connect(lambda: [self.targets.takeItem(self.targets.row(i)) for i in self.targets.selectedItems()]); bb.addWidget(b1)
        b2 = QPushButton("Run ITFA"); b2.setObjectName("primary"); b2.clicked.connect(self.run_itfa); bb.addWidget(b2)
        b3 = QPushButton("Export…"); b3.clicked.connect(self._export_itfa); bb.addWidget(b3)
        left.body.addLayout(bb)
        self.itfa_status = QLabel(""); self.itfa_status.setObjectName("muted"); self.itfa_status.setWordWrap(True); left.body.addWidget(self.itfa_status)
        lay.addWidget(left, 1)
        pc = Card("ITFA targeted component spectra")
        self.itfa_plot = pg.PlotWidget(background="w"); self.itfa_plot.addLegend(offset=(10, 10)); self.itfa_plot.getViewBox().disableAutoRange()
        pc.body.addWidget(self.itfa_plot, 1)
        self.itfa_table = QTableWidget(0, 1); pc.body.addWidget(self.itfa_table, 1)
        lay.addWidget(pc, 2)
        return w

    def _add_target(self) -> None:
        try:
            pct = [float(v) for v in self.itfa_pct.text().replace(";", ",").split(",") if v.strip()]
        except ValueError:
            self.itfa_status.setText("percentages must be numbers"); return
        if not pct or any(v < 0 for v in pct) or sum(pct) > 100.0001:
            self.itfa_status.setText("each ITFA target needs non-negative percentages that sum to at most 100"); return
        it = QListWidgetItem(f"{self.itfa_sample.currentText()} = {', '.join(f'{v:g}' for v in pct)} %")
        it.setData(Qt.UserRole, (self.itfa_sample.currentText(), pct))
        self.targets.addItem(it)

    def run_itfa(self) -> None:
        m = self.mcr_result
        if m is None:
            self.itfa_status.setText("run MCR-ALS first"); return
        k = m.ST.shape[0]
        fixed = {}
        for i in range(self.targets.count()):
            name, pct = self.targets.item(i).data(Qt.UserRole)
            if name not in self._mcr_labels:
                continue
            if len(pct) > k:
                self.itfa_status.setText(f"target {name}: {len(pct)} percentages for {k} components"); return
            idx = self._mcr_labels.index(name)
            total = float(m.C[idx].sum()) or 1.0
            row = np.array(pct + [0.0] * (k - len(pct))) / 100.0
            rest = max(0.0, 1.0 - row.sum())
            free = np.array([1.0 if v == 0 else 0.0 for v in row])
            if free.sum():
                base = m.C[idx] * free
                row = row + (base / base.sum() * rest if base.sum() > 0 else free / free.sum() * rest)
            fixed[idx] = row * total
        if not fixed:
            self.itfa_status.setText("add at least one anchored target"); return
        r = itfa(self._mcr_E * 0 + self._D_cache() if False else self._D_cache(), m, fixed, self.max_iter.value(), self.tol.value())
        self.itfa_result = r
        self.itfa_plot.clear(); self.itfa_plot.plotItem.legend.clear()
        for i in range(k):
            self.itfa_plot.plot(self._mcr_E, r.ST[i], pen=pg.mkPen(PAL[i % len(PAL)], width=1.6, style=DASH[i % len(DASH)]), name=f"ITFA component {i + 1}")
        _set_range(self.itfa_plot, [float(self._mcr_E[0]), float(self._mcr_E[-1])], [r.ST])
        props = r.proportions
        self.itfa_table.setRowCount(len(self._mcr_labels)); self.itfa_table.setColumnCount(k)
        self.itfa_table.setHorizontalHeaderLabels([f"comp {i + 1} %" for i in range(k)]); self.itfa_table.setVerticalHeaderLabels(self._mcr_labels)
        for rr in range(len(self._mcr_labels)):
            for c in range(k):
                self.itfa_table.setItem(rr, c, QTableWidgetItem(f"{props[rr, c]:.1f}"))
        self.itfa_status.setText(f"ITFA: {r.n_iter} iterations, RMSE {r.rmse:.4g}, {len(fixed)} anchored sample(s)")

    # ------------------------------------------------------------------ peaks
    def _peaks_tab(self) -> QWidget:
        w = QWidget(); lay = QHBoxLayout(w)
        left = Card("Peak decomposition")
        left.body.addWidget(QLabel("trace")); self.pk_trace = QComboBox(); left.body.addWidget(self.pk_trace)
        row = QHBoxLayout(); row.addWidget(QLabel("peaks")); self.pk_n = QSpinBox(); self.pk_n.setRange(1, 8); self.pk_n.setValue(2); row.addWidget(self.pk_n)
        row.addWidget(QLabel("shape")); self.pk_shape = QComboBox(); self.pk_shape.addItems(["gaussian", "lorentzian", "pseudo-voigt"]); row.addWidget(self.pk_shape)
        self.pk_step = QCheckBox("edge step (arctan)"); self.pk_step.setChecked(True); row.addWidget(self.pk_step)
        row.addWidget(QLabel("background")); self.pk_bg = QComboBox(); self.pk_bg.addItems(["linear", "constant"]); row.addWidget(self.pk_bg)
        left.body.addLayout(row)
        row2 = QHBoxLayout(); row2.addWidget(QLabel("range (eV)"))
        self.pk_lo = QLineEdit(); self.pk_lo.setPlaceholderText("auto"); self.pk_hi = QLineEdit(); self.pk_hi.setPlaceholderText("auto")
        row2.addWidget(self.pk_lo); row2.addWidget(self.pk_hi)
        row2.addWidget(QLabel("initial centres")); self.pk_centres = QLineEdit(); self.pk_centres.setPlaceholderText("auto (comma separated eV)"); row2.addWidget(self.pk_centres, 1)
        left.body.addLayout(row2)
        b = QPushButton("Run the decomposition"); b.setObjectName("primary"); b.clicked.connect(self.run_peaks); left.body.addWidget(b)
        self.pk_table = QTableWidget(0, 3); self.pk_table.setHorizontalHeaderLabels(["parameter", "value", "± (covariance)"]); left.body.addWidget(self.pk_table, 1)
        self.pk_info = QLabel(""); self.pk_info.setObjectName("muted"); self.pk_info.setWordWrap(True); left.body.addWidget(self.pk_info)
        eb = QPushButton("Export…"); eb.clicked.connect(self._export_peaks); left.body.addWidget(eb)
        lay.addWidget(left, 1)
        pc = Card("Data, fit, components and residual")
        self.pk_plot = pg.PlotWidget(background="w"); self.pk_plot.addLegend(offset=(10, 10)); self.pk_plot.getViewBox().disableAutoRange()
        self.pk_plot.setLabel("bottom", "energy (eV)")
        pc.body.addWidget(self.pk_plot, 1)
        lay.addWidget(pc, 2)
        self.peak_result = None
        return w

    def run_peaks(self) -> None:
        from aurora.core.fit import peak_decomposition
        curves = {n: (E, y) for n, E, y in self.curves()}
        name = self.pk_trace.currentText()
        if name not in curves:
            self.pk_info.setText("tick a trace 'Fit' in Compare first"); return
        E, y = curves[name]
        centres = None
        if self.pk_centres.text().strip():
            try:
                centres = [float(v) for v in self.pk_centres.text().replace(";", ",").split(",") if v.strip()]
            except ValueError:
                self.pk_info.setText("initial centres must be numbers"); return
        try:
            r = peak_decomposition(E, y, self.pk_n.value(), self.pk_shape.currentText(), self.pk_step.isChecked(), self.pk_bg.currentText(),
                                   centres, fit_range=self._range(self.pk_lo, self.pk_hi))
        except ValueError as err:
            self.pk_info.setText(str(err)); return
        self.peak_result = r; self._pk_name = name
        self.pk_table.setRowCount(len(r.params))
        for i, (k, v) in enumerate(r.params.items()):
            self.pk_table.setItem(i, 0, QTableWidgetItem(k)); self.pk_table.setItem(i, 1, QTableWidgetItem(f"{v:.4g}"))
            self.pk_table.setItem(i, 2, QTableWidgetItem(f"{r.errors[k]:.2g}" if np.isfinite(r.errors[k]) else "-"))
        self.pk_table.resizeColumnsToContents()
        self.pk_info.setText(f"{'converged' if r.converged else 'NOT converged: ' + r.message} · RMSE {r.rmse:.4g} · {len(r.E)} points")
        self.pk_plot.clear(); self.pk_plot.plotItem.legend.clear()
        self.pk_plot.plot(r.E, r.y, pen=pg.mkPen("#15213B", width=1.6), name=name)
        self.pk_plot.plot(r.E, r.fit, pen=pg.mkPen("#D55E00", width=1.6, style=Qt.DashLine), name="fit")
        for i, (k, c) in enumerate(r.components.items()):
            self.pk_plot.plot(r.E, c, pen=pg.mkPen(PAL[i % len(PAL)], width=1, style=DASH[i % len(DASH)]), name=k)
        resid = r.y - r.fit - 0.15 * (np.nanmax(r.y) - np.nanmin(r.y))
        self.pk_plot.plot(r.E, resid, pen=pg.mkPen("#5B6B8C", width=1), name="residual (offset)")
        _set_range(self.pk_plot, [float(r.E[0]), float(r.E[-1])], [r.y, r.fit, resid])

    def _export_peaks(self) -> None:
        if self.peak_result is None:
            self.pk_info.setText("run the decomposition first"); return
        p, _ = QFileDialog.getSaveFileName(self, "Export peak decomposition", "peaks.csv", "CSV (*.csv)")
        if p:
            self.export_peaks(Path(p))

    def export_peaks(self, path: Path) -> None:
        r = self.peak_result
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"# peak decomposition of {self._pk_name}; {'converged' if r.converged else 'not converged'}; RMSE {r.rmse:.6g}\n")
            f.write("parameter,value,uncertainty\n")
            for k, v in r.params.items():
                f.write(f"{k},{v:.6g},{r.errors[k]:.6g}\n")
            f.write("\nenergy_eV,data,fit,residual," + ",".join(r.components) + "\n")
            for i in range(len(r.E)):
                f.write(f"{r.E[i]:.6g},{r.y[i]:.6g},{r.fit[i]:.6g},{r.y[i] - r.fit[i]:.6g}," + ",".join(f"{c[i]:.6g}" for c in r.components.values()) + "\n")

    def _D_cache(self) -> np.ndarray:
        labels, E, D = self._matrix()
        return D

    def _export_itfa(self) -> None:
        if self.itfa_result is None:
            self.itfa_status.setText("run ITFA first"); return
        d = QFileDialog.getExistingDirectory(self, "Folder for the ITFA_* files")
        if d:
            self.export_mcr(Path(d), self.itfa_result, "ITFA")
