"""One source for the version and the metadata of the frozen app, used by packaging/aurora.spec, the build scripts, CI
and the tests. Importable without PyInstaller (only version_info() needs it) and without importing aurora.

    python packaging/specutil.py version        # aurora.__version__, read from aurora/__init__.py
    python packaging/specutil.py min-macos      # packaging/min_macos.txt
    python packaging/specutil.py stamp          # Python x.y.z + hash of constraints.txt and pyproject.toml
    python packaging/specutil.py build-json F   # write the build provenance (aurora/_build.json in the bundle)
    python packaging/specutil.py plist          # the Info.plist dict, as JSON

The version is never written anywhere else in packaging/: the spec, the Inno Setup script (/DMyAppVersion), the disk
image and the release notes all ask this module.
"""
from __future__ import annotations

import ast
import hashlib
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

PRODUCT = "AURORA"
COMPANY = "Washington State University"
DESCRIPTION = "AURORA - soft X-ray XAS and XRF stacks from the SGM beamline, Canadian Light Source"
COPYRIGHT = "© 2026 John Bussey and Sami Soudani, MIT licence"
BUNDLE_ID = "edu.wsu.aurora"
PYTHON = (3, 12)                         # the Python of every frozen build (build scripts, CI)


def version(root: Path = ROOT) -> str:
    """aurora.__version__ from aurora/__init__.py, parsed with ast (the package is not imported)."""
    tree = ast.parse((Path(root) / "aurora" / "__init__.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "__version__" for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise SystemExit("no __version__ = '...' in aurora/__init__.py")


def version_tuple(v: str | None = None) -> tuple[int, int, int, int]:
    """'x.y.z' -> (x, y, z, 0); a pre-release suffix after z is dropped ('x.y.zrc1' -> (x, y, z, 0))."""
    v = version() if v is None else v
    nums = []
    for part in v.split(".")[:3]:
        m = re.match(r"\d+", part)
        nums.append(int(m.group(0)) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2], 0)


def dotted4(v: str | None = None) -> str:
    return ".".join(str(n) for n in version_tuple(v))


def min_macos(root: Path = ROOT) -> str:
    """The minimum macOS of the bundle, packaging/min_macos.txt (checked against the binaries by macos_minos.py)."""
    text = (Path(root) / "packaging" / "min_macos.txt").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+", text):
        raise SystemExit(f"packaging/min_macos.txt must hold 'major.minor', not {text!r}")
    return text


def version_strings(exe_name: str = "AURORA.exe", v: str | None = None) -> dict[str, str]:
    """The StringFileInfo of the Windows version resource."""
    d4 = dotted4(v)
    return {
        "CompanyName": COMPANY,
        "FileDescription": DESCRIPTION,
        "FileVersion": d4,
        "InternalName": Path(exe_name).stem,
        "LegalCopyright": COPYRIGHT,
        "OriginalFilename": exe_name,
        "ProductName": PRODUCT,
        "ProductVersion": d4,
    }


def version_info(exe_name: str = "AURORA.exe", v: str | None = None):
    """PyInstaller's VSVersionInfo for EXE(version=...): FileVersion and ProductVersion x.y.z.0."""
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)
    nums = version_tuple(v)
    strings = version_strings(exe_name, v)
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
        kids=[StringFileInfo([StringTable("040904B0", [StringStruct(k, val) for k, val in strings.items()])]),
              VarFileInfo([VarStruct("Translation", [1033, 1200])])])


def plist(v: str | None = None, root: Path = ROOT) -> dict:
    """Info.plist entries of AURORA.app (BUNDLE(info_plist=...)). Any edit of the plist after the build must be followed
    by `codesign --force --deep -s -`."""
    v = version(root) if v is None else v
    return {
        "CFBundleName": PRODUCT,
        "CFBundleDisplayName": PRODUCT,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleShortVersionString": v,
        "CFBundleVersion": v,
        "LSMinimumSystemVersion": min_macos(root),
        "NSHumanReadableCopyright": COPYRIGHT,
        "LSApplicationCategoryType": "public.app-category.education",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,          # follow the system's light / dark appearance
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "Beamtime folder",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["public.folder"],
        }],
    }


def _git(*args: str, root: Path = ROOT) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, encoding="utf-8",
                             errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _dist_version(name: str) -> str:
    from importlib import metadata
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return ""


def build_info(root: Path = ROOT) -> dict:
    """Provenance of a build: version, git describe --dirty, commit and its date, and the key library versions of the
    build environment. About and the self-test show it (aurora/_build.json in the bundle)."""
    return {
        "version": version(root),
        "describe": _git("describe", "--tags", "--always", "--dirty", root=root),
        "commit": _git("rev-parse", "--short=10", "HEAD", root=root),
        "date": _git("log", "-1", "--format=%cI", root=root),
        "python": platform.python_version(),
        "pyside6": _dist_version("PySide6") or _dist_version("PySide6-Essentials"),
        "numpy": _dist_version("numpy"),
        "scipy": _dist_version("scipy"),
        "pyinstaller": _dist_version("pyinstaller"),
        "platform": sys.platform,
        "machine": platform.machine(),
    }


def write_build_json(path: Path, root: Path = ROOT) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_info(root), indent=1), encoding="utf-8")
    return path


def stamp(root: Path = ROOT) -> str:
    """What a build environment was made from: 'py<Python x.y.z> <hash of constraints.txt + pyproject.toml>'. The build
    scripts recreate .venv-build when it differs from the stamp written at its creation."""
    h = hashlib.sha256()
    for rel in ("packaging/constraints.txt", "pyproject.toml"):
        h.update((Path(root) / rel).read_bytes().replace(b"\r\n", b"\n"))
    return f"py{platform.python_version()} {h.hexdigest()[:16]}"


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "version"
    if cmd == "version":
        print(version())
    elif cmd == "min-macos":
        print(min_macos())
    elif cmd == "stamp":
        print(stamp())
    elif cmd == "build-json" and len(argv) > 1:
        print(write_build_json(Path(argv[1])))
    elif cmd == "plist":
        print(json.dumps(plist(), indent=1, ensure_ascii=False))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
