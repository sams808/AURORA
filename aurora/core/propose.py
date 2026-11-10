"""Zone proposals: the automatic first guess that the user accepts or corrects (docs/HANDOFF.md section 9).

Two sources: the zones sgm_report found (summary.json: name, centre, pixel count per scan), and bright pads of the
survey (Otsu threshold on the log map, connected regions) turned into rectangles.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from aurora.core.io.config import Zone
from aurora.core.survey import SurveyData


def otsu(v: np.ndarray) -> float:
    v = np.asarray(v, float)
    v = v[np.isfinite(v)]
    if v.size < 2:
        return float("nan")
    hist, edges = np.histogram(v, bins=128)
    centres = 0.5 * (edges[1:] + edges[:-1])
    w1 = np.cumsum(hist)[:-1]
    w2 = v.size - w1
    s1 = np.cumsum(hist * centres)[:-1]
    s2 = (hist * centres).sum() - s1
    with np.errstate(all="ignore"):
        m1 = s1 / w1; m2 = s2 / w2
        var = w1 * w2 * (m1 - m2) ** 2
    var[~np.isfinite(var)] = -1
    best = np.where(var >= var.max() * (1 - 1e-9))[0]          # an empty gap between the classes gives a flat maximum
    return float(edges[int(best[len(best) // 2]) + 1])          # take its middle, not its first bin


@dataclass
class Pad:
    id: int
    x0: float
    x1: float
    y0: float
    y1: float
    n_px: int
    level: float


def pads_from_survey(sv: SurveyData, channel: str | None = None, min_px: int = 6) -> list[Pad]:
    """Bright regions of the survey map (log XRF / I0, Otsu), numbered in reading order."""
    ch = channel or (sv.lines[0] if sv.lines else "total")
    vals = sv.values(ch, per_i0=True, log10=True, clip=None)
    g = sv.grid(vals)
    img = g.img
    fin = np.isfinite(img)
    if fin.sum() < min_px:
        return []
    thr = otsu(img[fin])
    mask = fin & (img > thr)
    lab, n = ndimage.label(mask)
    pads = []
    for k in range(1, n + 1):
        sel = lab == k
        if sel.sum() < min_px:
            continue
        rows, cols = np.where(sel)
        pads.append(Pad(0, float(g.xs[cols.min()]), float(g.xs[cols.max()]), float(g.ys[rows.min()]), float(g.ys[rows.max()]),
                        int(sel.sum()), float(np.nanmean(img[sel]))))
    pads.sort(key=lambda p: (-round(p.y1, 1), p.x0))              # reading order: top row first, then left to right
    for i, p in enumerate(pads):
        p.id = i + 1
    return pads


def zones_from_pads(pads: list[Pad], start_id: int = 1, labels: dict[int, str] | None = None) -> list[Zone]:
    out = []
    for p in pads:
        out.append(Zone(id=start_id + len(out), label=(labels or {}).get(p.id, f"pad {p.id}"), comment="proposed from the survey pads",
                        rects=[[round(p.x0, 3), round(p.x1, 3), round(p.y0, 3), round(p.y1, 3)]]))
    return out


# ----------------------------------------------------------------------------- a grid of zones (plate_layout.json)
def norm_text(s) -> str:
    """Lower-case ASCII letters and digits only (sgm_report `norm_text`): 'hematite' -> 'hematite',
    'holderB_Fe2O3' -> 'n2lawklr'."""
    import re
    import unicodedata
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower())


def layout_group_entry(gid: str, rows: int, cols: int, labels, first_number: int = 1) -> tuple[dict, dict]:
    """(group, samples) to add to a holder of plate_layout.json for a grid of rows x cols: the cells hold plate
    numbers from `first_number` in reading order (null for an empty position, None in `labels`), and samples maps
    each number to its label. `labels`: flat in reading order or nested rows; None = numbered positions without
    names. The layout's own format (sgm_report): {id, rows, cols, cells, match}."""
    rows, cols = int(rows), int(cols)
    flat = []
    if labels is not None:
        vals = list(labels)
        flat = [v for row in vals for v in row] if vals and all(isinstance(v, (list, tuple)) for v in vals) else vals
    cells, samples, n = [], {}, int(first_number)
    for r in range(rows):
        row = []
        for c in range(cols):
            i = r * cols + c
            if labels is not None and i < len(flat) and flat[i] is None:
                row.append(None)
                continue
            row.append(n)
            if labels is not None and i < len(flat) and flat[i]:
                samples[str(n)] = str(flat[i])
            n += 1
        cells.append(row)
    return {"id": str(gid), "rows": rows, "cols": cols, "cells": cells, "match": []}, samples


