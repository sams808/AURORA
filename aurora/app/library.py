"""The spectra library shared by Compare and Fit: Spectrum objects plus one TraceRequest per table row."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal

from aurora.app.preview import PAL
from aurora.core.compare import (NormResult, Spectrum, TraceRequest, find_reduced_csvs, load_config, load_spectra,
                                 process, save_config)


class Library(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.spectra: dict[str, Spectrum] = {}          # key = "label [signal]"
        self.requests: list[TraceRequest] = []
        self.sources: dict[str, str] = {}               # label -> file (for save/load config)
        self.default_shift = 0.0

    # ------------------------------------------------------------------ adding
    def add_spectra(self, specs: list[Spectrum], show_signals: tuple[str, ...] = ("fluo_sum_norm", "sdd-scaled", "TEY_norm", "TEY"),
                    norm: str | None = None) -> list[TraceRequest]:
        added = []
        for s in specs:
            self.spectra[s.key] = s
            if s.source:
                self.sources[s.label] = s.source
            if any(r.label == s.label and r.signal == s.signal for r in self.requests):
                continue
            if s.signal in show_signals or len(specs) == 1:
                r = TraceRequest(label=s.label, signal=s.signal, shift=self.default_shift,
                                 colour=PAL[len(self.requests) % len(PAL)],
                                 norm=norm if norm is not None else ("none" if s.signal.endswith("_norm") else "larch"))
                self.requests.append(r)
                added.append(r)
        if added:
            self.changed.emit()
        return added

    def add_file(self, path: Path, label: str | None = None) -> list[TraceRequest]:
        return self.add_spectra(load_spectra(path, label))

    def add_folder(self, folder: Path) -> list[TraceRequest]:
        out = []
        for p in find_reduced_csvs(folder):
            try:
                out += self.add_spectra(load_spectra(p))
            except ValueError:
                continue
        return out

    def add_traces(self, label: str, E: np.ndarray, fluo: np.ndarray | None, tey: np.ndarray | None, source: str = "") -> list[TraceRequest]:
        """Zone traces from Reduce (already normalised by the zone settings): one 'fluo' and one 'TEY' spectrum."""
        specs = []
        if fluo is not None:
            specs.append(Spectrum(label, "fluo", np.asarray(E, float), np.asarray(fluo, float), source))
        if tey is not None:
            specs.append(Spectrum(label, "TEY", np.asarray(E, float), np.asarray(tey, float), source))
        return self.add_spectra(specs, show_signals=("fluo", "TEY"), norm="none")

    # ------------------------------------------------------------------ rows
    def spectrum_of(self, r: TraceRequest) -> Spectrum | None:
        return self.spectra.get(f"{r.label} [{r.signal}]")

    def signals_of(self, label: str) -> list[str]:
        return [s.signal for s in self.spectra.values() if s.label == label]

    def remove(self, rows: list[int]) -> None:
        self.requests = [r for i, r in enumerate(self.requests) if i not in set(rows)]
        self.prune()
        self.changed.emit()

    def remove_label(self, label: str) -> None:
        self.requests = [r for r in self.requests if r.label != label]
        self.prune()
        self.changed.emit()

    def prune(self) -> None:
        """Forget spectra whose rows are all gone (so a label can be re-added later)."""
        used = {r.label for r in self.requests}
        self.spectra = {k: s for k, s in self.spectra.items() if s.label in used}
        self.sources = {k: v for k, v in self.sources.items() if k in used}

    def rename(self, old: str, new: str) -> bool:
        if any(r.label == new for r in self.requests):
            return False
        for r in self.requests:
            if r.label == old:
                r.label = new
        for k in list(self.spectra):
            s = self.spectra[k]
            if s.label == old:
                s.label = new
                self.spectra[s.key] = self.spectra.pop(k)
        if old in self.sources:
            self.sources[new] = self.sources.pop(old)
        self.changed.emit()
        return True

    def duplicate(self, row: int) -> None:
        r = self.requests[row]
        self.requests.insert(row + 1, TraceRequest.from_json(r.to_json()))
        self.changed.emit()

    def move(self, row: int, delta: int) -> None:
        j = row + delta
        if 0 <= row < len(self.requests) and 0 <= j < len(self.requests):
            self.requests[row], self.requests[j] = self.requests[j], self.requests[row]
            self.changed.emit()

    def set_all(self, attr: str, value, rows: list[int] | None = None) -> None:
        for i, r in enumerate(self.requests):
            if rows is None or i in rows:
                setattr(r, attr, value)
        self.changed.emit()

    # ------------------------------------------------------------------ processing
    def processed(self, only=None) -> list[tuple[TraceRequest, Spectrum, NormResult]]:
        out = []
        for r in self.requests:
            if only is not None and not only(r):
                continue
            s = self.spectrum_of(r)
            if s is None:
                continue
            out.append((r, s, process(s, r)))
        return out

    # ------------------------------------------------------------------ config
    def save(self, path: Path, extra: dict | None = None) -> None:
        save_config(path, self.requests, self.sources, extra)

    def load(self, path: Path) -> tuple[list[str], dict]:
        """Reload the config: spectra come back from their source files; returns (missing files, extra settings)."""
        reqs, sources, extra = load_config(path)
        missing = []
        for label, src in sources.items():
            p = Path(src)
            if p.exists():
                try:
                    for s in load_spectra(p, label):
                        self.spectra[s.key] = s
                    self.sources[label] = src
                except ValueError:
                    missing.append(src)
            else:
                missing.append(src)
        self.requests = reqs
        self.changed.emit()
        return missing, extra
