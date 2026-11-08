"""Settings dialog: the few defaults that are not per-scan, stored in QSettings (see aurora.app.settings)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
                               QLineEdit, QVBoxLayout, QWidget)

from aurora.app.settings import settings

DEFAULTS = {
    "despike_k": 6.0,
    "fluo_norm": "i0jump",
    "tey_norm": "i0jump",
    "open_last_project": True,
    "report_author": "Sami Soudani",
    "map_colormap": "viridis",
    "reference_dir": "",
    "appearance": "system",          # system / light / dark
    "text_size": "normal",           # normal / large / larger
    "advanced_mode": False,          # Easy mode on first start
}


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


class SettingsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("AURORA settings")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.k = QDoubleSpinBox(); self.k.setRange(1, 50); self.k.setValue(get("despike_k"))
        form.addRow("de-spike threshold k (new zone settings)", self.k)
        self.fn = QComboBox(); self.fn.addItems(["raw", "i0", "i0pre", "i0jump", "area", "max"]); self.fn.setCurrentText(get("fluo_norm"))
        form.addRow("default fluorescence normalisation", self.fn)
        self.tn = QComboBox(); self.tn.addItems(["raw", "i0", "i0pre", "i0jump", "area", "max"]); self.tn.setCurrentText(get("tey_norm"))
        form.addRow("default TEY normalisation", self.tn)
        self.cmap = QComboBox(); self.cmap.addItems(["viridis", "magma", "inferno", "plasma", "cividis"]); self.cmap.setCurrentText(get("map_colormap"))
        form.addRow("map colour map", self.cmap)
        self.author = QLineEdit(get("report_author"))
        form.addRow("report author", self.author)
        self.ref_dir = QLineEdit(get("reference_dir")); form.addRow("reference spectra folder", self.ref_dir)
        self.open_last = QCheckBox("open the last project at start"); self.open_last.setChecked(get("open_last_project"))
        form.addRow("", self.open_last)
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
        self.advanced = QCheckBox("advanced mode (every option; Easy mode shows the basic workflow)")
        self.advanced.setChecked(get("advanced_mode"))
        form.addRow("", self.advanced)
        lay.addLayout(form)
        note = QLabel("Per-scan choices (detectors, lines, normalisation, I0 source) live with each zone in the session file, "
                      "not here.")
        note.setObjectName("muted"); note.setWordWrap(True)
        lay.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def accept(self) -> None:
        s = settings()
        s.setValue("despike_k", float(self.k.value()))
        s.setValue("fluo_norm", self.fn.currentText())
        s.setValue("tey_norm", self.tn.currentText())
        s.setValue("map_colormap", self.cmap.currentText())
        s.setValue("report_author", self.author.text().strip() or DEFAULTS["report_author"])
        s.setValue("open_last_project", self.open_last.isChecked())
        s.setValue("reference_dir", self.ref_dir.text().strip())
        s.setValue("appearance", self.appearance.currentData())
        s.setValue("text_size", self.text_size.currentData())
        s.sync()
        from aurora.app.mode import is_advanced, set_advanced
        from aurora.app.theme import apply_theme
        apply_theme()
        if self.advanced.isChecked() != is_advanced():
            set_advanced(self.advanced.isChecked())
        super().accept()
