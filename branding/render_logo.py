"""Render the AURORA mark and wordmark from their SVGs, and build the Windows .ico and the macOS .icns from the PNGs.

    python branding/render_logo.py              # PNGs (Qt's SVG renderer), then aurora.ico and aurora.icns (Pillow)
    python branding/render_logo.py --icons      # aurora.ico and aurora.icns only, from the existing PNGs

aurora.icns holds the PNG of every size macOS asks for: 16, 32, 64, 128, 256, 512 px at 1x and the 2x (Retina)
variants up to 1024 px, each from its own aurora_mark_<size>.png, so Finder never scales a 1024 px image down to 16.
"""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ICNS element types with PNG payloads (macOS 10.7+): type -> pixel size
ICNS_TYPES = (
    (b"icp4", 16), (b"icp5", 32), (b"icp6", 64), (b"ic07", 128), (b"ic08", 256), (b"ic09", 512),
    (b"ic11", 32),     # 16 pt @2x
    (b"ic12", 64),     # 32 pt @2x
    (b"ic13", 256),    # 128 pt @2x
    (b"ic14", 512),    # 256 pt @2x
    (b"ic10", 1024),   # 512 pt @2x
)


def render_pngs() -> None:
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    _app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    jobs = [("aurora_mark.svg", [16, 32, 48, 64, 128, 256, 512, 1024]), ("aurora_mark_mono.svg", [16, 32, 64, 256]),
            ("aurora_wordmark.svg", [1400])]
    for svg, sizes in jobs:
        r = QSvgRenderer(str(HERE / svg))
        assert r.isValid(), svg
        vb = r.viewBoxF()
        for s in sizes:
            h = int(round(s * vb.height() / vb.width()))
            img = QImage(QSize(s, h), QImage.Format_ARGB32_Premultiplied)
            img.fill(QColor(0, 0, 0, 0))
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            r.render(p)
            p.end()
            out = HERE / svg.replace(".svg", f"_{s}.png")
            img.save(str(out))
            print("wrote", out.name, s, h)


def _png_bytes(size: int) -> bytes:
    from PIL import Image
    src = HERE / f"aurora_mark_{size}.png"
    im = Image.open(src) if src.is_file() else Image.open(HERE / "aurora_mark_1024.png").resize((size, size), Image.LANCZOS)
    if im.size != (size, size):
        im = im.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    im.convert("RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def write_icns(out: Path = HERE / "aurora.icns") -> Path:
    """An .icns file (big-endian 'icns' header, then one element per type: 4-byte type, 4-byte length, PNG data)."""
    cache: dict[int, bytes] = {}
    body = b""
    for kind, size in ICNS_TYPES:
        data = cache.setdefault(size, _png_bytes(size))
        body += kind + struct.pack(">I", 8 + len(data)) + data
    out.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)
    print("wrote", out.name, f"({len(ICNS_TYPES)} images)")
    return out


def write_ico(out: Path = HERE / "aurora.ico") -> Path:
    from PIL import Image
    base = Image.open(HERE / "aurora_mark_256.png")
    base.save(out, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", out.name)
    return out


if __name__ == "__main__":
    if "--icons" not in sys.argv[1:]:
        render_pngs()
    write_ico()
    write_icns()
