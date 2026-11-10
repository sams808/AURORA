"""The hand-drawn holder sketch is optional (owner request 4, BL-27), and so are the other panels with nothing to show.

- core (aurora.core.sketches): the lookup order .aurora/sketches/, sketches/, zones_html/sketches/, case-insensitive
  holder names, png / jpg / jpeg; Load copies into .aurora/sketches/ only, Remove deletes only that copy; a protected
  beamtime refuses both;
- Survey: no Sketch card for a holder without a sketch (1200 x 760 and 1500 x 940, Easy and Advanced), the map keeps
  the space; Load a sketch… (the button next to the holder and the map's right-click menu) shows it, Remove the
  sketch hides it again; a sketch kept by the user in sketches/ shows and cannot be removed; no text names a folder;
- the sample card collapses to one line without a sheet or without a row for the holder; the Grid of zones dialog
  shows no layout row without a plate layout;
- the manuals say the sketch is optional.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from aurora.core import sketches, testdata

pytest.importorskip("pyqtgraph")

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "aurora" / "resources" / "docs"


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=5):
    for _ in range(n):
        qapp.processEvents()


def image(path: Path, w: int = 60, h: int = 40, colour: str = "#3366aa") -> Path:
    from PySide6.QtGui import QColor, QImage
    path.parent.mkdir(parents=True, exist_ok=True)
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor(colour))
    assert img.save(str(path))
    return path


# ----------------------------------------------------------------------------- core
def test_lookup_order_and_names(qapp, tmp_path):
    root = tmp_path / "bt"
    assert sketches.find_sketch(root, "holderA") is None and sketches.find_sketch(root, None) is None
    old = image(root / "zones_html" / "sketches" / "holderA.png")
    assert sketches.find_sketch(root, "holderA") == old
    mine = image(root / "sketches" / "HOLDERA.JPG")                      # case-insensitive name and suffix
    assert sketches.find_sketch(root, "holderA") == mine
    own = image(root / ".aurora" / "sketches" / "holdera.jpeg")
    assert sketches.find_sketch(root, "holderA") == own and sketches.own_sketch(root, "holderA") == own
    assert sketches.is_own(root, own) and not sketches.is_own(root, mine)
    (root / "sketches" / "holderB.gif").write_bytes(b"GIF89a")              # not an offered type
    assert sketches.find_sketch(root, "holderB") is None


def test_add_and_remove_touch_only_aurora_s_copy(qapp, tmp_path):
    root = tmp_path / "bt"
    users = image(root / "sketches" / "holderA.png")
    src = image(tmp_path / "photo.JPG")
    out = sketches.add_sketch(root, "holderA", src)
    assert out == root / ".aurora" / "sketches" / "holderA.jpg" and out.read_bytes() == src.read_bytes()
    assert src.is_file()                                                   # copied, never moved
    png = sketches.add_sketch(root, "holderA", image(tmp_path / "drawing.png", colour="#aa3333"))
    assert png.name == "holderA.png" and not out.exists()                  # one copy per holder
    assert sketches.remove_sketch(root, "holderA") == [png]
    assert users.is_file() and sketches.find_sketch(root, "holderA") == users   # the user's own file stays
    assert sketches.remove_sketch(root, "holderA") == []
    with pytest.raises(ValueError):
        sketches.add_sketch(root, "holderA", tmp_path / "notes.txt")


def test_a_protected_beamtime_refuses(qapp, tmp_path, monkeypatch):
    from aurora.core.io.safety import ENV, ProtectedPathError
    root = tmp_path / "bt"
    root.mkdir()
    monkeypatch.setenv(ENV, str(root))
    with pytest.raises(ProtectedPathError):
        sketches.add_sketch(root, "holderA", image(tmp_path / "p.png"))
    assert not (root / ".aurora").exists()


# ----------------------------------------------------------------------------- Survey
def _window(qapp, root: Path, size, advanced: bool):
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.core.model import Project
    mode.set_advanced(advanced)
    win = MainWindow()
    win.resize(*size)
    win.show()
    win.home.set_project(Project.discover(root))
    win.show_perspective("survey")
    pump(qapp, 10)
    return win


def _close(win):
    from aurora.app import mode
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()
    mode.set_advanced(False)


def _visible_texts(widget) -> list[str]:
    from PySide6.QtWidgets import QAbstractButton, QLabel
    out = []
    for w in widget.findChildren(QLabel) + widget.findChildren(QAbstractButton):
        if w.isVisible():
            out.append(w.text() + " " + w.toolTip())
    return out


@pytest.mark.parametrize("size", [(1200, 760), (1500, 940)])
@pytest.mark.parametrize("advanced", [False, True])
def test_no_sketch_no_card(qapp, fake_project, size, advanced):
    win = _window(qapp, fake_project, size, advanced)
    try:
        sv = win.survey
        assert not sv.sketch_card.isVisible() and sv.sketch_path is None
        assert sv.sketch_btn.isVisible() and sv.sketch_btn.isEnabled() and sv.sketch_btn.text() == "Load a sketch…"
        assert not [t for t in _visible_texts(sv) if "sketches" in t or "zones_html" in t]     # no folder named
        assert sv.zones_card.isVisible() and sv.map.height() > 200
    finally:
        _close(win)


def test_load_and_remove_a_sketch(qapp, fake_project, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog
    win = _window(qapp, fake_project, (1200, 760), False)
    try:
        sv = win.survey
        photo = image(tmp_path / "holder photo.png", 300, 200)
        monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(photo), "")))
        zones_h = sv.zones_card.height()
        QTest.mouseClick(sv.sketch_btn, Qt.LeftButton)                     # Load a sketch…, next to the holder
        pump(qapp, 10)
        copy = fake_project / ".aurora" / "sketches" / f"{testdata.HOLDER}.png"
        assert copy.is_file() and sv.sketch_path == copy and sv.sketch_card.isVisible()
        assert not sv.sketch.pixmap().isNull() and sv.remove_sketch_btn.isEnabled()
        assert ".aurora/sketches" in sv.sketch.toolTip() and "300 × 200" in sv.sketch.toolTip()
        assert sv.zones_card.height() < zones_h                            # the card takes its share of the column
        QTest.mouseClick(sv.remove_sketch_btn, Qt.LeftButton)              # Remove the sketch
        pump(qapp, 10)
        assert not copy.exists() and photo.is_file() and not sv.sketch_card.isVisible()
        # the map's right-click menu offers the same two entries (a real right-click on the map)
        vp = sv.map.viewport()
        QTest.mouseClick(vp, Qt.RightButton, Qt.NoModifier, vp.rect().center())
        pump(qapp, 5)
        menu = sv.last_menu
        assert menu is not None
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        assert texts == ["Fit view", "Load a sketch…", "Remove the sketch"]
        assert menu.actions()[2].isEnabled() and not menu.actions()[3].isEnabled()
        menu.close()
        again = sv.map_menu(0.0, 5.0)
        next(a for a in again.actions() if a.text() == "Load a sketch…").trigger()   # the same dialog, the same copy
        again.deleteLater()
        pump(qapp, 10)
        assert copy.is_file() and sv.sketch_card.isVisible()
    finally:
        _close(win)


def test_a_sketch_the_user_keeps_shows_and_stays(qapp, fake_project):
    """BL-27: sketches/holderA.jpg (the user's own folder) is shown; Remove the sketch is offered only for AURORA's copy."""
    image(fake_project / "sketches" / f"{testdata.HOLDER}.jpg")
    win = _window(qapp, fake_project, (1200, 760), True)
    try:
        sv = win.survey
        assert sv.sketch_card.isVisible() and sv.sketch_path.name == f"{testdata.HOLDER}.jpg"
        assert not sv.remove_sketch_btn.isEnabled() and "removes only the copy it made" in sv.remove_sketch_btn.toolTip()
        assert sv.remove_sketch() == [] and (fake_project / "sketches" / f"{testdata.HOLDER}.jpg").is_file()
    finally:
        _close(win)


def test_a_protected_beamtime_offers_no_load(qapp, fake_project, monkeypatch):
    from aurora.core.io.safety import ENV
    monkeypatch.setenv(ENV, str(fake_project))
    win = _window(qapp, fake_project, (1200, 760), False)
    try:
        sv = win.survey
        assert not sv.sketch_btn.isEnabled() and "read-only" in sv.sketch_btn.toolTip()
        assert sv.map_menu(0.0, 5.0).actions()[2].isEnabled() is False
    finally:
        _close(win)


def test_the_sample_card_collapses_to_one_line(qapp, fake_project):
    from aurora.app.survey import NO_SHEET_TEXT
    win = _window(qapp, fake_project, (1200, 760), False)                  # the test variant has no sample sheet
    try:
        sv = win.survey
        assert sv.sheet_note.isVisible() and NO_SHEET_TEXT in sv.sheet_note.text() and not sv.sheet_table.isVisible()
        assert "How to add one" in sv.sheet_note.text() and ".csv" not in sv.sheet_note.text()
        from aurora.app.help import section_of
        from aurora.app.survey import SHEET_ANCHOR
        assert section_of(SHEET_ANCHOR) == "2.4"                           # the link opens the sample-sheet section
        assert sv.samples_card.height() < 120
    finally:
        _close(win)
    (fake_project / "samples.csv").write_text("Sample ID,Holder,Group\nq1,holderZ,G\nq2,holderY,G\n", encoding="utf-8")
    win = _window(qapp, fake_project, (1200, 760), False)
    try:
        sv = win.survey
        assert sv.sheet_note.text() == f"The sample sheet lists no sample on holder {testdata.HOLDER} " \
                                       "(its holders: holderY, holderZ)."
    finally:
        _close(win)
    (fake_project / "samples.csv").write_text(f"Sample ID,Holder,Group\nq1,{testdata.HOLDER},G\n", encoding="utf-8")
    win = _window(qapp, fake_project, (1200, 760), False)
    try:
        sv = win.survey
        assert not sv.sheet_note.isVisible() and sv.sheet_table.isVisible() and sv.sheet_table.rowCount() == 1
    finally:
        _close(win)


def test_the_grid_dialog_hides_an_empty_layout_row(qapp, fake_project):
    win = _window(qapp, fake_project, (1500, 940), True)
    try:
        sv = win.survey
        sd = win.session.stack(win.session.stack_id(win.session.project.find(testdata.STACK)))
        dlg = sv.open_grid_dialog(sd.bbox)
        pump(qapp)
        assert not dlg.layout_combo.isVisible() and not dlg.row_labels["layout group"].isVisible()
        assert not dlg.first_row.isVisible() and dlg.rows.isVisible()           # no sheet rows either
        dlg.reject()
    finally:
        _close(win)


def test_the_manuals_say_the_sketch_is_optional():
    manual = (DOCS / "MANUAL.md").read_text(encoding="utf-8")
    survey = manual.split("## 3. Survey", 1)[1].split("\n## ", 1)[0]
    assert "optional" in survey.lower() and "**Load a sketch…**" in survey and "**Remove the sketch**" in survey
    assert ".aurora/sketches/" in survey and "zones_html/sketches/" in survey
    guide = (DOCS / "USER_GUIDE.md").read_text(encoding="utf-8")
    sec = guide.split("## 2. Survey", 1)[1].split("\n## ", 1)[0]
    assert "optional" in sec.lower() and "sketch" in sec.lower()
