"""Optional hand-drawn sketches (or photos) of the holders, shown on Survey when one exists.

A sketch is never needed: nothing else in AURORA reads it. Lookup, for holder `h` of the project at `root`, the first
image whose name is `h` (compared without case) with a .png, .jpg or .jpeg suffix, in this order:

1. `<root>/.aurora/sketches/`   the copy AURORA made with Survey > Load a sketch…
2. `<root>/sketches/`           a folder the user keeps with the beamtime
3. `<root>/zones_html/sketches/` where the browser zone tool of sgm_report kept them (always .png there)

`add_sketch` copies an image into the first folder (never elsewhere), `remove_sketch` removes only that copy: the
user's own files in 2. and 3. are never changed. Both go through aurora.core.io.safety (a protected beamtime refuses).
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger("aurora.sketches")

SKETCH_EXTS = (".png", ".jpg", ".jpeg")
OWN_DIR = (".aurora", "sketches")
SKETCH_DIRS = (OWN_DIR, ("sketches",), ("zones_html", "sketches"))      # the lookup order
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg)"


def _folder(root: Path, parts: tuple[str, ...]) -> Path:
    return Path(root).joinpath(*parts)


def _matches(folder: Path, holder: str) -> list[Path]:
    """Images of `folder` named after `holder` (case-insensitive), in name order; [] when the folder is missing."""
    try:
        items = sorted(folder.iterdir())
    except OSError:
        return []
    h = holder.casefold()
    return [p for p in items if p.is_file() and p.suffix.lower() in SKETCH_EXTS and p.stem.casefold() == h]


def find_sketch(root: Path, holder: str | None) -> Path | None:
    """The holder's sketch by the lookup order of the module docstring, or None (no holder, no image)."""
    if not holder or root is None:
        return None
    for parts in SKETCH_DIRS:
        found = _matches(_folder(root, parts), holder)
        if found:
            return found[0]
    return None


def own_sketch(root: Path, holder: str | None) -> Path | None:
    """AURORA's own copy for the holder (<root>/.aurora/sketches/<holder>.<ext>), or None."""
    if not holder or root is None:
        return None
    found = _matches(_folder(root, OWN_DIR), holder)
    return found[0] if found else None


def is_own(root: Path, path: Path | None) -> bool:
    """True when `path` lies in AURORA's own sketch folder of the project (so Remove the sketch may delete it)."""
    if path is None:
        return False
    try:
        return Path(path).resolve().parent == _folder(root, OWN_DIR).resolve()
    except OSError:
        return False


def add_sketch(root: Path, holder: str, image: Path) -> Path:
    """Copy `image` (.png, .jpg or .jpeg) to <root>/.aurora/sketches/<holder><suffix> and return the copy. An earlier
    copy for the holder (another suffix too) is replaced. ValueError for another file type or an empty holder name;
    ProtectedPathError (a PermissionError) when the project is protected; OSError when the file cannot be read."""
    from aurora.core.io.safety import assert_writable
    image = Path(image)
    suffix = image.suffix.lower()
    if suffix not in SKETCH_EXTS:
        raise ValueError(f"{image.name} is not a PNG or JPEG image")
    if not holder or any(c in holder for c in '\\/:*?"<>|'):
        raise ValueError(f"no file name can be made from the holder name {holder!r}")
    folder = _folder(root, OWN_DIR)
    target = folder / f"{holder}{suffix}"
    assert_writable(target)
    if target.exists() and image.resolve() == target.resolve():
        return target                                     # already AURORA's copy
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{target.name}.{os.getpid()}.tmp"
    try:
        shutil.copyfile(image, tmp)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    for old in _matches(folder, holder):                  # one copy per holder: drop the other suffixes
        if old != target:
            try:
                old.unlink()
            except OSError as err:
                log.warning("%s: earlier sketch not removed (%s)", old, err)
    log.info("sketch of %s: %s copied to %s", holder, image, target)
    return target


def remove_sketch(root: Path, holder: str) -> list[Path]:
    """Delete AURORA's copies for the holder (<root>/.aurora/sketches/<holder>.*); returns what was deleted. The user's
    own sketches/ and zones_html/sketches/ files are never touched. ProtectedPathError when the project is protected."""
    from aurora.core.io.safety import assert_writable
    gone = []
    for p in _matches(_folder(root, OWN_DIR), holder):
        assert_writable(p)
        p.unlink()
        gone.append(p)
    if gone:
        log.info("sketch of %s removed: %s", holder, ", ".join(p.name for p in gone))
    return gone
