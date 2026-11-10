"""I50: the manuals ship in the Help menu, so every name they give must be the one on screen.

- every bold name (**…**) of USER_GUIDE, MANUAL and TUTORIALS is the text of a button, check box, action or menu, tab,
  card, label, list entry, column or window title of the demo-loaded window (Easy and Advanced, its dialogs and its
  context menus), '&' and '...' normalised; keyboard keys and paragraph lead-ins ('**After midnight.**') are not names;
- every button, check box, tool, menu entry, tab and card title of that window is named in MANUAL.md (renaming a
  control without the manual fails here, with the new name);
- every Settings row of MANUAL §10 is a setting that AURORA reads (aurora.app, outside the dialog that writes it);
- every image link resolves, the stated number of tutorials equals the '## Tutorial' headings, and the screenshot
  script writes into the bundled docs.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("pyqtgraph")

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "aurora" / "resources" / "docs"
APP = ROOT / "aurora" / "app"
MANUALS = ("USER_GUIDE.md", "MANUAL.md", "TUTORIALS.md")

BOLD = re.compile(r"\*\*(.+?)\*\*(:?)", re.S)       # a name may wrap onto the next line
FENCE = re.compile(r"```.*?```", re.S)
# a keyboard key or chord: Ctrl+Shift+A, F1, Esc, Del, Alt+Up, [ ], + / -, Ctrl+/ , Shift (Shift+click)
KEY = re.compile(r"^(?:(?:Ctrl|Shift|Alt|Cmd|Meta)\+)*(?:F\d{1,2}|Esc|Del|Delete|Return|Enter|PgUp|PgDown|Up|Down|Left|"
                 r"Right|Tab|Space|[A-Z0-9]|[\[\]+\-/])$|^(?:Ctrl|Shift|Alt|Cmd)$")

# Bold names that are on screen only in a state the fixture does not reach (a modal dialog or a menu built on a
# right-click and executed at once). Each must still be a string literal of aurora/app, so the list cannot go stale.
ALLOWED = {
    "Copy details": "crash.show_error: the error dialog of a failed task (modal)",
    "Open log folder": "crash.show_error: the error dialog of a failed task (modal)",
    "Project": "home: the title of the summary card while no scan is selected",
    "Show the region in Reduce": "check._queue_menu: right-click menu of a queued spectrum (exec)",
    "Remove from the queue": "check._queue_menu: right-click menu of a queued spectrum (exec)",
    "Show": "report: the button inside each Findings row (an item widget)",
    "?": "widgets.Card: the help button of every card (left out of the controls on purpose)",
}

# MANUAL §10 rows -> the settings key the row stands for
SETTINGS_ROWS = {
    "appearance": "appearance",
    "text size": "text_size",
    "report author": "report_author",
    "open the last project at start": "open_last_project",
    "advanced mode": "advanced_mode",
    "check GitHub for a newer AURORA at start (once a day)": "update_check",
    "de-spike threshold k": "despike_k",
    "default fluorescence normalisation": "fluo_norm",
    "default TEY normalisation": "tey_norm",
    "map colour map": "map_colormap",
    "reference spectra folder": "reference_dir",
}

NUMBERS = {w: i for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                                       "ten", "eleven", "twelve"])}


# ----------------------------------------------------------------------------- names
def norm(text) -> str:
    """The name as a reader sees it: no mnemonic '&', '...' as '…', no shortcut after a tab, no trailing ':' or ' ▾'."""
    t = re.sub(r"<[^>]+>", "", str(text or ""))
    t = t.split("\t", 1)[0]
    t = t.replace("&&", "\0").replace("&", "").replace("\0", "&")
    t = t.replace("...", "…").replace("\u00a0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*▾$", "", t).rstrip(":").rstrip()
    return t


def _menu_texts(menu, out: set) -> None:
    seen = set()
    stack = [menu]
    while stack:
        m = stack.pop()
        if m is None or id(m) in seen:
            continue
        seen.add(id(m))
        out.add(norm(m.title()))
        for a in m.actions():
            if not a.isSeparator():
                out.add(norm(a.text()))
                stack.append(a.menu())


def collect(root) -> dict[str, set[str]]:
    """{kind: names} of a window or dialog. 'controls' (buttons, check boxes, tools, the menu bar's entries, tabs, card
    titles) are what MANUAL must name; 'other' (labels, list entries, column titles, window titles, context menus,
    shortcut-only actions) only count as names the manuals may quote."""
    from PySide6.QtCore import QModelIndex, Qt
    from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QComboBox, QGroupBox, QLabel, QListWidget,
                                   QMenu, QTabBar, QTabWidget, QTreeWidget, QWidget)
    from aurora.app.widgets import Card
    controls: set[str] = set()
    other: set[str] = set()
    for w in [root] + root.findChildren(QWidget):
        if isinstance(w, QAbstractButton) and not w.property("aurora_help_button"):
            controls.add(norm(w.text()))
        if isinstance(w, (QTabWidget, QTabBar)):
            controls.update(norm(w.tabText(i)) for i in range(w.count()))
        if isinstance(w, Card):
            controls.add(norm(w.title.full_text() if hasattr(w.title, "full_text") else w.title.text()))
        if isinstance(w, QGroupBox):
            controls.add(norm(w.title()))
        if isinstance(w, QMenu):                     # context menus: names the manuals may quote
            _menu_texts(w, other)
        for a in w.actions():                        # shortcut-only actions (Help > Keyboard shortcuts lists them)
            if not a.isSeparator():
                other.add(norm(a.text()))
                if a.menu() is not None:
                    _menu_texts(a.menu(), other)
        if isinstance(w, QLabel):
            t = w.full_text() if hasattr(w, "full_text") else w.text()
            if t and len(t) < 160:
                other.add(norm(t))
        if isinstance(w, QComboBox):
            other.update(norm(w.itemText(i)) for i in range(w.count()))
        if isinstance(w, QListWidget):
            other.update(norm(w.item(i).text()) for i in range(w.count()))
        if isinstance(w, QTreeWidget):
            other.update(norm(w.headerItem().text(i)) for i in range(w.columnCount()))
        if isinstance(w, QAbstractItemView) and w.model() is not None:
            try:
                n = w.model().columnCount(QModelIndex())
            except TypeError:                        # list models keep columnCount private
                n = 0
            other.update(norm(w.model().headerData(i, Qt.Horizontal, Qt.DisplayRole) or "") for i in range(n))
        if w.isWindow():
            other.add(norm(w.windowTitle()))
    mb = root.menuBar() if hasattr(root, "menuBar") else None
    if mb is not None:
        for a in mb.actions():
            _menu_texts(a.menu(), controls)
    for name in list(controls):                      # 'Undo add a rectangle to zone 3' is the entry Undo
        if name.startswith(("Undo ", "Redo ")):
            controls.add(name.split(" ", 1)[0])
    controls.discard("")
    other.discard("")
    return {"controls": controls, "other": other}


def _dialog_names(win) -> set[str]:
    """The dialogs a user meets, built without exec(), and the context menus built on demand."""
    import tempfile
    from aurora.app import diagnostics
    from aurora.app.about import AboutDialog
    from aurora.app.dialogs import ReferencesDialog, StitchDialog
    from aurora.app.figure_export import FigureDialog
    from aurora.app.import_dialog import ImportDialog, RestoreDialog
    from aurora.app.rules_dialog import RulesDialog
    from aurora.app.session import ImportRow
    from aurora.app.session_ui import HistoryDialog
    from aurora.app.settings_dialog import SettingsDialog
    ses = win.session
    stacks = {sid: sd for sid, sd in ses._stacks.items() if sd is not None}
    makers = [
        lambda: SettingsDialog(win),
        lambda: ReferencesDialog(tempfile.gettempdir(), "", win),
        lambda: StitchDialog(stacks, win),
        lambda: FigureDialog(win),
        lambda: ImportDialog(Path("zones_all.json"), [ImportRow(key="holderA", holder="holderA", existing=1, incoming=2,
                                                                duplicates=1)], win),
        lambda: RestoreDialog(ses, win),
        lambda: RulesDialog(ses, win),
        lambda: HistoryDialog(ses.undo_stack, win),
        lambda: diagnostics.ShortcutsDialog(win, win),
        lambda: diagnostics.ReportProblemDialog(win, win),
        lambda: diagnostics.LicencesDialog(win),
        lambda: diagnostics.SelftestDialog({"version": "0", "ok": True, "checks": []}, win),
        lambda: AboutDialog(win),
    ]
    out: set[str] = set()
    for make in makers:
        d = make()
        for v in collect(d).values():
            out |= v
        d.deleteLater()
    sd = next(iter(stacks.values()))
    x0, x1, y0, y1 = sd.bbox
    win.survey.open_grid_dialog((x0, x1, y0, y1))
    d = win.survey.grid_dialog
    for v in collect(d).values():
        out |= v
    d.reject()
    menus: set[str] = set()
    hz = ses.holder_zones()
    if hz is not None and hz.zones:
        _menu_texts(win.reduce.zone_menu(hz.zones[0].id), menus)
    _menu_texts(win.reduce.map_menu(x0, y0), menus)
    for card in win.reduce._cards:
        _menu_texts(card.copy_menu(), menus)
        break
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem
    item = QListWidgetItem("demo")
    item.setData(Qt.UserRole, str(ROOT))
    _menu_texts(win.home.recent_context_menu(item), menus)
    return out | menus


@pytest.fixture(scope="module")
def ui(tmp_path_factory):
    """The names of the demo-loaded window in Advanced and in Easy mode, its dialogs and menus, and a failed open."""
    from aurora.app.main import create_app
    with pytest.MonkeyPatch.context() as mp:
        base = tmp_path_factory.mktemp("parity")
        mp.setenv("AURORA_SETTINGS_DIR", str(base / "settings"))       # never the user's real settings
        mp.setenv("AURORA_LOG_DIR", str(base / "logs"))
        app = create_app(["aurora-tests"])
        from aurora.app import crash, mode
        mp.setattr(crash, "show_error", lambda *a, **k: None)
        from demo_window import build_demo_window, pump
        mode.set_advanced(True)
        win = build_demo_window(app, base)
        names = collect(win)
        mode.set_advanced(False)
        pump(app, 10)
        easy = collect(win)
        mode.set_advanced(True)
        pump(app, 10)
        win.home.open_path(base / "no such beamtime")          # the failure panel: Retry, Choose another…, Keep …
        win.jobs.wait_idle(20000)
        pump(app, 10)
        failed = collect(win)
        dialogs = _dialog_names(win)
        pump(app, 5)
        ses = win.session
        if getattr(ses, "_save_timer", None) is not None:
            ses._save_timer.stop()
        win.jobs.wait_idle(20000)
        win.hide()
        win.deleteLater()
        pump(app, 5)
    controls = names["controls"] | easy["controls"] | failed["controls"]
    every = controls | names["other"] | easy["other"] | failed["other"] | dialogs | set(ALLOWED)
    return {"controls": controls, "every": every}


# ----------------------------------------------------------------------------- the manuals' bold names
def doc_text(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def bold_names(text: str) -> list[str]:
    """The bold names of a Markdown text, outside code blocks: menu paths split at ' > ', step badges ('1 · ') and
    keyboard keys dropped, paragraph lead-ins (ending with '.') skipped."""
    out = []
    for raw, colon in BOLD.findall(FENCE.sub("", text)):
        t = re.sub(r"\s+", " ", raw).strip()
        if not t or colon or t.endswith(".") or KEY.match(t):     # '**Top bar**: …' and '**After midnight.**' lead in
            continue
        for part in t.split(" > "):
            part = norm(re.sub(r"^\d+ · ", "", part.strip()))
            if part and not KEY.match(part):
                out.append(part)
    return out


def missing_in_ui(text: str, every: set[str]) -> list[str]:
    return sorted({n for n in bold_names(text) if n not in every})


@pytest.mark.parametrize("doc", MANUALS)
def test_every_bold_name_is_on_screen(ui, doc):
    missing = missing_in_ui(doc_text(doc), ui["every"])
    assert not missing, f"{doc} names controls that the window does not have: {missing}"


def test_a_name_the_window_does_not_have_is_caught(ui):
    text = "Click **Open project…**, then **Open beamtime…** (**Ctrl+O**). **After midnight.** **Top bar**: the crumb."
    assert missing_in_ui(text, ui["every"]) == ["Open beamtime…"]


def test_the_allow_list_is_still_in_the_code():
    source = "\n".join(p.read_text(encoding="utf-8") for p in APP.glob("*.py"))
    stale = [n for n in ALLOWED if f'"{n}"' not in source and f"'{n}'" not in source and f'("{n}"' not in source]
    assert not stale, f"allow-listed names no longer in aurora/app: {stale}"


# ----------------------------------------------------------------------------- the window's controls in MANUAL
def _skip_control(name: str) -> bool:
    """Data on screen rather than a control: scan titles, holders, paths, symbols, recent-project entries."""
    return (len(name) <= 1 or re.match(r"^\d\d:\d\d ", name) is not None or re.search(r"\d{4}-\d\d-\d\d", name) is not None
            or ":\\" in name or name in {"holderA", "no recent projects"} or re.fullmatch(r"\d+ .*", name) is not None)


def flat(text: str) -> str:
    """Markdown as plain running text: no bold marks, one space between words, '...' as '…'."""
    return re.sub(r"\s+", " ", text.replace("**", "").replace("...", "…"))


def undocumented(controls: set[str], manual: str) -> list[str]:
    text = flat(manual)
    return sorted(n for n in controls if not _skip_control(n) and not n.startswith(("Undo ", "Redo ")) and n not in text)


def test_every_control_of_the_window_is_in_the_manual(ui):
    missing = undocumented(ui["controls"], doc_text("MANUAL.md"))
    assert not missing, f"MANUAL.md does not name these controls of the window: {missing}"


def test_renaming_a_control_without_the_manual_fails(qapp_window):
    """The acceptance case: home.open_btn renamed on screen only. The parity check names the new text."""
    win = qapp_window
    win.home.open_btn.setText("Open beamtime…")
    names = collect(win)["controls"]
    assert "Open beamtime…" in undocumented(names, doc_text("MANUAL.md"))


@pytest.fixture
def qapp_window():
    from aurora.app.main import MainWindow, create_app
    create_app(["aurora-tests"])
    win = MainWindow()
    yield win
    win.close()


# ----------------------------------------------------------------------------- Settings rows
def settings_rows(manual: str) -> list[str]:
    """The first-column cells of the table of MANUAL §10, '(Advanced)' dropped, a cell naming two settings split."""
    sec = manual.split("## 10. Settings", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in sec.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(cells) < 2 or cells[0] in ("Setting", "") or set(cells[0]) <= set("-"):
            continue
        cell = re.sub(r"\s*\(Advanced\)\s*$", "", cells[0])
        rows += [c.strip() for c in re.split(r",\s+(?=default )", cell)]
    return rows


def app_sources(exclude=("settings_dialog.py",)) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in APP.glob("*.py") if p.name not in exclude}


def read_keys(sources: dict[str, str]) -> set[str]:
    """Settings keys read by the application: get("k") / setting("k"), mode's 'setting:k' and SETTING = "k"."""
    pat = re.compile(r"""(?:\bget|\bsetting)\(\s*["']([a-z_]+)["']\s*\)|["']setting:([a-z_]+)["']|\bSETTING\s*=\s*["']([a-z_]+)["']""")
    keys = set()
    for text in sources.values():
        for m in pat.finditer(text):
            keys.add(next(g for g in m.groups() if g))
    return keys


