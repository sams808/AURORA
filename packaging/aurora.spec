# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: onedir build on Windows and macOS.  Run from the repo root:  pyinstaller packaging/aurora.spec"""
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON_WIN = os.path.join(ROOT, "branding", "aurora.ico")

# xraydb needs scipy.interpolate and sqlalchemy (its tables are a bundled sqlite file)
datas = collect_data_files("xraydb") + [(os.path.join(ROOT, "aurora", "resources"), os.path.join("aurora", "resources"))]
hiddenimports = (collect_submodules("numpy") + collect_submodules("sqlalchemy") + collect_submodules("scipy.interpolate")
                 + ["PySide6.QtSvg", "h5py.defs", "h5py.utils", "h5py.h5ac", "h5py._proxy"])

a = Analysis(
    [os.path.join(ROOT, "aurora", "__main__.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "matplotlib.tests", "numpy.tests", "scipy.tests", "pandas", "IPython", "jupyter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="AURORA",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON_WIN if sys.platform == "win32" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AURORA")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="AURORA.app", icon=None, bundle_identifier="edu.wsu.aurora",
                 info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "0.1.0"})
