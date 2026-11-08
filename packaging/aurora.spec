# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir build on Windows (AURORA.exe + console twin aurora-cli.exe) and macOS (AURORA.app).

Run from the repo root in a CLEAN virtual environment (packaging/build_win.bat, packaging/build_mac.sh, CI):
    pyinstaller --noconfirm --clean packaging/aurora.spec

One version source: packaging/specutil.py reads aurora/__init__.py, and gives the Windows version resource, the macOS
Info.plist (LSMinimumSystemVersion from packaging/min_macos.txt) and the build provenance (aurora/_build.json in the
bundle, shown by About and the self-test). The licences (LICENSE, CREDITS.md, THIRD_PARTY_LICENSES.txt of the bundled
packages, the LGPL / GPL texts) go into aurora/resources/licenses (Help > Licences) and, on Windows, next to the exe.
"""
import os
import re
import shutil
import sys

sys.path.insert(0, SPECPATH)            # packaging/: specutil, third_party_licenses
import specutil                         # noqa: E402
import third_party_licenses             # noqa: E402

from PyInstaller.utils.hooks import collect_data_files, collect_submodules  # noqa: E402

ROOT = str(specutil.ROOT)
VERSION = specutil.version()
ICON_WIN = os.path.join(ROOT, "branding", "aurora.ico")
ICON_MAC = os.path.join(ROOT, "branding", "aurora.icns")
LIC = os.path.join("aurora", "resources", "licenses")
os.makedirs(workpath, exist_ok=True)
BUILD_JSON = str(specutil.write_build_json(os.path.join(workpath, "_build.json")))
print(f"AURORA {VERSION}: build {open(BUILD_JSON, encoding='utf-8').read()}")

datas = collect_data_files("xraydb") + [
    (os.path.join(ROOT, "aurora", "resources"), os.path.join("aurora", "resources")),
    (BUILD_JSON, "aurora"),
    (os.path.join(ROOT, "LICENSE"), LIC),
    (os.path.join(ROOT, "CREDITS.md"), LIC),
    (os.path.join(SPECPATH, "LICENSES", "*.txt"), os.path.join(LIC, "LICENSES")),
]
# numpy 2.x: frozen apps die on numpy._core._exceptions without the full submodule list; xraydb needs scipy.interpolate
# and sqlalchemy; pyqtgraph imports its submodules by name; matplotlib loads its backends by name at savefig time (the
# frozen report died at the first figure without backend_svg)
# numpy._pyinstaller (numpy's hook for PyInstaller, listed by collect_submodules) pulled PyInstaller (GPL), setuptools,
# altgraph and pefile into the application: build tools, never used at run time. numpy.f2py must stay: scipy's
# `from numpy import *` imports every name of numpy.__all__, f2py included (the frozen self-test failed without it).
NUMPY_BUILD_ONLY = ("numpy._pyinstaller",)
BUILD_TOOLS = ["PyInstaller", "setuptools", "pkg_resources", "_distutils_hack", "distutils", "altgraph", "pefile",
               "win32ctypes", "macholib", "pip", "wheel", *NUMPY_BUILD_ONLY]
hiddenimports = ([m for m in collect_submodules("numpy") if not m.startswith(NUMPY_BUILD_ONLY + ("numpy.tests",))]
                 + collect_submodules("sqlalchemy") + collect_submodules("scipy.interpolate")
                 + [m for m in collect_submodules("pyqtgraph") if not m.startswith(("pyqtgraph.examples", "pyqtgraph.opengl",
                                                                                     "pyqtgraph.jupyter", "pyqtgraph.canvas"))]
                 + ["PySide6.QtSvg", "PySide6.QtNetwork", "h5py.defs", "h5py.utils", "h5py.h5ac", "h5py._proxy",
                    "matplotlib.backends.backend_agg", "matplotlib.backends.backend_pdf", "matplotlib.backends.backend_svg",
                    "scipy.optimize", "scipy.signal", "scipy.cluster.vq", "scipy.ndimage", "scipy.linalg", "scipy.spatial"])

a = Analysis(
    [os.path.join(ROOT, "aurora", "__main__.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={"matplotlib": {"backends": ["Agg", "pdf", "svg"]}},
    excludes=["tkinter", "_tkinter", "PyQt5", "PyQt6", "PySide2", "matplotlib.tests", "numpy.tests", "scipy.tests", "pandas",
              "IPython", "jupyter", "notebook", "pytest", "pyqtgraph.examples", "pyqtgraph.opengl", "pyqtgraph.jupyter",
              "OpenGL", "torch", "sklearn", "tensorflow", "cv2", *BUILD_TOOLS],
    noarchive=False,
)

# THIRD_PARTY_LICENSES.txt of exactly the packages PyInstaller bundled
tops = {name.split(".")[0] for name, _src, _kind in a.pure}
tops |= {re.split(r"[\\/.]", dest)[0] for dest, _src, _kind in a.binaries}
BUNDLED = third_party_licenses.bundled_distributions(sorted(tops))
THIRD = str(third_party_licenses.write(os.path.join(workpath, "THIRD_PARTY_LICENSES.txt"), only=BUNDLED, version=VERSION))
print(f"AURORA {VERSION}: licences of {len(BUNDLED)} bundled distributions: {', '.join(sorted(BUNDLED))}")
licence_toc = [(os.path.join(LIC, "THIRD_PARTY_LICENSES.txt"), THIRD, "DATA")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="AURORA",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON_WIN if sys.platform == "win32" else ICON_MAC,
    version=specutil.version_info("AURORA.exe") if sys.platform == "win32" else None,
)
cli = None
if sys.platform == "win32":
    # console twin for the headless commands (aurora-cli report / selftest / project): output and exit code in a terminal
    cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="aurora-cli", debug=False, strip=False, upx=False,
              console=True, icon=ICON_WIN, version=specutil.version_info("aurora-cli.exe"))
coll = COLLECT(exe, *([cli] if cli is not None else []), a.binaries, a.datas + licence_toc, strip=False, upx=False,
               name="AURORA")

if sys.platform == "win32":
    # the licences next to the executables too (the zip and the installed folder show them at the top level)
    out = os.path.join(DISTPATH, "AURORA")
    for src in (os.path.join(ROOT, "LICENSE"), os.path.join(ROOT, "CREDITS.md"), THIRD):
        shutil.copy2(src, out)
    shutil.copytree(os.path.join(SPECPATH, "LICENSES"), os.path.join(out, "LICENSES"), dirs_exist_ok=True)
    tls = os.path.join(out, "_internal", "PySide6", "plugins", "tls")
    found = sorted(os.listdir(tls)) if os.path.isdir(tls) else []
    print(f"AURORA {VERSION}: Qt TLS backends {found}")
    if not any("schannel" in f or "openssl" in f for f in found):
        raise SystemExit("no Qt TLS backend in the bundle: Help > Check for updates could not reach GitHub")

if sys.platform == "darwin":
    plist = specutil.plist(VERSION)
    print(f"AURORA {VERSION}: LSMinimumSystemVersion {plist['LSMinimumSystemVersion']} (packaging/min_macos.txt)")
    app = BUNDLE(coll, name="AURORA.app", icon=ICON_MAC, bundle_identifier=specutil.BUNDLE_ID, version=VERSION,
                 info_plist=plist)
