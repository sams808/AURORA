"""`python -m aurora` and the entry script of the frozen app (packaging/aurora.spec)."""
import os
import sys


def prepare_environment() -> None:
    """Before anything imports matplotlib. In the frozen app, PyInstaller's runtime hook points MPLCONFIGDIR at a new
    temporary folder at every launch, so matplotlib rebuilt its font cache each time (seconds before the first
    report figure). The frozen app keeps it in the user cache folder instead: platformdirs user_cache_dir('AURORA')/
    matplotlib (%LOCALAPPDATA%\\AURORA\\Cache\\matplotlib, ~/Library/Caches/AURORA/matplotlib). From source, matplotlib's
    own default is left alone."""
    if not getattr(sys, "frozen", False):
        return
    try:
        from pathlib import Path
        from platformdirs import user_cache_dir
        d = Path(user_cache_dir("AURORA", appauthor=False)) / "matplotlib"
        d.mkdir(parents=True, exist_ok=True)
    except Exception:                       # noqa: BLE001  an unwritable cache folder keeps the temporary one
        return
    os.environ["MPLCONFIGDIR"] = str(d)


if __name__ == "__main__":
    prepare_environment()
    from aurora.cli import main
    raise SystemExit(main())
