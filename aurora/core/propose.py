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
