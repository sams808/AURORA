"""Compatibility fixes for pyqtgraph on the Qt versions AURORA ships with.

SVG export: Qt 6.12's QSvgGenerator writes closed paths as '... L0,0 Z', with the close command as its own token.
pyqtgraph 0.14's SVGExporter.correctCoordinates reads every token of a path as 'x,y' and fails on the 'Z'
(ValueError: not enough values to unpack), so every SVG export of a plot failed. install() wraps that function: each
'Z' token gets a dummy coordinate the exporter can transform, and the dummy is removed again afterwards, so every
subpath keeps its close command. Plain functions and strings only (no Qt object at module level).
"""
from __future__ import annotations

import importlib
import re

_DUMMY = re.compile(r"([Zz])-?[\d.]+(?:e[-+]?\d+)?,-?[\d.]+(?:e[-+]?\d+)?")


def _fixed(orig):
    def correct(node, defs, item, options):
        for p in node.getElementsByTagName("path"):
            d = p.getAttribute("d")
            if "Z" in d or "z" in d:
                p.setAttribute("d", " ".join(f"{t}0,0" if t in ("Z", "z") else t for t in d.split()))
        out = orig(node, defs, item, options)
        for p in node.getElementsByTagName("path"):
            d = p.getAttribute("d")
            if "Z" in d or "z" in d:
                p.setAttribute("d", _DUMMY.sub(r"\1", d))
        return out
    correct._aurora = True
    return correct


def install() -> bool:
    """Patch pyqtgraph's SVG exporter once (idempotent). Returns True when the patch is in place."""
    try:
        mod = importlib.import_module("pyqtgraph.exporters.SVGExporter")
    except ImportError:
        return False
    fn = getattr(mod, "correctCoordinates", None)
    if fn is None:
        return False
    if not getattr(fn, "_aurora", False):
        mod.correctCoordinates = _fixed(fn)
    return True
