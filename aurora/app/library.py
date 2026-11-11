"""The spectra library shared by Compare and Fit: Spectrum objects plus one TraceRequest per table row.

The library belongs to the open project: set_project restores <project>/.aurora/library.json (rows, sources, settings)
and .aurora/library_spectra.npz (the arrays of spectra that have no file: zone traces sent from Reduce, differences),
and every change is saved back, debounced, atomically (aurora.core.io.textio). Nothing is written when nothing
changed, and never into a protected folder (raw data, $AURORA_PROTECT_DIRS). A library.json that cannot be read is
kept as library.corrupt-<time>.json (its library_spectra.npz beside it, as library_spectra.corrupt-<time>.npz), the
library starts empty, the `warning` signal says so, and nothing is written until the library changes.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from collections import Counter
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEvent, QObject, QTimer, Signal, Slot

from aurora.app.theme import CURVES
from aurora.core.compare import (NormResult, Spectrum, TraceRequest, ZoneRef, _write_bytes_atomic, config_dict,
                                 find_reduced_csvs, layout_of, load_spectra, parse_zone_source, process,
                                 read_config, read_spectra_arrays, resolve_source, save_config, sidecar_path,
                                 spectra_arrays)

log = logging.getLogger("aurora.library")

AUTOSAVE_MS = 800
LIBRARY_FILE = "library.json"


def zone_row_label(zone_label: str, holder: str, sid: str) -> str:
    """'1 alpha · holderA 09:27 alpha_beta': the zone, the holder, the scan time (HH:MM) and the scan's short label."""
    from aurora.core.names import short_time, split_stack_id, strip_scan_suffix
    scan = split_stack_id(sid)[1]
    short = strip_scan_suffix(scan) if scan else ""
    hhmm = short_time(sid)
    parts = [p for p in (holder, hhmm or scan, short if short and short != scan else "") if p]
    return f"{zone_label} · {' '.join(parts)}" if parts else zone_label


def _easy_compare() -> dict:
    from aurora.app.mode import EASY_DEFAULTS
    return EASY_DEFAULTS["compare"]


