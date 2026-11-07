"""Rail and action icons: inline stroke SVG rendered to QIcon in the colour asked for (no icon files to ship)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

RESOURCES = Path(__file__).resolve().parent.parent / "resources"

_PATHS = {
    "home": '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
    "survey": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M9 3v18"/>',
    "reduce": '<path d="M3 17l5-9 4 6 3-4 6 7"/>',
    "check": '<path d="M4 12l5 5L20 6"/>',
    "compare": '<path d="M3 18h18M3 13c4 0 4-6 8-6s4 6 8 6M3 8c4 0 4-4 8-4"/>',
    "fit": '<circle cx="12" cy="12" r="8"/><path d="M12 4v8l5 3"/>',
    "maps": '<rect x="3" y="3" width="8" height="8"/><rect x="13" y="3" width="8" height="8"/>'
            '<rect x="3" y="13" width="8" height="8"/><rect x="13" y="13" width="8" height="8"/>',
    "report": '<path d="M6 3h9l5 5v13H6z M14 3v6h6 M9 13h7M9 17h7"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1'
                'M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "folder": '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
    "refresh": '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>',
}


def svg(name: str, colour: str = "#C9D6F0", size: int = 22) -> str:
    body = _PATHS[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
            f'stroke="{colour}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


@lru_cache(maxsize=256)
def icon(name: str, colour: str = "#C9D6F0", size: int = 22) -> QIcon:
    renderer = QSvgRenderer(QByteArray(svg(name, colour, size).encode()))
    ic = QIcon()
    for s in (size, size * 2):
        img = QImage(QSize(s, s), QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p)
        p.end()
        ic.addPixmap(QPixmap.fromImage(img))
    return ic


def two_state_icon(name: str, normal: str, checked: str, size: int = 22) -> QIcon:
    """Rail icon: `normal` colour when unchecked, `checked` colour when the perspective is selected."""
    ic = QIcon()
    for colour, mode_state in ((normal, (QIcon.Normal, QIcon.Off)), (checked, (QIcon.Normal, QIcon.On)),
                               (checked, (QIcon.Active, QIcon.On)), (checked, (QIcon.Selected, QIcon.On))):
        renderer = QSvgRenderer(QByteArray(svg(name, colour, size).encode()))
        for s in (size, size * 2):
            img = QImage(QSize(s, s), QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)
            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing)
            renderer.render(p)
            p.end()
            ic.addPixmap(QPixmap.fromImage(img), *mode_state)
    return ic


def app_icon() -> QIcon:
    ic = QIcon()
    for s in (16, 32, 64, 128, 256, 512):
        p = RESOURCES / f"aurora_mark_{s}.png"
        if p.exists():
            ic.addFile(str(p), QSize(s, s))
    return ic


def mark_pixmap(size: int) -> QPixmap:
    p = RESOURCES / f"aurora_mark_{512 if size > 256 else 256 if size > 128 else 128 if size > 64 else 64}.png"
    pm = QPixmap(str(p)) if p.exists() else QPixmap()
    return pm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation) if not pm.isNull() else pm
