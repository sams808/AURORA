"""Stitching several stacks (tiles) of one plate into one: trims per edge, automatic overlap halving, seam check,
and the baked result as a StackData usable by zones, traces and maps (CLS stitching_utils, reduced to the data part)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from aurora.core.stack import StackData
from aurora.core.zones import grid_pitch


@dataclass
class Trim:
    left: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    top: float = 0.0


def bbox(sd: StackData) -> tuple[float, float, float, float]:
    return float(sd.x.min()), float(sd.x.max()), float(sd.y.min()), float(sd.y.max())


def pixel_box(sd: StackData) -> tuple[float, float, float, float]:
    """Bounding box of the pixel squares (half a pitch beyond the outermost centres)."""
    px, py = sd.pitch
    x0, x1, y0, y1 = bbox(sd)
    return x0 - px / 2, x1 + px / 2, y0 - py / 2, y1 + py / 2


def auto_trims(stacks: list[StackData]) -> list[Trim]:
    """Split every pairwise overlap of the tiles' pixel boxes in half (the CLS 'Auto-Calculate Overlaps')."""
    trims = [Trim() for _ in stacks]
    boxes = [pixel_box(s) for s in stacks]
    for i in range(len(stacks)):
        for j in range(i + 1, len(stacks)):
            a, b = boxes[i], boxes[j]
            ox = min(a[1], b[1]) - max(a[0], b[0]); oy = min(a[3], b[3]) - max(a[2], b[2])
            if ox <= 0 or oy <= 0:
                continue
            if ox <= oy:                      # side by side: trim in x
                if a[0] < b[0]:
                    trims[i].right = max(trims[i].right, ox / 2); trims[j].left = max(trims[j].left, ox / 2)
                else:
                    trims[j].right = max(trims[j].right, ox / 2); trims[i].left = max(trims[i].left, ox / 2)
            else:                             # stacked: trim in y
                if a[2] < b[2]:
                    trims[i].top = max(trims[i].top, oy / 2); trims[j].bottom = max(trims[j].bottom, oy / 2)
                else:
                    trims[j].top = max(trims[j].top, oy / 2); trims[i].bottom = max(trims[i].bottom, oy / 2)
    return trims


def kept_mask(sd: StackData, t: Trim) -> np.ndarray:
    x0, x1, y0, y1 = bbox(sd)
    return (sd.x >= x0 + t.left - 1e-9) & (sd.x <= x1 - t.right + 1e-9) & (sd.y >= y0 + t.bottom - 1e-9) & (sd.y <= y1 - t.top + 1e-9)


@dataclass
class Seam:
    tiles: tuple[int, int]
    axis: str                 # 'x', 'y' or 'xy'
    gap_mm: float             # > 0 gap, < 0 overlap, 0 perfect (within half a pitch)
    verdict: str


def seams(stacks: list[StackData], trims: list[Trim]) -> list[Seam]:
    out = []
    kept = [(sd.x[kept_mask(sd, t)], sd.y[kept_mask(sd, t)]) for sd, t in zip(stacks, trims)]
    for i in range(len(stacks)):
        for j in range(i + 1, len(stacks)):
            (xi, yi), (xj, yj) = kept[i], kept[j]
            if not xi.size or not xj.size:
                continue
            px, py = stacks[i].pitch
            oy = min(yi.max(), yj.max()) - max(yi.min(), yj.min()); ox = min(xi.max(), xj.max()) - max(xi.min(), xj.min())
            if oy > 0 and ox <= 0:
                gap = (xj.min() - xi.max() if xi.max() < xj.min() else xi.min() - xj.max()) - px
                out.append(Seam((i, j), "x", float(gap), "PERFECT" if abs(gap) <= px / 2 else ("GAP" if gap > 0 else "OVERLAP")))
            elif ox > 0 and oy <= 0:
                gap = (yj.min() - yi.max() if yi.max() < yj.min() else yi.min() - yj.max()) - py
                out.append(Seam((i, j), "y", float(gap), "PERFECT" if abs(gap) <= py / 2 else ("GAP" if gap > 0 else "OVERLAP")))
            elif ox > 0 and oy > 0:
                out.append(Seam((i, j), "xy", float(-min(ox, oy)), "OVERLAP"))
    return out


def stitch(stacks: list[StackData], trims: list[Trim] | None = None, name: str | None = None) -> StackData:
    """Concatenate the kept pixels of every tile on the energies common to all (within 0.01 eV) and the ROI labels
    common to all; the result is a StackData named Stitched_<first>_to_<last>."""
    if len(stacks) < 2:
        raise ValueError("stitching needs at least two stacks")
    trims = trims or [Trim() for _ in stacks]
    common = set(np.round(stacks[0].E, 2))
    for s in stacks[1:]:
        common &= set(np.round(s.E, 2))
    E = np.array(sorted(common))
    if E.size == 0:
        raise ValueError("the stacks have no energy in common")
    labels = [l for l in stacks[0].labels if all(l in s.labels for s in stacks[1:])]
    if not labels:
        raise ValueError("the stacks have no ROI label in common")
    xs, ys, i0s, teys, rois = [], [], [], [], []
    valid = np.ones(E.size, bool)
    for sd, t in zip(stacks, trims):
        keep = kept_mask(sd, t)
        idx = np.array([int(np.argmin(np.abs(np.round(sd.E, 2) - e))) for e in E])
        xs.append(sd.x[keep]); ys.append(sd.y[keep])
        i0s.append(sd.i0[idx][:, keep]); teys.append(sd.tey[idx][:, keep])
        rois.append(np.stack([sd.roi_pix_det[idx][:, keep, sd.labels.index(l), :] for l in labels], axis=2))
        valid &= sd.valid[idx]
    x = np.concatenate(xs); y = np.concatenate(ys)
    order = np.lexsort((x, y))
    out = StackData(id=f"stitched_{stacks[0].id}", name=name or f"Stitched_{stacks[0].name}_to_{stacks[-1].name}", day=stacks[0].day,
                    E=E, x=x[order], y=y[order], i0=np.concatenate(i0s, axis=1)[:, order], tey=np.concatenate(teys, axis=1)[:, order],
                    roi_pix_det=np.concatenate(rois, axis=1)[:, order], valid=valid, labels=labels, main=stacks[0].main,
                    edge=stacks[0].edge, edge_ev=stacks[0].edge_ev, source=f"stitched from {len(stacks)} tiles",
                    notes=[f"tiles: {', '.join(s.name for s in stacks)}"])
    out.pitch = grid_pitch(out.x, out.y)
    return out
