"""WP5 I28: Easy Compare is John Bussey's default view (Show, Fit, Label, Signal, Shift eV, Colour) with one
normalisation path: rows come from saved John-format files, so every Easy row is raw / I0 plus larch."""
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QFileDialog,  # noqa: E402
                               QLineEdit, QSlider, QTabBar, QWidget)

from PySide6.QtCore import Qt  # noqa: E402

from aurora.app import mode  # noqa: E402

INTERACTIVE = (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox, QSlider)


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


def _inside(w, root, types) -> bool:
    p = w.parentWidget()
    while p is not None and p is not root:
        if isinstance(p, types):
            return True
        p = p.parentWidget()
    return False


def visible_interactive(page: QWidget) -> list[QWidget]:
    out = []
    for w in page.findChildren(QWidget):
        if not w.isVisible() or _inside(w, page, (QAbstractItemView, QComboBox, QAbstractSpinBox, QTabBar)):
            continue
        if w.property("aurora_help_button"):
            continue
        if isinstance(w, (QAbstractItemView, *INTERACTIVE)):
            out.append(w)
    return out


def write_zone_john_files(win, folder: Path) -> list[Path]:
    """Test helper standing in for Check's 'Save ticked → Compare' (I27): the demo zone's channel sums written in
    John Bussey's _full_reduced.csv layout, one folder per sample."""
    from aurora.core.zones import members, zone_sums
    ses = win.session
    z = ses.zone()
    out = []
    for sid in z.settings:
        sd = ses.stack_loaded(sid)
        if sd is None:
            continue
        s = zone_sums(sd, members(z, sd.x, sd.y, sd.pitch))
        det = s.det[s.main]                                         # (nE, 4) counts of the main line per SDD
        name = z.label.replace(" ", "_")
        d = folder / name
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{name}_full_reduced.csv"
        rows = [f"{e},{det[i, 0]},{det[i, 1]},{det[i, 2]},{det[i, 3]},{s.i0[i]},{s.tey[i]},{det[i].sum()},"
                f"{det[i].mean()},False" for i, e in enumerate(s.E)]
        p.write_text(f"# sample_label: {z.label}\n# test helper for 'Save ticked -> Compare'\n"
                     "energy,sdd1,sdd2,sdd3,sdd4,I0,TEY,sdd_total,sdd_mean,bad_point\n" + "\n".join(rows) + "\n",
                     encoding="utf-8")
        out.append(p)
    return out


def test_easy_compare_columns_and_limit(qapp, demo):
    from demo_window import pump
    win = demo
    assert not mode.is_advanced()
    win.show_perspective("compare")
    pump(qapp, 3)
    t = win.compare.table
    shown = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount()) if not t.isColumnHidden(c)]
    assert shown == ["Show", "Fit", "Label", "Signal", "Shift eV", "Colour"]
    ws = visible_interactive(win.compare)
    assert 0 < len(ws) <= 12, [type(w).__name__ + ":" + getattr(w, "text", lambda: "")() for w in ws]
    texts = {w.text() for w in ws if isinstance(w, QAbstractButton)}
    assert {"Add saved spectra…", "Remove", "▲", "▼", "Show all", "Hide all", "Export…"} <= texts
    # the diagnostics card stays: it is how a user checks the larch E0 and the lines
    assert win.compare.diag.isVisible()
    # Send the zone's traces to Compare is Advanced
    assert win.reduce.send_btn.property("aurora_advanced") or not win.reduce.send_btn.isVisibleTo(win.reduce)


def test_saved_zone_spectra_are_raw_over_i0_plus_larch(qapp, demo, tmp_path):
    win = demo
    lib = win.check.library
    assert lib is win.library
    lib.remove(list(range(len(lib.requests))))
    files = write_zone_john_files(win, tmp_path / "saved")
    assert files
    added = lib.add_folder(tmp_path / "saved")                    # what 'Save ticked → Compare' calls
    sigs = sorted(r.signal for r in added)
    assert sigs == ["TEY", "sdd-scaled"]
    for r in added:
        assert r.norm == "larch" and r.pre == (-20.0, -5.0) and r.post == (15.0, 35.0) and r.flatten
        s = lib.spectrum_of(r)
        assert Path(s.source).is_file() and s.meta.get("layout") == "john"
    # the values come from mode.EASY_DEFAULTS
    d = mode.EASY_DEFAULTS["compare"]
    assert added[0].norm == d["norm"] and tuple(d["pre"]) == added[0].pre and win.compare.spacing.value() == d["spacing"]
    assert win.compare.labels_on.isChecked() is d["labels"]


def test_easy_add_folder_takes_john_files_only(qapp, demo, tmp_path, monkeypatch):
    win = demo
    lib, cmp_ = win.library, win.compare
    lib.remove(list(range(len(lib.requests))))
    folder = tmp_path / "mixed"
    folder.mkdir()
    E = np.linspace(680, 720, 41)
    from aurora.core.testdata import write_john_reduced
    write_john_reduced(folder / "A_full_reduced.csv", "A", E, 1 / (1 + np.exp(-(E - 696))))
    (folder / "B_xanes.csv").write_text("energy_eV,PFY_F_Ka_over_I0,TEY_over_I0\n" + "\n".join(
        f"{e},{v},{v}" for e, v in zip(E, 1 / (1 + np.exp(-(E - 696))))), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(folder)))
    cmp_._add_folder()
    assert {r.label for r in lib.requests} == {"A"} and all(r.norm == "larch" for r in lib.requests)
    assert "B_xanes.csv" in cmp_.message.text() and "Easy mode" in cmp_.message.text()
    # Advanced reads every layout
    lib.remove(list(range(len(lib.requests))))
    mode.set_advanced(True)
    cmp_._add_folder()
    assert {r.label for r in lib.requests} == {"A", "B"}
    mode.set_advanced(False)


def test_advanced_shows_every_column_with_the_same_rows(qapp, demo):
    from demo_window import pump
    win = demo
    t = win.compare.table
    def cell(r, c):                     # the Signal cell shows words by mode; its data is the file's signal code
        it = t.item(r, c)
        if it is None:
            return ("", None)
        return (it.data(Qt.UserRole) if it.data(Qt.UserRole) else it.text(), it.checkState())
    before = [[cell(r, c) for c in range(t.columnCount())] for r in range(t.rowCount())]
    reqs = [r.to_json() for r in win.library.requests]
    mode.set_advanced(True)
    pump(qapp, 3)
    assert not any(t.isColumnHidden(c) for c in range(t.columnCount()))
    after = [[cell(r, c) for c in range(t.columnCount())] for r in range(t.rowCount())]
    assert after == before and [r.to_json() for r in win.library.requests] == reqs
    mode.set_advanced(False)
    pump(qapp)
    assert [r.to_json() for r in win.library.requests] == reqs
