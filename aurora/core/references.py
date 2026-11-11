"""A folder of reference spectra (CSV / text in any layout the Compare readers accept), listed with the edge each
file name suggests, so the right references are one tick away in Compare."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from aurora.core.names import element_names, element_symbols

EXTS = (".csv", ".txt", ".dat", ".nor", ".xmu")
# a shell as reference file names write it: K, L3, L23, L2,3, L3L2, M5, M45, N45 ...
_SHELL = r"[Kk]|[LlMmNn](?:\d(?:,?\d|[LlMmNn]\d)?)?"


@dataclass
class RefEntry:
    path: Path
    label: str
    edge: str = ""          # 'F K', 'Se L3', 'Fe L2,3', ... guessed from the file or folder name, '' when unknown


def _edge_text(el: str, shell: str) -> str:
    """'Fe', 'l23' -> 'Fe L2,3'; 'Se', 'l3l2' -> 'Se L3,2'; 'F', 'k' -> 'F K'."""
    s = shell.upper().replace(",", "")
    s = re.sub(r"^([KLMN])(\d)[KLMN]?(\d)$", r"\1\2,\3", s)
    return f"{el} {s}"


def guess_edge(name: str) -> str:
    """'NaF_F_K_edge.csv' -> 'F K'; 'CaF2-fluorine-k.txt' -> 'F K'; 'ref_Se_L3.csv' -> 'Se L3'; 'hematite_fe_l23' ->
    'Fe L2,3'; 'manganese_L3' -> 'Mn L3'; 'cobalt-L3' -> 'Co L3'. Element names are xraydb's (H..U), with the
    spellings aluminium and sulphur."""
    nm = name.replace("-", "_").replace(" ", "_")
    end = r"(?=_|$|\.)"
    m = re.search(rf"(?:^|_)([A-Za-z]{{1,2}})_({_SHELL}){end}", nm)
    if m and m.group(1).capitalize() in element_symbols():
        return _edge_text(m.group(1).capitalize(), m.group(2))
    words = element_names()
    m = re.search(r"(?i)(?:^|_)(" + "|".join(sorted(words, key=len, reverse=True)) + rf")_?({_SHELL}){end}", nm)
    if m:
        return _edge_text(words[m.group(1).lower()], m.group(2))
    m = re.search(rf"(?:^|_)([A-Z][a-z]?)((?:L|M|N)\d(?:,?\d|[LMN]\d)?){end}", nm)       # glued, capitalised: FeL23
    if m and m.group(1) in element_symbols():
        return _edge_text(m.group(1), m.group(2))
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
