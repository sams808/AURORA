"""One table of help texts and words: on the demo in Advanced mode, every perspective and every dialog gives (almost)
every control a tooltip, every input an accessible name or a buddy label, every table header a tooltip, units in
the spin boxes, and one vocabulary (normalisation names, SDD1..SDD4, no 'E#')."""
import re

import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractSpinBox, QComboBox, QLabel,  # noqa: E402
                               QLineEdit, QTabBar, QWidget)

from aurora.app import mode  # noqa: E402

UNIT = re.compile(r"\([^)]+\)\s*$")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def _inside(w, root, types) -> bool:
    p = w.parentWidget()
    while p is not None and p is not root:
        if isinstance(p, types):
            return True
        p = p.parentWidget()
    return False


def controls(root: QWidget):
    """Visible buttons, combo boxes, line edits and spin boxes (not the internal parts of views, combos, spins)."""
    out = []
    for w in root.findChildren(QWidget):
        if not w.isVisible() or _inside(w, root, (QAbstractItemView, QComboBox, QAbstractSpinBox, QTabBar)):
            continue
        if isinstance(w, (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox)):
            out.append(w)
    return out


def buddies(root: QWidget) -> dict[int, QLabel]:
    return {id(lab.buddy()): lab for lab in root.findChildren(QLabel) if lab.buddy() is not None}


def check_root(name: str, root: QWidget) -> list[str]:
    problems = []
    ws = controls(root)
    if not ws:
        return problems
    tipped = [w for w in ws if w.toolTip().strip()]
    if len(tipped) < 0.95 * len(ws):
        missing = [f"{type(w).__name__}:{getattr(w, 'text', lambda: '')()}" for w in ws if not w.toolTip().strip()]
        problems.append(f"{name}: {len(tipped)} of {len(ws)} with a tooltip; missing {missing}")
    bud = buddies(root)
    for w in ws:
        if isinstance(w, (QComboBox, QLineEdit, QAbstractSpinBox)) and not w.accessibleName() and id(w) not in bud:
            problems.append(f"{name}: {type(w).__name__} {w.toolTip()[:40]!r} has no accessible name or buddy")
        if isinstance(w, QAbstractSpinBox):
            lab = bud.get(id(w))
            if not w.suffix().strip() and not (lab is not None and UNIT.search(lab.text())):
                problems.append(f"{name}: spin box {w.accessibleName()!r} has no unit")
        texts = [getattr(w, "text", lambda: "")(), getattr(w, "prefix", lambda: "")()]
        if any("E#" in t for t in texts):
            problems.append(f"{name}: 'E#' in {texts}")
    return problems


def test_perspectives_on_the_demo(qapp, tmp_path):
    from demo_window import build_demo_window, pump
    from aurora.app.main import PERSPECTIVES
    mode.set_advanced(True)
    win = build_demo_window(qapp, tmp_path)
    problems = []
    for key, _t in PERSPECTIVES:
        page = win.pages[key]
        tabs = getattr(page, "tabs", None) if key in ("fit", "maps") else None
        for tab in ([None] if tabs is None else range(tabs.count())):
            win.show_perspective(key)
            if tab is not None:
                tabs.setCurrentIndex(tab)
            pump(qapp, 3)
            problems += check_root(f"{key}[{tab}]", page)
    # the top bar
    problems += check_root("top bar", win.crumb.parentWidget())
    # table headers of Compare, Check and Fit
    from PySide6.QtWidgets import QTableWidget
    for view in (win.compare.table, win.check.table, win.fit.lcf_table, win.fit.prop_table, win.fit.itfa_table,
                 win.fit.pk_table):
        assert isinstance(view, QTableWidget)
        for c in range(view.columnCount()):
            it = view.horizontalHeaderItem(c)
            if it is None or not it.toolTip():
                problems.append(f"header {c} of {view.accessibleName()!r} has no tooltip")
    # one vocabulary
    from aurora.app.vocab import DET_LABELS, NORM_LABELS
    assert [win.survey.sdd.itemText(i) for i in range(win.survey.sdd.count())] == ["sum", *DET_LABELS]
    assert [win.maps.det.itemText(i) for i in range(win.maps.det.count())] == ["sum", *DET_LABELS]
    card = win.reduce._cards[0]
    assert [card.fn.itemText(i) for i in range(card.fn.count())] == list(NORM_LABELS.values())
    assert win.reduce.map_energy.text().endswith(" eV") and "E#" not in win.reduce.map_energy.text()
    mode.set_advanced(False)
    win.session._save_timer.stop()
    win.close()
    assert not problems, "\n".join(problems)


def _dialogs(win, tmp_path):
    from aurora.app.about import AboutDialog
    from aurora.app.diagnostics import LicencesDialog, ReportProblemDialog, SelftestDialog, ShortcutsDialog
    from aurora.app.dialogs import ReferencesDialog, StitchDialog
    from aurora.app.settings_dialog import SettingsDialog
    from aurora.app.updates import UpdateDialog
    sd = next(iter(win.session._stacks.values()))
    return [("settings", SettingsDialog(win)), ("references", ReferencesDialog(str(tmp_path), "F", win)),
            ("stitch", StitchDialog({"a": sd, "b": sd}, win)), ("about", AboutDialog(win)),
            ("shortcuts", ShortcutsDialog(win)), ("report a problem", ReportProblemDialog(win)),
            ("licences", LicencesDialog(win)),
            ("self-test", SelftestDialog({"version": "0", "checks": [{"name": "x", "ok": True}]}, win)),
            ("update", UpdateDialog({"version": "9.9.9", "notes": "n", "page": "https://x"}, win))]


def test_dialogs(qapp, tmp_path):
    from demo_window import build_demo_window, pump
    mode.set_advanced(True)
    win = build_demo_window(qapp, tmp_path)
    problems = []
    for name, d in _dialogs(win, tmp_path):
        d.show()
        pump(qapp, 3)
        problems += check_root(name, d)
        d.close()
        d.deleteLater()
    mode.set_advanced(False)
    win.session._save_timer.stop()
    win.close()
    assert not problems, "\n".join(problems)


def test_settings_normalisation_words_and_codes(qapp):
    from aurora.app.settings_dialog import SettingsDialog, get
    from aurora.app.vocab import NORM_LABELS
    d = SettingsDialog()
    for box in (d.fn, d.tn):
        assert [box.itemText(i) for i in range(box.count())] == list(NORM_LABELS.values())
        assert [box.itemData(i) for i in range(box.count())] == list(NORM_LABELS)
        assert box.currentData() == "i0jump"
    d.fn.setCurrentIndex(d.fn.findData("area"))
    d.accept()
    assert get("fluo_norm") == "area"
    d.deleteLater()


def test_help_table_is_complete():
    from aurora.app.help_text import HELP
    for key, (tip, name, anchor) in HELP.items():
        assert tip.strip() and name.strip() and anchor, key
        assert not tip.startswith(" ") and "  " not in name, key
