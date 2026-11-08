"""Check a built report folder the way a release check does: a PDF with enough pages, and an SVG next to every figure
PNG (the PNG and SVG come from the same matplotlib figure; a frozen build without matplotlib's svg backend wrote PNGs
only, or crashed). Used by the self-test and by CI on the frozen app's output:

    python -m aurora.core.reportcheck <report folder> [--min-pages 5]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PREVIEW_DIRS = ("pages",)              # page previews of the PDF: PNG only, by design


def pdf_pages(path: Path) -> int:
    """Number of pages of a PDF written by matplotlib (uncompressed object dictionaries): its /Type /Page objects."""
    data = Path(path).read_bytes()
    return len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", data))


def figure_pngs(folder: Path) -> list[Path]:
    return sorted(p for p in Path(folder).rglob("*.png") if p.parent.name not in PREVIEW_DIRS)


def check(folder: Path, min_pages: int = 5) -> dict:
    """{'pdf', 'pages', 'pngs', 'svgs', 'missing_svg', 'problems'} for the newest PDF under `folder`."""
    folder = Path(folder)
    pdfs = sorted(folder.rglob("*.pdf"), key=lambda p: p.stat().st_mtime)
    problems = []
    pdf = pdfs[-1] if pdfs else None
    pages = pdf_pages(pdf) if pdf else 0
    if pdf is None:
        problems.append(f"no PDF under {folder}")
    elif pages < min_pages:
        problems.append(f"{pdf.name} has {pages} pages, fewer than {min_pages}")
    pngs = figure_pngs(folder)
    missing = [p for p in pngs if not p.with_suffix(".svg").is_file()]
    if not pngs:
        problems.append("no figure PNG")
    if missing:
        problems.append(f"{len(missing)} PNG without an SVG: " + ", ".join(p.name for p in missing[:5]))
    svgs = sorted(Path(folder).rglob("*.svg"))
    return {"pdf": pdf, "pages": pages, "pngs": pngs, "svgs": svgs, "missing_svg": missing, "problems": problems}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aurora.core.reportcheck", description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--min-pages", type=int, default=5)
    a = ap.parse_args(argv)
    r = check(a.folder, a.min_pages)
    print(f"{r['pdf']}: {r['pages']} pages; {len(r['pngs'])} figure PNG, {len(r['svgs'])} SVG")
    for p in r["problems"]:
        print("FAIL:", p)
    return 1 if r["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