class Library(QObject):
    changed = Signal()
    restored = Signal()                                  # a project's library or a config was loaded (view settings too)
    saved = Signal(str)                                  # the path written by the autosave
    warning = Signal(str)                                # the library of the project could not be read (the window says)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self.spectra: dict[str, Spectrum] = {}          # key = "label [signal]"
        self.requests: list[TraceRequest] = []
        self.sources: dict[str, str] = {}               # label -> file (for save/load config)
        self.missing: dict[str, str] = {}               # label -> file not found at the last load
        self.default_shift = 0.0
        self.project = None                             # the open Project (set_project), None before one is open
        self.extra: dict = {}                           # Compare's view settings kept with the library (x range, ...)
        self._edge_cache: dict[str, float | None] = {}
        self._last_text: str | None = None
        self._last_npz: bytes | None = None
        self._restoring = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(AUTOSAVE_MS)
        self._timer.timeout.connect(self.flush)
        self.changed.connect(self.touch)
        if parent is not None and hasattr(parent, "installEventFilter"):
            parent.installEventFilter(self)             # the window closes: write a pending change

    # ------------------------------------------------------------------ project, autosave
    def library_file(self) -> Path | None:
        root = getattr(self.project, "root", None)
        return Path(root) / ".aurora" / LIBRARY_FILE if root else None

    def set_project(self, project) -> None:
        """A project was opened: write a pending change of the previous one, then restore this project's library.
        Before any project, rows built in memory are kept and saved into the first project opened (when it has no
        library of its own). Reopening the same project keeps the library as it is."""
        old_root = getattr(self.project, "root", None)
        new_root = getattr(project, "root", None)
        if old_root is not None and new_root is not None and Path(old_root).resolve() == Path(new_root).resolve():
            self.project = project
            return
        self.flush()
        had_project = self.project is not None
        self.project = project
        self._edge_cache.clear()
        self._last_text = self._last_npz = None
        path = self.library_file()
        if path is not None and path.is_file():
            try:
                self._restoring = True
                self._load_dict(read_config(path), path, base=Path(new_root))
                self._last_text, self._last_npz = self._snapshot()
            except (OSError, ValueError, KeyError, TypeError) as err:
                log.warning("%s unreadable (%s); the library starts empty", path, err)
                self._clear()
                self._last_text, self._last_npz = self._snapshot()     # closing writes nothing over it
                kept = self._set_aside(path)
                self.warning.emit(f"The Compare library of this project could not be read ({err}); it starts empty. "
                                  + (f"The file was kept as {kept.name}." if kept is not None else
                                     "The file was left as it is."))
            finally:
                self._restoring = False
            self.changed.emit()
            self.restored.emit()
            return
        if had_project:
            self._clear()
            self._last_text, self._last_npz = self._snapshot()     # an empty library is not written
            self.changed.emit()
            self.restored.emit()
        elif self.requests:
            self.touch()                                # rows made before the project: saved into it
        else:
            self._last_text, self._last_npz = self._snapshot()

    def _clear(self) -> None:
        self.requests, self.spectra, self.sources, self.missing = [], {}, {}, {}
        self.extra = {}

    @staticmethod
    def _set_aside(path: Path) -> Path | None:
        """A library.json that does not read: renamed library.corrupt-<time>.json, with its library_spectra.npz
        (the arrays of its zone traces and differences) renamed to match, so a later save never writes over them.
        None when it could not be moved (a protected or read-only folder: then nothing is written there either)."""
        from aurora.core.io.safety import assert_writable
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target, n = path.with_name(f"library.corrupt-{stamp}.json"), 2
        while target.exists():
            target, n = path.with_name(f"library.corrupt-{stamp}-{n}.json"), n + 1
        try:
            assert_writable(target)
            os.replace(path, target)
        except OSError as err:
            log.warning("the damaged %s could not be kept aside (%s)", path, err)
            return None
        npz = sidecar_path(path).with_name("library_spectra.npz")
        if npz.exists():
            try:
                os.replace(npz, target.with_name(target.stem.replace("library.", "library_spectra.") + ".npz"))
            except OSError as err:
                log.warning("%s not kept aside with the damaged library (%s)", npz, err)
        log.warning("the damaged library was kept as %s", target)
        return target

    @Slot()
    def touch(self) -> None:
        """A row or setting changed: save the library of the project after a short delay."""
        if self._restoring or self.library_file() is None:
            return
        self._timer.start()

    def pending(self) -> bool:
        return self._timer.isActive()

    def _stored_spectra(self) -> list[Spectrum]:
        """Spectra without a readable file of their own (zone traces, differences): kept as arrays."""
        out = []
        for s in self.spectra.values():
            src = self.sources.get(s.label, "")
            if not (src and src == s.source and Path(src).is_file()):
                out.append(s)
        return out

    def _snapshot(self, base: Path | None = None) -> tuple[str, bytes]:
        base = base if base is not None else (Path(self.project.root) if self.project is not None else None)
        stored, data = spectra_arrays(self._stored_spectra())
        d = config_dict(self.requests, self.sources, base, self.extra, stored if stored else [], self.default_shift)
        d.pop("app", None)
        text = json.dumps(d, indent=1, ensure_ascii=False)
        return text, (data if stored else b"")

    @Slot()
    def flush(self) -> bool:
        """Write the project's library now when it changed since the last write. True when something was written."""
        self._timer.stop()
        path = self.library_file()
        if path is None:
            return False
        try:
            text, data = self._snapshot()
            if text == self._last_text and data == self._last_npz:
                return False
            from aurora.core.io.textio import write_text_atomic
            npz = sidecar_path(path).with_name("library_spectra.npz")
            if data:
                _write_bytes_atomic(npz, data)
            elif npz.exists():
                from aurora.core.io.safety import assert_writable
                assert_writable(npz)
                npz.unlink()
            d = json.loads(text)
            from aurora import __version__
            d["app"] = f"AURORA {__version__}"
            write_text_atomic(path, json.dumps(d, indent=1, ensure_ascii=False))
            self._last_text, self._last_npz = text, data
            log.info("library saved to %s (%d rows)", path, len(self.requests))
            self.saved.emit(str(path))
            return True
        except OSError as err:                          # a protected or read-only folder: kept in memory
            log.info("library not saved to %s: %s", path, err)
            return False

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Close and obj is self.parent():
            self.flush()
        return False

    # ------------------------------------------------------------------ adding
    def new_request(self, s: Spectrum, norm: str | None = None) -> TraceRequest:
        """A row for a new spectrum, from mode.EASY_DEFAULTS['compare'] (larch, E0-relative windows, flatten,
        derivative E0) plus the default shift for new rows."""
        d = _easy_compare()
        return TraceRequest(label=s.label, signal=s.signal, shift=self.default_shift,
                            colour=CURVES[len(self.requests) % len(CURVES)],
                            norm=norm if norm is not None else ("none" if s.signal.endswith("_norm") else d["norm"]),
                            pre=tuple(d["pre"]), post=tuple(d["post"]), flatten=bool(d["flatten"]),
                            scale=float(d["scale"]), e0_mode="deriv")

    def add_spectra(self, specs: list[Spectrum], show_signals: tuple[str, ...] = ("fluo_sum_norm", "sdd-scaled", "TEY_norm", "TEY"),
                    norm: str | None = None) -> list[TraceRequest]:
        added = []
        for s in specs:
            self.spectra[s.key] = s
            self._edge_cache.pop(s.key, None)
            if s.source:
                self.sources[s.label] = s.source
                self.missing.pop(s.label, None)
            if any(r.label == s.label and r.signal == s.signal for r in self.requests):
                continue
            xanes = s.meta.get("layout") == "xanes" if isinstance(s.meta, dict) else False   # sgm_report: PFY / I0, TEY / I0
            if s.signal in show_signals or len(specs) == 1 or xanes:
                r = self.new_request(s, norm)
                # John Bussey's rule for his files: only the sdd-scaled row of a file is shown at first (TEY hidden)
                if isinstance(s.meta, dict) and s.meta.get("layout") == "john" and s.signal != "sdd-scaled" \
                        and any(o.signal == "sdd-scaled" for o in specs):
                    r.show = False
                self.requests.append(r)
                added.append(r)
        if added:
            self.changed.emit()
        else:
            self.touch()
        return added

    def add_file(self, path: Path, label: str | None = None) -> list[TraceRequest]:
        return self.add_spectra(load_spectra(path, label))

    def add_folder(self, folder: Path, john_only: bool = False) -> list[TraceRequest]:
        """The reduced CSVs of a folder (John's rule, one level down too). john_only (Easy mode): only John Bussey's
        _full_reduced.csv / _absorption.csv, so every row is raw / I0 plus larch; the others are listed in
        self.last_skipped."""
        out = []
        self.last_skipped: list[Path] = []
        for p in find_reduced_csvs(folder):
            if john_only and layout_of(p) != "john":
                self.last_skipped.append(p)
                continue
            try:
                specs = load_spectra(p)
            except ValueError as err:
                log.info("%s skipped: %s", p, err)
                self.last_skipped.append(p)
                continue
            if john_only and any(s.meta.get("layout") != "john" for s in specs):
                self.last_skipped.append(p)
                continue
            out += self.add_spectra(specs)
        if self.last_skipped:
            log.info("add_folder %s: %d files skipped (%s)", folder, len(self.last_skipped),
                     ", ".join(p.name for p in self.last_skipped)[:200])
        return out

    def add_traces(self, label: str, E: np.ndarray, fluo: np.ndarray | None, tey: np.ndarray | None, source: str = "",
                   zone_ref: ZoneRef | None = None, err: dict | None = None) -> list[TraceRequest]:
        """Zone traces from Reduce (already normalised by the zone settings): one 'fluo' and one 'TEY' spectrum.
        A source 'zone N of <sid>' gives each a ZoneRef {holder, zone, sid, signal} for provenance, and the label gains
        the scan's short label when it lacks it."""
        if zone_ref is None:
            zs = parse_zone_source(source)
            if zs is not None:
                holder = ""
                if self.project is not None:
                    from aurora.core.names import split_stack_id
                    scan = self.project.find(split_stack_id(zs[1])[1]) if hasattr(self.project, "find") else None
                    holder = getattr(scan, "holder", "") or ""
                zone_ref = ZoneRef(holder=holder, zone=zs[0], sid=zs[1], signal="")
        if zone_ref is not None:
            from aurora.core.names import split_stack_id, strip_scan_suffix
            scan = split_stack_id(zone_ref.sid)[1]
            short = strip_scan_suffix(scan) if scan else ""
            if short and short != scan and short not in label:
                label = f"{label} {short}"
        specs = []
        for sig, y in (("fluo", fluo), ("TEY", tey)):
            if y is None:
                continue
            meta = {}
            if zone_ref is not None:
                meta["zone"] = ZoneRef(zone_ref.holder, zone_ref.zone, zone_ref.sid, sig)
            if err and err.get(sig) is not None:
                meta["err"] = np.asarray(err[sig], float)
            specs.append(Spectrum(label, sig, np.asarray(E, float), np.asarray(y, float), source, meta))
        return self.add_spectra(specs, show_signals=("fluo", "TEY"), norm="none")

    # ------------------------------------------------------------------ rows
    def spectrum_of(self, r: TraceRequest) -> Spectrum | None:
        return self.spectra.get(f"{r.label} [{r.signal}]")

    def signals_of(self, label: str) -> list[str]:
        return [s.signal for s in self.spectra.values() if s.label == label]

    def row_by_key(self, key: str) -> TraceRequest | None:
        """The row 'label [signal]', else the first row of that label."""
        for r in self.requests:
            if r.key == key:
                return r
        return next((r for r in self.requests if r.label == key), None)

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
        self.missing = {k: v for k, v in self.missing.items() if k in used}

    def rename(self, old: str, new: str) -> bool:
        if any(r.label == new for r in self.requests):
            return False
        for r in self.requests:
            if r.label == old:
                r.label = new
        for r in self.requests:                         # rows whose E0 follows a renamed row
            if r.e0_mode.startswith("row:"):
                ref = r.e0_mode[4:]
                if ref == old or ref.startswith(old + " ["):
                    r.e0_mode = "row:" + new + ref[len(old):]
        for k in list(self.spectra):
            s = self.spectra[k]
            if s.label == old:
                s.label = new
                self.spectra[s.key] = self.spectra.pop(k)
        if old in self.sources:
            self.sources[new] = self.sources.pop(old)
        self._edge_cache.clear()
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

    # ------------------------------------------------------------------ the Easy defaults of a row
    def easy_values(self, r: TraceRequest) -> dict:
        """The values a new row gets for the columns Easy mode hides (Norm, the windows, Flatten, E0, Scale) and the
        E0 smoothing: mode.EASY_DEFAULTS['compare'], Norm 'none' for a trace normalised already (a *_norm signal, a
        zone trace from Reduce, a difference)."""
        from aurora.core.compare import E0_SMOOTH_EV
        d = _easy_compare()
        s = self.spectra.get(r.key)
        done = r.signal.endswith("_norm") or r.signal == "difference" or (s is not None and (
            (isinstance(s.meta, dict) and "zone" in s.meta) or parse_zone_source(str(s.source or "")) is not None))
        return {"norm": "none" if done else d["norm"], "pre": tuple(d["pre"]),
                "post": tuple(d["post"]), "flatten": bool(d["flatten"]), "e0_mode": "deriv",
                "scale": float(d["scale"]), "e0_smooth": E0_SMOOTH_EV}

    def easy_differences(self) -> dict[int, list[str]]:
        """row -> the fields of easy_values in which the row differs (rows that do not differ left out)."""
        out = {}
        for i, r in enumerate(self.requests):
            have = {f: getattr(r, f) for f in self.easy_values(r)}
            fields = [f for f, v in self.easy_values(r).items()
                      if (tuple(have[f]) if isinstance(have[f], list) else have[f]) != v]
            if fields:
                out[i] = fields
        return out

    def reset_to_easy(self) -> int:
        """'Reset to Easy defaults' in Compare: the differing fields of every row rewritten in one change. Returns the
        number of fields."""
        diffs = self.easy_differences()
        for i, fields in diffs.items():
            r, want = self.requests[i], self.easy_values(self.requests[i])
            for f in fields:
                setattr(r, f, want[f])
            if "e0_mode" in fields:
                r.e0 = None                     # a fixed E0 goes with its mode
        if diffs:
            self.changed.emit()
        return sum(len(v) for v in diffs.values())

    # ------------------------------------------------------------------ edges and E0
    def edge_of(self, s: Spectrum) -> tuple[str, str] | None:
        """The absorption edge of a spectrum: its meta 'edge', else the file or folder name ('…_fluorine_k',
        'NaF_F_K.csv'), else the scan of a zone trace, else the edge most stacks of the project were set up for."""
        from aurora.core.names import target_edge
        from aurora.core.references import guess_edge
        e = s.meta.get("edge") if isinstance(s.meta, dict) else None
        if isinstance(e, (tuple, list)) and len(e) == 2:
            return (str(e[0]), str(e[1]))
        if isinstance(e, str) and len(e.split()) == 2:
            return tuple(e.split())                     # type: ignore[return-value]
        z = s.meta.get("zone") if isinstance(s.meta, dict) else None
        if isinstance(z, ZoneRef) and self.project is not None:
            from aurora.core.names import split_stack_id
            scan = self.project.find(split_stack_id(z.sid)[1]) if hasattr(self.project, "find") else None
            if scan is not None and getattr(scan, "edge", None):
                return tuple(scan.edge)                 # type: ignore[return-value]
        if s.source and not s.source.startswith(("zone ", "difference of")):
            p = Path(s.source)
            for name in (p.stem, p.parent.name):
                te = target_edge("_" + name)
                if te:
                    return te
                g = guess_edge(name)
                if g:
                    return tuple(g.split())             # type: ignore[return-value]
        if self.project is not None:
            edges = Counter(tuple(sc.edge) for sc in getattr(self.project, "scans", []) if getattr(sc, "edge", None))
            if edges:
                return edges.most_common(1)[0][0]
        return None

    def edge_ev_of(self, s: Spectrum) -> float | None:
        if s.key not in self._edge_cache:
            from aurora.core.lines import edge_energy
            e = self.edge_of(s)
            self._edge_cache[s.key] = edge_energy(*e) if e else None
        return self._edge_cache[s.key]

    def edge_text_of(self, s: Spectrum) -> str:
        from aurora.core.names import pretty_edge
        e = self.edge_of(s)
        return pretty_edge(e) if e else ""

    # ------------------------------------------------------------------ processing
    def _process(self, r: TraceRequest, s: Spectrum, cache: dict, seen: tuple = ()) -> NormResult:
        if id(r) in cache:
            return cache[id(r)]
        e0_ref = None
        if r.e0_mode.startswith("row:"):
            ref = self.row_by_key(r.e0_mode[4:])
            if ref is not None and ref is not r and id(ref) not in seen:
                rs = self.spectrum_of(ref)
                if rs is not None:
                    e0_ref = self._process(ref, rs, cache, seen + (id(r),)).e0
        nr = process(s, r, edge_ev=self.edge_ev_of(s), e0_ref=e0_ref)
        cache[id(r)] = nr
        return nr

    def process_row(self, r: TraceRequest) -> NormResult | None:
        s = self.spectrum_of(r)
        return None if s is None else self._process(r, s, {})

    def processed(self, only=None) -> list[tuple[TraceRequest, Spectrum, NormResult]]:
        out, cache = [], {}
        for r in self.requests:
            if only is not None and not only(r):
                continue
            s = self.spectrum_of(r)
            if s is None:
                continue
            out.append((r, s, self._process(r, s, cache)))
        return out

    # ------------------------------------------------------------------ config
    def save(self, path: Path, extra: dict | None = None) -> list[Path]:
        """Save config…: the JSON (paths absolute and relative to it) plus, when some spectra have no file, the
        sidecar <name>_spectra.npz, so a fresh library reloads everything. Returns the files written."""
        return save_config(path, self.requests, self.sources, {**self.extra, **(extra or {})},
                           self._stored_spectra(), self.default_shift)

    def load(self, path: Path) -> tuple[list[str], dict]:
        """Load config…: the rows replace the library; spectra come back from their files (absolute path, else the
        one relative to the config) and from the sidecar. Returns (missing files, extra settings)."""
        path = Path(path)
        d = read_config(path)
        extra = self._load_dict(d, path, base=path.parent)
        self.changed.emit()
        self.restored.emit()
        return list(self.missing.values()), extra

    def _load_dict(self, d: dict, path: Path, base: Path) -> dict:
        from aurora.core.compare import _CONFIG_KEYS
        reqs = [TraceRequest.from_json(r) for r in d.get("rows", [])]
        sources = dict(d.get("sources", {}))
        rel = dict(d.get("sources_rel", {}))
        self._clear()
        self._edge_cache.clear()
        for label, src in sources.items():
            p = resolve_source(src, rel.get(label), base)
            if p is None:
                self.missing[label] = src
                self.sources[label] = src
                continue
            try:
                for s in load_spectra(p, label):
                    self.spectra[s.key] = s
                self.sources[label] = str(p)
            except ValueError as err:
                log.info("%s unreadable: %s", p, err)
                self.missing[label] = src
                self.sources[label] = src
        stored = d.get("stored") or []
        if stored:
            npz = sidecar_path(path) if path.name != LIBRARY_FILE else path.with_name("library_spectra.npz")
            for s in read_spectra_arrays(npz, stored):
                if s.key not in self.spectra:
                    self.spectra[s.key] = s
                    self.missing.pop(s.label, None)
        self.requests = reqs
        self.default_shift = float(d.get("default_shift", 0.0) or 0.0)
        extra = {k: v for k, v in d.items() if k not in _CONFIG_KEYS}
        self.extra = dict(extra)
        return extra

    def relocate(self, folder: Path) -> tuple[int, list[str]]:
        """Locate…: find the missing files of the library under `folder` (a moved folder), by file name, preferring
        the candidate whose parent folders match the old path best. Returns (labels found, labels still missing)."""
        folder = Path(folder)
        found = 0
        for label, src in list(self.missing.items()):
            old = Path(src)
            cands = [p for p in folder.rglob(old.name) if p.is_file()] if old.name else []
            if not cands:
                continue
            def score(p: Path) -> int:
                n = 0
                for a, b in zip(reversed(p.parts), reversed(old.parts)):
                    if a.lower() != b.lower():
                        break
                    n += 1
                return n
            best = max(cands, key=score)
            try:
                for s in load_spectra(best, label):
                    self.spectra[s.key] = s
            except ValueError as err:
                log.info("%s unreadable: %s", best, err)
                continue
            self.sources[label] = str(best)
            del self.missing[label]
            found += 1
        self._edge_cache.clear()
        self.changed.emit()
        return found, list(self.missing)
