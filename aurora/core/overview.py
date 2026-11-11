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
    sheet: Path | None = None                  # the sample sheet AURORA uses (aurora.core.io.config.find_sample_sheet)
    sheet_rows: int = 0
    sheet_plates: list[str] = field(default_factory=list)
    choice: object = None                      # the SheetChoice (columns recognised, CSVs skipped and why)


def project_facts(project, chosen: str | None = None) -> ProjectFacts:
    """The sample sheet the session will use (find_sample_sheet, the same rule as Session): `chosen` (a path relative
    to the root), else the session's File > Choose sample sheet… read from .aurora/session.json, else the rules."""
    from aurora.core.io.config import find_sample_sheet, session_ui_value
    root = Path(project.root)
    if chosen is None:
        chosen = session_ui_value(root, "sample_sheet")
    choice = find_sample_sheet(root, chosen if isinstance(chosen, str) else None)
    facts = ProjectFacts(choice=choice)
    if choice.path is not None:
        facts.sheet, facts.sheet_rows, facts.sheet_plates = choice.path, len(choice.rows), choice.holders
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
             else "<day>/<scan>, the day folders without raw/" if Path(project.raw) == Path(project.root)
             else "raw/<day>/<scan>" + ("" if Path(project.raw) == Path(project.root) / "raw"
                                         else f" ({shown_path(project.raw, project.root)})")),
            ("days", (f"{len(days)}: {_names(days)}" if days else "none")
             + (f" (opened alone; left out: {_names(project.days_left_out)})" if project.days_left_out else "")),
            ("holders", f"{len(holders)}: {_names(holders)}" if holders else "none (no survey found)"),
            ("scans", f"{len(scans)}" + (f" ({counts})" if counts else ""))]
    notes = list(getattr(project, "layout_notes", []) or [])
    note_rows = [("layout note", n) for n in notes]
    if note_rows and (not days or not holders or not scans):
        rows[1:1] = note_rows                   # nothing found: what was not recognised comes first
    else:
        rows += note_rows
    continued = [s for d in project.days for s in d.continued]
    if continued:
        rows.append(("after midnight", f"{len(continued)} stack(s) continue a holder of the previous day: "
                     + _names([f"{s.name} ({s.holder})" for s in continued], 6)))
    cal = project.calibration
    if cal is not None:
        rows.append(("calibration", cal.summary(project.root)))
    else:
        rows.append(("calibration", "none (no config/sdd_calibration.json), nominal 10 eV per channel"))
    bins = project.roi_bins
    roi_where = shown_path(Path(project.config_dir) / "roi_bins.json", project.root)
    if bins is not None and bins.labels:
        where = shown_path(bins.path, project.root) if bins.path else "roi_bins.json"
        rows.append(("ROI bins", f"{where}: {_names(bins.labels)}"))
    elif bins is not None and getattr(bins, "exists", False):
        rows.append(("ROI bins", f"{roi_where} has no usable line: total XRF only (Reduce step 2, Save as a fixed "
                     "line… adds one)"))
    else:
        rows.append(("ROI bins", f"none found ({roi_where}): total XRF only"))
    rows += sheet_rows(project, facts)
    if project.plate_layout:
        rows.append(("plate layout", f"config/plate_layout.json: {_names(sorted(project.plate_layout), 10)}"))
    shown_cal = shown_path(cal.path, project.root) if cal is not None and cal.path is not None else None
    for where, problems in (getattr(project, "config_problems", {}) or {}).items():
        if where != shown_cal:                  # the calibration row above says its problems already
            rows.append(("config problem", f"{where}: {'; '.join(problems)}"))
    return rows


def sheet_rows(project, facts: ProjectFacts) -> list[tuple[str, str]]:
    """The sample-sheet rows of the project summary: the sheet used, its samples and holders, the columns recognised,
    how it was chosen; the CSVs not used, each with its reason; a sheet whose holders are none of the beamtime's."""
    from aurora.core.names import norm_holder
    choice = facts.choice
    out: list[tuple[str, str]] = []
    if facts.sheet is not None:
        text = f"{facts.sheet.name}, {facts.sheet_rows} sample{'s' if facts.sheet_rows != 1 else ''}"
        if facts.sheet_plates:
            text += f" on {_names(facts.sheet_plates, 8)}"
        cols = choice.columns_text() if choice is not None else ""
        if cols:
            text += f" (columns: {cols})"
        if choice is not None and choice.chosen:
            text += "; chosen with File > Choose sample sheet…"
        if choice is not None and choice.note:
            text += f"; {choice.note}"
        out.append(("sample sheet", text))
        if choice is not None and "holder" not in choice.columns:
            out.append(("sample sheet", "no holder column: its samples are offered on every holder (a column named "
                                        "Holder, Plate or Mount says where each sample sits)"))
        else:
            mine = {norm_holder(h.name) for d in project.days for h in d.holders}
            theirs = {norm_holder(h) for h in facts.sheet_plates}
            if mine and theirs and not mine & theirs:
                col = choice.columns.get("holder", "holder") if choice is not None else "holder"
                out.append(("sample sheet", f"its {col} column names {_names(facts.sheet_plates, 5)}, none of the "
                                            f"holders of this beamtime ({_names(sorted(h.name for d in project.days for h in d.holders), 5)})"))
    else:
        out.append(("sample sheet", "none found (a CSV with a 'Sample ID' column at the project root)"))
    for p, why in (choice.skipped if choice is not None else []):
        out.append(("CSV not used", f"{Path(p).name}: {why}"))
    return out


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
        if getattr(scan, "edge_note", ""):
            rows.append(("edge note", scan.edge_note))
    if getattr(scan, "kind_from_h5", False):
        rows.append(("kind", f"{scan.kind}, from the plan_name of its .h5 (the folder name does not end in "
                             f"_{scan.kind})"))
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