def unread_rows(manual: str, sources: dict[str, str]) -> list[str]:
    keys = read_keys(sources)
    return [r for r in settings_rows(manual) if SETTINGS_ROWS.get(r) not in keys]


def test_every_settings_row_is_a_setting_aurora_reads():
    from aurora.app.settings_dialog import DEFAULTS
    manual = doc_text("MANUAL.md")
    rows = settings_rows(manual)
    assert sorted(rows) == sorted(SETTINGS_ROWS), rows
    assert set(SETTINGS_ROWS.values()) == set(DEFAULTS), "a setting of the dialog is not documented in MANUAL §10"
    assert not unread_rows(manual, app_sources()), unread_rows(manual, app_sources())


def test_a_setting_nobody_reads_fails_on_section_10():
    """The acceptance case: without the get('map_colormap') reads, §10's 'map colour map' row is reported."""
    sources = {k: re.sub(r"""get\(\s*["']map_colormap["']\s*\)""", "None", v) for k, v in app_sources().items()}
    assert unread_rows(doc_text("MANUAL.md"), sources) == ["map colour map"]


def test_the_settings_dialog_rows_are_the_documented_ones(qapp_window):
    from PySide6.QtWidgets import QCheckBox, QFormLayout
    from aurora.app.settings_dialog import SettingsDialog
    d = SettingsDialog(qapp_window)
    form = d.form
    labels = []
    for r in range(form.rowCount()):
        lab = form.itemAt(r, QFormLayout.LabelRole)
        fld = form.itemAt(r, QFormLayout.FieldRole)
        text = lab.widget().text() if lab is not None and lab.widget() is not None else ""
        if not text and fld is not None and isinstance(fld.widget(), QCheckBox):
            text = fld.widget().text()
        labels.append(norm(text))
    d.deleteLater()
    manual_rows = settings_rows(doc_text("MANUAL.md"))
    for lab in labels:
        assert any(lab.startswith(r) for r in manual_rows), f"Settings row '{lab}' is not in MANUAL §10"


