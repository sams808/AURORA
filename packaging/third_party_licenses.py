"""Write THIRD_PARTY_LICENSES.txt for the build about to be shipped: a header (scope, the Qt / PySide6 LGPL terms,
bundled data files with their own terms) and one block per package of the build environment (name, version,
licence, URL, licence text), read from importlib.metadata so no extra tool is needed.

    <build venv python> packaging/third_party_licenses.py <output file>

Run by packaging/build_win.bat, packaging/build_mac.sh and CI after AURORA is installed into the build venv.
"""
from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

SEP = "=" * 72
BUILD_ONLY = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "macholib", "pip",
              "setuptools", "wheel", "pytest", "pluggy", "iniconfig", "packaging", "colorama"}


def header(version: str) -> str:
    title = f"THIRD-PARTY LICENCES -- AURORA {version}"
    return f"""{title}
{"=" * len(title)}

AURORA itself is MIT-licensed (LICENSE next to this file), copyright 2026 Sami Soudani.
AURORA is version 2 of John Bussey's SGM XAS GUI, based on James J. Dynes' SGM_Beamline_Analysis Jupyter notebooks
(Canadian Light Source); no code of the notebooks' sf.py (aXis2000 port, GPL-3) is included.

This file lists every package of the build environment (Python {sys.version.split()[0]}, {sys.platform}). Build-time
only, NOT present in the distributed application: {", ".join(sorted(BUILD_ONLY))}.

PyInstaller is GPL-2.0-or-later WITH the Bootloader exception: the bootloader linked into the AURORA executable
may be distributed under AURORA's own licence.

Qt, PySide6 and shiboken6 (The Qt Company)
------------------------------------------
PySide6, PySide6_Essentials, PySide6_Addons and shiboken6 are used under the GNU Lesser General Public License
version 3 (LGPL-3.0-only). The Qt libraries ship unmodified as separate shared libraries inside the application
folder (_internal/PySide6 on Windows, Contents/Frameworks on macOS) and can be replaced by the user with another
build of the same Qt major version, as the LGPL requires. Sources and terms:

    Qt source           https://code.qt.io
    PySide6 source      https://code.qt.io/cgit/pyside/pyside-setup.git
    PySide6 licensing   https://doc.qt.io/qtforpython-6/licenses.html
    LGPL-3.0 text       LICENSES/LGPL-3.0.txt next to this file
    GPL-3.0 text        LICENSES/GPL-3.0.txt (the LGPL-3.0 is a set of permissions on top of it)

Other bundled data with their own terms
---------------------------------------
matplotlib fonts: DejaVu (Bitstream Vera licence) and STIX (SIL OFL), licence files under matplotlib/mpl-data/fonts.
pyqtgraph CET colour maps: CC BY 4.0, licence file under pyqtgraph/colors/maps.
xraydb: the X-ray database (xraydb.sqlite) is public domain / NIST data as described in its package metadata.

{SEP}

"""


def block(dist) -> str:
    md = dist.metadata
    name, ver = md.get("Name", "?"), dist.version
    lic = md.get("License-Expression") or md.get("License") or ""
    if not lic or len(lic) > 120:
        cls = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
        lic = "; ".join(cls) or (lic[:120] + "…" if lic else "see the licence text")
    url = md.get("Home-page") or next((u.split(",", 1)[-1].strip() for u in md.get_all("Project-URL") or []), "")
    texts = []
    for f in dist.files or []:
        n = f.name.upper()
        if any(k in n for k in ("LICENSE", "LICENCE", "COPYING", "NOTICE")) and f.suffix.lower() in ("", ".txt", ".md", ".rst"):
            try:
                texts.append(Path(f.locate()).read_text(encoding="utf-8", errors="replace").strip())
            except (OSError, FileNotFoundError):
                pass
    body = "\n\n".join(texts) if texts else "(no licence file in the installed package; licence as stated above)"
    return f"{name}\n{ver}\n{lic}\n{url}\n\n{body}\n\n{SEP}\n\n"


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "THIRD_PARTY_LICENSES.txt"
    import aurora
    dists = {}
    for d in metadata.distributions():
        n = (d.metadata.get("Name") or "").lower()
        if n and n not in BUILD_ONLY and n not in dists:
            dists[n] = d
    if "aurora-sgm" not in dists:
        raise SystemExit("aurora-sgm is not installed in this environment: install it before writing the licences")
    text = header(aurora.__version__) + "".join(block(dists[k]) for k in sorted(dists))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}: {len(dists)} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
