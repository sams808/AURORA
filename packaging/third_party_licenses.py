"""THIRD_PARTY_LICENSES.txt for the build about to be shipped: a header (scope, the Qt / PySide6 LGPL terms, bundled
data files with their own terms) and one block per third-party package (name, version, licence, URL, licence text),
read from importlib.metadata, so no extra tool is needed.

    <build venv python> packaging/third_party_licenses.py <output file>

packaging/aurora.spec calls generate() with the distributions whose modules PyInstaller actually bundled and puts the
file into the bundle (aurora/resources/licenses/, which Help > Licences reads) and next to the executable. From the
command line every package of the environment is listed except the build-only tools.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEP = "=" * 72
# build-time tools: never inside the distributed application
BUILD_ONLY = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "macholib", "pip",
              "setuptools", "wheel", "pytest", "pluggy", "iniconfig", "pyyaml", "uv"}
SELF = "aurora-sgm"
# Qt for Python is used under the LGPL-3.0: its wheels carry the text of the Qt commercial licence only, which is not
# the licence AURORA uses, so the LGPL text of packaging/LICENSES replaces it
QT_DISTS = ("pyside6", "shiboken6")          # every PySide6* distribution and shiboken6
LICENCE_WORDS = ("LICENSE", "LICENCE", "COPYING", "NOTICE")
CODE_SUFFIXES = (".py", ".pyc", ".pyi", ".pyd", ".so", ".dll", ".dylib", ".json", ".html", ".js", ".css", ".png", ".svg")


@dataclass
class Entry:
    name: str
    version: str
    licence: str
    url: str
    text: str                       # the licence text(s) shipped with the package


def _norm(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _is_licence_file(name: str) -> bool:
    """LICENSE, COPYING.txt, NOTICE.md, ... and the files whose suffix names the licence: LICENSE.APACHE and
    LICENSE.BSD (packaging), LICENSE.external (fontTools). Never code or data (license.py, licenses.json)."""
    n, suffix = name.upper(), Path(name).suffix.lower()
    if not any(k in n for k in LICENCE_WORDS):
        return False
    return suffix in ("", ".txt", ".md", ".rst") or (n.startswith(LICENCE_WORDS) and suffix[1:].isalpha()
                                                     and suffix not in CODE_SUFFIXES)


def _licence_files(dist) -> list[str]:
    texts = []
    for f in dist.files or []:
        n = f.name.upper()
        if not _is_licence_file(f.name):
            continue
        if "QT-COMMERCIAL" in n:
            continue
        try:
            t = Path(f.locate()).read_text(encoding="utf-8", errors="replace").strip()
        except (OSError, FileNotFoundError):
            continue
        if t:
            texts.append(t)
    return texts


def _qt_text() -> str:
    p = HERE / "LICENSES" / "LGPL-3.0.txt"
    lgpl = p.read_text(encoding="utf-8", errors="replace").strip() if p.is_file() else ""
    return ("Used under the GNU Lesser General Public License version 3 (LGPL-3.0-only). The GNU GPL version 3, on "
            "which the LGPL builds, is in LICENSES/GPL-3.0.txt.\n\n" + lgpl).strip()


def entry(dist) -> Entry:
    md = dist.metadata
    name, ver = md.get("Name", "?"), dist.version
    lic = md.get("License-Expression") or md.get("License") or ""
    full_text_in_field = len(lic) > 120
    if not lic or full_text_in_field:
        cls = [c.split("::")[-1].strip() for c in md.get_all("Classifier") or [] if c.startswith("License ::")]
        short = "; ".join(cls)
    else:
        short = lic
    url = md.get("Home-page") or next((u.split(",", 1)[-1].strip() for u in md.get_all("Project-URL") or []), "")
    if _norm(name).startswith(QT_DISTS):
        texts = [_qt_text()]
        short = short or "LGPL-3.0-only"
    else:
        texts = _licence_files(dist)
        if not texts and full_text_in_field:
            texts = [lic.strip()]                       # some packages put the whole licence into the License field
    text = "\n\n".join(texts) if texts else f"(no licence file in the installed package; licence: {short or 'see URL'})"
    return Entry(name=name, version=ver, licence=short or "see the licence text", url=url, text=text)


def distributions() -> dict[str, object]:
    """Every distribution of this environment by normalised name (the first one found wins, as for imports)."""
    out = {}
    for d in metadata.distributions():
        n = _norm(d.metadata.get("Name") or "")
        if n and n not in out:
            out[n] = d
    return out


def bundled_distributions(modules) -> set[str]:
    """Normalised names of the distributions providing these top-level modules (PyInstaller's TOC of the bundle)."""
    pd = metadata.packages_distributions()
    out = set()
    for m in modules:
        top = str(m).split(".")[0]
        for dist in pd.get(top, []):
            out.add(_norm(dist))
    return out


def generate(only=None) -> list[Entry]:
    """One Entry per third-party package: those named in `only` (normalised or not), else every package of the
    environment except the build-only tools. AURORA itself is excluded (LICENSE)."""
    dists = distributions()
    if only is not None:
        names = {_norm(n) for n in only}
    else:
        names = set(dists) - BUILD_ONLY
    names.discard(SELF)
    return [entry(dists[n]) for n in sorted(names) if n in dists]


def header(version: str, entries: list[Entry], scope: str) -> str:
    title = f"THIRD-PARTY LICENCES -- AURORA {version}"
    return f"""{title}
{"=" * len(title)}

AURORA itself is MIT-licensed (LICENSE next to this file), copyright 2026 Sami Soudani.
AURORA is version 2 of John Bussey's SGM XAS GUI, based on James J. Dynes' SGM_Beamline_Analysis Jupyter notebooks
(Canadian Light Source); no code of the notebooks' sf.py (aXis2000 port, GPL-3) is included.

{scope} ({len(entries)} packages; Python {sys.version.split()[0]}, {sys.platform}).
The Python interpreter itself is distributed under the PSF License Agreement (https://docs.python.org/3/license.html).

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
xraydb: MIT licence (its block below). Its database xraydb.sqlite compiles published X-ray tables (Elam, Chantler,
Waasmaier-Kirfel, Keski-Rahkonen and Krause, Krause and Oliver and others); its documentation,
https://xraypy.github.io/XrayDB/, describes the data sources.

{SEP}

"""


def block(e: Entry) -> str:
    return f"{e.name}\n{e.version}\n{e.licence}\n{e.url}\n\n{e.text}\n\n{SEP}\n\n"


def text(version: str | None = None, only=None) -> str:
    if version is None:
        sys.path.insert(0, str(HERE))
        import specutil
        version = specutil.version()
    entries = generate(only)
    scope = ("This file lists every third-party package bundled into the application" if only is not None else
             "This file lists every package of the build environment except the build-only tools "
             + ", ".join(sorted(BUILD_ONLY)))
    return header(version, entries, scope) + "".join(block(e) for e in entries)


def write(out: Path, only=None, version: str | None = None) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text(version, only), encoding="utf-8")
    return out


def main() -> int:
    if len(sys.argv) < 2:                     # never a default inside packaging/ (no generated file belongs there)
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    if SELF not in distributions():
        raise SystemExit("aurora-sgm is not installed in this environment: install it before writing the licences")
    write(out)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
