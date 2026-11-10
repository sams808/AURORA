"""Fit perspective (John Bussey's tabs 4-6): LCF, PCA + MCR-ALS, ITFA on the rows ticked 'Fit' in Compare, plus a
peak decomposition.

Every fit says what it did: a chip reads 'converged', 'not converged: <message>' or 'N parameters undetermined
(σ > |value|)'; rows at a bound or undetermined are tinted, with the reason in their tooltip. LCF (with its
bootstrap), MCR-ALS and ITFA run off the GUI thread through window.jobs, with progress and Cancel. Exports draw the
figure again at the Figure size (aurora.core.figures), never a screen grab.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QSizePolicy, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from aurora.app import jobs, nav
from aurora.app.help import set_help_anchor
from aurora.app.help_text import HELP, explain, explain_all, header_tips, labelled
from aurora.app.library import Library
from aurora.app.mode import mark_advanced, mark_advanced_tab, mark_easy
from aurora.app.theme import curve, dash, make_plot, on_theme_changed, role_pen
from aurora.app.vocab import plural, unit_suffix
from aurora.app.widgets import (Card, Chip, ElidedLabel, EmptyState, FlowLayout, TaskBar, default_dir, default_path,
                                guarded_write, notify_saved, overlay_host, pair, remember_dir, set_enabled, set_plot_hint)
from aurora.core.fit import (LcfResult, McrResult, PcaResult, common_grid, fit_status, itfa, lcf, mcr_als, pca,
                             prepare_matrix, same_matrix, unit_of)
from aurora.core.io.textio import open_atomic

TINT_ROLE = int(Qt.UserRole) + 0x7B0          # the tone of a tinted row (re-read after a theme change)
RESAMPLE = (("moving blocks", "block"), ("single points (iid)", "iid"))


def _component_index(i: int) -> int:
    """Series index of fitted component i: CURVES[1] (dashed) is the fit itself, so components skip it."""
    return i if i == 0 else i + 1


def _set_range(plot, xs, ys) -> None:
    v = np.concatenate([np.asarray(y, float).ravel() for y in ys]) if ys else np.array([])
    v = v[np.isfinite(v)]
    if xs and v.size:
        plot.getViewBox().setRange(xRange=(min(xs), max(xs)), yRange=(float(v.min()), float(v.max()) if v.max() > v.min() else float(v.min()) + 1),
                                   padding=0.05, disableAutoRange=True)


def _percent_tips(table: QTableWidget) -> None:
    header_tips(table, {c: "fit.col.percent" for c in range(table.columnCount())})


def set_chip(chip: Chip, text: str, tone: str) -> None:
    """The result chip of a fit: its text and tone ('ok', 'warn', 'bad'), hidden while empty."""
    chip.setText(text)
    chip.setProperty("chip", tone or "muted")
    chip.style().unpolish(chip)
    chip.style().polish(chip)
    chip.setVisible(bool(text))


def tint(item: QTableWidgetItem, tone: str | None, why: str = "") -> None:
    """A row cell tinted by meaning (the tone's background and text tokens) with the reason as its tooltip."""
    from aurora.app.theme import TOKENS, TONES
    item.setData(TINT_ROLE, tone)
    if not tone:
        return
    bg, fg = TONES[tone]
    item.setBackground(QBrush(QColor(TOKENS[bg])))
    item.setForeground(QBrush(QColor(TOKENS[fg])))
    if why:
        item.setToolTip(why)


def _chip() -> Chip:
    c = Chip("", "muted")
    c.setWordWrap(True)
    c.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    c.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)     # a pill as wide as its text
    c.hide()
    return c


