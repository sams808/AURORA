"""Zone geometry and channel sums: the zone tool's `gridPitch` and `members` (docs/HANDOFF.md section 3.2), polygon
zones (John Bussey's polygon ROI), the export of a zone in the zone tool's own terms, the sums over a zone's pixels,
and `zone_traces`, which hands those sums to aurora.core.spectra.

Membership of a stack pixel in a zone:
- rectangles: the pixel square touches the rectangle (0.45 pitch tolerance), the zone tool's rule;
- painted squares: the pixel centre lies within 0.51 of the square's pitch;
- polygons (Zone.polys, mm): the pixel centre lies inside the polygon (matplotlib.path.Path.contains_points, the rule
  of John Bussey's GUI);
the union of the three, minus the erase rectangles and erase squares.

De-spiking, normalisation and the trace arithmetic live in aurora.core.spectra; the names are re-exported here so
`from aurora.core.zones import despike, normalise, ZoneTraces, ...` keeps working.
"""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field

import numpy as np

from aurora.core.io.config import ScanSettings, Zone
from aurora.core.spectra import (NORM_MODES, I0Score, Normalised, ZoneTraces, build_traces, despike,  # noqa: F401
                                 good_i0_curve, i0_score, interp, normalise, run_median)

log = logging.getLogger("aurora.zones")

__all__ = ["NORM_MODES", "I0Score", "Normalised", "ZoneSums", "ZoneTraces", "build_traces", "despike", "export_json",
           "export_ready",
           "geometry", "good_i0_curve", "grid_pitch", "i0_score", "interp", "members", "normalise", "polygon_inside",
           "run_median", "set_geometry", "shape_text", "zone_bbox", "zone_sums", "zone_traces"]

RECT_TOL = 0.45                 # a pixel square touches a rectangle: half a pitch minus 5 % (the zone tool)
PAINT_TOL = 0.51                # a painted square holds the pixel whose centre is within 0.51 pitch
GEOMETRY_KEYS = ("rects", "erects", "paint", "erase", "polys")


# ----------------------------------------------------------------------------- geometry
def grid_pitch(x: np.ndarray, y: np.ndarray | None) -> tuple[float, float]:
    """(pitch x, pitch y) of a snake / fly scan: rows are plateaus of y, columns the within-row x step."""
    x = np.asarray(x, float)
    n = len(x)
    if n == 0:
        return 0.1, 0.1
    py, on_row = 0.1, None
    if y is not None:
        y = np.asarray(y, float)
        yr = np.round(y, 3)
        vals, counts = np.unique(yr, return_counts=True)
        plateaus = vals[counts >= max(3, 0.25 * counts.max())]
        if len(plateaus) < 2:
            plateaus = vals
        d = np.diff(plateaus)
        py = float(np.median(d)) if d.size else max(1e-3, float(y.max() - y.min()))
        on_row = np.isin(yr, plateaus)
    dx = np.abs(np.diff(x))
    if on_row is not None:
        dx = dx[on_row[1:] & on_row[:-1]]
    dx = dx[dx > 1e-3]
    px = float(np.median(dx)) if dx.size else 0.1
    if y is None and not dx.size:
        px = max((x.max() - x.min()) / np.sqrt(n), 1e-3)
    return px, py


