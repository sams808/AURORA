"""One vocabulary for names and units, used by every perspective and dialog.

    from aurora.app.vocab import NORM_LABELS, DET_LABELS, plural, unit_suffix
    combo.addItem(NORM_LABELS["i0jump"], "i0jump")        # the text is shown, the code is the item data
    spin.setSuffix(unit_suffix("eV"))                     # ' eV'
    f"{plural(n, 'holder')}"                              # '1 holder', '2 holders'

Plain strings only (no Qt objects at module level).
"""
from __future__ import annotations

# normalisation codes (ScanSettings.fluo_norm / tey_norm, Settings defaults) -> the words shown to the user
NORM_LABELS: dict[str, str] = {
    "raw": "raw counts",
    "i0": "/ I0",
    "i0pre": "/ I0, pre-edge = 0",
    "i0jump": "/ I0, edge jump = 1",
    "area": "/ I0, area = 1",
    "max": "/ I0, maximum = 1",
}

# the four silicon drift detectors, as shown everywhere (combos, notes, legends, readouts)
DET_LABELS: tuple[str, ...] = ("SDD1", "SDD2", "SDD3", "SDD4")
DET_SUM = "sum"


def det_label(det) -> str:
    """'sdd3' / 2 / 'SDD3' -> 'SDD3' (an index counts from 0); anything else unchanged."""
    if isinstance(det, int) and 0 <= det < len(DET_LABELS):
        return DET_LABELS[det]
    s = str(det)
    if s.lower().startswith("sdd") and s[3:].isdigit():
        return f"SDD{int(s[3:])}"
    return s


def det_items() -> list[str]:
    """Items of a detector combo: the sum, then SDD1..SDD4."""
    return [DET_SUM, *DET_LABELS]


# spin-box suffixes (a leading space, as Qt shows the suffix right after the number)
UNITS: dict[str, str] = {
    "eV": " eV",
    "um": " µm",
    "mm": " mm",
    "percent": " %",
    "iterations": " iterations",
    "kcps": " kcps",
    "points": " points",
    "pixels": " px",
    "draws": " draws",
    "components": " components",
    "clusters": " clusters",
    "peaks": " peaks",
    "MAD": " MAD",
    "dpi": " dpi",
}


def unit_suffix(kind: str) -> str:
    """The spin-box suffix of a unit kind ('eV' -> ' eV'); an unknown kind is used as the unit itself."""
    return UNITS.get(kind, f" {kind}" if kind else "")


_IRREGULAR = {"spectrum": "spectra", "energy": "energies", "entry": "entries", "pixel": "pixels", "px": "px",
              "is": "are", "was": "were", "match": "matches", "box": "boxes", "rect": "rects"}


def plural_word(n: int, word: str, many: str | None = None) -> str:
    """The word alone, singular for exactly one: plural_word(2, 'scan') -> 'scans'."""
    if n == 1:
        return word
    if many is not None:
        return many
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    return word + "s"


def plural(n: int, word: str, many: str | None = None) -> str:
    """'1 holder', '2 holders', '0 scans', '3 spectra': never '1 days'."""
    return f"{n} {plural_word(int(n), word, many)}"