# ----------------------------------------------------------------------------- Easy mode: the same rows in plain words
NOT_RECORDED = "not recorded"


def what_it_is(scan) -> str:
    """The first row of an Easy scan summary: what this scan is for, in one sentence."""
    st = scan.status or ""
    if scan.kind == "survey":
        e = f" at {scan.e_min:.0f} eV" if scan.e_min == scan.e_min else ""
        return f"A map of the whole holder{e}, used to find the samples."
    if scan.kind == "stack":
        if st == "complete":
            n = scan.n_energies
            rng = (f" ({scan.e_min:.0f}–{scan.e_max:.0f} eV)" if scan.e_min == scan.e_min and scan.e_max == scan.e_max
                   else "")
            return (f"A map of this area at {n} energies{rng}: outline a sample on it in Reduce to get its "
                    "spectrum.")
        if st == "aborted":
            return "This stack stopped early: it is drawn on the Survey map but gives no spectrum."
        if st == "in_progress":
            return "Still being measured, or still being copied: Reload (F5) later."
        if st == "test":
            return "A beamline test, not a sample."
    return ""


def easy_project_rows(project, facts: ProjectFacts | None = None) -> list[tuple[str, str]]:
    """project_rows in plain words (Easy mode): the layout row hidden, the calibration as the 'detector energy scale',
    the ROI bins as 'emission windows', and what a missing optional file means."""
    out = []
    for key, val in project_rows(project, facts):
        if key == "layout":
            continue
        if key == "holders" and val.startswith("none"):
            val = "none: no survey found (each holder starts with a survey)"
        elif key == "after midnight":
            n = sum(len(d.continued) for d in project.days)
            val = val.replace(f"{n} stack(s) continue", f"{n} {'stack continues' if n == 1 else 'stacks continue'}")
        elif key == "calibration":
            cal = project.calibration
            key = "detector energy scale"
            if cal is not None and cal.calibrated:
                val = val.replace("(last updated", "(updated")
            elif cal is None or not getattr(cal, "exists", False):
                val = "standard, 10 eV per channel (no config/sdd_calibration.json; fine for the fixed windows)"
            else:                                       # a file AURORA cannot use: its problems stay named
                val = val.replace("nominal", "standard")
        elif key == "ROI bins":
            key = "emission windows"
            bins = project.roi_bins
            if bins is not None and bins.labels:
                where = shown_path(bins.path, project.root) if bins.path else "roi_bins.json"
                val = f"{_names(bins.labels)} ({where})"
            else:
                val = ("none: AURORA counts all X-rays, or a window you set in Reduce step 2 (config/roi_bins.json is "
                       "optional)")
        elif key == "sample sheet" and val.startswith("none"):
            val = ("none: type the sample names yourself (optional: a CSV file with a 'Sample ID' column in the "
                   "beamtime folder)")
        out.append((key, val))
    return out


def easy_scan_rows(scan, project=None, incomplete=None, check_error: str = "") -> list[tuple[str, str]]:
    """scan_rows in plain words (Easy mode): 'what it is' first, rows without a recorded value hidden, pixels instead
    of points."""
    out = []
    first = what_it_is(scan)
    if first:
        out.append(("what it is", first))
    for key, val in scan_rows(scan, project, incomplete, check_error):
        if val == NOT_RECORDED:
            continue
        if key == "estimated total":
            val = val.replace("(num_maps × time per map", "(number of maps × time per map")
        elif key == "grid" and scan.grid != (0, 0):
            nx, ny = scan.grid
            apart = f", {scan.pitch[0]:.3f} × {scan.pitch[1]:.3f} mm apart" if scan.pitch else ""
            val = f"{nx} × {ny} pixels{apart} ({scan.n_points} measured)"
        out.append((key, val))
    return out

