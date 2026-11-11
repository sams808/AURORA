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


# ----------------------------------------------------------------------------- plain words (Easy mode; aurora.glossary)
# Compare / Fit signal codes -> the words shown (Advanced mode adds the code in parentheses)
SIGNAL_LABELS: dict[str, str] = {
    "sdd-scaled": "fluorescence", "fluo": "fluorescence", "fluo_sum_norm": "fluorescence", "pfy": "fluorescence",
    "fluo_sum_raw": "fluorescence, raw counts", "sdd_total": "fluorescence, plain sum",
    "sdd_mean": "fluorescence, mean of the detectors",
    "ipfy": "inverse yield (IPFY)", "ipfy_sum_norm": "inverse yield (IPFY)", "ipfy_sum_raw": "inverse yield (IPFY), raw",
    "TEY": "electron yield (TEY)", "tey": "electron yield (TEY)", "TEY_norm": "electron yield (TEY)",
    "tey_norm": "electron yield (TEY)", "TEY_raw": "electron yield (TEY), raw counts",
    "tey_raw": "electron yield (TEY), raw counts", "I0": "beam intensity (I0)", "i0": "beam intensity (I0)",
}


def signal_label(code: str, advanced: bool = False) -> str:
    """'sdd-scaled' -> 'fluorescence' (Advanced: 'fluorescence (sdd-scaled)'); 'sdd2_norm' -> 'detector 2 (SDD2)';
    an unknown code is shown unchanged."""
    import re
    c = str(code or "")
    word = SIGNAL_LABELS.get(c)
    if word is None:
        m = re.fullmatch(r"(ipfy_)?sdd(\d)(?:_(?:norm|raw|scaled))?", c, re.IGNORECASE)
        if m:
            word = (f"inverse yield, detector {m.group(2)}" if m.group(1) else f"detector {m.group(2)} (SDD{m.group(2)})")
    if word is None:
        return c
    if advanced and word != c:
        return f"{word} ({c})"
    return word


def edge_words(edge_text: str) -> str:
    """'F K' -> 'F K-edge'; 'U N4,5' -> 'U N4,5-edges'; 'Se L3,2' / 'Se L3/L2' -> 'Se L3,2-edges'; '' -> ''."""
    import re
    t = " ".join(str(edge_text or "").split())
    if not t:
        return ""
    if t.lower().endswith(("edge", "edges")):
        return t
    parts = t.split(" ")
    shell = parts[-1]
    many = bool(re.search(r"[,/]|\d\d", shell)) or bool(re.search(r"[A-Z]\d.*[A-Z]\d", shell))
    return f"{t}-edges" if many else f"{t}-edge"


def det_words(dets) -> str:
    """['sdd1', 'sdd2', 'sdd4'] -> 'detectors 1, 2 and 4'; one -> 'detector 3'; none -> 'no detector'."""
    nums = []
    for d in dets or ():
        lab = det_label(d)
        if lab.startswith("SDD") and lab[3:].isdigit():
            nums.append(int(lab[3:]))
    nums = sorted(set(nums))
    if not nums:
        return "no detector"
    if len(nums) == 1:
        return f"detector {nums[0]}"
    if nums == list(range(1, 5)):
        return "the four detectors"
    return "detectors " + ", ".join(str(n) for n in nums[:-1]) + f" and {nums[-1]}"


def channel_words(channel: str, per_i0: bool = False, invert: bool = False, log: bool = False) -> str:
    """What a map's colour shows, in words: 'F Kα' -> 'fluorine X-rays (F Kα) counted by the detectors'; 'total' ->
    'all X-rays counted by the detectors (total XRF)'; 'TEY' -> 'electron yield (TEY)'; 'I0' -> 'beam intensity (I0)'."""
    c = str(channel or "")
    low = c.lower()
    if low in ("", "total", "total xrf", "xrf", "all"):
        what = "all X-rays counted by the detectors (total XRF)"
    elif low == "tey":
        what = "electron yield (TEY)"
    elif low == "i0":
        what = "beam intensity (I0)"
    else:
        sym = c.split(" ")[0]
        name = ELEMENT_NAMES.get(sym, "")
        what = f"{name} X-rays ({c}) counted by the detectors" if name else f"the {c} X-rays counted by the detectors"
    if per_i0 and low != "i0":
        what += ", divided by the beam intensity"
    if log:
        what += ", log scale"
    return what


ELEMENT_NAMES = {
    "B": "boron", "C": "carbon", "N": "nitrogen", "O": "oxygen", "F": "fluorine", "Ne": "neon", "Na": "sodium",
    "Mg": "magnesium", "Al": "aluminium", "Si": "silicon", "P": "phosphorus", "S": "sulfur", "Cl": "chlorine",
    "K": "potassium", "Ca": "calcium", "Ti": "titanium", "V": "vanadium", "Cr": "chromium", "Mn": "manganese",
    "Fe": "iron", "Co": "cobalt", "Ni": "nickel", "Cu": "copper", "Zn": "zinc", "Se": "selenium", "Sb": "antimony",
    "Bi": "bismuth", "U": "uranium", "Ce": "cerium", "La": "lanthanum", "Nd": "neodymium", "Gd": "gadolinium",
}

STATUS_WORDS = {"complete": "complete", "aborted": "stopped early (aborted)", "in_progress": "in progress",
                "test": "beamline test", "survey": "survey"}


def flag_words(flag: str) -> str:
    """A scan flag in words: 'saturated SDD1, SDD2' -> 'detectors 1 and 2 saturated'; 'I0 invalid' -> 'beam reading
    (I0) not usable'; other flags unchanged."""
    import re
    f = str(flag or "").strip()
    m = re.fullmatch(r"saturated\s*(.*)", f, re.IGNORECASE)
    if m:
        dets = re.findall(r"SDD\s*(\d)", m.group(1), re.IGNORECASE)
        return f"{det_words([f'sdd{d}' for d in dets])} saturated" if dets else "a detector saturated"
    if f.lower() == "i0 invalid":
        return "beam reading (I0) not usable"
    if f.lower().startswith("high rate"):
        return "very high count rate on " + det_words(re.findall(r"SDD\d", f)) if "SDD" in f else "very high count rate"
    return f

