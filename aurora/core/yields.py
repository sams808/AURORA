"""Fluorescence yields: inverse partial fluorescence yield (IPFY), one definition for the whole application.

IPFY divides the incident flux by the counts of a NON-resonant emission line (for example O Kα across the F K edge).
That line's intensity falls when the absorption of the edge element rises, so I0 / S_nr is proportional to the total
absorption and free of the saturation and self-absorption of the resonant PFY (Achkar et al., Phys. Rev. B 83,
081106(R), 2011, "Bulk sensitive x-ray absorption spectroscopy free of self-absorption effects").

Modes:
- 'ratio' (default): IPFY = I0 / S_nr. Energies with S_nr <= 0 have no defined ratio: NaN, counted in `n_nonpos`.
- 'cls': the CLS SGM dashboard form, kept for compatibility with plots made there (plot_sgm_bsky_data.py and the
  cluster notebooks of SGM_Beamline_Analysis): N = S_nr / |I0| (I0 = 0 taken as 1), then -N + |min(-N)| + 500. It
  inverts the PFY and shifts it positive; it is not proportional to the absorption.
- 'neg': -PFY = -(S_nr / I0), also a compatibility form.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

IPFY_MODES = ("ratio", "cls", "neg")
CLS_OFFSET = 500.0                      # the dashboard's constant baseline (plot_sgm_bsky_data.py)
MODE_TEXT = {"ratio": "ratio", "cls": "CLS dashboard form, compatibility", "neg": "−PFY, compatibility"}


@dataclass
class Ipfy:
    y: np.ndarray
    mode: str
    n_nonpos: int = 0           # energies with S_nr <= 0 (ratio mode: left out as NaN)


def ipfy(S_nr, I0, mode: str = "ratio") -> Ipfy:
    """IPFY of the counts `S_nr` of a non-resonant line against the incident flux `I0` (same length)."""
    S = np.asarray(S_nr, float)
    i0 = np.asarray(I0, float) if I0 is not None else np.full(S.shape, np.nan)
    if mode not in IPFY_MODES:
        raise ValueError(f"unknown IPFY mode {mode!r}; use one of {', '.join(IPFY_MODES)}")
    if mode == "ratio":
        nonpos = np.isfinite(S) & (S <= 0)
        with np.errstate(all="ignore"):
            y = np.where(S > 0, i0 / S, np.nan)
        return Ipfy(y=y, mode=mode, n_nonpos=int(nonpos.sum()))
    if mode == "cls":
        active = np.abs(i0)
        safe = np.where(active <= 0, 1.0, active)
        with np.errstate(all="ignore"):
            n = S / safe
        neg = -n
        fin = np.isfinite(neg)
        offset = (np.abs(np.min(neg[fin])) + CLS_OFFSET) if fin.any() else np.nan
        return Ipfy(y=neg + offset, mode=mode)
    with np.errstate(all="ignore"):
        return Ipfy(y=-np.where(i0 > 0, S / i0, np.nan), mode=mode)


def describe(line: str, mode: str) -> str:
    """'signal: IPFY (I0 / O Kα, ratio)', the note printed with the traces and in the report."""
    if mode == "ratio":
        return f"signal: IPFY (I0 / {line}, ratio)"
    if mode == "cls":
        return f"signal: IPFY (−N + |min(−N)| + 500 with N = {line} / I0, {MODE_TEXT['cls']})"
    return f"signal: IPFY (−{line} / I0, {MODE_TEXT['neg']})"
