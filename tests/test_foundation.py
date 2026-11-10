"""Theme (light / dark readability), Easy / Advanced mode, in-app manuals, credits, logging."""
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402

from aurora.app import theme  # noqa: E402

TEXT_PAIRS = [("ink", "paper"), ("ink", "card"), ("muted", "paper"), ("muted", "card"), ("muted", "muted_bg"),
              ("ok_fg", "ok_bg"), ("warn_fg", "warn_bg"), ("bad_fg", "bad_bg"), ("plot_fg", "plot_bg"), ("map_fg", "map_bg"),
              ("rail_text", "rail"), ("link", "card"), ("select_fg", "select"), ("tooltip_fg", "tooltip_bg"),
              ("ink", "alt"), ("muted", "header"), ("ink", "header"), ("on_action", "action")]


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.mark.parametrize("name", ["LIGHT", "DARK"])
def test_text_contrast(name):
    t = getattr(theme, name)
    bad = [(fg, bg, round(theme.contrast(t[fg], t[bg]), 2)) for fg, bg in TEXT_PAIRS if theme.contrast(t[fg], t[bg]) < 4.5]
    assert not bad, f"{name}: text below 4.5:1 {bad}"
    assert theme.contrast(t["edge"], t["card"]) >= 3.0, "input borders below 3:1"
    assert theme.contrast(t["disabled"], t["card"]) >= 3.0, "disabled text below 3:1"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_plot_colours_contrast(scheme):
    t = theme.DARK if scheme == "dark" else theme.LIGHT
    bg = t["plot_bg"]
    assert all(theme.contrast(c, bg) >= 3.0 for c in theme.CURVES), [(c, round(theme.contrast(c, bg), 2)) for c in theme.CURVES]
    for role, (src, _style) in theme.ROLES.items():          # every semantic plot colour reads on its background
        c = theme.CURVES[int(src[6:])] if src.startswith("curve:") else t[src]
        on = t["map_bg"] if role == "roi" else bg
        assert theme.contrast(c, on) >= 3.0, (role, c, round(theme.contrast(c, on), 2))


def test_readable_adjusts_both_ways():
    assert theme.contrast(theme.readable("#0072B2", "#172033"), "#172033") >= 3.0      # blue lightened on dark
    assert theme.contrast(theme.readable("#F0E442", "#FFFFFF"), "#FFFFFF") >= 3.0      # yellow darkened on white
    assert theme.readable("#0072B2", "#FFFFFF") == "#0072B2"                          # already fine: unchanged


def test_apply_theme_switches_palette_and_plots(qapp, monkeypatch):
    import pyqtgraph as pg
    w = pg.PlotWidget()
    w.setLabel("bottom", "energy (eV)")
    w.setTitle("a title")
    theme.style_plot(w)
    for sch, t in (("dark", theme.DARK), ("light", theme.LIGHT)):
        assert theme.apply_theme(qapp, appearance=sch) == sch
        assert qapp.palette().color(QPalette.Active, QPalette.Text).name().upper() == t["ink"].upper()
        assert qapp.palette().color(QPalette.Active, QPalette.Base).name().upper() == t["card"].upper()
        assert theme.TOKENS["plot_bg"] == t["plot_bg"] and theme.PAL[0] == theme.CURVES[0]          # one palette
        assert theme.LABELS[0] == (theme.LABELS_DARK if sch == "dark" else theme.LABELS_LIGHT)[0]
        ax = w.getPlotItem().getAxis("bottom")
        assert ax.labelStyle["color"] == t["plot_fg"] and ax.textPen().color().name().upper() == t["plot_fg"].upper()
        assert w.getPlotItem().titleLabel.opts["color"] == t["plot_fg"]
        assert theme.pc("ink") == t["plot_ink"] and theme.pc("fit") == theme.CURVES[1]
    w.deleteLater()


def test_main_window_easy_advanced(qapp, fake_project):
    from aurora.app.main import ADVANCED_PERSPECTIVES, MainWindow
    from aurora.app import mode
    from PySide6.QtWidgets import QLabel
    win = MainWindow()
    win.show()
    assert mode.is_advanced() is False                       # first start = Easy mode
    marked = QLabel("advanced thing", win.home)
    mode.mark_advanced(marked)
    qapp.processEvents()
    assert win.mode_btn.text() == "Easy mode" and not win.mode_btn.isChecked()
    for k in ADVANCED_PERSPECTIVES:
        assert not win.rail_buttons[k].isVisible()
    assert marked.isHidden()
    win.mode_action.trigger()                                # View > Advanced mode
    qapp.processEvents()
    assert mode.is_advanced() and win.mode_btn.isChecked() and win.mode_btn.text() == "Advanced mode"
    for k in ADVANCED_PERSPECTIVES:
        assert win.rail_buttons[k].isVisible()
    assert not marked.isHidden()
    win.show_perspective("maps")
    win.mode_btn.click()                                     # back to Easy: Maps closes, Home shows
    qapp.processEvents()
    assert not mode.is_advanced() and win.stack.currentWidget() is win.home
    win.close()


