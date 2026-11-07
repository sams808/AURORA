"""A folder of reference spectra (CSV / text in any layout the Compare readers accept), listed with the edge each
file name suggests, so the right references are one tick away in Compare."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from aurora.core.names import element_symbols

EXTS = (".csv", ".txt", ".dat", ".nor", ".xmu")
ELEMENT_WORDS = {"fluorine": "F", "oxygen": "O", "carbon": "C", "nitrogen": "N", "sodium": "Na", "calcium": "Ca",
                 "selenium": "Se", "uranium": "U", "copper": "Cu", "iron": "Fe", "aluminium": "Al", "aluminum": "Al",
                 "silicon": "Si", "magnesium": "Mg", "potassium": "K", "barium": "Ba", "zirconium": "Zr"}


@dataclass
class RefEntry:
    path: Path
    label: str
    edge: str = ""          # 'F K', 'Se L3', ... guessed from the file or folder name, '' when unknown


def guess_edge(name: str) -> str:
    """'NaF_F_K_edge.csv' -> 'F K'; 'CaF2-fluorine-k.txt' -> 'F K'; 'ref_Se_L3.csv' -> 'Se L3'."""
    nm = name.replace("-", "_").replace(" ", "_")
    m = re.search(r"(?i)(?:^|_)([A-Za-z]{1,2})_([KLMN]\d?)(?:_|$|\.)", nm)
    if m and m.group(1).capitalize() in element_symbols():
        return f"{m.group(1).capitalize()} {m.group(2).upper()}"
    m = re.search(r"(?i)(" + "|".join(ELEMENT_WORDS) + r")_?([klmn]\d?)(?:_|$|\.)", nm)
    if m:
        return f"{ELEMENT_WORDS[m.group(1).lower()]} {m.group(2).upper()}"
    return ""


def list_references(folder: Path, edge: str | None = None) -> list[RefEntry]:
    """Every spectrum file under the folder (one level of subfolders too); `edge` keeps the element's edges only."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    files = sorted(p for p in list(folder.glob("*")) + list(folder.glob("*/*")) if p.is_file() and p.suffix.lower() in EXTS)
    out = []
    for p in files:
        e = guess_edge(p.stem) or guess_edge(p.parent.name)
        if edge and e and e.split()[0] != edge.split()[0]:
            continue
        label = re.sub(r"(?i)[_\-\s]*(?:ref(?:erence)?|edge)[_\-\s]*", " ", p.stem).strip() or p.stem
        label = re.sub(r"[_\-]+", " ", label).strip()
        out.append(RefEntry(path=p, label=label, edge=e))
    return out
