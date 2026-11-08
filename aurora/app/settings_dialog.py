"""Settings dialog: the few defaults that are not per-scan, stored in QSettings (see aurora.app.settings).

get(key) reads a setting with its default. on_settings_changed(owner, fn) calls fn() after the dialog is accepted
(for example every MapView re-reads map_colormap).
"""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QLineEdit, QVBoxLayout, QWidget)

from aurora.app.settings import settings

DEFAULTS = {
    "despike_k": 6.0,
    "fluo_norm": "i0jump",
    "tey_norm": "i0jump",
    "open_last_project": True,
    "report_author": "",              # '' = resolved when a report is built (the OS account name); a value set is kept
    "map_colormap": "viridis",
    "reference_dir": "",
    "appearance": "system",          # system / light / dark
    "text_size": "normal",           # normal / large / larger
    "advanced_mode": False,          # Easy mode on first start
    "update_check": False,           # opt-in: ask GitHub for a newer release at start (at most once a day)
}
COLORMAPS = ["viridis", "magma", "inferno", "plasma", "cividis"]


def get(key: str):
    v = settings().value(key, DEFAULTS[key])
    d = DEFAULTS[key]
    if isinstance(d, bool):
        return v in (True, "true", "True", 1, "1")
    if isinstance(d, float):
        try:
            return float(v)
        except (TypeError, ValueError):
            return d
    return str(v)


class SettingsNotifier(QObject):
    changed = Signal()


def notifier() -> SettingsNotifier | None:
    """Lives as a child of the QApplication (never at module level)."""
    app = QCoreApplication.instance()
    if app is None:
        return None
    n = app.findChild(SettingsNotifier, "aurora_settings_notifier")
    if n is None:
        n = SettingsNotifier(app)
        n.setObjectName("aurora_settings_notifier")
    return n


class _Hook(QObject):
    def __init__(self, owner: QObject, fn):
        super().__init__(owner)
        self._fn = fn

    @Slot()
    def fire(self) -> None:
        try:
            self._fn()
        except RuntimeError:            # the owner's C++ side is already gone
            pass


def on_settings_changed(owner: QObject, fn) -> None:
    """Call fn() each time the Settings dialog is accepted, for as long as `owner` lives."""
    n = notifier()
    if n is not None:
        n.changed.connect(_Hook(owner, fn).fire)


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        from aurora.app.help import set_help_anchor
        from aurora.app.help_text import explain, explain_buttons
        from aurora.app.vocab import NORM_LABELS, unit_suffix
        self.setWindowTitle("AURORA settings")
        set_help_anchor(self, "10-settings")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.form = form
        # Easy rows
        self.appearance = QComboBox()
        for key, label in (("system", "follow the system"), ("light", "light"), ("dark", "dark")):
            self.appearance.addItem(label, key)
        self.appearance.setCurrentIndex(max(0, self.appearance.findData(get("appearance"))))
        form.addRow("appearance", self.appearance)
        self.text_size = QComboBox()
        for key, label in (("normal", "normal"), ("large", "large"), ("larger", "larger")):
            self.text_size.addItem(label, key)
        self.text_size.setCurrentIndex(max(0, self.text_size.findData(get("text_size"))))
        form.addRow("text size", self.text_size)
        self.author = QLineEdit(get("report_author"))
        self.author.setPlaceholderText("blank: resolved when a report is built")
        form.addRow("report author", self.author)
        self.open_last = QCheckBox("open the last project at start"); self.open_last.setChecked(get("open_last_project"))
        form.addRow("", self.open_last)
        self.advanced = QCheckBox("advanced mode (every option; Easy mode shows the basic workflow)")
        self.advanced.setChecked(get("advanced_mode"))
        form.addRow("", self.advanced)
        self.update_check = QCheckBox("check GitHub for a newer AURORA at start (once a day)")
        self.update_check.setChecked(get("update_check"))
        form.addRow("", self.update_check)
        # Advanced rows
        self.k = QDoubleSpinBox(); self.k.setRange(1, 50); self.k.setValue(get("despike_k")); self.k.setSuffix(unit_suffix("MAD"))
        form.addRow("de-spike threshold k (new zone settings)", self.k)
        self.fn = QComboBox()
        self.tn = QComboBox()
        for box, key in ((self.fn, "fluo_norm"), (self.tn, "tey_norm")):
            for code, text in NORM_LABELS.items():
                box.addItem(text, code)
            box.setCurrentIndex(max(0, box.findData(get(key))))
        form.addRow("default fluorescence normalisation", self.fn)
        form.addRow("default TEY normalisation", self.tn)
        self.cmap = QComboBox(); self.cmap.addItems(COLORMAPS); self.cmap.setCurrentText(get("map_colormap"))
        form.addRow("map colour map", self.cmap)
        self.ref_dir = QLineEdit(get("reference_dir")); form.addRow("reference spectra folder", self.ref_dir)
        lay.addLayout(form)
        note = QLabel("Per-scan choices (detectors, lines, normalisation, I0 source) live with each zone in the session file, "
                      "not here.")
        note.setObjectName("muted"); note.setWordWrap(True)
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Help)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        buttons.helpRequested.connect(self.show_help)
        self.help_btn = buttons.button(QDialogButtonBox.Help)
        explain_buttons(buttons)
        lay.addWidget(buttons)
        for w, key in ((self.appearance, "appearance"), (self.text_size, "text_size"), (self.author, "author"),
                       (self.open_last, "open_last"), (self.advanced, "advanced"), (self.update_check, "update_check"),
                       (self.k, "k"), (self.fn, "fluo"), (self.tn, "tey"), (self.cmap, "cmap"), (self.ref_dir, "ref_dir")):
            explain(w, f"settings.{key}")
        from aurora.app.mode import mark_advanced_row
        mark_advanced_row(form, self.k, self.fn, self.tn, self.cmap, self.ref_dir)

    def show_help(self) -> None:
        from aurora.app.help import PERSPECTIVE_HELP, open_help
        open_help(self, "MANUAL.md", PERSPECTIVE_HELP["settings"])

    def accept(self) -> None:
        s = settings()
        s.setValue("despike_k", float(self.k.value()))
        s.setValue("fluo_norm", self.fn.currentData())
        s.setValue("tey_norm", self.tn.currentData())
        s.setValue("map_colormap", self.cmap.currentText())
        s.setValue("report_author", self.author.text().strip())
        s.setValue("open_last_project", self.open_last.isChecked())
        s.setValue("update_check", self.update_check.isChecked())
        s.setValue("reference_dir", self.ref_dir.text().strip())
        s.setValue("appearance", self.appearance.currentData())
        s.setValue("text_size", self.text_size.currentData())
        s.sync()
        from aurora.app.mode import is_advanced, set_advanced
        from aurora.app.theme import apply_theme
        apply_theme()
        if self.advanced.isChecked() != is_advanced():
            set_advanced(self.advanced.isChecked())
        n = notifier()
        if n is not None:
            n.changed.emit()
        super().accept()
