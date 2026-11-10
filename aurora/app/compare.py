"""Compare perspective (John Bussey's tab 3): the library table, the stacked plot, normalisation diagnostics, peaks.

Every cell shows the state in force: typed editors per column (a combo for Norm, Signal and E0, spin boxes with units
for the energies), and a cell that cannot be read is put back with the reason in the message line under the table.
The figure export draws the shown traces again at a fixed size (aurora.core.figures), never a screen grab.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSplitter,
                               QStyledItemDelegate, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from aurora.app import mode, nav
from aurora.app.help_text import HELP, explain, header_tips, labelled, say
from aurora.app.library import Library
from aurora.app.mode import mark_advanced, mark_advanced_column, on_mode_changed
from aurora.app.theme import CURVE_NAMES, CURVES, curve, dash, label_for, make_plot, on_theme_changed, role_pen, set_tone, swatch_icon
from aurora.app.vocab import edge_words, plural, signal_label, unit_suffix
from aurora.app.widgets import (Card, ElidedLabel, EmptyState, FlowLayout, default_dir, default_path, guarded_write,
                                notify_saved, overlay_host, pair, remember_dir, set_enabled, set_plot_hint)
from aurora.core.compare import (E0_MODE_TEXT, E0_MODES, NORMS, derivative, difference, export_wide_csv,
                                 legacy_auto_windows, local_peaks, sidecar_path, stack_offsets)

# (header, help key) per column, in John Bussey's order with Fit next to Show so it is always in view
COLUMNS = (("Show", "show"), ("Fit", "fit"), ("Label", "label"), ("Signal", "signal"), ("Norm", "norm"),
           ("Shift eV", "shift"), ("Pre lo", "pre_lo"), ("Pre hi", "pre_hi"), ("Post lo", "post_lo"),
           ("Post hi", "post_hi"), ("E0", "e0_mode"), ("E0 eV", "e0"), ("Flatten", "flatten"), ("Spacing", "spacing"),
           ("Scale", "scale"), ("Diag", "diag"), ("Colour", "colour"))
COLS = tuple(h for h, _k in COLUMNS)
COL = {k: i for i, (_h, k) in enumerate(COLUMNS)}
COL_HELP = tuple(k for _h, k in COLUMNS)
BOOL_COLS = (COL["show"], COL["fit"], COL["flatten"], COL["diag"])
EASY_COLS = (COL["show"], COL["fit"], COL["label"], COL["signal"], COL["shift"], COL["colour"])
ADVANCED_COLS = tuple(i for i in range(len(COLUMNS)) if i not in EASY_COLS)
LABEL_MIN_PX = 170                  # the Label column in Advanced: at least this wide, the table scrolls sideways
EMPTY_TEXT = {   # Advanced?
    False: "No spectra here yet. Save spectra in Check (Save ticked → Compare), or add a folder of saved spectra.",
    True: "The library is empty: add saved spectra, references, or send a zone's traces from Reduce.",
}
EASY_PRE, EASY_POST = tuple(mode.EASY_DEFAULTS["compare"]["pre"]), tuple(mode.EASY_DEFAULTS["compare"]["post"])   # eV from E0
EASY_E0_WORDS = {"deriv": "where the spectrum rises fastest", "half": "where the spectrum reaches half of its edge step",
                 "table": "the table value of the edge", "fixed": "a value typed in Advanced mode"}
AUTO_PRE, AUTO_POST = "auto 15 %", "auto 20 %"


def pick_colour(parent: QWidget, initial: str, title: str) -> str | None:
    """A colour chosen in QColorDialog ('#RRGGBB'), None when cancelled."""
    d = QColorDialog(QColor(initial), parent)
    d.setWindowTitle(title)
    ok = d.exec()
    out = d.currentColor().name().upper() if ok else None
    d.deleteLater()
    return out


def colour_name(c: str) -> str:
    c = (c or "").upper()
    return CURVE_NAMES[CURVES.index(c)] if c in CURVES else c


class _ComboDelegate(QStyledItemDelegate):
    """A combo box editor; items_fn(row) gives the items."""

    def __init__(self, items_fn, name: str, parent=None):
        super().__init__(parent)
        self.items_fn = items_fn
        self.name = name

    def items(self, row: int) -> list[str]:
        return list(self.items_fn(row))

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.addItems(self.items(index.row()))
        cb.setAccessibleName(self.name)
        return cb

    def setEditorData(self, editor, index):
        i = editor.findText(str(index.data() or ""))
        editor.setCurrentIndex(max(0, i))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText())


class _SpinDelegate(QStyledItemDelegate):
    """A spin box editor with a unit; spec_fn(row) -> (lo, hi, decimals, step, suffix, special text or '')."""

    def __init__(self, spec_fn, name: str, parent=None):
        super().__init__(parent)
        self.spec_fn = spec_fn
        self.name = name

    def createEditor(self, parent, option, index):
        lo, hi, dec, step, suffix, special = self.spec_fn(index.row())
        sp = QDoubleSpinBox(parent)
        sp.setRange(lo, hi)
        sp.setDecimals(dec)
        sp.setSingleStep(step)
        sp.setSuffix(suffix)
        if special:
            sp.setSpecialValueText(special)
        sp.setAccessibleName(self.name)
        return sp

    def setEditorData(self, editor, index):
        try:
            editor.setValue(float(str(index.data() or "").split()[0]))
        except (ValueError, IndexError):
            editor.setValue(editor.minimum())

    def setModelData(self, editor, model, index):
        v = editor.value()
        special = editor.specialValueText()
        model.setData(index, "" if special and v <= editor.minimum() else f"{v:g}")


class ComparePerspective(QWidget):
    def __init__(self, library: Library, parent: QWidget | None = None):
        super().__init__(parent)
        self.lib = library
        self._filling = False
        self._missing_seen: set = set()
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(12)
        head = QHBoxLayout()
        h1 = QLabel("Compare"); h1.setObjectName("h1")
        self.subtitle = sub = ElidedLabel(say("compare.subtitle"), Qt.ElideRight); sub.setObjectName("muted")
        head.addWidget(h1); head.addWidget(sub, 1)
        self.head_buttons: dict[str, QPushButton] = {}
        for text, fn, key in (("Add saved spectra…", self._add_folder, "add_folder"), ("Add CSV…", self._add_csv, "add_csv"),
                              ("References…", self._references, "references"),
                              ("Save config…", self._save_config, "save_config"),
                              ("Load config…", self._load_config, "load_config"),
                              ("Figure size…", self._figure_options, "figure_size"), ("Export…", self._export, "export")):
            b = QPushButton(text); b.clicked.connect(fn); head.addWidget(b)
            explain(b, f"compare.{key}")
            self.head_buttons[text] = b
        self.export_btn = self.head_buttons["Export…"]
        self.figure_btn = self.head_buttons["Figure size…"]
        root.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        self.library_card = left = Card("Library", help_anchor="62-the-table")
        from aurora.app.help_text import term_button
        self.signal_help = term_button("signal")            # Easy: 'What is the signal?' (the Signal column)
        self.signal_help.setToolTip(self.signal_help.toolTip() + "\nThe Signal column says which one each row plots.")
        left.header.addWidget(self.signal_help)
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
        self.default_shift = QDoubleSpinBox(); self.default_shift.setRange(-100, 100); self.default_shift.setDecimals(2)
        self.default_shift.setSingleStep(0.1); self.default_shift.setSuffix(unit_suffix("eV"))
        self.default_shift.setValue(self.lib.default_shift)
        self.default_shift_pair = pair(labelled("default shift for new rows", self.default_shift, "compare.default_shift"),
                                       self.default_shift)
        ops.addWidget(self.default_shift_pair)
        left.body.addWidget(ops_box)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setAccessibleName("library of spectra")
        self.table.setHorizontalScrollMode(QTableWidget.ScrollPerPixel)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemSelectionChanged.connect(self._draw_diagnostics)
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.delegates = {
            COL["norm"]: _ComboDelegate(lambda _r: NORMS, "normalisation", self.table),
            COL["signal"]: _ComboDelegate(self._signals_of_row, "signal", self.table),
            COL["e0_mode"]: _ComboDelegate(self._e0_modes_of_row, "E0 choice", self.table),
            COL["shift"]: _SpinDelegate(lambda _r: (-100.0, 100.0, 3, 0.1, unit_suffix("eV"), ""), "shift", self.table),
            COL["e0"]: _SpinDelegate(lambda _r: (0.0, 30000.0, 2, 0.1, unit_suffix("eV"), "derivative"), "E0", self.table),
            COL["spacing"]: _SpinDelegate(lambda _r: (-0.1, 100.0, 2, 0.1, " (signal units)", "common"), "spacing", self.table),
            COL["scale"]: _SpinDelegate(lambda _r: (0.001, 1000.0, 3, 0.1, " ×", ""), "scale", self.table),
        }
        for key in ("pre_lo", "pre_hi", "post_lo", "post_hi"):
            self.delegates[COL[key]] = _SpinDelegate(lambda r, k=key: self._window_spec(r, k), key.replace("_", " "), self.table)
        for c, d in self.delegates.items():
            self.table.setItemDelegateForColumn(c, d)
        left.body.addWidget(self.table, 1)
        # the message line: what could not be read, missing files (with Locate…), what was written; replot keeps it
        self.message_row = QWidget()
        ml = QHBoxLayout(self.message_row); ml.setContentsMargins(0, 0, 0, 0)
        self.message = QLabel(""); self.message.setWordWrap(True)
        ml.addWidget(self.message, 1)
        self.locate_btn = QPushButton("Locate…"); self.locate_btn.clicked.connect(self._locate); ml.addWidget(self.locate_btn)
        self.dismiss_btn = QPushButton("Dismiss"); self.dismiss_btn.clicked.connect(self.clear_message); ml.addWidget(self.dismiss_btn)
        explain(self.locate_btn, "compare.locate")
        explain(self.dismiss_btn, "compare.dismiss")
        self.message_row.hide()
        left.body.addWidget(self.message_row)
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
        from aurora.core.compare import E0_SMOOTH_EV
        self.e0_smooth = QDoubleSpinBox(); self.e0_smooth.setRange(0.0, 20.0); self.e0_smooth.setDecimals(2)
        self.e0_smooth.setSingleStep(0.1); self.e0_smooth.setValue(E0_SMOOTH_EV); self.e0_smooth.setSuffix(unit_suffix("eV"))
        rng.addWidget(pair(labelled("E0 derivative smoothing", self.e0_smooth, "compare.e0_smooth"), self.e0_smooth))
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
        self.label_inset = QDoubleSpinBox(); self.label_inset.setRange(0, 200); self.label_inset.setDecimals(1)
        self.label_inset.setSingleStep(0.5); self.label_inset.setSuffix(unit_suffix("eV"))
        inset_pair = pair(labelled("labels in from the right edge", self.label_inset, "compare.label_inset"), self.label_inset)
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
        # Advanced: spacing, labels, label position, derivative, difference; mark peaks with prominence and distance
        self.plot_opts = (spacing_pair, self.labels_on, self.signal_in_label, inset_pair, self.deriv, self.deriv_smooth,
                          self.diff_btn)
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
        self.diag_card = dc = Card(say("compare.diag_title"), help_anchor="64-diagnostics-and-export")
        self.diag = make_plot(x="incident energy (eV)", y="raw signal", legend="inside")
        self.diag.getViewBox().disableAutoRange()
        dc.body.addWidget(self.diag, 1)
        self.diag_note = QLabel(""); self.diag_note.setObjectName("muted"); self.diag_note.setWordWrap(True)
        dc.body.addWidget(self.diag_note)
        self.peaks_label = QLabel(""); self.peaks_label.setObjectName("muted"); self.peaks_label.setWordWrap(True)
        dc.body.addWidget(self.peaks_label)
        right.addWidget(dc)
        right.setSizes([560, 260])
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        dc.setMinimumHeight(200)
        self.diag_card = dc
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([600, 900])
        self.split = split
        self.body = overlay_host(split)
        root.addWidget(self.body, 1)
        self.empty_state = EmptyState(EMPTY_TEXT[False], [], icon="compare", parent=self.body)
        self._empty_content()
        for w, key in ((self.labels_on, "labels"), (self.signal_in_label, "signal_in_label"), (self.deriv, "deriv"),
                       (self.deriv_smooth, "smooth"), (self.diff_btn, "difference"), (self.show_peaks, "peaks")):
            explain(w, f"compare.{key}")
        header_tips(self.table, {i: f"compare.col.{k}" for i, k in enumerate(COL_HELP)})
        for w in (self.x_lo, self.x_hi):
            w.editingFinished.connect(self._view_changed)
        self.spacing.valueChanged.connect(self._view_changed)
        self.label_inset.valueChanged.connect(self._view_changed)
        for w in (self.labels_on, self.signal_in_label):
            w.toggled.connect(self._view_changed)
        for w in (self.show_peaks, self.deriv):
            w.toggled.connect(self.replot)
        self.prominence.valueChanged.connect(self.replot); self.distance.valueChanged.connect(self.replot)
        self.deriv_smooth.valueChanged.connect(self.replot)
        self.default_shift.valueChanged.connect(self._default_shift_changed)
        library.changed.connect(self._fill_table)
        library.changed.connect(self.replot)
        library.changed.connect(self.update_actions)
        library.changed.connect(self._check_missing)
        library.changed.connect(self._draw_diagnostics)
        library.restored.connect(self._apply_view)
        on_theme_changed(self, lambda _s: (self._fill_table(), self.replot(), self._draw_diagnostics(), self._retone()))
        # Easy mode: John Bussey's Compare (saved spectra, show / fit / label / signal / shift / colour, x range, export)
        h, o = self.head_buttons, self.op_buttons
        mark_advanced(h["Add CSV…"], h["References…"], h["Save config…"], h["Load config…"], o["Remove sample"],
                      o["Rename"], o["Duplicate"], self.default_shift_pair, self.range_row, *self.plot_opts,
                      *self.peaks_row)
        mark_advanced_column(self.table, *ADVANCED_COLS)
        from aurora.app.mode import mark_easy
        mark_easy(self.signal_help)
        on_mode_changed(self, lambda _adv: self._mode_texts(), call_now=False)
        self._fill_table()
        self.replot()
        self._draw_diagnostics()
        self.update_actions()
        self._easy_split()

    def _empty_content(self) -> None:
        """The empty library says what each mode can do (Easy: saved spectra and Check)."""
        if mode.is_advanced():
            buttons = [("Add saved spectra…", self._add_folder), ("References…", self._references, True)]
        else:
            buttons = [("Add saved spectra…", self._add_folder), ("Go to Check", lambda: nav.go(self, "check"))]
        self.empty_state.set_content(EMPTY_TEXT[mode.is_advanced()], buttons)

    def _easy_split(self) -> None:
        """Easy: the library (six columns, signal words) takes 45 % of the width; Advanced keeps 40 %."""
        total = max(sum(self.split.sizes()), 1000)
        self.split.setSizes([int(total * (0.4 if mode.is_advanced() else 0.45)),
                             int(total * (0.6 if mode.is_advanced() else 0.55))])

    def _mode_texts(self) -> None:
        self._easy_split()
        self._apply_widths()
        self.subtitle.setText(say("compare.subtitle"))
        self.diag_card.set_title(say("compare.diag_title"))
        self._empty_content()
        self._fill_table()
        self.replot()
        self._draw_diagnostics()

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

    def col(self, header: str) -> int:
        """The column index of a header ('Label', 'Fit', ...)."""
        return COLS.index(header)

    # ------------------------------------------------------------------ message line
    def say(self, text: str, tone: str = "warn", locate: bool = False) -> None:
        """Show a message under the table; replot never overwrites it (Dismiss, or the next message, does)."""
        self.message.setTextFormat(Qt.PlainText)
        self.message.setText(text)
        self.message.setProperty("aurora_tone", tone)
        set_tone(self.message, tone)
        self.locate_btn.setVisible(locate)
        self.dismiss_btn.setVisible(not locate)        # a missing-files message stays until the files are found
        self.message_row.show()

    def notify(self, paths) -> str:
        set_tone(self.message, "muted")
        self.message.setProperty("aurora_tone", "muted")
        self.locate_btn.hide()
        self.dismiss_btn.show()
        self.message_row.show()
        return notify_saved(self.message, paths)

    def clear_message(self) -> None:
        self.message.setText("")
        self.message_row.hide()

    def _retone(self) -> None:
        tone = self.message.property("aurora_tone")
        if tone:
            set_tone(self.message, tone)

    def _check_missing(self) -> None:
        miss = set(self.lib.missing)
        if miss and miss != self._missing_seen:
            names = ", ".join(Path(v).name or k for k, v in list(self.lib.missing.items())[:4])
            more = f" and {len(miss) - 4} more" if len(miss) > 4 else ""
            self.say(f"{plural(len(miss), 'file')} of the library not found ({names}{more}): their rows stay in the "
                     f"table without a spectrum. Locate… finds a moved folder.", "warn", locate=True)
        elif not miss and self.locate_btn.isVisible():
            self.clear_message()
        self._missing_seen = miss

    def _locate(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder that now holds the missing spectra",
                                             default_dir("reduced", self.lib))
        if not d:
            return
        remember_dir("reduced", Path(d))
        found, still = self.lib.relocate(Path(d))
        if still:
            self.say(f"{plural(found, 'file')} found under {d}; still missing: {', '.join(still)}", "warn", locate=True)
        else:
            self.say(f"{plural(found, 'file')} found under {d}: every row has its spectrum again", "ok")

    # ------------------------------------------------------------------ table
    def _signals_of_row(self, row: int) -> list[str]:
        if row >= len(self.lib.requests):
            return []
        return self.lib.signals_of(self.lib.requests[row].label)

    def _e0_modes_of_row(self, row: int) -> list[str]:
        if row >= len(self.lib.requests):
            return list(E0_MODES)
        me = self.lib.requests[row]
        return list(E0_MODES) + [f"row:{r.key}" for r in self.lib.requests if r is not me]

    def _window_spec(self, row: int, key: str):
        r = self.lib.requests[row] if row < len(self.lib.requests) else None
        if r is not None and r.norm == "legacy":
            return (0.0, 30000.0, 2, 0.5, unit_suffix("eV"), AUTO_PRE if key.startswith("pre") else AUTO_POST)
        return (-200.0, 400.0, 2, 0.5, unit_suffix("eV"), "")

    def _cell_values(self, r, nr) -> dict:
        if r.norm == "legacy":
            pre = [AUTO_PRE] * 2 if r.legacy_pre is None else [f"{v:g}" for v in r.legacy_pre]
            post = [AUTO_POST] * 2 if r.legacy_post is None else [f"{v:g}" for v in r.legacy_post]
        else:
            pre = [f"{v:g}" for v in r.pre]
            post = [f"{v:g}" for v in r.post]
        e0 = "" if nr is None or not np.isfinite(nr.e0) else f"{nr.e0:.2f}"
        return {"show": r.show, "fit": r.fit, "label": r.label, "signal": r.signal, "norm": r.norm,
                "shift": f"{r.shift:g}", "pre_lo": pre[0], "pre_hi": pre[1], "post_lo": post[0], "post_hi": post[1],
                "e0_mode": r.e0_mode, "e0": e0, "flatten": r.flatten,
                "spacing": "" if r.spacing_after is None else f"{r.spacing_after:g}", "scale": f"{r.scale:g}",
                "diag": r.diagnostics, "colour": ""}

    def _fill_row(self, i: int, r, nr) -> None:
        """Write a row's cells. Existing items are updated in place, never replaced, so an edit handled inside the
        item's own itemChanged signal never deletes the item that emitted it."""
        vals = self._cell_values(r, nr)
        editable = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable
        for c, (_h, key) in enumerate(COLUMNS):
            v = vals[key]
            it = self.table.item(i, c)
            if it is None:
                it = QTableWidgetItem()
                self.table.setItem(i, c, it)
            tip = ""
            if c in BOOL_COLS:
                it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                it.setText("")
                it.setCheckState(Qt.Checked if v else Qt.Unchecked)
            else:
                it.setFlags((Qt.ItemIsEnabled | Qt.ItemIsSelectable) if c == COL["colour"] else editable)
                it.setText(signal_label(str(v), mode.is_advanced()) if key == "signal" else str(v))
                if key == "signal":
                    it.setData(Qt.UserRole, str(v))
            if c == COL["label"]:                      # ink text plus the series swatch (never coloured text)
                it.setIcon(swatch_icon(r.colour or curve(i), dash(i)))
            if c == COL["e0"]:
                how = E0_MODE_TEXT.get(r.e0_mode, f"same as row {r.e0_mode[4:]}" if r.e0_mode.startswith("row:") else r.e0_mode)
                tip = (f"E0 found: {how}." + (f"\n{nr.e0_note}" if nr is not None and nr.e0_note else "")
                       + "\nType a value to fix E0; empty it for the derivative again.")
            if c in (COL["pre_lo"], COL["pre_hi"], COL["post_lo"], COL["post_hi"]) and r.norm == "legacy":
                tip = "legacy: absolute energies in eV; 'auto' = the first 15 % / the last 20 % of the scan"
            it.setToolTip(tip)
        b = self.table.cellWidget(i, COL["colour"])
        if not isinstance(b, QToolButton):             # one button per row, reused (never a stale one left behind)
            b = QToolButton()
            b.setAutoRaise(True)
            explain(b, "compare.colour")
            b.clicked.connect(lambda _c=False, bb=b: self._choose_colour_at(bb.property("aurora_row")))
            self.table.setCellWidget(i, COL["colour"], b)
        b.setProperty("aurora_row", i)
        b.setIcon(swatch_icon(r.colour or curve(i), dash(i), 14))
        b.setAccessibleName(f"colour of {r.key}: {colour_name(r.colour or curve(i))}")
        b.setToolTip(f"{HELP['compare.colour'][0]} Now {colour_name(r.colour or curve(i))}.")

    def _fill_table(self) -> None:
        self._filling = True
        try:
            results = {id(r): nr for r, _s, nr in self.lib.processed()}
            self.table.setRowCount(len(self.lib.requests))
            for i, r in enumerate(self.lib.requests):
                self._fill_row(i, r, results.get(id(r)))
            self._apply_widths()
        finally:
            self._filling = False
        if abs(self.default_shift.value() - self.lib.default_shift) > 1e-12:
            self.default_shift.blockSignals(True)
            self.default_shift.setValue(self.lib.default_shift)
            self.default_shift.blockSignals(False)

    def _apply_widths(self) -> None:
        """Columns sized to their content; the Label column stretches in Easy and keeps a real width in Advanced (the
        table scrolls sideways there), so labels stay readable with every column shown."""
        hdr = self.table.horizontalHeader()
        lab = COL["label"]
        hdr.setSectionResizeMode(lab, QHeaderView.Interactive)
        self.table.resizeColumnsToContents()
        if mode.is_advanced():
            hdr.setSectionResizeMode(lab, QHeaderView.Interactive)
            self.table.setColumnWidth(lab, max(LABEL_MIN_PX, min(self.table.sizeHintForColumn(lab) + 12, 320)))
        else:
            hdr.setSectionResizeMode(lab, QHeaderView.Stretch)

    def _refill_row(self, row: int) -> None:
        if row >= len(self.lib.requests):
            return
        r = self.lib.requests[row]
        self._filling = True
        try:
            self._fill_row(row, r, self.lib.process_row(r))
        finally:
            self._filling = False

    def _choose_colour_at(self, row) -> None:
        if isinstance(row, int) and 0 <= row < len(self.lib.requests):
            self._choose_colour(self.lib.requests[row])

    def _choose_colour(self, r) -> None:
        if r not in self.lib.requests:
            return
        i = self.lib.requests.index(r)
        c = pick_colour(self, r.colour or curve(i), f"Colour of {r.key}")
        if c:
            r.colour = c
            self.lib.changed.emit()

    def _apply_cell(self, r, row: int, col: int, it: QTableWidgetItem, txt: str) -> None:
        """Write one edited cell into its row; ValueError(reason) when the text cannot be used."""
        key = COLUMNS[col][1]
        if key in ("show", "fit", "flatten", "diag"):
            on = it.checkState() == Qt.Checked
            setattr(r, {"show": "show", "fit": "fit", "flatten": "flatten", "diag": "diagnostics"}[key], on)
        elif key == "label":
            if txt and txt != r.label and not self.lib.rename(r.label, txt):
                raise ValueError(f"a sample named {txt} already exists")
        elif key == "signal":
            sigs = self.lib.signals_of(r.label)
            code = next((c for c in sigs if txt in (c, signal_label(c), signal_label(c, True))), None)
            if code is None:
                raise ValueError(f"not a signal of {r.label} ({', '.join(signal_label(c, True) for c in sigs)})")
            r.signal = code
        elif key == "norm":
            if txt not in NORMS:
                raise ValueError(f"not one of {', '.join(NORMS)}")
            r.norm = txt
        elif key == "shift":
            r.shift = self._number(txt or "0", "a shift in eV")
        elif key in ("pre_lo", "pre_hi", "post_lo", "post_hi"):
            self._apply_window(r, key, txt)
        elif key == "e0_mode":
            if txt not in E0_MODES and not (txt.startswith("row:") and self.lib.row_by_key(txt[4:]) not in (None, r)):
                raise ValueError(f"not one of {', '.join(E0_MODES)} or row:<another row>")
            if txt == "fixed" and r.e0 is None:
                nr = self.lib.process_row(r)
                r.e0 = float(nr.e0) if nr is not None and np.isfinite(nr.e0) else None
            r.e0_mode = txt
        elif key == "e0":
            if not txt:
                r.e0_mode, r.e0 = "deriv", None
            else:
                v = self._number(txt, "an energy in eV")
                if v <= 0:
                    raise ValueError("E0 must be a positive energy")
                r.e0_mode, r.e0 = "fixed", v
        elif key == "spacing":
            r.spacing_after = None if not txt else self._number(txt, "an offset")
        elif key == "scale":
            v = self._number(txt or "1", "a multiplier")
            if v == 0:
                raise ValueError("a scale of 0 hides the trace")
            r.scale = v

    @staticmethod
    def _number(txt: str, what: str) -> float:
        try:
            v = float(txt.split()[0]) if txt.split() else float(txt)
        except (ValueError, IndexError):
            raise ValueError(f"not {what}") from None
        if not np.isfinite(v):
            raise ValueError(f"not {what}")
        return v

    def _apply_window(self, r, key: str, txt: str) -> None:
        lo_hi = 0 if key.endswith("_lo") else 1
        pre = key.startswith("pre")
        if r.norm == "legacy":
            blank = (not txt) or txt.startswith("auto")
            s = self.lib.spectrum_of(r)
            auto = legacy_auto_windows(s.E + r.shift) if s is not None else ((0.0, 0.0), (0.0, 0.0))
            cur = (r.legacy_pre if pre else r.legacy_post) or (auto[0] if pre else auto[1])
            if blank:
                new = None
            else:
                v = self._number(txt, "an energy in eV")
                new = (v, cur[1]) if lo_hi == 0 else (cur[0], v)
                if new[0] >= new[1]:
                    raise ValueError("the window must start below its end")
            if pre:
                r.legacy_pre = new
            else:
                r.legacy_post = new
            return
        v = self._number(txt, "eV from E0")
        cur = r.pre if pre else r.post
        new = (v, cur[1]) if lo_hi == 0 else (cur[0], v)
        if new[0] >= new[1]:
            raise ValueError("the window must start below its end")
        if pre:
            r.pre = new
        else:
            r.post = new

    def _item_changed(self, it: QTableWidgetItem) -> None:
        if self._filling:
            return
        row, col = it.row(), it.column()
        if row >= len(self.lib.requests) or col == COL["colour"]:
            return
        r = self.lib.requests[row]
        txt = it.text().strip()
        before = r.to_json()
        try:
            self._apply_cell(r, row, col, it, txt)
        except ValueError as err:
            self._refill_row(row)
            shown = self.table.item(row, col).text() if self.table.item(row, col) is not None else ""
            self.say(f"could not read '{txt}' in {COLS[col]} of {r.key}: {err}; the cell keeps {shown or 'its value'}",
                     "warn")
            return
        if COLUMNS[col][1] == "label":
            return                                     # rename emitted changed: the table and the plot follow
        if r.to_json() != before:
            self.lib.touch()
        self._fill_table() if COLUMNS[col][1] in ("norm", "e0_mode", "e0", "shift") else self._refill_row(row)
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
        if (self.pre_lo.value() >= self.pre_hi.value()) or (self.post_lo.value() >= self.post_hi.value()):
            self.say("each window must start below its end; nothing changed", "warn")
            return
        self.lib.set_all("pre", (self.pre_lo.value(), self.pre_hi.value()), rows)
        self.lib.set_all("post", (self.post_lo.value(), self.post_hi.value()), rows)
        self.lib.set_all("e0_smooth", self.e0_smooth.value(), rows)

    def _default_shift_changed(self, v: float) -> None:
        self.lib.default_shift = float(v)
        self.lib.touch()

    # ------------------------------------------------------------------ view settings kept with the library
    def view_settings(self) -> dict:
        return {"x_range": self._xrange(), "spacing": self.spacing.value(), "labels": self.labels_on.isChecked(),
                "signal_in_label": self.signal_in_label.isChecked(), "label_inset_ev": self.label_inset.value()}

    def _view_changed(self, *_a) -> None:
        self.lib.extra.update(self.view_settings())
        self.lib.touch()
        self.replot()

    def _apply_view(self) -> None:
        """A project's library or a config was loaded: its x range, spacing and label settings come back."""
        ex = self.lib.extra
        ws = (self.x_lo, self.x_hi, self.spacing, self.labels_on, self.signal_in_label, self.label_inset)
        for w in ws:
            w.blockSignals(True)
        try:
            xr = ex.get("x_range")
            if isinstance(xr, (list, tuple)) and len(xr) == 2:
                self.x_lo.setText(f"{float(xr[0]):g}"); self.x_hi.setText(f"{float(xr[1]):g}")
            elif "x_range" in ex:
                self.x_lo.clear(); self.x_hi.clear()
            if "spacing" in ex:
                self.spacing.setValue(float(ex["spacing"]))
            if "labels" in ex:
                self.labels_on.setChecked(bool(ex["labels"]))
            if "signal_in_label" in ex:
                self.signal_in_label.setChecked(bool(ex["signal_in_label"]))
            if "label_inset_ev" in ex:
                self.label_inset.setValue(float(ex["label_inset_ev"]))
        except (TypeError, ValueError):
            pass
        finally:
            for w in ws:
                w.blockSignals(False)
        self.default_shift.blockSignals(True)
        self.default_shift.setValue(self.lib.default_shift)
        self.default_shift.blockSignals(False)
        self.replot()

    # ------------------------------------------------------------------ files
    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder of saved spectra (*_full_reduced.csv, *_xanes.csv, AURORA zone CSVs)",
                                             default_dir("reduced", self.lib))
        if not d:
            return
        remember_dir("reduced", Path(d))
        easy = not mode.is_advanced()
        n = len(self.lib.add_folder(Path(d), john_only=easy))
        skipped = getattr(self.lib, "last_skipped", [])
        self.status.setText(f"{plural(n, 'trace')} added from {d}" if n else f"no reduced CSVs found in {d}")
        if easy and skipped:
            names = ", ".join(p.name for p in skipped[:3]) + (f" and {len(skipped) - 3} more" if len(skipped) > 3 else "")
            self.say(f"{plural(len(skipped), 'file')} not in John Bussey's format skipped in Easy mode ({names}); "
                     f"Advanced mode reads them", "muted")

    def _add_csv(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add CSV / text spectra", default_dir("csv", self.lib),
                                                "Spectra (*.csv *.txt *.dat *.nor);;All (*.*)")
        n = 0
        for f in files:
            try:
                n += len(self.lib.add_file(Path(f)))
            except ValueError as err:
                self.say(str(err), "warn")
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
                    self.say(str(err), "warn")
            self.status.setText(f"{plural(n, 'reference trace')} added")

    def _save_config(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Save compare config", default_path("config", "compare_config.json", self.lib),
                                           "JSON (*.json)")
        if p:
            remember_dir("config", p)
            out = []
            with guarded_write(self, Path(p).name) as g:
                out = self.lib.save(Path(p), self.view_settings())
            if g.ok:
                self.notify(out)

    def _load_config(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "Load compare config", default_path("config", "compare_config.json", self.lib),
                                           "JSON (*.json)")
        if p:
            remember_dir("config", p)
            try:
                missing, _extra = self.lib.load(Path(p))
            except (OSError, ValueError, KeyError, TypeError) as err:
                QMessageBox.warning(self, "Load config", f"Could not read {Path(p).name}: {err}")
                return
            if not missing:
                self.say(f"loaded {Path(p).name}: {plural(len(self.lib.requests), 'row')}", "ok")

    def _figure_options(self) -> None:
        from aurora.app.figure_export import open_dialog, summary
        if open_dialog(self):
            self.status.setText(f"figures: {summary()}")
        self._figure_tip()

    def _figure_tip(self) -> None:
        from aurora.app.figure_export import summary
        base = HELP["compare.figure_size"][0]
        self.figure_btn.setProperty("aurora_tip", f"{base}\nNow: {summary()}")
        self.figure_btn.setToolTip(f"{base}\nNow: {summary()}")

    def _export(self) -> None:
        p, _ = QFileDialog.getSaveFileName(self, "Export comparison (.csv, the figure and the config next to it)",
                                           default_path("compare", "comparison.csv", self.lib), "CSV (*.csv)")
        if not p:
            return
        remember_dir("compare", p)
        out = []
        with guarded_write(self, Path(p).name) as g:
            out = self.export(Path(p))
        if g.ok:
            self.notify(out)

    def export(self, path: Path, opts=None) -> list[Path]:
        """The wide CSV of the shown traces as processed (the quantity the figure shows: the first derivative when it
        is ticked, '<label>-d/dE <signal>' columns; without the offsets), the figure at the chosen size (PNG / SVG /
        PDF, drawn again with matplotlib, offsets and x range as on screen), and the config JSON (+ its sidecar of
        arrays)."""
        from aurora.app.figure_export import current_options, export_colour, styled
        from aurora.core.figures import FigureTrace, compare_figure, save_figure
        path = Path(path)
        traces = self.plot_traces()
        export_wide_csv(path, [(t.r, t.E, t.base) for t in traces], quantity="d/dE " if self.deriv.isChecked() else "")
        out = [path]
        xr = self._xrange()
        o = styled(opts or current_options(), x_range=tuple(xr) if xr else None, ylabel=self._ylabel(units=True),
                   labels=self.labels_on.isChecked(), label_inset_ev=self.label_inset.value(),
                   title="", legend=False)
        o.metadata["Title"] = f"Compare: {plural(len(traces), 'trace')}"
        fts = []
        for t in traces:
            c, lab, dname = export_colour(t.i, t.colour)
            fts.append(FigureTrace(t.text, t.E, t.y, c, lab, dname))
        fig = compare_figure(fts, o)
        out += save_figure(fig, path, o)
        cfg = path.with_name(path.stem + "_config.json")
        out += self.lib.save(cfg, self.view_settings())
        self.status.setText("exported " + ", ".join(p.name for p in out))
        return out

    # ------------------------------------------------------------------ plot
    def _xrange(self):
        try:
            return [float(self.x_lo.text()), float(self.x_hi.text())]
        except ValueError:
            return None

    def x_range(self):
        """The Compare x range (eV) as a tuple, None when automatic (Fit uses it as its default range)."""
        xr = self._xrange()
        return (min(xr), max(xr)) if xr and xr[0] != xr[1] else None

    def shown(self):
        return self.lib.processed(lambda r: r.show)

    def _ylabel(self, units: bool = False) -> str:
        if not mode.is_advanced() and not units:
            if self.deriv.isChecked():
                return "slope of the scaled absorption (per eV), curves offset"
            return "absorption, scaled so the edge step is 1 (curves offset)"
        if self.deriv.isChecked():
            return "d/dE of the normalised signal (1/eV), offset" if units else "d/dE of the normalised signal, offset"
        return "normalised signal (edge step = 1), offset"

    def plot_traces(self) -> list:
        """The shown traces as drawn: row index, colour, shifted energies, offset values, label text."""
        items = self.shown()
        offsets = stack_offsets([r for r, _, _ in items], self.spacing.value())
        rows = {id(r): k for k, r in enumerate(self.lib.requests)}
        out = []
        for (r, s, nr), off in zip(items, offsets):
            i = rows.get(id(r), 0)                      # the table row: same colour and dash as its swatch
            E = s.E + r.shift
            base = derivative(E, nr.y, int(self.deriv_smooth.value())) if self.deriv.isChecked() else nr.y
            out.append(SimpleNamespace(r=r, s=s, nr=nr, i=i, E=E, base=base, y=base + off, off=off,
                                       colour=r.colour or curve(i),
                                       text=r.label + (f" ({r.signal})" if self.signal_in_label.isChecked() else "")))
        return out

    def replot(self) -> None:
        self.plot.clear()
        traces = self.plot_traces()
        if not traces:
            self.status.setText("add saved spectra, or send a zone from Reduce" if mode.is_advanced() else
                                "add saved spectra (Add saved spectra…), or save some in Check")
            set_plot_hint(self.plot, "tick Show on a row to plot it" if self.lib.requests else "no spectra yet")
            self.update_actions()
            return
        set_plot_hint(self.plot, None)
        xr = self._xrange()
        xs, ys, failed, peaks_txt, notes = [], [], [], [], []
        drawn = []
        unscaled = []                                   # Easy: rows drawn 0 to 1 because their scaling failed
        easy = not mode.is_advanced()
        for t in traces:
            if not np.isfinite(t.base).any():
                if not easy:
                    failed.append(t.r.label); continue
                y = np.asarray(t.s.y, float)
                fin = np.isfinite(y)
                if not fin.any():
                    failed.append(t.r.label); continue
                lo_, hi_ = float(np.nanmin(y)), float(np.nanmax(y))
                t.base = (y - lo_) / (hi_ - lo_) if hi_ > lo_ else np.zeros_like(y)
                t.y = t.base + t.off
                t.text = t.text + " (not scaled)"
                self.plot.plot(t.E, t.y, pen=pg.mkPen(t.colour, width=1.4, style=Qt.DashLine), connect="finite")
                unscaled.append(t)
                sel = np.isfinite(t.y) & ((t.E >= xr[0]) & (t.E <= xr[1]) if xr else True)
                if sel.any():
                    xs += [float(t.E[sel].min()), float(t.E[sel].max())]; ys.append(t.y[sel])
                    drawn.append(t)
                continue
            pen = pg.mkPen(t.colour, width=1.4, style=dash(t.i))
            self.plot.plot(t.E, t.y, pen=pen, connect="finite")
            if t.nr.e0_note:
                notes.append(t.r.key)
            sel = np.isfinite(t.y) & ((t.E >= xr[0]) & (t.E <= xr[1]) if xr else True)
            if sel.any():
                xs += [float(t.E[sel].min()), float(t.E[sel].max())]; ys.append(t.y[sel])
                drawn.append(t)
            if self.show_peaks.isChecked():
                pk = local_peaks(t.E, t.base, self.prominence.value(), int(self.distance.value()))
                if pk:
                    self.plot.addItem(pg.ScatterPlotItem([p[0] for p in pk], [p[1] + t.off for p in pk], size=7,
                                                         brush=pg.mkBrush(t.colour), pen=None), ignoreBounds=True)
                    peaks_txt.append(f"{t.r.label}: " + ", ".join(f"{p[0]:.1f}" for p in pk))
        if xs and ys:
            x0, x1 = (xr[0], xr[1]) if xr else (min(xs), max(xs))
            if self.labels_on.isChecked():
                xl = x1 - max(0.0, self.label_inset.value())
                for t in drawn:
                    sel = np.isfinite(t.y) & (t.E >= x0) & (t.E <= x1)
                    e, v = t.E[sel], t.y[sel]
                    xa = min(max(xl, float(e.min())), float(e.max()))
                    ti = pg.TextItem(t.text, color=label_for(t.colour), anchor=(1, 1))
                    ti.setPos(xa, float(np.interp(xa, e, v)))
                    self.plot.addItem(ti, ignoreBounds=True)
            v = np.concatenate(ys)
            span = float(np.nanmax(v) - np.nanmin(v)) or 1.0
            self.plot.getViewBox().setRange(xRange=(x0, x1), yRange=(float(np.nanmin(v)), float(np.nanmax(v)) + 0.06 * span),
                                            padding=0.05, disableAutoRange=True)
        self.plot.setLabel("left", self._ylabel())
        n_ok = len(traces) - len(failed) - len(unscaled)
        if easy:
            self.status.setText(" ".join(x for x in (f"{plural(n_ok, 'spectrum')} plotted.",
                                                     self.unscaled_text(unscaled, traces),
                                                     (f"{', '.join(failed)}: no values to draw." if failed else ""))
                                         if x))
        else:
            self.status.setText(f"{plural(n_ok, 'trace')} plotted"
                                + (f"; normalisation failed (all NaN) for: {', '.join(failed)}: adjust the pre / post "
                                   "windows" if failed else "")
                                + (f"; E0 note on {', '.join(notes)} (see the E0 eV cell and the diagnostics)" if notes else ""))
        self.peaks_label.setText("peak maxima (shifted energies): " + " · ".join(peaks_txt) if peaks_txt else "")
        self.update_actions()

    def unscaled_text(self, unscaled, traces) -> str:
        """Easy: why rows could not be scaled the standard way, with their energies and the edge, in one sentence."""
        if not unscaled:
            return ""
        failed_sig = {}
        for t in unscaled:
            failed_sig.setdefault(t.r.label, set()).add(t.r.signal)
        ok_labels = {t.r.label for t in traces if t not in unscaled}
        names = []
        for t in unscaled:
            lab = t.r.label
            n = f"{lab} ({signal_label(t.r.signal)})" if lab in ok_labels else lab      # one name per sample
            if n not in names:
                names.append(n)
        lo = min(float(np.nanmin(t.E)) for t in unscaled)
        hi = max(float(np.nanmax(t.E)) for t in unscaled)
        s0 = unscaled[0].s
        edge = self.lib.edge_ev_of(s0)
        edge_txt = edge_words(self.lib.edge_text_of(s0) or "")
        many = len(names) > 1
        who = ", ".join(names[:-1]) + f" and {names[-1]}" if many else names[0]
        their = "their" if many else "its"
        if edge is not None and np.isfinite(edge):
            pre_lo, pre_hi = EASY_PRE
            post_lo, post_hi = EASY_POST
            before = lo > edge + pre_hi
            after = hi < edge + post_lo
            side = ("before and after" if before and after else "before" if before else "after" if after else "")
            where = f" ({edge_txt + ' ' if edge_txt else ''}at about {edge:.0f} eV)"
            why = (f"{their} energies ({lo:.0f}–{hi:.0f} eV) do not reach far enough {side} the edge{where}" if side else
                   f"the edge step could not be measured on {their} energies ({lo:.0f}–{hi:.0f} eV)")
        else:
            why = f"the edge step could not be measured on {their} energies ({lo:.0f}–{hi:.0f} eV)"
        return (f"{who} could not be scaled the standard way: {why}. "
                f"{'They are' if many else 'It is'} drawn scaled 0 to 1 instead (dashed).")

    def _difference(self) -> None:
        rows = self.selected_rows()
        if len(rows) != 2:
            self.status.setText("select exactly two rows (Ctrl-click) for a difference"); return
        ra, rb = self.lib.requests[rows[0]], self.lib.requests[rows[1]]
        sa, sb = self.lib.spectrum_of(ra), self.lib.spectrum_of(rb)
        if sa is None or sb is None:
            return
        d = difference(sa, sb, self.lib.process_row(ra).y, self.lib.process_row(rb).y, ra.shift, rb.shift)
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
            self.diag_note.setText("")
            set_plot_hint(self.diag, "select a row to see its raw signal and fitted lines")
            return
        set_plot_hint(self.diag, None)
        nr = self.lib.process_row(r)
        E = s.E + r.shift
        self.diag.plot(E, s.y, pen=role_pen("raw", 1.4), connect="finite", name="raw signal")
        if nr.pre_line is not None:
            self.diag.plot(E, nr.pre_line, pen=role_pen("pre", 1.2), name="pre-edge line")
        if nr.post_line is not None:
            self.diag.plot(E, nr.post_line, pen=role_pen("post", 1.2), name="post-edge line")
        if np.isfinite(nr.e0):
            self.diag.addItem(pg.InfiniteLine(nr.e0, angle=90, pen=role_pen("e0", 1.2)), ignoreBounds=True)
        edge_ev = self.lib.edge_ev_of(s)
        if edge_ev is not None and np.isfinite(edge_ev) and E[np.isfinite(E)].min() <= edge_ev <= E[np.isfinite(E)].max():
            self.diag.addItem(pg.InfiniteLine(edge_ev, angle=90, pen=role_pen("edge", 1.0)), ignoreBounds=True)
        fin = np.isfinite(s.y)
        if fin.any():
            self.diag.getViewBox().setRange(xRange=(float(E[fin].min()), float(E[fin].max())),
                                            yRange=(float(s.y[fin].min()), float(s.y[fin].max())), padding=0.05, disableAutoRange=True)
        how = {"deriv": "derivative", "half": "half step", "table": "tabulated", "fixed": "fixed"}.get(
            r.e0_mode, f"as {r.e0_mode[4:]}" if r.e0_mode.startswith("row:") else r.e0_mode)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self, self._fit_diag)
        if not mode.is_advanced():
            self.diag.setTitle(self.diag_title_easy(r, nr))
            self.diag_note.setText(self.diag_note_easy(r, nr, s, edge_ev))
            return
        self.diag.setTitle(f"{r.label} [{r.signal}] · {r.norm} · E0 {nr.e0:.2f} eV ({how}) · step {nr.step:.3g}")
        edge = f"tabulated {self.lib.edge_text_of(s)} edge {edge_ev:.1f} eV (dashed line)" if edge_ev else "no tabulated edge known"
        self.diag_note.setText(" · ".join(x for x in (f"E0 {nr.e0:.2f} eV: {E0_MODE_TEXT.get(r.e0_mode, how)}", edge,
                                                      nr.note, nr.e0_note) if x))

    def _fit_diag(self) -> None:
        """Easy: the scaling card as tall as its wrapped note (so the note never covers the plot). Advanced keeps the
        fixed minimum: its plot options need the height above."""
        import shiboken6
        if not shiboken6.isValid(self.diag_card):
            return
        if mode.is_advanced():
            self.diag_card.setMinimumHeight(200)
            return
        from aurora.app.widgets import fit_to_content
        fit_to_content(self.diag_card, 200)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self, self._fit_diag)

    @staticmethod
    def diag_title_easy(r, nr) -> str:
        e0 = f" · edge at {nr.e0:.1f} eV" if np.isfinite(nr.e0) else ""
        step = (f" · edge step {nr.step:.2g}" if nr.step is not None and np.isfinite(nr.step) else
                " · edge step not measured")
        return f"{r.label} · {signal_label(r.signal)}{e0}{step}"

    def diag_note_easy(self, r, nr, s, edge_ev) -> str:
        """Easy: where the edge was found, how far from the table value, and how the spectrum was scaled."""
        words = edge_words(self.lib.edge_text_of(s) or "")
        how = EASY_E0_WORDS.get(r.e0_mode, "as set in Advanced mode")
        parts = []
        if np.isfinite(nr.e0):
            first = f"Edge position (E0): {nr.e0:.1f} eV, {how}"
            if edge_ev is not None and np.isfinite(edge_ev):
                d = nr.e0 - edge_ev
                side = "above" if d >= 0 else "below"
                first += (f"; {abs(d):.1f} eV {side} the table value for the {words or 'edge'} ({edge_ev:.1f} eV, "
                          "dashed line)")
            parts.append(first + ".")
        if nr.step is None or not np.isfinite(nr.step):
            E = s.E + r.shift
            ref = nr.e0 if np.isfinite(nr.e0) else (edge_ev if edge_ev is not None else np.nan)
            before = np.isfinite(ref) and not np.any((E >= ref + EASY_PRE[0]) & (E <= ref + EASY_PRE[1]))
            after = np.isfinite(ref) and not np.any((E >= ref + EASY_POST[0]) & (E <= ref + EASY_POST[1]))
            side = "before and after" if before and after else "before" if before else "after" if after else ""
            parts.append(f"Edge step: not measured: no energies {side} the edge in the standard ranges (20 to 5 eV "
                         "below and 15 to 35 eV above the edge position)." if side else
                         "Edge step: not measured on these energies.")
        elif r.norm in ("larch", "tey_line"):
            parts.append("Scaled so that the part before the edge is 0 and the step at the edge is 1 (grey lines: the "
                         "levels fitted before and after the edge).")
        elif r.norm == "legacy":
            parts.append("Scaled with constant levels before and after the edge (grey lines), set in Advanced mode.")
        else:
            parts.append("Shown as it was saved, without further scaling.")
        return " ".join(parts)
