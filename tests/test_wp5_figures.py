"""WP5 I32: publication figures of Compare and Fit at a fixed size (John Bussey's 'Plot size'), drawn again with
matplotlib in the export colours, never grabbed from the screen."""
import re
import struct
import zlib

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from aurora.app import mode  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.fixture
def demo(qapp, tmp_path):
    from demo_window import build_demo_window
    win = build_demo_window(qapp, tmp_path)
    yield win
    win.session._save_timer.stop()
    win.close()


@pytest.fixture
def no_pyqtgraph_export(monkeypatch):
    """Any pyqtgraph exporter fails the test: the exports must draw the figure again."""
    from pyqtgraph import exporters
    called = []

    def boom(self, *a, **k):
        called.append(type(self).__name__)
        raise AssertionError("a pyqtgraph exporter was called")
    for name in ("ImageExporter", "SVGExporter"):
        monkeypatch.setattr(getattr(exporters, name), "export", boom)
    return called


def png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def pdf_text(path) -> str:
    """The text drawn on the pages of a PDF written by matplotlib (Type 42 fonts: two-byte codes)."""
    raw = path.read_bytes()
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        body = m.group(1)
        try:
            body = zlib.decompress(body)
        except zlib.error:
            pass
        for arr in re.findall(rb"\[(.*?)\]\s*TJ", body, re.S):
            parts = re.findall(rb"\(((?:\\.|[^\\)])*)\)", arr, re.S)
            b = b"".join(re.sub(rb"\\([()\\])", rb"\1", p) for p in parts)
            b = re.sub(rb"\\([0-7]{3})", lambda mm: bytes([int(mm.group(1), 8)]), b)
            try:
                out.append(b.decode("utf-16-be") if len(b) % 2 == 0 and b[:1] == b"\x00" else b.decode("latin-1"))
            except UnicodeDecodeError:
                out.append(b.decode("latin-1"))
    return " ".join(out)


def test_compare_export_at_a_fixed_size(qapp, demo, tmp_path, no_pyqtgraph_export):
    from aurora.core.compare import wide_columns
    from aurora.core.figures import FigureOptions
    c = demo.compare
    out = c.export(tmp_path / "comparison.csv", FigureOptions(width_in=3.5, height_in=4.5, font_pt=8, dpi=600))
    names = [p.name for p in out]
    assert names[:4] == ["comparison.csv", "comparison.png", "comparison.svg", "comparison.pdf"]
    assert "comparison_config.json" in names
    assert png_size(tmp_path / "comparison.png") == (2100, 2700)
    raw = (tmp_path / "comparison.pdf").read_bytes()
    assert re.findall(rb"MediaBox\s*\[\s*0 0 252 324\s*\]", raw) and len(re.findall(rb"/Type\s*/Page\b", raw)) == 1
    svg = (tmp_path / "comparison.svg").read_text(encoding="utf-8")
    assert svg.lstrip().startswith(("<?xml", "<svg")) and 'width="252pt"' in svg and 'height="324pt"' in svg
    traces = c.plot_traces()
    text = pdf_text(tmp_path / "comparison.pdf")
    for t in traces:
        assert t.text in text, (t.text, text[:300])
        assert t.text in svg
    head = (tmp_path / "comparison.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert head == wide_columns([(t.r, t.E, t.nr.y) for t in traces])
    assert not no_pyqtgraph_export
    # the export colours: white background whatever the screen scheme
    from aurora.app.theme import export_tokens
    assert export_tokens()["plot_bg"].lower() in svg.lower() or "#ffffff" in svg.lower()


def test_dark_screen_exports_light_figures(qapp, demo, tmp_path, no_pyqtgraph_export):
    from aurora.app import theme
    from aurora.core.figures import FigureOptions
    theme.apply_theme(qapp, appearance="dark")
    try:
        demo.compare.export(tmp_path / "dark.csv", FigureOptions(dpi=100, formats=("png",)))
        from PySide6.QtGui import QImage
        img = QImage(str(tmp_path / "dark.png"))
        assert img.width() == 350 and img.height() == 450
        assert img.pixelColor(2, 2).name().upper() == theme.export_tokens()["plot_bg"].upper()
    finally:
        theme.apply_theme(qapp, appearance="light")


def test_fit_exports_use_the_same_path(qapp, demo, tmp_path, no_pyqtgraph_export):
    from aurora.app.figure_export import store_options
    from aurora.core.figures import FigureOptions
    store_options(FigureOptions(width_in=7.0, height_in=4.5, font_pt=9, dpi=100, formats=("png", "pdf")))
    fit = demo.fit
    out = fit.export_lcf(tmp_path / "lcf.csv")
    assert [p.name for p in out] == ["lcf.csv", "lcf.png", "lcf.pdf"] and png_size(tmp_path / "lcf.png") == (700, 450)
    assert "linear combination" in pdf_text(tmp_path / "lcf.pdf")
    files = fit.export_mcr(tmp_path / "mcr", fit.mcr_result, "MCR")
    assert {p.name for p in files} >= {"MCR_component_spectra.csv", "MCR_component_makeup.csv",
                                       "MCR_component_spectra.png", "MCR_component_spectra.pdf"}
    pk = fit.export_peaks(tmp_path / "peaks.csv")
    assert [p.name for p in pk] == ["peaks.csv", "peaks.png", "peaks.pdf"]
    assert not no_pyqtgraph_export


def test_figure_dialog(qapp, monkeypatch):
    from test_a11y import check_root
    from aurora.app.figure_export import FigureDialog, current_options, summary
    d = FigureDialog()
    d.show()
    qapp.processEvents()
    assert d.width.value() == 3.5 and d.height.value() == 4.5 and d.dpi.value() == 600
    assert all(c.isChecked() for c in d.fmt.values()) and "2100 × 2700 px" in d.pixels.text()
    assert not check_root("figure dialog", d)
    d.presets[1].click()
    d.font.setValue(10)
    d.fmt["svg"].setChecked(False)
    d.accept()
    o = current_options()
    assert o.width_in == 7.0 and o.font_pt == 10 and o.formats == ("png", "pdf")
    assert summary(o).startswith("7 × 4.5 in, 10 pt, PNG 600 dpi (4200 × 2700 px)")
    d.deleteLater()


def test_figure_size_button_in_easy(qapp, demo):
    from demo_window import pump
    assert not mode.is_advanced()
    demo.show_perspective("compare")
    pump(qapp)
    assert demo.compare.figure_btn.isVisible() and demo.compare.export_btn.isVisible()
    demo.show_perspective("fit")
    pump(qapp)
    assert demo.fit.figure_btn.isVisible()