def add_layout_group(path, holder: str, group: dict, samples: dict) -> None:
    """Add (or replace, same id) a group of a holder in plate_layout.json, with its plate numbers' names: the file is
    created when missing, the previous one is kept as plate_layout.backup-<time>.json, everything else in it stays,
    and the write is atomic. ProtectedPathError in a protected folder; ValueError when the file is not a JSON
    object."""
    import datetime as dt
    import json
    from pathlib import Path
    from aurora.core.io.config import load_json_checked
    from aurora.core.io.safety import assert_writable
    from aurora.core.io.textio import write_text_atomic
    path = Path(path)
    assert_writable(path)
    raw, problem = load_json_checked(path)
    if problem or (raw is not None and not isinstance(raw, dict)):
        raise ValueError(f"{path.name} is {problem or 'not a JSON object'}: fix or move it first, AURORA does not "
                         "write over it")
    raw = dict(raw or {})
    lay = raw.get(holder)
    if not isinstance(lay, dict):
        lay = {}
    lay = dict(lay)
    lay["samples"] = {**(lay.get("samples") if isinstance(lay.get("samples"), dict) else {}), **samples}
    groups = [g for g in (lay.get("groups") if isinstance(lay.get("groups"), list) else [])
              if not (isinstance(g, dict) and str(g.get("id")) == str(group["id"]))]
    lay["groups"] = groups + [group]
    raw[holder] = lay
    if path.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        write_text_atomic(path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}"), path.read_text(encoding="utf-8-sig"))
    write_text_atomic(path, json.dumps(raw, indent=1, ensure_ascii=False))


def layout_groups(layout: dict, holder: str) -> list[dict]:
    """The groups of plate_layout.json for a holder (rows, cols, cells of plate numbers, match words, names), each
    with its sample names resolved: g['labels'] = the names in the sketch's reading order (None = an empty position),
    nested like g['cells'], or the flat g['names'] list of a group without a known arrangement."""
    lay = layout.get(holder) if holder and isinstance(layout, dict) else None
    if not isinstance(lay, dict):
        return []
    samples = lay.get("samples") if isinstance(lay.get("samples"), dict) else {}
    samples = {str(k): v for k, v in samples.items()}
    out = []
    for g in lay.get("groups") if isinstance(lay.get("groups"), list) else []:
        from aurora.core.io.config import _check_layout_group
        if _check_layout_group(g) is not None:          # a group of the wrong shape is never used (never raises)
            continue
        g = dict(g)
        if g.get("cells"):
            g["labels"] = [[(samples.get(str(n)) or str(n)) if n is not None else None for n in row]
                           for row in g["cells"]]
        else:
            g["labels"] = [n for n in g.get("names") or [] if n]
        out.append(g)
    return out


def layout_group(layout: dict, holder: str, label: str) -> dict | None:
    """The layout group of a scan (sgm_report `Config.group_for`): the group one of whose 'match' words (or, unless
    'match_names' is false, a sample name, first 9 letters) is in the scan label; the longest match wins. Words of
    fewer than 3 letters never match."""
    lay = layout.get(holder) if holder and isinstance(layout, dict) else None
    if not isinstance(lay, dict) or not lay.get("groups"):
        return None
    t = norm_text(label)
    samples = lay.get("samples") if isinstance(lay.get("samples"), dict) else {}
    samples = {str(k): v for k, v in samples.items()}
    best, best_len = None, 0
    for g in layout_groups(layout, holder):
        words = [norm_text(w) for w in g.get("match") or []]
        if g.get("match_names", True):
            words += [norm_text(samples.get(str(n), ""))[:9] for row in g.get("cells") or [] for n in row
                      if n is not None and samples.get(str(n))]
            words += [norm_text(n)[:9] for n in g.get("names") or [] if n]
        hits = [w for w in words if len(w) >= 3 and w in t]
        if hits and max(map(len, hits)) > best_len:
            best, best_len = g, max(map(len, hits))
    return best


