"""Reduced-stack caches: sgm_report.py v2.5's under processed/<day>/<scan>/ (70 of them exist for the Nov 2026 beamtime:
`_reduced_cache.npz` with the per-pixel, per-detector ROI sums, `_spectra_cache.npz`, `_count_rate.json`) and AURORA's
own `_reduced_cache.npz` under <root>/.aurora/cache/<day>/<scan>/.

Integrity rules (ReducedCache.open):
- every member is read inside one try, so a truncated or corrupt file never escapes as an exception later;
- a damaged cache is renamed `_reduced_cache.npz.bad` (unless the folder is protected) and the stack is reduced from raw;
- AURORA's caches hold only numbers and unicode text and are opened with allow_pickle=False; pickled object arrays are
  accepted only in sgm_report's caches;
- the signature records the energy count, the newest energy-folder mtime and the XRF windows, so a cache reduced with
  other ROI bins is recognised (StackData.load re-reduces AURORA's, and reports sgm_report's).
"""
from __future__ import annotations

import json
import logging
import os
import pickle
import re
import struct
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger("aurora.io")


def load_npz(path: Path | None):
    """npz or None (missing, truncated by a killed run, or unreadable)."""
    if path is None or not Path(path).exists():
        return None
    try:
        return np.load(path, allow_pickle=True)
    except Exception:
        return None


