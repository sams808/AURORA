"""What Home says about a project and a scan, without Qt: the project summary (days, holders, scans by status, the
calibration, ROI bins, sample sheet and plate layout found) and John Bussey's 'Scan Analysis Summary' (date, project,
scan name, grating, mirror stripe, time per map and the estimated total, grid and pitch, x / y range, energy
regions, energy folders found vs planned and which maps are incomplete).

Everything here reads folder names, .h5 attributes already in ScanInfo and, for `incomplete_energies`, the sizes and
row counts of the raw files. Nothing is written.
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from aurora.core.constants import BYTES_PER_PIXEL, DETS
from aurora.core.names import energy_regions

STATUS_ORDER = ("survey", "complete", "in_progress", "aborted", "test", "empty", "unreadable", "other")


@dataclass
class ProjectFacts:
    """Files found next to the scans (computed in the discovery job: it globs and reads the sample sheet)."""
    sheet: Path | None = None                  # the sample sheet AURORA uses (first CSV at the root that parses)
    sheet_rows: int = 0
    sheet_plates: list[str] = field(default_factory=list)


def project_facts(project) -> ProjectFacts:
    """The sample sheet the session will use: the first CSV at the project root with sample rows (same rule as
    Session.set_project)."""
    from aurora.core.io.config import read_sample_sheet
    facts = ProjectFacts()
    try:
        sheets = sorted(Path(project.root).glob("*.csv"))
    except OSError:
        return facts
    for p in sheets:
        try:
            rows = read_sample_sheet(p)
        except Exception:                       # noqa: BLE001  any CSV that is not a sample sheet
            continue
        if rows:
            facts.sheet, facts.sheet_rows = p, len(rows)
            facts.sheet_plates = sorted({r.plate.strip() for r in rows if r.plate.strip()})
            break
    return facts


def status_counts(scans) -> list[tuple[str, int]]:
    """(status, count) in a fixed order: survey, complete, in progress, aborted, test, empty, unreadable, other."""
    c = Counter(s.status or "other" for s in scans)
    order = list(STATUS_ORDER) + sorted(k for k in c if k not in STATUS_ORDER)
    return [(k, c[k]) for k in order if c.get(k)]


def shown_path(path, root) -> str:
    """A path relative to the project root when it lies inside it ('config/roi_bins.json'), else in full."""
    try:
        return Path(path).relative_to(Path(root)).as_posix()
    except ValueError:
        return str(path)


def _names(items: list[str], limit: int = 12) -> str:
    return ", ".join(items[:limit]) + (f", … ({len(items) - limit} more)" if len(items) > limit else "")


def project_rows(project, facts: ProjectFacts | None = None) -> list[tuple[str, str]]:
    """The project summary as (label, text) rows."""
    facts = facts or ProjectFacts()
    days = [d.date or d.path.name for d in project.days]
    holders = [h.name for d in project.days for h in d.holders]
    scans = project.scans
    counts = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in status_counts(scans))
    rows = [("folder", str(project.root)),
            ("layout", "a loose folder of scan folders (John Bussey's GUI layout)" if project.loose
             else "raw/<day>/<scan>" + ("" if Path(project.raw) == Path(project.root) / "raw"
                                         else f" ({shown_path(project.raw, project.root)})")),
            ("days", f"{len(days)}: {_names(days)}" if days else "none"),
            ("holders", f"{len(holders)}: {_names(holders)}" if holders else "none (no survey found)"),
            ("scans", f"{len(scans)}" + (f" ({counts})" if counts else ""))]
    continued = [s for d in project.days for s in d.continued]
    if continued:
        rows.append(("after midnight", f"{len(continued)} stack(s) continue a holder of the previous day: "
                     + _names([f"{s.name} ({s.holder})" for s in continued], 6)))
    cal = project.calibration
    if cal is not None and cal.calibrated:
        rows.append(("calibration", f"{shown_path(cal.path, project.root)} (last updated {cal.last_updated})"))
    else:
        where = cal.path if cal is not None and cal.path is not None else Path(project.config_dir) / "sdd_calibration.json"
        rows.append(("calibration", f"none found ({shown_path(where, project.root)}): the default gain is used, "
                     "not calibrated"))
    bins = project.roi_bins
    if bins is not None and bins.labels:
        where = shown_path(bins.path, project.root) if bins.path else "roi_bins.json"
        rows.append(("ROI bins", f"{where}: {_names(bins.labels)}"))
    else:
        rows.append(("ROI bins", f"none found ({shown_path(Path(project.config_dir) / 'roi_bins.json', project.root)}): "
                     "total XRF only"))
    if facts.sheet is not None:
        rows.append(("sample sheet", f"{facts.sheet.name}, {facts.sheet_rows} samples"
                     + (f" on {_names(facts.sheet_plates, 8)}" if facts.sheet_plates else "")))
    else:
        rows.append(("sample sheet", "none found (a CSV with a 'Sample ID' column at the project root)"))
    if project.plate_layout:
        rows.append(("plate layout", f"config/plate_layout.json: {_names(sorted(project.plate_layout), 10)}"))
    return rows


# ----------------------------------------------------------------------------- one scan
def format_duration(seconds: float) -> str:
    """'30 s', '15.6 min', '2.3 h'."""
    if seconds != seconds or seconds < 0:
        return ""
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


def _float(md: dict, *keys) -> float | None:
    for k in keys:
        try:
            return float(md[k])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _ev(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".") if v == v else "?"


def regions_text(E) -> str:
    """'680–685 eV in 0.5 eV steps; 685–700 eV in 0.1 eV steps; …'."""
    out = []
    for a, b, step in energy_regions(E):
        out.append(f"{_ev(a)} eV" if step == 0 else f"{_ev(a)}–{_ev(b)} eV in {step:g} eV steps")
    return "; ".join(out)


def _energies_text(values: list[float], limit: int = 8) -> str:
    vals = [f"{v:.2f}" for v in values]
    return ", ".join(vals[:limit]) + (f", … ({len(vals) - limit} more)" if len(vals) > limit else "") + " eV"


def missing_energies(scan, tol: float = 0.005) -> list[float]:
    """Planned energies (eV) without an energy folder."""
    have = [e for e, _ in scan.energy_dirs]
    return [e for e in scan.planned if not any(abs(e - h) <= tol for h in have)]


def incomplete_energies(scan, progress=None) -> list[tuple[float, str]]:
    """Energy folders whose map is incomplete, as (eV, reason), in the reduction's rule (sgm_report `Stack.reduce`):
    a map with fewer points than the scan's grid, or SDD files shorter than the mcc file, or a file missing. Reads the
    mcc row count and the SDD file sizes only (no spectra). progress(i, n, msg) is called per folder (it raises
    Cancelled when the job is cancelled)."""
    n_pts = int(scan.n_points)
    out = []
    dirs = list(scan.energy_dirs)
    for i, (e, d) in enumerate(dirs):
        if progress:
            progress(i, len(dirs), f"{scan.name} {e:.2f} eV")
        try:
            with open(Path(d) / "mcc_flyer_0.csv", "rb") as f:
                n_mcc = sum(1 for line in f if line.strip() and not line.lstrip().startswith(b"#"))
            n_sdd = [os.path.getsize(Path(d) / f"{det}_0.bin") // BYTES_PER_PIXEL for det in DETS]
        except OSError as err:
            out.append((e, f"file missing ({type(err).__name__})"))
            continue
        n = min([n_mcc] + n_sdd)
        if n < n_pts or any(k != n_mcc for k in n_sdd):
            out.append((e, f"{n} of {n_pts} points"))
    return out


def scan_rows(scan, project=None, incomplete: list[tuple[float, str]] | None = None,
              check_error: str = "") -> list[tuple[str, str]]:
    """John Bussey's 'Scan Analysis Summary' of one scan as (label, text) rows. `incomplete`: the result of
    incomplete_energies (None = not checked yet); `check_error`: why that check failed."""
    md = scan.md or {}
    rows: list[tuple[str, str]] = []
    day = scan.day or ""
    if scan.continues:
        day += f" (after midnight: continues holder {scan.holder} of {scan.continues})"
    rows.append(("date", day))
    proj_id = md.get("project", "")
    rows.append(("project", proj_id if proj_id else
                 (f"{project.root.name} (folder; no project id in the file)" if project is not None else "")))
    rows.append(("scan", scan.name))
    if md.get("scan_name") and md["scan_name"] != scan.name:
        rows.append(("scan name", md["scan_name"]))
    rows.append(("holder", scan.holder))
    if scan.kind == "stack":
        rows.append(("edge", scan.edge_text))
    rows.append(("status", f"{scan.status.replace('_', ' ')} ({scan.status_reason})" if scan.status_reason
                 else scan.status.replace("_", " ")))
    grating = md.get("grating_selection") or md.get("grating") or ""
    rows.append(("grating", grating.upper() if grating else "not recorded"))
    stripe = md.get("mirror_stripe") or md.get("stripe") or ""
    rows.append(("mirror stripe", stripe.capitalize() if stripe else "not recorded"))
    if scan.kind == "stack":
        tpm = _float(md, "time_per_map")
        n_maps = _float(md, "num_maps")
        n_maps = n_maps if n_maps is not None else float(scan.n_planned or scan.n_energies or 0)
        if tpm is not None:
            rows.append(("time per map", f"{tpm:g} s per map"))
            if n_maps:
                rows.append(("estimated total", f"{n_maps:g} maps × {tpm:g} s = {format_duration(n_maps * tpm)} "
                             "(num_maps × time per map, without the moves between maps)"))
        else:
            rows.append(("time per map", "not recorded"))
    if scan.grid != (0, 0):
        nx, ny = scan.grid
        pitch = f", pitch {scan.pitch[0]:.3f} × {scan.pitch[1]:.3f} mm" if scan.pitch else ""
        rows.append(("grid", f"{nx} × {ny} points{pitch} ({scan.n_points} measured)"))
    if scan.x_range:
        rows.append(("x range", f"{scan.x_range[0]:.3f} … {scan.x_range[1]:.3f} mm"))
    if scan.y_range:
        rows.append(("y range", f"{scan.y_range[0]:.3f} … {scan.y_range[1]:.3f} mm"))
    if scan.kind == "survey":
        if scan.e_min == scan.e_min:
            rows.append(("energy", f"{scan.e_min:.2f} eV"))
    elif scan.kind == "stack":
        E = scan.planned_energies
        rows.append(("energy regions", regions_text(E) if E else "none"))
        found, planned = scan.n_energies, scan.n_planned
        text = f"{found} found" + (f" of {planned} planned" if planned else "")
        miss = missing_energies(scan)
        if miss:
            text += f"; missing {_energies_text(miss)}"
        if check_error:
            text += f"; the maps could not be checked ({check_error})"
        elif incomplete is None:
            text += "; checking the maps …" if scan.energy_dirs else ""
        elif incomplete:
            text += "; incomplete maps: " + ", ".join(f"{e:.2f} eV ({why})" for e, why in incomplete[:8])
            if len(incomplete) > 8:
                text += f", … ({len(incomplete) - 8} more)"
        elif scan.energy_dirs:
            text += "; every map complete"
        rows.append(("energy folders", text))
    if scan.flags:
        rows.append(("flags", ", ".join(scan.flags)))
    if scan.error:
        rows.append(("error", scan.error))
    return rows