@dataclass
class GridCell:
    """One cell of a grid of zones, in the map's reading order (row 0 = top, col 0 = left)."""
    row: int
    col: int
    cell: tuple[float, float, float, float]     # the whole cell, x0, x1, y0, y1 (mm)
    rect: tuple[float, float, float, float]     # the zone rectangle (the cell shrunk to `fill`, or snapped to a pad)
    label: str | None = None                    # None: no label given (or an empty position of the layout)
    group: str = ""
    empty: bool = False                         # the layout marks this position empty (null)

    @property
    def name(self) -> str:
        return f"row {self.row + 1}, col {self.col + 1}"


def _matrix(values, sr: int, sc: int, fill=None) -> np.ndarray:
    """An (sr, sc) object array from a flat list in reading order or from nested rows; missing entries = fill."""
    m = np.full((sr, sc), fill, dtype=object)
    if values is None:
        return m
    if isinstance(values, str):
        m[:, :] = values
        return m
    vals = list(values)
    if vals and all(isinstance(v, (list, tuple)) for v in vals):             # nested rows (layout cells)
        for r, row in enumerate(vals[:sr]):
            for c, v in enumerate(list(row)[:sc]):
                m[r, c] = v
        return m
    for i, v in enumerate(vals[:sr * sc]):
        m[i // sc, i % sc] = v
    return m


def grid_cells(bbox, rows: int, cols: int, fill: float = 0.7, rotation: int = 0, labels=None,
               groups=None) -> list[GridCell]:
    """The cells of a rows x cols grid over bbox = (x0, x1, y0, y1) mm, in reading order (top row first, then left to
    right; y grows upwards as on the survey map). Each zone rectangle is its cell shrunk about the cell centre to
    `fill` of the cell's width and height.

    labels / groups are given in the layout's own reading order, flat or as nested rows (plate_layout.json cells):
    rows x cols for rotation 0 and 180, cols x rows for 90 and 270. `rotation` (degrees, counter-clockwise like
    np.rot90 and sgm_report) is how the plate sat on the beamline with respect to the layout: 180 reverses the
    assignment (a holder mounted upside down, such as N3). groups may also be one string for every cell."""
    x0, x1 = sorted((float(bbox[0]), float(bbox[1])))
    y0, y1 = sorted((float(bbox[2]), float(bbox[3])))
    rows, cols = int(rows), int(cols)
    if rows < 1 or cols < 1:
        raise ValueError("rows and cols must be at least 1")
    if not 0 < float(fill) <= 1:
        raise ValueError("fill must be in (0, 1]")
    if int(rotation) % 360 not in (0, 90, 180, 270):
        raise ValueError("rotation must be 0, 90, 180 or 270 degrees")
    k = (int(rotation) % 360) // 90
    sr, sc = (rows, cols) if k % 2 == 0 else (cols, rows)
    lab = np.rot90(_matrix(labels, sr, sc), k)
    # an entry that is in the list but None is an empty position of the layout; a list too short just gives no label
    marks = None if labels is None or isinstance(labels, str) else \
        [[True] * len(row) for row in labels] if labels and all(isinstance(v, (list, tuple)) for v in labels) else \
        [True] * len(list(labels))
    given = np.rot90(_matrix(marks, sr, sc, fill=False), k)
    grp = np.rot90(_matrix(groups, sr, sc, fill=""), k)
    w, h = (x1 - x0) / cols, (y1 - y0) / rows
    out = []
    for r in range(rows):
        for c in range(cols):
            cx, cy = x0 + (c + 0.5) * w, y1 - (r + 0.5) * h
            cell = (x0 + c * w, x0 + (c + 1) * w, y1 - (r + 1) * h, y1 - r * h)
            rect = (cx - fill * w / 2, cx + fill * w / 2, cy - fill * h / 2, cy + fill * h / 2)
            label = lab[r, c]
            out.append(GridCell(row=r, col=c, cell=cell, rect=rect, label=None if label is None else str(label),
                                group=str(grp[r, c] or ""), empty=bool(given[r, c]) and label is None))
    return out


def snap_to_pads(cells: list[GridCell], pads: list[Pad], pitch: tuple[float, float] = (0.0, 0.0),
                 min_share: float = 0.3) -> tuple[list[GridCell], list[GridCell]]:
    """Each cell shrinks to the bright survey pad(s) inside it (pads_from_survey): the pads whose centre lies in the
    cell, or that cover at least `min_share` of the cell's area (sgm_report's rule for a cell owning a pad: >= 30 %).
    The new rectangle is their footprint (half a survey pitch beyond the outermost pixel centres) clipped to the
    cell. Returns (kept, skipped): cells without a pad are skipped (blank positions of the plate)."""
    hx, hy = float(pitch[0]) / 2, float(pitch[1]) / 2
    kept, skipped = [], []
    for cell in cells:
        cx0, cx1, cy0, cy1 = cell.cell
        area = max((cx1 - cx0) * (cy1 - cy0), 1e-12)
        mine = []
        for p in pads:
            px0, px1, py0, py1 = p.x0 - hx, p.x1 + hx, p.y0 - hy, p.y1 + hy
            mx, my = (px0 + px1) / 2, (py0 + py1) / 2
            ox = max(0.0, min(cx1, px1) - max(cx0, px0))
            oy = max(0.0, min(cy1, py1) - max(cy0, py0))
            if (cx0 <= mx <= cx1 and cy0 <= my <= cy1) or ox * oy >= min_share * area:
                mine.append((px0, px1, py0, py1))
        if not mine:
            skipped.append(cell)
            continue
        x0 = max(cx0, min(m[0] for m in mine)); x1 = min(cx1, max(m[1] for m in mine))
        y0 = max(cy0, min(m[2] for m in mine)); y1 = min(cy1, max(m[3] for m in mine))
        if x1 <= x0 or y1 <= y0:
            skipped.append(cell)
            continue
        kept.append(GridCell(row=cell.row, col=cell.col, cell=cell.cell, rect=(x0, x1, y0, y1), label=cell.label,
                             group=cell.group, empty=cell.empty))
    return kept, skipped


def zones_from_cells(cells: list[GridCell], start_id: int = 1, rows: int | None = None,
                     cols: int | None = None) -> list[Zone]:
    """Zones (one rectangle each) from grid cells, numbered from start_id in the cells' order. A cell without a label
    is named 'zone N' (N = its id), like a new zone of Reduce."""
    shape = f"grid {rows} x {cols}, " if rows and cols else "grid, "
    out = []
    for cell in cells:
        zid = start_id + len(out)
        out.append(Zone(id=zid, label=cell.label or f"zone {zid}", group=cell.group,
                        comment=f"{shape}{cell.name}", rects=[[round(v, 6) for v in cell.rect]]))
    return out


def zones_from_grid(bbox, rows: int, cols: int, fill: float = 0.7, rotation: int = 0, labels=None, groups=None,
                    start_id: int = 1) -> list[Zone]:
    """A rows x cols grid of rectangular zones over bbox = (x0, x1, y0, y1) mm, in reading order (top row first): see
    grid_cells for fill, rotation, labels and groups. Every cell gives a zone."""
    cells = grid_cells(bbox, rows, cols, fill=fill, rotation=rotation, labels=labels, groups=groups)
    return zones_from_cells(cells, start_id=start_id, rows=rows, cols=cols)


def zones_from_summary(summary_scans: list[dict], pitches: dict[str, tuple[float, float]], start_id: int = 1) -> list[Zone]:
    """sgm_report's zones (name, centre x/y, px count) as square rectangles of the right area, one per distinct name."""
    out, seen = [], set()
    for sc in summary_scans:
        px, py = pitches.get(sc.get("scan", ""), (0.2, 0.2))
        for z in sc.get("zones", []):
            name = z.get("name") or f"zone {z.get('id')}"
            if name in seen or z.get("x") is None or not z.get("px"):
                continue
            seen.add(name)
            side = np.sqrt(float(z["px"]))
            hx, hy = side * px / 2, side * py / 2
            out.append(Zone(id=start_id + len(out), label=name, comment=f"proposed from sgm_report ({sc.get('scan', '')[:6]}, {z['px']} px)",
                            rects=[[round(z["x"] - hx, 3), round(z["x"] + hx, 3), round(z["y"] - hy, 3), round(z["y"] + hy, 3)]]))
    return out