def save_npz(path: Path, **arrays) -> None:
    """Atomic write: a run killed mid-way never leaves a half-written cache behind."""
    path = Path(path)
    from aurora.core.io.safety import assert_writable
    assert_writable(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp.npz")
    try:
        with open(tmp, "wb") as f:              # explicit handle: closed before the rename (Windows refuses otherwise)
            np.savez(f, **arrays)
    except BaseException:                       # a full disk mid-write: no partial temporary file is left behind
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:                 # a virus scanner may still hold the new file for a moment
            time.sleep(0.2 * (attempt + 1))
        except FileNotFoundError:               # the temporary file vanished (seen in %TEMP% folders): write directly
            break
    try:
        with open(path, "wb") as f:
            np.savez(f, **arrays)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _slug(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", label.replace("α", "a").replace("β", "b").replace("ℓ", "l"))


def roi_labels_from_csv(scan_proc_dir: Path, nroi: int) -> list[str] | None:
    """ROI labels from the newest `*_xanes.csv` written by sgm_report (older caches lack `roi_labels`).

    The `PFY_<slug>_over_I0` columns are in roi_keys order; the '# ROIs' header line maps slug -> label.
    Newest file first: stale zone CSVs may remain when deletion was refused on the share.
    """
    csvs = sorted(Path(scan_proc_dir).glob("*_xanes.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not csvs:
        return None
    hdr, cols = [], None
    with open(csvs[0], encoding="utf-8") as f:
        for line in f:
            if line.startswith("#"):
                hdr.append(line.rstrip("\n"))
            else:
                cols = [c.strip() for c in line.split(",")]
                break
    known = {"totalXRF": "total XRF"}
    for h in hdr:
        if h.startswith("# ROIs"):
            for part in h.split(": ", 1)[1].split("; "):
                m = re.match(r"(.+?) \d+ eV ch ", part)
                if m:
                    known[_slug(m.group(1))] = m.group(1)
    if not cols:
        return None
    order = [c[4:-8] for c in cols if c.startswith("PFY_") and c.endswith("_over_I0") and not re.search(r"_SDD\d_over_I0$", c)]
    labels = [known.get(sl, sl) for sl in order]
    return labels if len(labels) == nroi else None


CACHE_NAME = "_reduced_cache.npz"
AURORA_FORMAT = "aurora2"           # signature prefix of AURORA's caches: unicode members, readable without pickle
TOTAL_LABELS = ("total XRF",)       # the total window is AURORA's / sgm_report's own, not a line of roi_bins.json

Windows = dict[str, dict[str, tuple[int, int]]]          # line label -> {sdd: (first channel, last channel + 1)}


def windows_from_signature(sig: str) -> Windows | None:
    """The XRF windows a cache was reduced with, read from its signature: sgm_report 'v2.5|nE|mtime|{json}' and AURORA
    'aurora2|nE|mtime|{json}'. None when the signature carries none (an older format)."""
    parts = str(sig).split("|", 3)
    if len(parts) != 4:
        return None
    try:
        raw = json.loads(parts[3])
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None
    out: Windows = {}
    try:
        for lab, w in raw.items():
            out[str(lab)] = {str(d): (int(b[0]), int(b[1])) for d, b in dict(w).items()}
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return out


def _norm(w: Windows) -> dict:
    return {str(lab): {str(d): (int(b[0]), int(b[1])) for d, b in v.items()} for lab, v in w.items()}


def aurora_signature(n_energies: int, last_mtime: float, windows: Windows) -> str:
    """Signature of an AURORA cache: format, energy count, newest energy-folder mtime (s), the windows as JSON."""
    js = json.dumps({lab: {d: [int(b[0]), int(b[1])] for d, b in v.items()} for lab, v in _norm(windows).items()},
                    sort_keys=True, ensure_ascii=False)
    return f"{AURORA_FORMAT}|{int(n_energies)}|{last_mtime:.0f}|{js}"


# errors that mean the file's content is broken (as opposed to an unreachable share or a locked file)
_DAMAGE = (zipfile.BadZipFile, EOFError, ValueError, KeyError, IndexError, TypeError, AttributeError, zlib.error,
           UnicodeDecodeError, pickle.UnpicklingError, struct.error)


@dataclass
class ReducedCache:
    """`_reduced_cache.npz`: the fixed-window ROI reduction of one stack, per pixel and per detector."""
    path: Path
    signature: str
    i0: np.ndarray                  # (nE, n_pix)
    tey: np.ndarray                 # (nE, n_pix)
    roi_pix_det: np.ndarray         # (nE, n_pix, n_roi, 4) float32
    roi_det: np.ndarray             # (nE, n_roi, 4) summed over pixels
    valid: np.ndarray               # (nE,) bool, False for skipped energies
    roi_labels: list[str]
    skipped: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    windows: Windows | None = None  # the windows of the signature (None: not recorded)
    fmt: str = ""                   # 'aurora' (current AURORA format) or 'sgm_report' (anything else)

    @property
    def n_energies(self) -> int:
        return int(self.i0.shape[0])

    @property
    def n_pix(self) -> int:
        return int(self.i0.shape[1])

    @property
    def roi_pix(self) -> np.ndarray:
        """ROI sums over the four detectors, (nE, n_pix, n_roi)."""
        return self.roi_pix_det.sum(axis=3, dtype=float)

    # ------------------------------------------------------------------ reading
    @classmethod
    def load(cls, scan_proc_dir: Path, allow_pickle: bool = True) -> "ReducedCache | None":
        """The cache of a scan folder, or None (missing, damaged, unreadable); see open() for the reason."""
        return cls.open(scan_proc_dir, allow_pickle=allow_pickle)[0]

    @classmethod
    def open(cls, scan_proc_dir: Path, allow_pickle: bool = True) -> "tuple[ReducedCache | None, str]":
        """(cache, problem). problem is '' when the cache is fine or simply absent; otherwise it says why the cache
        was not used, in words for the status line ('damaged cache (...): renamed to _reduced_cache.npz.bad, reduced
        from raw'). A damaged file is renamed *.bad unless its folder is protected; a cache AURORA cannot read
        without pickle (written by AURORA 0.3) is left for the next reduction to replace."""
        p = Path(scan_proc_dir) / CACHE_NAME
        try:
            if not p.is_file():
                return None, ""
        except OSError:
            return None, ""
        opened = False
        try:
            # our own handle: np.load leaks its file when the zip directory is broken, and Windows then refuses the
            # rename to *.bad until the garbage collector runs
            with open(p, "rb") as fh, np.load(fh, allow_pickle=allow_pickle) as z:
                opened = isinstance(getattr(z, "files", None), list)          # a readable npz (zip) archive
                if "roi_pix_det" not in z.files:
                    return None, ""                      # another kind of cache (no per-pixel arrays): not ours to judge
                arrays = {k: z[k] for k in z.files}      # every member read now: a truncated one raises here
            rc = cls._from_arrays(p, arrays, Path(scan_proc_dir))
        except ValueError as err:
            # a sound archive whose members are pickled object arrays: AURORA 0.3 wrote those (garbage that is no
            # archive at all raises the same 'allow_pickle' error from np.load, and is damage)
            if opened and not allow_pickle and "allow_pickle" in str(err):
                log.info("%s: written by an earlier AURORA (pickled labels): reduced again", p)
                return None, "cache from an earlier AURORA version: re-reducing"
            return None, cls._quarantine(p, err)
        except _DAMAGE as err:
            return None, cls._quarantine(p, err)
        except OSError as err:
            if "interpret" in str(err):                  # np.load: neither npz nor pickle, i.e. garbage
                return None, cls._quarantine(p, err)
            # an unreachable share or a locked file is no damage: nothing is renamed
            log.warning("%s: cache not readable (%s)", p, err)
            return None, f"cache not readable ({err.strerror or err}): reduced from raw"
        log.info("%s: reduced cache %d energies x %d pixels, ROIs %s", p, rc.n_energies, rc.n_pix, rc.roi_labels)
        return rc, ""

    @classmethod
    def _from_arrays(cls, p: Path, z: dict, scan_proc_dir: Path) -> "ReducedCache":
        rpd = z["roi_pix_det"]
        i0, tey = z["i0"], z["tey"]
        if rpd.ndim != 4 or rpd.shape[3] != 4:
            raise ValueError(f"roi_pix_det has shape {rpd.shape}, not (energies, pixels, lines, 4)")
        n_e, n_pix, nroi = (int(v) for v in rpd.shape[:3])
        if i0.shape != (n_e, n_pix) or tey.shape != (n_e, n_pix):
            raise ValueError(f"i0 {i0.shape} / tey {tey.shape} do not match roi_pix_det {rpd.shape}")
        valid = np.asarray(z["valid"]).astype(bool)
        if valid.shape != (n_e,):
            raise ValueError(f"valid has shape {valid.shape}, not ({n_e},)")
        roi_det = z["roi_det"] if "roi_det" in z else np.nansum(rpd, axis=1)
        sig = str(z["signature"]) if "signature" in z else ""
        labels = [str(v) for v in np.asarray(z["roi_labels"]).ravel()] if "roi_labels" in z else None
        if not labels or len(labels) != nroi:
            labels = roi_labels_from_csv(scan_proc_dir, nroi) or [f"roi{i + 1}" for i in range(nroi)]
        skipped = []
        for s in (np.asarray(z["skipped"], dtype=object).tolist() if "skipped" in z else []):
            s = tuple(s) if isinstance(s, (list, tuple)) else (s,)
            try:
                skipped.append((float(s[0]), *[str(v) for v in s[1:]]))
            except (TypeError, ValueError):
                skipped.append(tuple(str(v) for v in s))
        notes = [str(n) for n in np.asarray(z["notes"]).ravel()] if "notes" in z else []
        fmt = "aurora" if sig.startswith(AURORA_FORMAT + "|") else "sgm_report"
        return cls(path=p, signature=sig, i0=i0, tey=tey, roi_pix_det=rpd, roi_det=roi_det, valid=valid,
                   roi_labels=labels, skipped=skipped, notes=notes, windows=windows_from_signature(sig), fmt=fmt)

    @staticmethod
    def _quarantine(p: Path, err: Exception) -> str:
        """Rename a damaged cache to *.bad (kept for a bug report, never read again) and say so."""
        what = f"{type(err).__name__}: {err}"[:160]
        bad = p.with_name(p.name + ".bad")
        try:
            from aurora.core.io.safety import assert_writable
            assert_writable(bad)
            os.replace(p, bad)
        except OSError as e2:
            log.warning("%s: damaged cache (%s), left in place (%s)", p, what, e2)
            return f"damaged cache ({what}), left in place: reduced from raw"
        log.warning("%s: damaged cache (%s), renamed to %s", p, what, bad.name)
        return f"damaged cache ({what}): renamed to {bad.name}, reduced from raw"

    # ------------------------------------------------------------------ checks against the current state
    def stale_reason(self, n_energies: int, n_pix: int, last_mtime: float, windows: Windows) -> str:
        """'' when this AURORA cache was made from the same energy folders with the same windows; otherwise the reason
        in words, ending in ': re-reducing'."""
        parts = self.signature.split("|", 3)
        if self.fmt != "aurora" or len(parts) != 4:
            return "cache from an earlier AURORA version: re-reducing"
        if self.windows is None or _norm(self.windows) != _norm(windows):
            return "ROI bins changed since this cache: re-reducing"
        if parts[1] != str(int(n_energies)) or self.n_energies != int(n_energies) or self.n_pix != int(n_pix):
            return "energy folders changed since this cache: re-reducing"
        if parts[2] != f"{last_mtime:.0f}":
            return "energy folders changed since this cache: re-reducing"
        return ""

    def window_differences(self, windows: Windows) -> list[str]:
        """sgm_report caches: every line of the cache whose windows differ from `windows` (config/roi_bins.json), in
        words, e.g. 'F Kα (sdd2 65-79 ch, roi_bins.json 66-80 ch)'. A cache holding only some of the project's lines is
        normal (sgm_report picks the lines per sample) and is not a difference. [] when the signature has no windows."""
        if self.windows is None:
            return []
        want_all = _norm(windows)
        out = []
        for lab, have in _norm(self.windows).items():
            if lab in TOTAL_LABELS:
                continue
            want = want_all.get(lab)
            if want is None:
                out.append(f"{lab} (not in roi_bins.json)")
                continue
            diff = [f"{d} {have[d][0]}-{have[d][1] - 1} ch, roi_bins.json {want[d][0]}-{want[d][1] - 1} ch"
                    for d in sorted(set(have) & set(want)) if have[d] != want[d]]
            if diff:
                out.append(f"{lab} ({'; '.join(diff)})")
        return out


def load_count_rate(scan_proc_dir: Path) -> dict | None:
    """`_count_rate.json`: total counts of the brightest pixels at the probed energies (dict with 'rows')."""
    p = Path(scan_proc_dir) / "_count_rate.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
