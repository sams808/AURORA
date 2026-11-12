"""AURORA: soft X-ray XAS and XRF stack analysis for the SGM beamline (Canadian Light Source)."""
import sys
from pathlib import Path

__version__ = "0.4.1"
APP_NAME = "AURORA"
ORG_NAME = "AURORA"


def resources_dir() -> Path:
    """The folder of the bundled files (icons, style-sheet images, manuals, licences): the only way a module finds them.

    - frozen build (PyInstaller onedir, Windows): <sys._MEIPASS>/aurora/resources, as packaging/aurora.spec bundles it;
    - macOS .app: the same path under Contents/Frameworks, a link to the copy in Contents/Resources (PyInstaller >= 6);
      Contents/Resources/aurora/resources is tried as well;
    - source checkout or installed package: the resources folder next to this file.
    """
    if getattr(sys, "frozen", False):
        candidates = []
        base = getattr(sys, "_MEIPASS", None)
        if base:
            candidates.append(Path(base) / "aurora" / "resources")
        exe = Path(sys.executable).resolve()
        candidates.append(exe.parent / "_internal" / "aurora" / "resources")
        candidates.append(exe.parent.parent / "Resources" / "aurora" / "resources")       # .app/Contents/MacOS/AURORA
        for c in candidates:
            if c.is_dir():
                return c
    return Path(__file__).resolve().parent / "resources"