# ----------------------------------------------------------------------------- images, counts, screenshots
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


@pytest.mark.parametrize("doc", MANUALS + ("INSTALL.md",))
def test_every_image_link_resolves(doc):
    images = [t for t in LINK.findall(FENCE.sub("", doc_text(doc))) if t.lower().endswith((".png", ".jpg", ".svg"))]
    for target in images:
        assert (DOCS / target).is_file(), (doc, target)
    if doc == "MANUAL.md":
        assert len(images) >= 10


def stated_counts(text: str) -> list[int]:
    words = "|".join(NUMBERS)
    out = []
    for m in re.finditer(rf"\b({words}|\d+)\s+(?:tutorials|walkthroughs)\b", text, flags=re.I):
        w = m.group(1).lower()
        out.append(int(w) if w.isdigit() else NUMBERS[w])
    return out


def test_stated_tutorial_counts_equal_the_tutorials():
    n = len(re.findall(r"^## Tutorial \d+", doc_text("TUTORIALS.md"), flags=re.M))
    assert n >= 8
    texts = {"TUTORIALS.md": doc_text("TUTORIALS.md"), "USER_GUIDE.md": doc_text("USER_GUIDE.md"),
             "MANUAL.md": doc_text("MANUAL.md"), "README.md": (ROOT / "README.md").read_text(encoding="utf-8"),
             "docs/README.md": (ROOT / "docs" / "README.md").read_text(encoding="utf-8")}
    assert stated_counts(texts["TUTORIALS.md"]), "TUTORIALS.md does not say how many tutorials it holds"
    for name, text in texts.items():
        for c in stated_counts(text):
            assert c == n, f"{name} says {c} tutorials, TUTORIALS.md has {n}"


