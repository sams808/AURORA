# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir build on Windows (AURORA.exe + console twin aurora-cli.exe) and macOS (AURORA.app).

Run from the repo root in a CLEAN virtual environment (packaging/build_win.bat, packaging/build_mac.sh, CI):
    pyinstaller --noconfirm --clean packaging/aurora.spec
"""
import ast
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))


def _version() -> str:
    """aurora.__version__ without importing the package (single source of the version)."""
    tree = ast.parse(open(os.path.join(ROOT, "aurora", "__init__.py"), encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "__version__" for t in node.targets):
            return node.value.value
    raise SystemExit("no __version__ in aurora/__init__.py")


VERSION = _version()
ICON_WIN = os.path.join(ROOT, "branding", "aurora.ico")
ICON_MAC = os.path.join(ROOT, "branding", "aurora.icns")

datas = collect_data_files("xraydb") + [(os.path.join(ROOT, "aurora", "resources"), os.path.join("aurora", "resources"))]
for extra in ("LICENSE", "CREDITS.md"):
    datas.append((os.path.join(ROOT, extra), "."))
# numpy 2.x: frozen apps die on numpy._core._exceptions without the full submodule list; xraydb needs scipy.interpolate
# and sqlalchemy; pyqtgraph imports its submodules by name; matplotlib backends are loaded by name at savefig time
hiddenimports = (collect_submodules("numpy") + collect_submodules("sqlalchemy") + collect_submodules("scipy.interpolate")
                 + [m for m in collect_submodules("pyqtgraph") if not m.startswith(("pyqtgraph.examples", "pyqtgraph.opengl",
                                                                                     "pyqtgraph.jupyter", "pyqtgraph.canvas"))]
                 + ["PySide6.QtSvg", "h5py.defs", "h5py.utils", "h5py.h5ac", "h5py._proxy",
                    "matplotlib.backends.backend_agg", "matplotlib.backends.backend_pdf", "matplotlib.backends.backend_svg",
                    "scipy.optimize", "scipy.signal", "scipy.cluster.vq", "scipy.ndimage", "scipy.linalg", "scipy.spatial"])

a = Analysis(
    [os.path.join(ROOT, "aurora", "__main__.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["Agg", "pdf", "svg", "QtAgg"]}},
    excludes=["tkinter", "_tkinter", "PyQt5", "PyQt6", "PySide2", "matplotlib.tests", "numpy.tests", "scipy.tests", "pandas",
              "IPython", "jupyter", "notebook", "pytest", "pyqtgraph.examples", "pyqtgraph.opengl", "pyqtgraph.jupyter",
              "OpenGL", "torch", "sklearn", "tensorflow", "cv2"],
    noarchive=False,
)
pyz = PYZ(a.pure)

version_file = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)
    nums = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:3]) + (0,)
    version_file = VSVersionInfo(
        ffi=FixedFileInfo(filevers=nums, prodvers=nums, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "Sami Soudani, Washington State University"),
            StringStruct("FileDescription", "AURORA - soft X-ray XAS and XRF stacks (SGM, CLS)"),
            StringStruct("FileVersion", VERSION), StringStruct("ProductName", "AURORA"),
            StringStruct("ProductVersion", VERSION), StringStruct("LegalCopyright", "(c) 2026 Sami Soudani, MIT licence"),
            StringStruct("OriginalFilename", "AURORA.exe"), StringStruct("InternalName", "AURORA")])]),
              VarFileInfo([VarStruct("Translation", [1033, 1200])])])

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="AURORA",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON_WIN if sys.platform == "win32" else ICON_MAC,
    version=version_file,
)
targets = [exe]
if sys.platform == "win32":
    # console twin for the headless commands (aurora-cli report / selftest / project): output and exit code in a terminal
    cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="aurora-cli", debug=False, strip=False, upx=False,
              console=True, icon=ICON_WIN, version=version_file)
    targets.append(cli)
coll = COLLECT(*targets, a.binaries, a.datas, strip=False, upx=False, name="AURORA")


def _macos_minos(binaries) -> str:
    """Highest LC_BUILD_VERSION / LC_VERSION_MIN_MACOSX of the bundled Mach-O files: the real minimum macOS."""
    try:
        from macholib.MachO import MachO
        from macholib.mach_o import LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX
    except ImportError:
        return "11.0"
    best = (11, 0)
    for _dest, src, _kind in binaries:
        try:
            m = MachO(src)
        except Exception:
            continue
        for h in m.headers:
            for cmd in h.commands:
                lc, c = cmd[0], cmd[1]
                if lc.cmd in (LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX):
                    v = c.minos if lc.cmd == LC_BUILD_VERSION else c.version
                    ver = ((v >> 16) & 0xFFFF, (v >> 8) & 0xFF)
                    best = max(best, ver)
    return f"{best[0]}.{best[1]}"


if sys.platform == "darwin":
    minos = os.environ.get("AURORA_MACOS_MIN") or _macos_minos(a.binaries)
    print(f"AURORA {VERSION}: LSMinimumSystemVersion {minos}")
    app = BUNDLE(
        coll, name="AURORA.app", icon=ICON_MAC, bundle_identifier="edu.wsu.aurora", version=VERSION,
        info_plist={
            "CFBundleName": "AURORA",
            "CFBundleDisplayName": "AURORA",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": os.environ.get("AURORA_BUILD_NUMBER", VERSION),
            "LSMinimumSystemVersion": minos,
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,          # follow the system's light / dark appearance
            "NSHumanReadableCopyright": "(c) 2026 Sami Soudani, MIT licence",
            "LSApplicationCategoryType": "public.app-category.education",
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Beamtime folder",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["public.folder"],
            }],
        },
    )
