import sys
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QColor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtCore import QSize
app = QGuiApplication(sys.argv)
jobs = [("aurora_mark.svg", [16, 32, 48, 64, 128, 256, 512, 1024]), ("aurora_mark_mono.svg", [16, 32, 64, 256]), ("aurora_wordmark.svg", [1400])]
for svg, sizes in jobs:
    r = QSvgRenderer(svg)
    assert r.isValid(), svg
    vb = r.viewBoxF()
    for s in sizes:
        h = int(round(s * vb.height() / vb.width()))
        img = QImage(QSize(s, h), QImage.Format_ARGB32_Premultiplied)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.SmoothPixmapTransform)
        r.render(p); p.end()
        out = svg.replace(".svg", f"_{s}.png"); img.save(out); print("wrote", out, s, h)
# Windows .ico from the 16..256 mark PNGs via Pillow
from PIL import Image
base = Image.open("aurora_mark_256.png")
base.save("aurora.ico", sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("wrote aurora.ico")