def test_the_screenshots_go_into_the_bundled_docs():
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_screenshots", ROOT / "docs" / "make_screenshots.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.OUT.resolve() == (DOCS / "img").resolve()
    shots = {p.stem for p in (DOCS / "img").glob("*.png")}
    used = {Path(t).stem for d in MANUALS for t in LINK.findall(doc_text(d)) if t.endswith(".png")}
    assert used <= shots


def test_the_screenshot_demo_lies_outside_the_home_folder():
    """Home and Report show the demo's full path, so the shots would carry the account name of whoever makes them
    (the temporary folder of Windows is inside the profile): the demo goes elsewhere."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_screenshots", ROOT / "docs" / "make_screenshots.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    base = mod.demo_root()
    try:
        assert base.is_dir() and not base.resolve().is_relative_to(Path.home().resolve()), base
        assert Path.home().name not in base.parts
    finally:
        base.rmdir()


def test_the_screenshot_script_refuses_a_path_with_the_account_name(monkeypatch, tmp_path):
    """BL-04: a demo path that names the account (user name or home folder name) stops the script with exit 2 and the
    reason, before any window or demo is made; a neutral path passes."""
    import getpass
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_screenshots", ROOT / "docs" / "make_screenshots.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(getpass, "getuser", lambda: "jdoe42")
    assert mod.account_in_paths([Path("/data/AURORA-docs/demo_beamtime")]) == ""
    why = mod.account_in_paths([Path("/data/jdoe42/demo_beamtime")])
    assert "jdoe42" in why and "screenshots would show it" in why
    inside = tmp_path / "Users" / "JDOE42" / "shots"
    inside.mkdir(parents=True)
    monkeypatch.setattr(mod, "demo_root", lambda: inside)
    called = []
    import aurora.cli
    monkeypatch.setattr(aurora.cli, "cmd_demo", lambda *a: called.append(a) or 0)
    assert mod.main(["--out", str(tmp_path / "out")]) == 2 and not called