def test_help_menu_manuals(qapp):
    from aurora.app.help import DOCS, docs_dir
    from aurora.app.main import MainWindow
    win = MainWindow()
    titles = [a.text() for a in win.menus["help"].actions()]
    for _name, title, _tip in DOCS:
        assert title in titles
    for name, _t, _tip in DOCS:
        assert (docs_dir() / name).exists()
        win.help_actions[name].trigger()
        qapp.processEvents()
        h = win._help
        assert h.isVisible() and h.current_doc() == name
        text = h.browser.toPlainText()
        assert len(text) > 500 and "AURORA" in text
    # anchors on headings, cross-document link, search
    win.show_help("MANUAL.md", "4-reduce")
    qapp.processEvents()
    doc = win._help.browser.document()
    names = set()
    b = doc.begin()
    while b.isValid():
        it = b.begin()
        while not it.atEnd():
            names.update(it.fragment().charFormat().anchorNames())
            it += 1
        b = b.next()
    assert {"1-the-window", "4-reduce", "13-troubleshooting"} <= names
    win._help.find_edit.setText("Richardson-Lucy")
    assert win._help.find()
    win._help.find_edit.setText("zzz-not-in-the-manual")
    assert not win._help.find() and win._help.find_info.text() == "not found"
    win._help.close()
    win.close()


def test_demo_and_log_actions(qapp, tmp_path, monkeypatch):
    import platformdirs
    from aurora.app.main import MainWindow
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda *a, **k: str(tmp_path / "data"))
    monkeypatch.setenv("AURORA_LOG_DIR", str(tmp_path / "logs"))
    opened = []
    from PySide6.QtGui import QDesktopServices
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    win = MainWindow()
    win.demo_action.trigger()                          # the demo is written in a job, then opened
    assert win.jobs.wait_idle(60000)
    qapp.processEvents()
    assert (tmp_path / "data" / "demo_beamtime" / "raw").is_dir() and win.session.project is not None
    win.open_log_folder()
    assert opened and opened[-1].replace("\\", "/").endswith("logs")
    win.close()


def test_credits_everywhere(qapp, tmp_path):
    from pathlib import Path
    from aurora import __version__
    from aurora.credits import CREDITS, CREDITS_SHORT, FOOTER
    from aurora.app.about import CREDITS as about_credits, AboutDialog
    assert "version 2 of John Bussey's SGM XAS GUI" in CREDITS_SHORT and "James J. Dynes" in CREDITS_SHORT
    assert "Jupyter notebooks" in CREDITS_SHORT and about_credits == CREDITS and CREDITS.startswith(CREDITS_SHORT)
    # About: the credits paragraph, the author, the licence, a Licences… button
    d = AboutDialog()
    text = d.text()
    assert CREDITS_SHORT in text and "Sami Soudani" in text and "MIT licence" in text and "Sam " not in text
    assert d.licences_btn.text() == "Licences…"
    d.deleteLater()
    # README opens 'Lineage and credits' with CREDITS_SHORT verbatim; CREDITS.md quotes CREDITS verbatim
    root = Path(__file__).resolve().parent.parent
    readme = root.joinpath("README.md").read_text(encoding="utf-8")
    section = readme.split("## Lineage and credits", 1)[1].lstrip()
    assert section.startswith(CREDITS_SHORT)
    assert f"> {CREDITS}" in root.joinpath("CREDITS.md").read_text(encoding="utf-8").splitlines()
    # a built report: CREDITS on the cover, FOOTER in every page footer, the PDF metadata
    from aurora.core.model import Project
    from aurora.core.report import ReportBuilder
    from aurora.core.testdata import make_fake_project, make_fake_zones
    proj = Project.discover(make_fake_project(tmp_path / "beamtime"))
    b = ReportBuilder(proj, make_fake_zones(proj), tmp_path / "report", title="Credits test", author="A B", png_dpi=60)
    res = b.build()
    assert CREDITS_SHORT in b.cover_credits and b.cover_credits == CREDITS
    assert len(b.footers) == res.pages >= 3
    assert all(f.startswith(f"AURORA {__version__} · {FOOTER} · ") and " · A B · " in f for f in b.footers)
    raw = res.pdf.read_bytes().decode("latin-1")
    esc = lambda t: t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")      # noqa: E731
    assert f"/Creator (AURORA {__version__})" in raw and f"/Subject ({esc(CREDITS_SHORT)})" in raw
    assert "/Author (A B)" in raw and "Sami Soudani" not in raw
    b2 = ReportBuilder(proj, make_fake_zones(proj), tmp_path / "report2", title="No author", author="", png_dpi=60)
    b2.build()
    assert all(" ·  · " not in f for f in b2.footers) and "/Author" not in b2.out.joinpath(
        next(p.name for p in b2.out.glob("*.pdf"))).read_bytes().decode("latin-1")
    # the self-test JSON and the Report a problem diagnostics carry CREDITS_SHORT
    from aurora.app.diagnostics import diagnostics_text
    assert CREDITS_SHORT in diagnostics_text(None)


def test_logging_setup(tmp_path, monkeypatch):
    import logging
    monkeypatch.setenv("AURORA_LOG_DIR", str(tmp_path / "logs"))
    from aurora.logsetup import setup_logging
    root = logging.getLogger()
    before = list(root.handlers)
    try:
        path = setup_logging()
        logging.getLogger("aurora.test").warning("hello log")
        for h in root.handlers:
            h.flush()
        assert path.exists() and "hello log" in path.read_text(encoding="utf-8")
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                h.close()