class FitPerspective(QWidget):
    def __init__(self, library: Library, parent: QWidget | None = None):
        super().__init__(parent)
        self.lib = self.library = library
        self.lcf_result: LcfResult | None = None
        self.pca_result: PcaResult | None = None
        self.mcr_result: McrResult | None = None
        self.itfa_result: McrResult | None = None
        self.peak_result = None
        self._mcr_labels: list[str] = []
        self._lcf_names = None
        self._lcf_origin = ""
        self._mcr_E = None
        self._pk_name = ""
        self._comp_name = "MCR-ALS"
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Fit"); h1.setObjectName("h1")
        sub = ElidedLabel("rows ticked 'Fit' in Compare, with their shifts and normalisation", Qt.ElideRight)
        sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub, 1)
        self.figure_btn = QPushButton("Figure size…"); self.figure_btn.clicked.connect(self._figure_options)
        explain(self.figure_btn, "fit.figure_size")
        head.addWidget(self.figure_btn)
        root.addLayout(head)
        self.tabs = QTabWidget()
        self.lcf_page, self.mcr_page, self.itfa_page, self.peaks_page = (self._lcf_tab(), self._mcr_tab(), self._itfa_tab(),
                                                                         self._peaks_tab())
        self.tabs.addTab(self.lcf_page, "LCF")
        self.tabs.addTab(self.mcr_page, "PCA / MCR-ALS")
        self.tabs.addTab(self.itfa_page, "ITFA")
        self.tabs.addTab(self.peaks_page, "Peaks")
        for page, anchor in ((self.lcf_page, "71-lcf-linear-combination-fit"), (self.mcr_page, "72-pca--mcr-als"),
                             (self.itfa_page, "73-itfa"), (self.peaks_page, "74-peaks")):
            set_help_anchor(page, anchor)
        self.body = overlay_host(self.tabs)
        root.addWidget(self.body, 1)
        self.taskbar = TaskBar(self)
        root.addWidget(self.taskbar)
        self.empty_state = EmptyState("No spectrum is ticked 'Fit' in Compare yet: the fits work on those rows.",
                                      [("Go to Compare", lambda: nav.go(self, "compare"))], icon="fit", parent=self.body)
        library.changed.connect(self.refresh)
        on_theme_changed(self, lambda _s: self.redraw())
        # Easy mode: the LCF tab of John Bussey's GUI with fixed options shown read-only; MCR-ALS (his tab 5), ITFA
        # (his tab 6), Peaks, the LCF options and the uncertainty / F-test detail are Advanced
        for page in (self.mcr_page, self.itfa_page, self.peaks_page):
            mark_advanced_tab(self.tabs, page)
        mark_advanced(self.lcf_options, self.lcf_detail)
        mark_easy(self.lcf_fixed)
        for w in (self.sum_one, self.allow_neg):
            w.toggled.connect(self._fixed_text)
        self.bootstrap.valueChanged.connect(self._fixed_text)
        self.resample.currentIndexChanged.connect(self._fixed_text)
        self._fixed_text()
        self.refresh()
        self.redraw()

    def _fixed_text(self, *_) -> None:
        """The LCF options in force, shown read-only in Easy mode (the hidden widgets keep their values)."""
        n = self.bootstrap.value()
        parts = ["coefficients sum to 1" if self.sum_one.isChecked() else "coefficients free to sum to any value",
                 "negative coefficients allowed" if self.allow_neg.isChecked() else "no negative coefficients",
                 f"± from {n} bootstrap draws" if n else "± from the covariance (no bootstrap)",
                 "fit range: the Compare x range when the boxes are blank"]
        self.lcf_fixed.setText(" · ".join(parts))

    def update_actions(self) -> None:
        n = len(self.curves())
        set_enabled(self.lcf_btn, n >= 2, "tick at least two rows 'Fit' in Compare (a target and a component)")
        set_enabled(self.lcf_export_btn, self.lcf_result is not None, "run LCF first")
        set_enabled(self.pca_btn, n >= 2, "tick at least two rows 'Fit' in Compare")
        set_enabled(self.mcr_btn, n >= 2, "tick at least two rows 'Fit' in Compare")
        set_enabled(self.mcr_export_btn, self.mcr_result is not None, "run MCR-ALS first")
        set_enabled(self.itfa_add_btn, self.itfa_sample.count() > 0, "tick rows 'Fit' in Compare first")
        set_enabled(self.itfa_btn, self.mcr_result is not None, "run MCR-ALS first")
        set_enabled(self.itfa_export_btn, self.itfa_result is not None, "run ITFA first")
        set_enabled(self.pk_btn, n >= 1, "tick a row 'Fit' in Compare first")
        set_enabled(self.pk_export_btn, self.peak_result is not None, "run the decomposition first")
        self.empty_state.set_shown(n == 0)

    def redraw(self) -> None:
        """Draw every result again with the current colours (after a theme change)."""
        self._draw_lcf()
        self._fill_lcf_table()
        self._draw_pca()
        self._draw_components()
        self._draw_itfa()
        self._draw_peaks()
        self._fill_pk_table()

    def _figure_options(self) -> None:
        from aurora.app.figure_export import open_dialog, summary
        open_dialog(self)
        base = HELP["fit.figure_size"][0]
        self.figure_btn.setProperty("aurora_tip", f"{base}\nNow: {summary()}")
        self.figure_btn.setToolTip(f"{base}\nNow: {summary()}")

    def _busy(self, key: str, status: QLabel, what: str) -> bool:
        job = jobs.of(self).job(key)
        if job is not None and job.is_running():
            status.setText(f"{what} is still running: wait for it or Cancel it below")
            return True
        return False

    # ------------------------------------------------------------------ data
    def fit_rows(self):
        return self.lib.processed(lambda r: r.fit)

    def curves(self):
        """[(label, E shifted, y normalised)] of the Fit rows."""
        return [(f"{r.label} [{r.signal}]", s.E + r.shift, nr.y) for r, s, nr in self.fit_rows() if np.isfinite(nr.y).any()]

    def curves_with_err(self) -> dict:
        """{label: (E shifted, y normalised[, err normalised])} of the Fit rows; err when the spectrum carries one."""
        out = {}
        for r, s, nr in self.fit_rows():
            if not np.isfinite(nr.y).any():
                continue
            key = f"{r.label} [{r.signal}]"
            out[key] = (s.E + r.shift, nr.y) if nr.err is None else (s.E + r.shift, nr.y, nr.err)
        return out

    def _range(self, lo: QLineEdit, hi: QLineEdit):
        try:
            a, b = float(lo.text()), float(hi.text())
        except ValueError:
            return None
        return (min(a, b), max(a, b)) if a != b else None

    def _compare_range(self):
        """The Compare x range (the default LCF range), None when Compare has none."""
        win = nav.window_of(self)
        comp = getattr(win, "compare", None) if win is not None else None
        if comp is None:
            return None
        return comp.x_range() if hasattr(comp, "x_range") else comp._xrange()

    def lcf_range(self) -> tuple[tuple[float, float] | None, str]:
        """(range, where it comes from): the typed range, else the Compare x range, else the whole common range."""
        r = self._range(self.lcf_lo, self.lcf_hi)
        if r is not None:
            return r, "typed"
        c = self._compare_range()
        if c is not None:
            return (float(c[0]), float(c[1])), "the Compare x range"
        return None, "the whole common range"

    def _range_hint(self) -> None:
        c = self._compare_range()
        self.lcf_lo.setPlaceholderText(f"{c[0]:g} (Compare)" if c else "auto")
        self.lcf_hi.setPlaceholderText(f"{c[1]:g} (Compare)" if c else "auto")

    def showEvent(self, e) -> None:
        self._range_hint()
        super().showEvent(e)

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
        self.mcr_info.setText(f"{plural(len(names), 'spectrum', 'spectra')} ticked for the fits in Compare")
        cur = self.pk_trace.currentText(); self.pk_trace.blockSignals(True); self.pk_trace.clear(); self.pk_trace.addItems(names)
        if cur in names:
            self.pk_trace.setCurrentText(cur)
        self.pk_trace.blockSignals(False)
        self.itfa_sample.clear(); self.itfa_sample.addItems(names)
        self._range_hint()
        self.update_actions()

    # ------------------------------------------------------------------ LCF
    def _lcf_tab(self) -> QWidget:
        w = QWidget(); lay = QHBoxLayout(w)
        left = Card("Linear combination", help_anchor="71-lcf-linear-combination-fit")
        self.target = QComboBox()
        self.target.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.target.setMinimumContentsLength(14)
        left.body.addWidget(labelled("target", self.target, "fit.target")); left.body.addWidget(self.target)
        self.components = QListWidget()
        left.body.addWidget(labelled("components (tick)", self.components, "fit.components"))
        left.body.addWidget(self.components, 1)
        rng = QHBoxLayout()
        self.lcf_lo = QLineEdit(); self.lcf_lo.setPlaceholderText("auto"); self.lcf_hi = QLineEdit(); self.lcf_hi.setPlaceholderText("auto")
        rng.addWidget(pair(labelled("fit range (eV)", self.lcf_lo, "fit.lcf_lo"), self.lcf_lo)); rng.addWidget(self.lcf_hi)
        explain(self.lcf_hi, "fit.lcf_hi")
        left.body.addLayout(rng)
        self.lcf_options = QWidget()                   # sum to 1, negatives, bootstrap count, resampling: Advanced
        ol = QVBoxLayout(self.lcf_options); ol.setContentsMargins(0, 0, 0, 0)
        self.sum_one = QCheckBox("force coefficients to sum to 1"); self.sum_one.setChecked(True); ol.addWidget(self.sum_one)
        self.allow_neg = QCheckBox("allow negative coefficients"); ol.addWidget(self.allow_neg)
        bs_box = QWidget(); bs = FlowLayout(bs_box, spacing=6)
        self.bootstrap = QSpinBox(); self.bootstrap.setRange(0, 5000); self.bootstrap.setValue(200)
        self.bootstrap.setSuffix(unit_suffix("draws"))
        bs.addWidget(pair(labelled("bootstrap", self.bootstrap, "fit.bootstrap"), self.bootstrap))
        self.resample = QComboBox()
        for text, code in RESAMPLE:
            self.resample.addItem(text, code)
        bs.addWidget(pair(labelled("resample", self.resample, "fit.resample"), self.resample))
        ol.addWidget(bs_box)
        left.body.addWidget(self.lcf_options)
        self.lcf_fixed = QLabel(""); self.lcf_fixed.setObjectName("muted"); self.lcf_fixed.setWordWrap(True)
        left.body.addWidget(self.lcf_fixed)
        b = QPushButton("Run LCF"); b.setObjectName("primary"); b.clicked.connect(self.run_lcf); left.body.addWidget(b)
        self.lcf_btn = b
        lay.addWidget(left, 1)
        right = QSplitter(Qt.Vertical)
        pc = Card("Target, fit and residual", help_anchor="71-lcf-linear-combination-fit")
        self.lcf_plot = make_plot(x="energy (eV)", legend="inside"); self.lcf_plot.getViewBox().disableAutoRange()
        pc.body.addWidget(self.lcf_plot, 1)
        right.addWidget(pc)
        rc = Card("Coefficients", help_anchor="71-lcf-linear-combination-fit")
        self.lcf_chip = _chip()
        rc.body.addWidget(self.lcf_chip)
        self.lcf_table = QTableWidget(0, 4)
        self.lcf_table.setHorizontalHeaderLabels(["component", "coefficient", "±", "note"])
        self.lcf_table.setAccessibleName("LCF coefficients")
        header_tips(self.lcf_table, {0: "fit.col.component", 1: "fit.col.coefficient", 2: "fit.col.uncertainty",
                                     3: "fit.col.note"})
        rc.body.addWidget(self.lcf_table, 1)
        self.lcf_info = QLabel(""); self.lcf_info.setObjectName("muted"); self.lcf_info.setWordWrap(True); rc.body.addWidget(self.lcf_info)
        self.lcf_detail = QLabel(""); self.lcf_detail.setObjectName("muted"); self.lcf_detail.setWordWrap(True)
        rc.body.addWidget(self.lcf_detail)
        eb = QPushButton("Export figure + coefficients…"); eb.clicked.connect(self._export_lcf); rc.body.addWidget(eb)
        self.lcf_export_btn = eb
        explain_all([(self.sum_one, "fit.sum_one"), (self.allow_neg, "fit.allow_neg"), (b, "fit.run_lcf"), (eb, "fit.export_lcf")])
        right.addWidget(rc)
        self.lcf_table.setMinimumHeight(118)          # the header and three coefficients stay in view
        right.setStretchFactor(0, 1)
        right.setStretchFactor(1, 1)
        right.setSizes([420, 420])
        lay.addWidget(right, 2)
        return w

    def run_lcf(self):
        """Fit the target with the ticked components, off the GUI thread (window.jobs, with Cancel). Returns the Job,
        or None when the inputs are not ready (the reason is shown)."""
        if self._busy("fit:lcf", self.lcf_info, "LCF"):
            return None
        curves = self.curves_with_err()
        tname = self.target.currentText()
        comps = [self.components.item(i).text() for i in range(self.components.count()) if self.components.item(i).checkState() == Qt.Checked and self.components.item(i).text() != tname]
        if tname not in curves:
            self.lcf_info.setText("add spectra in Compare and tick 'Fit' first"); return None
        if not comps:
            self.lcf_info.setText("tick at least one component (other than the target)"); return None
        rng, origin = self.lcf_range()
        target, parts = curves[tname], [curves[c][:2] for c in comps]
        try:
            common_grid([target[:2]] + parts, rng)
        except ValueError as err:
            self.lcf_info.setText(str(err)); return None
        opts = dict(fit_range=rng, sum_to_one=self.sum_one.isChecked(), allow_negative=self.allow_neg.isChecked(),
                    bootstrap=self.bootstrap.value(), resample=self.resample.currentData() or "block")

        def work(progress):
            try:
                return lcf(target, parts, progress=progress, **opts)
            except ValueError as err:                  # a calm message, not an error dialog
                return err
        self.lcf_info.setText("fitting …")
        return jobs.of(self).submit("fit:lcf", work, on_done=lambda r: self._lcf_done(r, tname, comps, origin),
                                    label="LCF", taskbar=self.taskbar)

    def _lcf_done(self, r, tname: str, comps: list[str], origin: str) -> None:
        if isinstance(r, Exception):
            self.lcf_info.setText(str(r)); return
        self.lcf_result = r; self._lcf_names = (tname, comps); self._lcf_origin = origin
        self._fill_lcf_table()
        self.lcf_info.setText(f"χ²ν {r.redchi:.3g} = χ² {r.chi2:.4g} / (N {r.n_points} − p {r.n_params}) · R-factor "
                              f"{r.r_factor:.3g} · sum of coefficients {r.total:.3f} · {r.E[0]:.1f}–{r.E[-1]:.1f} eV "
                              f"({origin})")
        self.lcf_detail.setText(self._lcf_detail_text(r, comps))
        self._draw_lcf()
        self.update_actions()

    def _lcf_detail_text(self, r: LcfResult, comps: list[str]) -> str:
        lines = [r.uncertainty_text().replace("± from", "uncertainty: ± from", 1),
                 f"σ: {r.sigma_source}" + (f" (median {np.median(r.sigma):.3g})" if r.sigma is not None else "")]
        if r.autocorr_length > 1.5:
            lines.append(f"the residual is correlated over {r.autocorr_length:.1f} points: the F-test counts "
                         f"{r.n_points / r.autocorr_length:.0f} independent points")
        tests = []
        for ft in r.f_tests:
            name = comps[ft.component] if 0 <= ft.component < len(comps) else f"component {ft.component + 1}"
            if ft.improves():
                tests.append(f"adding {name} improves the fit beyond noise, p = {ft.p_value:.2g}")
            else:
                tests.append(f"{name} does not improve the fit beyond noise, p = {ft.p_value:.2g}")
        if tests:
            lines.append("F-test (Hamilton): " + "; ".join(tests))
        for f in r.flags:
            for k, c in enumerate(comps):
                f = f.replace(f"component {k + 1} ", f"{c} ")
            lines.append(f)
        return "\n".join(lines)

    def _fill_lcf_table(self) -> None:
        r = self.lcf_result
        if r is None or self._lcf_names is None:
            self.lcf_table.setRowCount(0)
            set_chip(self.lcf_chip, "", "muted")
            return
        _t, comps = self._lcf_names
        head = f"± ({r.n_draws} bootstrap draws)" if r.n_draws else "± (covariance)"
        self.lcf_table.horizontalHeaderItem(2).setText(head)
        self.lcf_table.setRowCount(len(comps))
        und = r.undetermined
        bound = r.at_bound if r.at_bound is not None else np.zeros(len(comps), bool)
        for i, c in enumerate(comps):
            u = r.uncertainties[i]
            note, why = "", ""
            if bound[i]:
                note, why = "at the 0 bound", "the coefficient sits at the 0 bound: this component is not needed, or a negative one would fit better"
            elif und[i]:
                note, why = "undetermined", "σ > |value|: the data do not determine this coefficient"
            cells = [QTableWidgetItem(c), QTableWidgetItem(f"{r.coefficients[i]:.4f}"),
                     QTableWidgetItem(f"{u:.4f}" if np.isfinite(u) else "–"), QTableWidgetItem(note)]
            for k, it in enumerate(cells):
                if note:
                    tint(it, "warn", why)
                self.lcf_table.setItem(i, k, it)
        self.lcf_table.resizeColumnsToContents()
        set_chip(self.lcf_chip, *fit_status(r.converged, r.message, int(und.sum())))

    def _draw_lcf(self) -> None:
        r = self.lcf_result
        self.lcf_plot.clear(); self.lcf_plot.plotItem.legend.clear()
        if r is None or self._lcf_names is None:
            set_plot_hint(self.lcf_plot, "choose a target and components, then Run LCF")
            return
        set_plot_hint(self.lcf_plot, None)
        tname, comps = self._lcf_names
        self.lcf_plot.plot(r.E, r.target, pen=role_pen("target", 2), name=f"target {tname}")
        self.lcf_plot.plot(r.E, r.fit, pen=role_pen("fit", 1.6), name="linear combination")
        for i, c in enumerate(comps):
            k = _component_index(i)
            self.lcf_plot.plot(r.E, r.coefficients[i] * r.components[i], pen=pg.mkPen(curve(k), width=1, style=dash(k)), name=f"{r.coefficients[i]:.2f} x {c}")
        resid = r.target - r.fit - 0.2 * (np.nanmax(r.target) - np.nanmin(r.target))
        self.lcf_plot.plot(r.E, resid, pen=role_pen("resid", 1), name="residual (offset)")
        _set_range(self.lcf_plot, [float(r.E[0]), float(r.E[-1])], [r.target, r.fit, resid])

    def _export_lcf(self) -> None:
        if self.lcf_result is None:
            self.lcf_info.setText("run LCF first"); return
        p, _ = QFileDialog.getSaveFileName(self, "Export LCF (.csv, and the figure next to it)",
                                           default_path("fit", "lcf_coefficients.csv", self.lib), "CSV (*.csv)")
        if p:
            remember_dir("fit", p)
            with guarded_write(self, Path(p).name):
                self.export_lcf(Path(p))

    def lcf_figure_series(self):
        from aurora.app.figure_export import export_colour, role_colour
        from aurora.core.figures import FigureSeries
        r = self.lcf_result; tname, comps = self._lcf_names
        out = []
        c, d = role_colour("target")
        out.append(FigureSeries(f"target {tname}", r.E, r.target, c, d, 1.2))
        c, d = role_colour("fit")
        out.append(FigureSeries("linear combination", r.E, r.fit, c, d, 1.0))
        for i, name in enumerate(comps):
            c, _lab, d = export_colour(_component_index(i))
            out.append(FigureSeries(f"{r.coefficients[i]:.2f} × {name}", r.E, r.coefficients[i] * r.components[i], c, d, 0.8))
        c, d = role_colour("resid")
        resid = r.target - r.fit - 0.2 * (np.nanmax(r.target) - np.nanmin(r.target))
        out.append(FigureSeries("residual (offset)", r.E, resid, c, d, 0.8))
        return out

    def _save_figure(self, base: Path, series, ylabel: str, title: str) -> list[Path]:
        from aurora.app.figure_export import current_options, styled
        from aurora.core.figures import fit_figure, save_figure
        o = styled(current_options(), xlabel="incident energy (eV)", ylabel=ylabel, title="", legend=True)
        o.metadata["Title"] = title
        return save_figure(fit_figure(series, o), base, o)

    def export_lcf(self, path: Path) -> list[Path]:
        """The coefficients (with χ², reduced χ², N, p, R-factor, the uncertainty method, σ, flags and F-tests in the
        header) and the curves as CSV, the figure at the Figure size (MANUAL §7.1); says what it wrote."""
        from aurora import __version__
        r = self.lcf_result; tname, comps = self._lcf_names
        with open_atomic(path) as f:            # refused in raw/ or a protected folder before anything is written
            f.write(f"# LCF of {tname}; RMSE {r.rmse:.6g}; R-factor {r.r_factor:.6g}; sum {r.total:.6g}; range {r.E[0]:.2f}-{r.E[-1]:.2f} eV\n")
            f.write(f"# AURORA {__version__} (version 2 of John Bussey's SGM XAS GUI)\n")
            f.write(f"# chi-square {r.chi2:.6g}; reduced chi-square {r.redchi:.6g} = chi-square / (N - p), N = {r.n_points}, "
                    f"p = {r.n_params}; range from {self._lcf_origin or 'typed'}\n")
            f.write(f"# sigma: {r.sigma_source}" + (f"; median {np.median(r.sigma):.6g}" if r.sigma is not None else "") + "\n")
            f.write(f"# uncertainty: {r.uncertainty_text()}\n")
            f.write(f"# constraints: sum to 1 {'on' if r.sum_to_one else 'off'}; negative coefficients "
                    f"{'allowed' if r.allow_negative else 'not allowed'}; solver {r.message or 'ok'}\n")
            for line in self._lcf_detail_text(r, comps).splitlines()[2:]:
                f.write(f"# {line}\n")
            f.write("component,coefficient,uncertainty,covariance_uncertainty,note\n")
            und = r.undetermined
            bound = r.at_bound if r.at_bound is not None else np.zeros(len(comps), bool)
            cu = r.cov_uncertainties if r.cov_uncertainties is not None else np.full(len(comps), np.nan)
            for k, (c, v, u) in enumerate(zip(comps, r.coefficients, r.uncertainties)):
                note = "at the 0 bound" if bound[k] else ("undetermined (sigma > |value|)" if und[k] else "")
                f.write(f"{c},{v:.6g},{u:.6g},{cu[k]:.6g},{note}\n")
            f.write("\nenergy_eV,target,fit,residual," + ",".join(comps) + "\n")
            for i in range(len(r.E)):
                f.write(f"{r.E[i]:.6g},{r.target[i]:.6g},{r.fit[i]:.6g},{r.target[i] - r.fit[i]:.6g}," + ",".join(f"{r.coefficients[k] * r.components[k][i]:.6g}" for k in range(len(comps))) + "\n")
        out = [path]
        out += self._save_figure(path, self.lcf_figure_series(), "normalised signal (edge step = 1)", f"LCF of {tname}")
        notify_saved(self.lcf_info, out)
        return out

    # ------------------------------------------------------------------ PCA / MCR-ALS
    def _mcr_tab(self) -> QWidget:
        w = QWidget(); lay = QHBoxLayout(w)
        left = Card("MCR-ALS", help_anchor="72-pca--mcr-als")
        self.mcr_info = QLabel(""); self.mcr_info.setObjectName("muted"); self.mcr_info.setWordWrap(True); left.body.addWidget(self.mcr_info)
        rng = QHBoxLayout()
        self.mcr_lo = QLineEdit(); self.mcr_lo.setPlaceholderText("auto"); self.mcr_hi = QLineEdit(); self.mcr_hi.setPlaceholderText("auto")
        rng.addWidget(pair(labelled("range (eV)", self.mcr_lo, "fit.mcr_lo"), self.mcr_lo)); rng.addWidget(self.mcr_hi)
        explain(self.mcr_hi, "fit.mcr_hi")
        left.body.addLayout(rng)
        row_box = QWidget(); row = FlowLayout(row_box, spacing=6)
        self.n_comp = QSpinBox(); self.n_comp.setRange(1, 10); self.n_comp.setValue(2); self.n_comp.setSuffix(unit_suffix("components"))
        row.addWidget(self.n_comp)
        self.max_iter = QSpinBox(); self.max_iter.setRange(1, 5000); self.max_iter.setValue(200); self.max_iter.setSuffix(unit_suffix("iterations"))
        row.addWidget(pair(labelled("max", self.max_iter, "fit.max_iter"), self.max_iter))
        self.tol = QDoubleSpinBox(); self.tol.setDecimals(8); self.tol.setRange(0, 1); self.tol.setValue(1e-6)
        row.addWidget(pair(labelled("tolerance (relative)", self.tol, "fit.tol"), self.tol))
        left.body.addWidget(row_box)
        self.clip_neg = QCheckBox("clip negative values to 0 before the fit"); left.body.addWidget(self.clip_neg)
        bb = FlowLayout(spacing=6)
        b1 = QPushButton("Update PCA scree"); b1.clicked.connect(self.run_pca); bb.addWidget(b1)
        b2 = QPushButton("Run MCR-ALS"); b2.setObjectName("primary"); b2.clicked.connect(self.run_mcr); bb.addWidget(b2)
        b3 = QPushButton("Export…"); b3.clicked.connect(self._export_mcr); bb.addWidget(b3)
        bbw = QWidget(); bbw.setLayout(bb); left.body.addWidget(bbw)
        self.pca_btn, self.mcr_btn, self.mcr_export_btn = b1, b2, b3
        explain_all([(self.n_comp, "fit.n_comp"), (self.clip_neg, "fit.clip_neg"), (b1, "fit.pca"), (b2, "fit.run_mcr"),
                     (b3, "fit.export_mcr")])
        self.mcr_chip = _chip()
        left.body.addWidget(self.mcr_chip)
        self.scree = make_plot(x="PCA component", y="explained variance (%)")
        self.scree.getViewBox().disableAutoRange()
        left.body.addWidget(self.scree, 1)
        self.prop_table = QTableWidget(0, 1); self.prop_table.setAccessibleName("component percentages per sample")
        self.prop_table.setHorizontalHeaderLabels(["comp 1 %"]); _percent_tips(self.prop_table)
        left.body.addWidget(self.prop_table, 1)
        self.mcr_status = QLabel(""); self.mcr_status.setObjectName("muted"); self.mcr_status.setWordWrap(True); left.body.addWidget(self.mcr_status)
        lay.addWidget(left, 1)
        pc = Card("Component spectra", help_anchor="72-pca--mcr-als")
        self.comp_plot = make_plot(x="energy (eV)", legend="inside"); self.comp_plot.getViewBox().disableAutoRange()
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
        self._draw_pca()
        self.mcr_status.setText("PCA: " + ", ".join(f"PC{i + 1} {100 * v:.1f} %" for i, v in enumerate(p.explained[:6])))

    def _draw_pca(self) -> None:
        self.scree.clear()
        p = self.pca_result
        if p is None:
            set_plot_hint(self.scree, "Update PCA scree to see the explained variance")
            return
        set_plot_hint(self.scree, None)
        k = np.arange(1, len(p.explained) + 1)
        self.scree.plot(k, 100 * p.explained, pen=pg.mkPen(curve(0), width=1.5), symbol="o", symbolBrush=curve(0), symbolSize=7)
        self.scree.plot(k, 100 * p.cumulative, pen=pg.mkPen(curve(1), width=1.2, style=Qt.DashLine), symbol="s", symbolBrush=curve(1), symbolSize=6)
        self.scree.getViewBox().setRange(xRange=(0.5, len(k) + 0.5), yRange=(0, 100), padding=0.05, disableAutoRange=True)

    def run_mcr(self):
        """MCR-ALS on the Fit rows, off the GUI thread (window.jobs, with Cancel). Returns the Job or None."""
        if self._busy("fit:mcr", self.mcr_status, "MCR-ALS"):
            return None
        try:
            labels, E, D = self._matrix()
        except ValueError as err:
            self.mcr_status.setText(str(err)); return None
        k = min(self.n_comp.value(), len(labels))
        max_iter, tol, clip = self.max_iter.value(), self.tol.value(), self.clip_neg.isChecked()
        self.mcr_status.setText(f"MCR-ALS on {plural(len(labels), 'spectrum', 'spectra')} …")
        return jobs.of(self).submit("fit:mcr", lambda progress: mcr_als(D, k, max_iter, tol, clip_negative=clip,
                                                                        progress=progress, E=E, labels=labels),
                                    on_done=self._mcr_done, label="MCR-ALS", taskbar=self.taskbar)

    def _mcr_done(self, m: McrResult) -> None:
        self.mcr_result, self._mcr_E, self._mcr_labels = m, m.E, list(m.labels)
        k = m.ST.shape[0]
        self._show_components(m, m.E, m.labels, "MCR-ALS")
        self.mcr_status.setText(f"MCR-ALS: {plural(len(m.labels), 'spectrum', 'spectra')}, {plural(k, 'component')}, "
                                f"RMSE {m.rmse:.4g}, {plural(m.n_iter, 'iteration')}")
        set_chip(self.mcr_chip, *fit_status(m.converged, m.message, 0))
        self.update_actions()

    def _draw_components(self) -> None:
        self.comp_plot.clear(); self.comp_plot.plotItem.legend.clear()
        m, E = self.mcr_result, self._mcr_E
        if m is None or E is None:
            set_plot_hint(self.comp_plot, "Run MCR-ALS to see the component spectra")
            return
        set_plot_hint(self.comp_plot, None)
        for i in range(m.ST.shape[0]):
            self.comp_plot.plot(E, m.ST[i], pen=pg.mkPen(curve(i), width=1.6, style=dash(i)), name=f"{self._comp_name} component {i + 1}")
        _set_range(self.comp_plot, [float(E[0]), float(E[-1])], [m.ST])

    def _show_components(self, m: McrResult, E, labels, name: str) -> None:
        self._comp_name = name
        self._draw_components()
        k = m.ST.shape[0]
        props = m.proportions
        self.prop_table.setRowCount(len(labels)); self.prop_table.setColumnCount(k)
        self.prop_table.setHorizontalHeaderLabels([f"comp {i + 1} %" for i in range(k)])
        _percent_tips(self.prop_table)
        self.prop_table.setVerticalHeaderLabels(labels)
        for r in range(len(labels)):
            for c in range(k):
                self.prop_table.setItem(r, c, QTableWidgetItem(f"{props[r, c]:.1f}"))
        self.prop_table.resizeColumnsToContents()

    def _export_mcr(self) -> None:
        if self.mcr_result is None:
            self.mcr_status.setText("run MCR-ALS first"); return
        d = QFileDialog.getExistingDirectory(self, "Folder for the MCR_* files", default_dir("fit", self.lib))
        if d:
            remember_dir("fit", Path(d))
            with guarded_write(self, "the MCR files") as g:
                out = self.export_mcr(Path(d), self.mcr_result, "MCR")
            if g.ok:
                notify_saved(self.mcr_status, out)

    def export_mcr(self, folder: Path, m: McrResult, prefix: str) -> list[Path]:
        from aurora.app.figure_export import export_colour
        from aurora.core.figures import FigureSeries
        from aurora.core.io.safety import assert_writable
        assert_writable(folder)                 # before the folder is made: never inside raw/ or a protected folder
        folder.mkdir(parents=True, exist_ok=True)
        E = m.E if m.E is not None else self._mcr_E
        labels = m.labels or self._mcr_labels
        p1 = folder / f"{prefix}_component_spectra.csv"
        with open_atomic(p1) as f:
            f.write("energy_eV," + ",".join(f"component{i + 1}" for i in range(m.ST.shape[0])) + "\n")
            for i in range(len(E)):
                f.write(f"{E[i]:.6g}," + ",".join(f"{v:.6g}" for v in m.ST[:, i]) + "\n")
        p2 = folder / f"{prefix}_component_makeup.csv"
        props = m.proportions
        with open_atomic(p2) as f:
            f.write("sample," + ",".join(f"component{i + 1}_percent" for i in range(m.ST.shape[0])) + "\n")
            for lab, row in zip(labels, props):
                f.write(lab + "," + ",".join(f"{v:.3f}" for v in row) + "\n")
        series = []
        for i in range(m.ST.shape[0]):
            c, _lab, d = export_colour(i)
            series.append(FigureSeries(f"{prefix} component {i + 1}", E, m.ST[i], c, d, 1.0))
        figs = self._save_figure(folder / f"{prefix}_component_spectra", series, "component spectrum (a.u.)",
                                 f"{prefix} component spectra")
        self.mcr_status.setText(f"wrote {p1.name} and {p2.name} to {folder}")
        return [p1, p2, *figs]

    # ------------------------------------------------------------------ ITFA
    def _itfa_tab(self) -> QWidget:
        w = QWidget(); lay = QHBoxLayout(w)
        left = Card("ITFA: anchored targets", help_anchor="73-itfa")
        info = QLabel("Refines the MCR-ALS concentration profiles with some samples' component percentages held fixed. "
                      "Use only with independent chemical evidence.")
        info.setObjectName("muted"); info.setWordWrap(True); left.body.addWidget(info)
        row_box = QWidget(); row = FlowLayout(row_box, spacing=6)
        self.itfa_sample = QComboBox()
        self.itfa_sample.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.itfa_sample.setMinimumContentsLength(12)
        row.addWidget(pair(labelled("sample", self.itfa_sample, "fit.itfa_sample"), self.itfa_sample))
        self.itfa_pct = QLineEdit(); self.itfa_pct.setPlaceholderText("e.g. 100, 0")
        row.addWidget(pair(labelled("percentages (%, one per component)", self.itfa_pct, "fit.itfa_pct"), self.itfa_pct))
        b = QPushButton("Add target"); b.clicked.connect(self._add_target); row.addWidget(b)
        self.itfa_add_btn = b
        left.body.addWidget(row_box)
        self.targets = QListWidget(); self.targets.setAccessibleName("anchored targets"); left.body.addWidget(self.targets, 1)
        bb = FlowLayout(spacing=6)
        b1 = QPushButton("Remove target"); b1.clicked.connect(lambda: [self.targets.takeItem(self.targets.row(i)) for i in self.targets.selectedItems()]); bb.addWidget(b1)
        b2 = QPushButton("Run ITFA"); b2.setObjectName("primary"); b2.clicked.connect(self.run_itfa); bb.addWidget(b2)
        b3 = QPushButton("Export…"); b3.clicked.connect(self._export_itfa); bb.addWidget(b3)
        bbw = QWidget(); bbw.setLayout(bb); left.body.addWidget(bbw)
        self.itfa_btn, self.itfa_export_btn = b2, b3
        explain_all([(b, "fit.add_target"), (b1, "fit.remove_target"), (b2, "fit.run_itfa"), (b3, "fit.export_itfa")])
        self.itfa_chip = _chip()
        left.body.addWidget(self.itfa_chip)
        self.itfa_status = QLabel(""); self.itfa_status.setObjectName("muted"); self.itfa_status.setWordWrap(True); left.body.addWidget(self.itfa_status)
        lay.addWidget(left, 1)
        pc = Card("ITFA targeted component spectra", help_anchor="73-itfa")
        self.itfa_plot = make_plot(x="energy (eV)", legend="inside"); self.itfa_plot.getViewBox().disableAutoRange()
        pc.body.addWidget(self.itfa_plot, 1)
        self.itfa_table = QTableWidget(0, 1); self.itfa_table.setAccessibleName("ITFA component percentages per sample")
        self.itfa_table.setHorizontalHeaderLabels(["comp 1 %"]); _percent_tips(self.itfa_table)
        pc.body.addWidget(self.itfa_table, 1)
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

    def _clear_itfa(self) -> None:
        self.itfa_result = None
        self.itfa_table.setRowCount(0)
        set_chip(self.itfa_chip, "", "muted")
        self._draw_itfa()

    def run_itfa(self):
        """ITFA on the matrix of the MCR run (refused calmly when the ticked rows changed since), off the GUI thread.
        Returns the Job or None."""
        m = self.mcr_result
        if m is None:
            self.itfa_status.setText("run MCR-ALS first"); return None
        if self._busy("fit:itfa", self.itfa_status, "ITFA"):
            return None
        try:
            labels, E, D = self._matrix()
        except ValueError:
            labels, E, D = [], None, None
        if E is None or not same_matrix(m, labels, E, D):
            self._clear_itfa()
            self.itfa_status.setText("the spectra differ from the MCR run; rerun MCR first")
            self.update_actions()
            return None
        k = m.ST.shape[0]
        fixed = {}
        for i in range(self.targets.count()):
            name, pct = self.targets.item(i).data(Qt.UserRole)
            if name not in m.labels:
                continue
            if len(pct) > k:
                self.itfa_status.setText(f"target {name}: {len(pct)} percentages for {plural(k, 'component')}"); return None
            idx = m.labels.index(name)
            total = float(m.C[idx].sum()) or 1.0
            row = np.array(pct + [0.0] * (k - len(pct))) / 100.0
            rest = max(0.0, 1.0 - row.sum())
            free = np.array([1.0 if v == 0 else 0.0 for v in row])
            if free.sum():
                base = m.C[idx] * free
                row = row + (base / base.sum() * rest if base.sum() > 0 else free / free.sum() * rest)
            fixed[idx] = row * total
        if not fixed:
            self.itfa_status.setText("add at least one anchored target"); return None
        max_iter, tol = self.max_iter.value(), self.tol.value()
        self.itfa_status.setText("ITFA …")
        return jobs.of(self).submit("fit:itfa", lambda progress: itfa(None, m, fixed, max_iter, tol, progress=progress),
                                    on_done=lambda r: self._itfa_done(r, len(fixed)), label="ITFA", taskbar=self.taskbar)

    def _itfa_done(self, r: McrResult, n_fixed: int) -> None:
        self.itfa_result = r
        self._draw_itfa()
        k = r.ST.shape[0]
        labels = r.labels or self._mcr_labels
        props = r.proportions
        self.itfa_table.setRowCount(len(labels)); self.itfa_table.setColumnCount(k)
        self.itfa_table.setHorizontalHeaderLabels([f"comp {i + 1} %" for i in range(k)]); self.itfa_table.setVerticalHeaderLabels(labels)
        _percent_tips(self.itfa_table)
        for rr in range(len(labels)):
            for c in range(k):
                self.itfa_table.setItem(rr, c, QTableWidgetItem(f"{props[rr, c]:.1f}"))
        self.itfa_status.setText(f"ITFA: {plural(r.n_iter, 'iteration')}, RMSE {r.rmse:.4g}, {n_fixed} anchored "
                                 f"{'sample' if n_fixed == 1 else 'samples'}")
        set_chip(self.itfa_chip, *fit_status(r.converged, r.message, 0))
        self.update_actions()

    def _draw_itfa(self) -> None:
        self.itfa_plot.clear(); self.itfa_plot.plotItem.legend.clear()
        r = self.itfa_result
        E = (r.E if r is not None and r.E is not None else self._mcr_E)
        if r is None or E is None:
            set_plot_hint(self.itfa_plot, "Run ITFA (after MCR-ALS) to see the targeted components")
            return
        set_plot_hint(self.itfa_plot, None)
        for i in range(r.ST.shape[0]):
            self.itfa_plot.plot(E, r.ST[i], pen=pg.mkPen(curve(i), width=1.6, style=dash(i)), name=f"ITFA component {i + 1}")
        _set_range(self.itfa_plot, [float(E[0]), float(E[-1])], [r.ST])

    # ------------------------------------------------------------------ peaks
    def _peaks_tab(self) -> QWidget:
        w = QWidget(); lay = QHBoxLayout(w)
        left = Card("Peak decomposition", help_anchor="74-peaks")
        self.pk_trace = QComboBox()
        self.pk_trace.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.pk_trace.setMinimumContentsLength(14)
        left.body.addWidget(labelled("trace", self.pk_trace, "fit.pk_trace")); left.body.addWidget(self.pk_trace)
        row_box = QWidget(); row = FlowLayout(row_box, spacing=6)
        self.pk_n = QSpinBox(); self.pk_n.setRange(1, 8); self.pk_n.setValue(2); self.pk_n.setSuffix(unit_suffix("peaks"))
        row.addWidget(self.pk_n)
        self.pk_shape = QComboBox(); self.pk_shape.addItems(["gaussian", "lorentzian", "pseudo-voigt"])
        row.addWidget(pair(labelled("shape", self.pk_shape, "fit.pk_shape"), self.pk_shape))
        self.pk_step = QCheckBox("edge step (arctan)"); self.pk_step.setChecked(True); row.addWidget(self.pk_step)
        self.pk_bg = QComboBox(); self.pk_bg.addItems(["linear", "constant"])
        row.addWidget(pair(labelled("background", self.pk_bg, "fit.pk_bg"), self.pk_bg))
        left.body.addWidget(row_box)
        row2_box = QWidget(); row2 = FlowLayout(row2_box, spacing=6)
        self.pk_lo = QLineEdit(); self.pk_lo.setPlaceholderText("auto"); self.pk_hi = QLineEdit(); self.pk_hi.setPlaceholderText("auto")
        for e in (self.pk_lo, self.pk_hi):
            e.setMaximumWidth(90)
        row2.addWidget(pair(labelled("range (eV)", self.pk_lo, "fit.pk_lo"), self.pk_lo, self.pk_hi))
        self.pk_centres = QLineEdit(); self.pk_centres.setPlaceholderText("auto (comma separated eV)"); self.pk_centres.setMinimumWidth(180)
        row2.addWidget(pair(labelled("initial centres (eV)", self.pk_centres, "fit.pk_centres"), self.pk_centres))
        left.body.addWidget(row2_box)
        b = QPushButton("Run the decomposition"); b.setObjectName("primary"); b.clicked.connect(self.run_peaks); left.body.addWidget(b)
        self.pk_btn = b
        self.pk_chip = _chip()
        left.body.addWidget(self.pk_chip)
        self.pk_table = QTableWidget(0, 4); self.pk_table.setHorizontalHeaderLabels(["parameter", "value", "± (covariance)", "unit"])
        self.pk_table.setAccessibleName("peak parameters")
        header_tips(self.pk_table, {0: "fit.col.parameter", 1: "fit.col.value", 2: "fit.col.error", 3: "fit.col.unit"})
        left.body.addWidget(self.pk_table, 1)
        self.pk_areas = QLabel(""); self.pk_areas.setObjectName("muted"); self.pk_areas.setWordWrap(True); left.body.addWidget(self.pk_areas)
        self.pk_info = QLabel(""); self.pk_info.setObjectName("muted"); self.pk_info.setWordWrap(True); left.body.addWidget(self.pk_info)
        eb = QPushButton("Export…"); eb.clicked.connect(self._export_peaks); left.body.addWidget(eb)
        self.pk_export_btn = eb
        explain_all([(self.pk_n, "fit.pk_n"), (self.pk_step, "fit.pk_step"), (self.pk_hi, "fit.pk_hi"), (b, "fit.run_peaks"),
                     (eb, "fit.export_peaks")])
        lay.addWidget(left, 1)
        pc = Card("Data, fit, components and residual", help_anchor="74-peaks")
        self.pk_plot = make_plot(x="energy (eV)", legend="inside"); self.pk_plot.getViewBox().disableAutoRange()
        pc.body.addWidget(self.pk_plot, 1)
        lay.addWidget(pc, 2)
        return w

    def run_peaks(self) -> None:
        from aurora.core import fit as core_fit
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
            r = core_fit.peak_decomposition(E, y, self.pk_n.value(), self.pk_shape.currentText(), self.pk_step.isChecked(),
                                            self.pk_bg.currentText(), centres, fit_range=self._range(self.pk_lo, self.pk_hi))
        except ValueError as err:
            self.pk_info.setText(str(err)); return
        self.peak_result = r; self._pk_name = name
        self._fill_pk_table()
        self.pk_info.setText(f"{'converged' if r.converged else 'not converged: ' + r.message} · RMSE {r.rmse:.4g} · {len(r.E)} points"
                             + ("" if r.converged else " · the fit is not drawn; the dashed lines mark the initial centres"))
        self._draw_peaks()
        self.update_actions()

    def _pk_rows(self, r, areas: bool = True) -> list[tuple[str, float, float, str, str]]:
        """(parameter, value, error, unit, why it is tinted or '') of every parameter (and every peak area)."""
        und = set(getattr(r, "undetermined", []))
        at_bound = set(getattr(r, "centres_at_bound", []))
        rows = []
        for k, v in r.params.items():
            e = r.errors.get(k, float("nan"))
            why = ""
            if k in at_bound:
                why = "the centre sits at the edge of the fit range: widen the range or move the initial centre"
            elif k in und:
                why = "σ > |value|: the data do not determine this parameter"
            rows.append((k, v, e, unit_of(k), why))
        for k, (v, e) in ((getattr(r, "areas", {}) or {}).items() if areas else ()):
            why = "σ > |value|: the area is not determined" if not np.isfinite(e) or e > abs(v) else ""
            rows.append((k, v, e, unit_of(k), why))
        return rows

    def _fill_pk_table(self) -> None:
        r = self.peak_result
        if r is None:
            self.pk_table.setRowCount(0)
            set_chip(self.pk_chip, "", "muted")
            self.pk_areas.setText("")
            self.pk_areas.hide()
            return
        rows = self._pk_rows(r, areas=False)
        self.pk_table.setRowCount(len(rows))
        for i, (k, v, e, unit, why) in enumerate(rows):
            cells = [QTableWidgetItem(k), QTableWidgetItem(f"{v:.4g}"),
                     QTableWidgetItem(f"{e:.2g}" if np.isfinite(e) else "–"), QTableWidgetItem(unit)]
            for c, it in enumerate(cells):
                if why:
                    tint(it, "warn", why)
                self.pk_table.setItem(i, c, it)
        self.pk_table.resizeColumnsToContents()
        n_und = len(getattr(r, "undetermined", []))
        set_chip(self.pk_chip, *fit_status(r.converged, r.message, n_und))
        areas = getattr(r, "areas", {}) or {}
        self.pk_areas.setText(("peak areas (a.u.·eV, ± from the covariance): " + " · ".join(
            f"{k.split('_')[0]} {v:.4g} ± {e:.2g}" if np.isfinite(e) else f"{k.split('_')[0]} {v:.4g} ± –"
            for k, (v, e) in areas.items())) if areas and r.converged else "")
        self.pk_areas.setVisible(bool(self.pk_areas.text()))

    def _draw_peaks(self) -> None:
        self.pk_plot.clear(); self.pk_plot.plotItem.legend.clear()
        r = self.peak_result
        if r is None:
            set_plot_hint(self.pk_plot, "choose a trace and Run the decomposition")
            return
        set_plot_hint(self.pk_plot, None)
        self.pk_plot.plot(r.E, r.y, pen=role_pen("ink", 1.6), name=self._pk_name)
        if not r.converged:
            # a fit that did not converge is not drawn (HANDOFF §7): the data, the initial centres and a note
            init = r.initial_centres() if hasattr(r, "initial_centres") else []
            for c in init:
                self.pk_plot.addItem(pg.InfiniteLine(c, angle=90, pen=role_pen("edge", 1.0)), ignoreBounds=True)
            from aurora.app.theme import TOKENS
            note = pg.TextItem("fit not shown: not converged", color=TOKENS["bad_fg"], anchor=(1, 0))
            ymax = float(np.nanmax(r.y)) if np.isfinite(r.y).any() else 1.0
            note.setPos(float(r.E[-1]), ymax)                # top right, clear of the data
            self.pk_plot.addItem(note, ignoreBounds=True)
            _set_range(self.pk_plot, [float(r.E[0]), float(r.E[-1])], [r.y])
            return
        self.pk_plot.plot(r.E, r.fit, pen=role_pen("fit", 1.6), name="fit")
        for i, (k, c) in enumerate(r.components.items()):
            j = _component_index(i)
            self.pk_plot.plot(r.E, c, pen=pg.mkPen(curve(j), width=1, style=dash(j)), name=k)
        resid = r.y - r.fit - 0.15 * (np.nanmax(r.y) - np.nanmin(r.y))
        self.pk_plot.plot(r.E, resid, pen=role_pen("resid", 1), name="residual (offset)")
        _set_range(self.pk_plot, [float(r.E[0]), float(r.E[-1])], [r.y, r.fit, resid])

    def peaks_figure_series(self):
        from aurora.app.figure_export import export_colour, role_colour
        from aurora.core.figures import FigureSeries
        r = self.peak_result
        c, d = role_colour("ink")
        out = [FigureSeries(self._pk_name or "data", r.E, r.y, c, d, 1.2)]
        if not r.converged:
            c, d = role_colour("edge")
            out.append(FigureSeries("initial centres", np.asarray(r.initial_centres(), float), None, c, "dot", 0.8, kind="vline"))
            from aurora.app.theme import export_tokens
            out.append(FigureSeries("fit not shown: not converged", np.array([]), None, export_tokens()["bad_fg"], kind="text"))
            return out
        c, d = role_colour("fit")
        out.append(FigureSeries("fit", r.E, r.fit, c, d, 1.0))
        for i, (k, comp) in enumerate(r.components.items()):
            c, _lab, d = export_colour(_component_index(i))
            out.append(FigureSeries(k, r.E, comp, c, d, 0.8))
        c, d = role_colour("resid")
        out.append(FigureSeries("residual (offset)", r.E, r.y - r.fit - 0.15 * (np.nanmax(r.y) - np.nanmin(r.y)), c, d, 0.8))
        return out

    def _export_peaks(self) -> None:
        if self.peak_result is None:
            self.pk_info.setText("run the decomposition first"); return
        p, _ = QFileDialog.getSaveFileName(self, "Export peak decomposition", default_path("fit", "peaks.csv", self.lib),
                                           "CSV (*.csv)")
        if p:
            remember_dir("fit", p)
            out = []
            with guarded_write(self, Path(p).name) as g:
                out = self.export_peaks(Path(p))
            if g.ok:
                notify_saved(self.pk_info, out)

    def export_peaks(self, path: Path) -> list[Path]:
        """Every parameter with its uncertainty and unit, the peak areas, the curves as CSV, and the figure."""
        from aurora import __version__
        r = self.peak_result
        with open_atomic(path) as f:
            f.write(f"# peak decomposition of {self._pk_name}; {'converged' if r.converged else 'not converged: ' + r.message}; RMSE {r.rmse:.6g}\n")
            f.write(f"# AURORA {__version__} (version 2 of John Bussey's SGM XAS GUI)\n")
            f.write(f"# shape {getattr(r, 'shape', '')}, parameterised by the FWHM in eV; step_width = the 25-75 % rise "
                    f"of the arctan step (the FWHM of its derivative); areas from the covariance\n")
            f.write("parameter,value,uncertainty,unit,note\n")
            for k, v, e, unit, why in self._pk_rows(r):
                f.write(f"{k},{v:.6g},{e:.6g},{unit},{why.split(':')[0] if why else ''}\n")
            if r.converged:
                f.write("\nenergy_eV,data,fit,residual," + ",".join(r.components) + "\n")
                for i in range(len(r.E)):
                    f.write(f"{r.E[i]:.6g},{r.y[i]:.6g},{r.fit[i]:.6g},{r.y[i] - r.fit[i]:.6g}," + ",".join(f"{c[i]:.6g}" for c in r.components.values()) + "\n")
            else:
                f.write("\nenergy_eV,data\n")
                for i in range(len(r.E)):
                    f.write(f"{r.E[i]:.6g},{r.y[i]:.6g}\n")
        return [path, *self._save_figure(path, self.peaks_figure_series(), "normalised signal (edge step = 1)",
                                         f"peaks of {self._pk_name}")]

    def _export_itfa(self) -> None:
        if self.itfa_result is None:
            self.itfa_status.setText("run ITFA first"); return
        d = QFileDialog.getExistingDirectory(self, "Folder for the ITFA_* files", default_dir("fit", self.lib))
        if d:
            remember_dir("fit", Path(d))
            with guarded_write(self, "the ITFA files") as g:
                out = self.export_mcr(Path(d), self.itfa_result, "ITFA")
            if g.ok:
                notify_saved(self.itfa_status, out)
