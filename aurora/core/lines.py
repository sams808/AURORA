"""Edges and emission lines, from xraydb only (never typed in by hand)."""
from __future__ import annotations

import re
from functools import lru_cache

# the scan-name shell token -> xraydb edge name
SHELL_TO_EDGE = {"K": "K", "L": "L3", "L1": "L1", "L2": "L2", "L3": "L3", "L3L2": "L3", "L32": "L3",
                 "M": "M5", "M4": "M4", "M5": "M5", "M45": "M5", "N": "N5", "N1": "N1", "N2": "N2", "N3": "N3",
                 "N4": "N4", "N5": "N5", "N45": "N5"}

LINE_LABEL_BY_SHELL = {"K": "Kα", "L": "Lα", "M": "Mα"}


def edge_name(shell: str) -> str:
    """The xraydb edge of a scan-name shell: 'K' -> 'K', 'L' / 'L23' / 'L2,3' / 'L3L2' -> 'L3', 'M45' -> 'M5',
    'N45' -> 'N5' (two subshells in one token: the lower-energy one, which has the higher number)."""
    s = str(shell).upper().replace(",", "")
    if s in SHELL_TO_EDGE:
        return SHELL_TO_EDGE[s]
    m = re.fullmatch(r"([KLMN])(\d)[KLMN]?(\d)", s)
    if m:
        return f"{m.group(1)}{max(m.group(2), m.group(3))}"
    return s


@lru_cache(maxsize=512)
def edge_energy(el: str, shell: str) -> float | None:
    """Edge energy in eV for ('U', 'N45'), ('Fe', 'L23') etc., None when xraydb has no such edge."""
    import xraydb
    name = edge_name(shell)
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


def line_element(label: str | None) -> str | None:
    """'O Kα' -> 'O', 'Fe Lα' -> 'Fe'; None for 'total XRF' or anything that does not start with an element symbol."""
    tok = (label or "").strip().split(" ", 1)[0]
    if not tok:
        return None
    from aurora.core.names import element_symbols
    return tok if tok in element_symbols() else None


def default_signal(scan, summary: dict | None = None, line: str | None = None) -> str:
    """The signal a new zone x scan uses: 'pfy' or 'ipfy' (ScanSettings.signal).

    'ipfy' when the sgm_report summary mode starts with 'IPFY' ('IPFY O Kα'), or when the chosen line (`line`, else the
    line of the summary mode) belongs to another element than the scan's edge: a non-resonant line, the sgm_report
    rule. 'pfy' otherwise, also when the edge or the line is unknown."""
    if summary is None:
        summary = getattr(scan, "summary", None) or {}
    mode = str((summary or {}).get("mode") or "").strip()
    if mode.upper().startswith("IPFY"):
        return "ipfy"
    el = line_element(line or line_of_mode(mode))
    edge = getattr(scan, "edge", None)
    if el and edge and str(edge[0]) and el != str(edge[0]):
        return "ipfy"
    return "pfy"


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
