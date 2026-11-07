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
        s.sync()
        super().accept()