def polygon_inside(poly, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Boolean mask of the points (x, y) inside the polygon [[x, y], ...] (mm): John Bussey's rule,
    matplotlib.path.Path.contains_points on the pixel centres. Fewer than 3 vertices hold nothing."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    if poly is None or len(poly) < 3 or x.size == 0:
        return np.zeros(len(x), bool)
    from matplotlib.path import Path as MplPath             # lazy: only polygon zones need matplotlib
    return MplPath(np.asarray(poly, float)[:, :2]).contains_points(np.column_stack([x, y]))


def members(zone: Zone, x: np.ndarray, y: np.ndarray, pitch: tuple[float, float]) -> np.ndarray:
    """Pixel indices of a stack inside the zone. A pixel counts when its square touches a rectangle (0.45 pitch
    tolerance), sits in a painted square, or has its centre inside a polygon; erase rectangles and squares remove it
    again."""
    x = np.asarray(x, float); y = np.asarray(y, float)
    hx, hy = pitch[0] * RECT_TOL, pitch[1] * RECT_TOL
    inside = np.zeros(len(x), bool)
    for r in zone.rects:
        inside |= (x >= r[0] - hx) & (x <= r[1] + hx) & (y >= r[2] - hy) & (y <= r[3] + hy)
    for s in zone.paint:
        inside |= (np.abs(x - s[0]) <= s[2] * PAINT_TOL) & (np.abs(y - s[1]) <= s[3] * PAINT_TOL)
    for poly in getattr(zone, "polys", None) or []:
        inside |= polygon_inside(poly, x, y)
    if inside.any():
        out = np.zeros(len(x), bool)
        for r in zone.erects:
            out |= (x >= r[0] - hx) & (x <= r[1] + hx) & (y >= r[2] - hy) & (y <= r[3] + hy)
        for s in zone.erase:
            out |= (np.abs(x - s[0]) <= s[2] * PAINT_TOL) & (np.abs(y - s[1]) <= s[3] * PAINT_TOL)
        inside &= ~out
    return np.where(inside)[0]


def zone_bbox(zone: Zone) -> tuple[float, float, float, float] | None:
    """(x0, x1, y0, y1) mm around the zone's rectangles, painted squares and polygons; None for an empty zone."""
    xs, ys = [], []
    for r in zone.rects:
        xs += [r[0], r[1]]; ys += [r[2], r[3]]
    for s in zone.paint:
        xs += [s[0] - s[2] / 2, s[0] + s[2] / 2]; ys += [s[1] - s[3] / 2, s[1] + s[3] / 2]
    for poly in zone.polys:
        xs += [p[0] for p in poly]; ys += [p[1] for p in poly]
    if not xs:
        return None
    return float(min(xs)), float(max(xs)), float(min(ys)), float(max(ys))


def geometry(zone: Zone) -> dict:
    """The zone's drawing in mm (rects, erects, paint, erase, polys), a JSON-ready copy (Copy geometry)."""
    return {k: copy.deepcopy(getattr(zone, k)) for k in GEOMETRY_KEYS}


def set_geometry(zone: Zone, geo: dict, dx: float = 0.0, dy: float = 0.0) -> None:
    """Replace the zone's drawing by `geo` (the form geometry() returns), shifted by (dx, dy) mm."""
    def rect(r):
        return [float(r[0]) + dx, float(r[1]) + dx, float(r[2]) + dy, float(r[3]) + dy]

    def square(s):
        return [float(s[0]) + dx, float(s[1]) + dy, float(s[2]), float(s[3])]
    zone.rects = [rect(r) for r in geo.get("rects", [])]
    zone.erects = [rect(r) for r in geo.get("erects", [])]
    zone.paint = [square(s) for s in geo.get("paint", [])]
    zone.erase = [square(s) for s in geo.get("erase", [])]
    zone.polys = [[[float(p[0]) + dx, float(p[1]) + dy] for p in poly] for poly in geo.get("polys", []) if len(poly) >= 3]


def shape_text(zone: Zone) -> str:
    """'polygon with 12 vertices', '1 rectangle', '2 rectangles, 3 painted pixels', 'nothing drawn'."""
    def n_of(n: int, word: str) -> str:
        return f"{n} {word}" if n == 1 else f"{n} {word}s"
    if len(zone.polys) == 1 and not (zone.rects or zone.paint):
        return f"polygon with {len(zone.polys[0])} vertices"
    parts = []
    if zone.polys:
        parts.append(n_of(len(zone.polys), "polygon"))
    if zone.rects:
        parts.append(n_of(len(zone.rects), "rectangle"))
    if zone.paint:
        parts.append(n_of(len(zone.paint), "painted pixel"))
    return ", ".join(parts) if parts else "nothing drawn"


def _points_of(value):
    """(x, y, pitch) from a StackData-like object or an (x, y, pitch) tuple; None for a bare pitch."""
    if hasattr(value, "x") and hasattr(value, "y") and hasattr(value, "pitch"):
        return np.asarray(value.x, float), np.asarray(value.y, float), tuple(value.pitch)
    if isinstance(value, (tuple, list)) and len(value) == 3 and np.ndim(value[0]) == 1:
        return np.asarray(value[0], float), np.asarray(value[1], float), tuple(value[2])
    return None


def export_ready(zone: Zone, pitches) -> dict:
    """The zone as the HTML zone tool reads it (zones_all.json). The tool knows rectangles and painted squares only:
    every polygon is rasterised into painted squares centred on the pixels inside it, at the pitch of every stack in
    `pitches` (sid -> StackData or (x, y, pitch): the loaded stacks), so the tool finds the same members; the result is
    checked per stack, with erase squares added where a square of another pitch would reach a pixel outside the
    polygon. 'polys' itself is kept for AURORA. A bare (pitch x, pitch y) carries no pixel positions, so no exact
    square can be placed for it: it adds nothing (logged)."""
    out = copy.deepcopy(zone.to_json())
    if not zone.polys:
        return out
    stacks = dict(pitches or {})
    paint = [list(s) for s in out.get("paint", [])]
    erase = [list(s) for s in out.get("erase", [])]
    have = {(round(s[0], 4), round(s[1], 4), round(s[2], 4), round(s[3], 4)) for s in paint}

    def add(dst, keys, cx, cy, px, py):
        k = (round(cx, 4), round(cy, 4), round(px, 4), round(py, 4))
        if k not in keys:
            keys.add(k)
            dst.append([round(float(cx), 6), round(float(cy), 6), float(px), float(py)])

    pts = {sid: _points_of(v) for sid, v in stacks.items()}
    for sid, v in stacks.items():
        p = pts[sid]
        if p is not None:
            x, y, pitch = p
            in_poly = np.zeros(len(x), bool)
            for poly in zone.polys:
                in_poly |= polygon_inside(poly, x, y)
            for i in members(zone, x, y, pitch):
                if in_poly[i]:
                    add(paint, have, x[i], y[i], pitch[0], pitch[1])
        else:
            log.info("zone %s: %s has a pitch but no pixel positions; its polygon is not rasterised for it", zone.id, sid)
    # verify per stack with coordinates: the tool's rule (no polygons) must give AURORA's members
    erased = {(round(s[0], 4), round(s[1], 4), round(s[2], 4), round(s[3], 4)) for s in erase}
    for _round in range(3):
        tool = Zone(id=zone.id, label=zone.label, rects=out.get("rects", []), erects=out.get("erects", []),
                    paint=paint, erase=erase)
        changed = False
        for sid, p in pts.items():
            if p is None:
                continue
            x, y, pitch = p
            want = set(members(zone, x, y, pitch).tolist())
            got = set(members(tool, x, y, pitch).tolist())
            for i in sorted(got - want):
                add(erase, erased, x[i], y[i], pitch[0], pitch[1]); changed = True
            for i in sorted(want - got):
                add(paint, have, x[i], y[i], pitch[0], pitch[1]); changed = True
        if not changed:
            break
    else:
        log.warning("zone %s: the rasterised polygon still differs from AURORA's members on some stack", zone.id)
    out["paint"] = paint
    out["erase"] = erase
    return out


def export_json(zones_file, stacks) -> dict:
    """The zone tool's zones_all.json for a ZonesFile: to_json(export=True) with every zone through export_ready, so
    polygon zones reach the HTML tool as painted squares. `stacks`: sid -> StackData (or (x, y, pitch)) of the loaded
    stacks; a holder's zones use the stacks whose sid they carry settings or members for (all stacks when none)."""
    out = zones_file.to_json(export=True)
    stacks = dict(stacks or {})
    for hk, hz in zones_file.holders.items():
        zs = []
        for z in hz.zones:
            mine = {sid: v for sid, v in stacks.items() if sid in z.settings or sid in z.members} or stacks
            zs.append(export_ready(z, mine) if z.polys else z.to_json())
        out["holders"][hk]["zones"] = zs
    return out


# ----------------------------------------------------------------------------- channel sums
@dataclass
class ZoneSums:
    """Sums over a zone's pixels of one stack, the input of aurora.core.spectra.build_traces."""
    E: np.ndarray                               # (nE,) eV
    n_pix: int                                  # pixels in the zone
    i0: np.ndarray                              # (nE,) I0 summed over the pixels, NaN at skipped energies
    tey: np.ndarray                             # (nE,)
    det: dict[str, np.ndarray]                  # line label -> (nE, 4) counts per SDD
    main: str                                   # the stack's main line (used when the settings name none)
    edge_ev: float | None = None
    valid: np.ndarray | None = None             # (nE,) bool, energies with a usable map
    sid: str = ""
    windows: dict[str, list[int]] | None = None     # per-SDD channel windows [lo, hi) used, None = the fixed line bins
    notes: list[str] = field(default_factory=list)
    edge: tuple[str, str] | None = None         # the stack's edge ('F', 'K'): IPFY of its own line is 1 / PFY


def zone_sums(stack, pix, settings: ScanSettings | None = None) -> ZoneSums:
    """Channel sums over the zone pixels of a StackData: I0, TEY and every line of the stack per SDD.

    When ScanSettings.roi holds per-detector windows {sdd: [lo, hi)}, the counts of the settings' line are the sums of
    those channels read from the raw energy folders (aurora.core.xrf_windows.window_sums, cached); ZoneSums.windows
    says so. Otherwise, and when the raw folders of the stack cannot be found (a note says why), the fixed line bins
    of the stack are used."""
    pix = np.asarray(pix, int)
    s = stack.sum_over(pix)
    out = ZoneSums(E=stack.E, n_pix=int(s["n"]), i0=s["i0"], tey=s["tey"], det=dict(s["det"]), main=stack.main,
                   edge_ev=getattr(stack, "edge_ev", None), valid=getattr(stack, "valid", None),
                   sid=str(getattr(stack, "id", "")), edge=getattr(stack, "edge", None))
    roi = getattr(settings, "roi", None) if settings is not None else None
    if roi and pix.size:
        from aurora.core import xrf_windows as xw
        windows = xw.normalise_windows(roi)
        line = (settings.line or stack.main) if settings is not None else stack.main
        try:
            sums = xw.stack_window_sums(stack, pix, windows)
        except (OSError, ValueError, KeyError) as err:
            sums = None
            out.notes.append(f"emission windows {xw.describe(windows)} not applied ({err}): the fixed {line} bins "
                             "were used")
        if sums is not None:
            out.det[line] = sums
            out.windows = windows
            out.notes.append(f"emission windows {xw.describe(windows)} (read from the raw spectra) in place of the "
                             f"fixed {line} bins")
    return out


def zone_traces(stack, pix, settings: ScanSettings, good_curve: np.ndarray | None = None) -> ZoneTraces | None:
    """The zone tool's `zoneTraces`: sums over the zone pixels (zone_sums), then aurora.core.spectra.build_traces.
    `stack` is a StackData; None when the zone holds no pixel of it. When emission windows were used, the trace's line
    names them ('F Kα · windows 60:74 ch') and its notes say where the counts came from."""
    pix = np.asarray(pix, int)
    if pix.size == 0:
        return None
    sums = zone_sums(stack, pix, settings)
    t = build_traces(sums, settings, good_curve)
    if t is not None:
        for n in sums.notes:
            if n not in t.notes:
                t.notes.append(n)
        if sums.windows:
            from aurora.core import xrf_windows as xw
            t.line = f"{t.line} · windows {xw.describe(sums.windows)}"
    return t
