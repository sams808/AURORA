"""Edges and emission lines, from xraydb only (never typed in by hand)."""
from __future__ import annotations

import re
from functools import lru_cache

# the scan-name shell token -> xraydb edge name
SHELL_TO_EDGE = {"K": "K", "L": "L3", "L1": "L1", "L2": "L2", "L3": "L3", "L3L2": "L3", "L32": "L3",
                 "M": "M5", "M4": "M4", "M5": "M5", "M45": "M5", "N": "N5", "N1": "N1", "N2": "N2", "N3": "N3",
                 "N4": "N4", "N5": "N5", "N45": "N5"}

LINE_LABEL_BY_SHELL = {"K": "Kα", "L": "Lα", "M": "Mα"}


@lru_cache(maxsize=512)
def edge_energy(el: str, shell: str) -> float | None:
    """Edge energy in eV for ('U', 'N45') etc., None when xraydb has no such edge."""
    import xraydb
    name = SHELL_TO_EDGE.get(shell.upper(), shell.upper())
    try:
        return float(xraydb.xray_edge(el, name).energy)
    except Exception:
        return None


def line_of_mode(mode: str | None) -> str | None:
    """'PFY F Kα' / 'IPFY O Kα' -> 'F Kα'; 'TEY' / 'total XRF' -> None."""
    m = re.match(r"(?:I?PFY)\s+(.+)$", mode or "")
    return m.group(1) if m else None


def resonant_line(el: str, shell: str) -> str:
    """Emission line label that a scan of edge (el, shell) excites resonantly: F K -> 'F Kα', Se L -> 'Se Lα'."""
    return f"{el} {LINE_LABEL_BY_SHELL.get(shell[0].upper(), 'Kα')}"


def main_line(labels: list[str], edge: tuple[str, str] | None, mode: str | None = None) -> str:
    """The ROI used for the main trace: sgm_report's choice when a summary mode exists, else the resonant line of the
    target edge when it is among the labels, else the first line that is not the total."""
    ml = line_of_mode(mode)
    if ml in labels and ml != "total XRF":
        return ml
    if edge is not None:
        res = resonant_line(*edge)
        if res in labels:
            return res
    return next((l for l in labels if l != "total XRF"), labels[0] if labels else "total XRF")
