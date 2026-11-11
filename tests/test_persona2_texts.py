"""Second persona round: the words, links and pages a newcomer reads (a geology student and a glass chemist, neither an
X-ray absorption specialist). Each test names the finding it covers (M01-M27, S-01-S-34 of the walkthroughs)."""
from __future__ import annotations

import contextlib
import io
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def pump(qapp, n=6):
    for _ in range(n):
        qapp.processEvents()


def _demo(root: Path) -> Path:
    from aurora.cli import cmd_demo
    with contextlib.redirect_stdout(io.StringIO()):
        assert cmd_demo(root) == 0
    return root


@pytest.fixture
def demo_win(qapp, tmp_path):
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.core.model import Project
    mode.set_advanced(False)
    win = MainWindow()
    win.resize(1200, 760)
    win.show()
    win.home.set_project(Project.discover(_demo(tmp_path / "demo")))
    pump(qapp)
    win.jobs.wait_idle(30000)
    yield win
    mode.set_advanced(False)
    win.jobs.wait_idle(30000)
    win.session._save_timer.stop()
    win.close()


def _contrast(a: str, b: str) -> float:
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# ----------------------------------------------------------------------------- M02, S-03, S-26: the top bar
def test_the_next_link_reads_on_the_navy_bar_and_follows_the_theme(qapp, demo_win):
    from aurora.app import theme
    from aurora.app.theme import DARK, LIGHT
    for t in (LIGHT, DARK):
        assert _contrast(t["chrome_link"], t["night"]) >= 4.5                     # was 2.8:1 in light
    win = demo_win
    win.refresh_guide()
    assert LIGHT["chrome_link"].lower() in win.next_label.text().lower()
    rd = win.reduce
    try:
        theme.apply_theme(qapp, "dark")
        pump(qapp)
        assert DARK["link"].lower() in rd.next_link.text().lower()               # repainted on the switch
    finally:
        theme.apply_theme(qapp, "light")
        pump(qapp)
    assert LIGHT["link"].lower() in rd.next_link.text().lower()


def test_the_save_indicator_is_never_squeezed(qapp, demo_win):
    win = demo_win
    lab = win.findChild(type(win.next_label), "saveind")
    if lab is None or not lab.text():
        pytest.skip("no save indicator shown")
    assert lab.minimumWidth() >= lab.fontMetrics().horizontalAdvance(lab.text())
    assert win._next_room() <= 420


# ----------------------------------------------------------------------------- M23: the Survey draws
def test_the_guide_points_to_the_survey_for_the_first_outline():
    from aurora.app import guide
    s = guide.GuideState(has_project=True, n_complete_stacks=1, visited=frozenset({"survey"}))
    step = guide.next_step(s)
    assert step.page == "survey" and step.sentence.startswith("Next: draw around your first sample (Draw, then drag)")
    s = guide.GuideState(has_project=True, n_complete_stacks=1, n_zones=1)
    assert guide.next_step(s).sentence == "Next: select the sample and press Add to Check."


# ----------------------------------------------------------------------------- M04, S-31: a read-only beamtime
def test_a_protected_beamtime_opens_without_a_box_and_says_what_to_do(qapp, tmp_path, monkeypatch):
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.app.session import FOLDER_READ_ONLY_EASY
    from aurora.core.model import Project
    demo = _demo(tmp_path / "ro")
    monkeypatch.setenv("AURORA_PROTECT_DIRS", str(demo))
    mode.set_advanced(False)
    win = MainWindow()
    boxes, msgs = [], []
    win.session.warned.connect(lambda t, m: boxes.append(t))
    win.session.status.connect(msgs.append)
    try:
        win.home.set_project(Project.discover(demo))
        pump(qapp)
        assert win.session.read_only and not boxes                     # the banner says it: no 'Read-only session'
        assert FOLDER_READ_ONLY_EASY in msgs and not any("((" in m or "export them" in m for m in msgs)
        assert "working folder" in FOLDER_READ_ONLY_EASY and "(" in FOLDER_READ_ONLY_EASY
    finally:
        win.session._save_timer.stop()
        win.close()


# ----------------------------------------------------------------------------- M03, M14: the working folder
def test_a_working_folder_asks_about_outlines_saved_elsewhere_and_keeps_the_holder(qapp, tmp_path, monkeypatch):
    import json
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.core import testdata
    from aurora.core.model import Project
    demo = _demo(tmp_path / "bt")
    zf = testdata.make_fake_zones(Project.discover(demo))
    d = zf.to_json()
    d.pop("host", None)
    d["exported"] = "2026-11-08T15:16:00"
    (demo / ".aurora").mkdir(exist_ok=True)
    (demo / ".aurora" / "session.json").write_text(json.dumps(d), encoding="utf-8")      # an older session, no host
    monkeypatch.setenv("AURORA_PROTECT_DIRS", str(demo))
    mode.set_advanced(False)
    win = MainWindow()
    asked = []
    win.ask_keep_outlines = lambda n, per, when: asked.append((n, when)) or "empty"
    try:
        win.home.set_project(Project.discover(demo))
        pump(qapp)
        n, per, when = win.session.earlier_outlines()
        assert n >= 1 and when == "2026-11-08 15:16"
        note = win.earlier_outlines_text(n, per, when)
        assert note.startswith(f"This beamtime holds {n} outline") and "saved earlier (2026-11-08 15:16)" in note
        assert note in win.home.details.toPlainText()                                      # said on Home
        keys = list(win.session.holder_keys)
        win.session.set_holder(keys[-1])
        assert win.set_work_folder(tmp_path / "work" / demo.name)
        pump(qapp)
        assert asked and asked[0][0] == n
        assert win.session.current_holder == keys[-1]                                     # the holder stays (M14)
        assert not any(hz.zones for hz in win.session.zones.holders.values())              # started empty
        rows = dict(__import__("aurora.core.overview", fromlist=["x"]).easy_project_rows(win.session.project))
        assert rows["working folder"].startswith(str(tmp_path / "work" / demo.name))
    finally:
        win.session._save_timer.stop()
        win.close()


# ----------------------------------------------------------------------------- M01, M22, S-24: Home
def test_home_rows_keep_their_words(qapp, demo_win):
    from aurora.app.home import COLS
    from aurora.core import overview
    win = demo_win
    tree = win.home.tree
    day = tree.topLevelItem(0)
    holder = day.child(0)
    assert "1 holder" in day.text(0) and "scan" in day.text(0) and day.text(COLS.index("Pixels")) == ""
    assert "complete stack" in holder.text(0) and holder.text(COLS.index("Status")) == ""
    scans = [holder.child(i) for i in range(holder.childCount())]
    stack = next(s for s in scans if s.text(0).endswith("_stack") and s.text(COLS.index("Status")) == "complete")
    assert stack.text(COLS.index("Energies")) == "670–735 eV"                             # no decimals in Easy
    head = tree.header()
    fm = tree.fontMetrics()
    for c in ("Edge", "Status"):                                                           # never cut (M01)
        i = COLS.index(c)
        assert head.sectionSize(i) >= fm.horizontalAdvance(stack.text(i)), c
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QStyleOptionViewItem
    opt = QStyleOptionViewItem()                                  # a narrow Scan column: the sample part (S-04)
    opt.initFrom(tree)
    opt.rect = QRect(0, 0, tree.fontMetrics().horizontalAdvance("alpha_beta") + 12, 20)
    tree.itemDelegateForColumn(0).initStyleOption(opt, tree.indexFromItem(stack, 0))
    assert opt.text == "alpha_beta" and stack.text(0) == "092758_alpha_beta_f_k_stack"
    tree.setCurrentItem(holder)
    pump(qapp)
    assert win.home.details_card.title.text() == "Holder" and "complete stack" in win.home.details.toPlainText()
    rows = dict(overview.easy_project_rows(win.session.project, overview.project_facts(win.session.project)))
    assert rows["sample sheet"] == "samples.csv: 2 samples on holderA" and "columns" not in rows["sample sheet"]
    srows = dict(overview.easy_scan_rows(win.session.project.find(stack.text(0)), win.session.project))
    assert "project" not in srows                                                          # no empty project id


# ----------------------------------------------------------------------------- S-11, M18, M26: Check messages
def test_check_names_the_falling_signal_and_an_end_point_spike():
    from types import SimpleNamespace
    from aurora.core import qc
    E = np.linspace(670, 735, 66)
    base = dict(E=E, i0_raw=np.ones(66), tey_raw=np.ones(66), masked={}, line="F Kα", n_pix=12, settings=None,
                det_raw=[np.ones(66)] * 4, bad_mask=lambda: np.zeros(66, bool))
    t = SimpleNamespace(**base, notes=["TEY: edge step negative (-0.8): the signal falls across the edge; kept"],
                        spikes={"sdd4": [0]})
    msgs = [(lv, str(m)) for lv, m in qc.check_zone(t) if str(m) != qc.MSG_NO_BAD]
    tey = [(lv, m) for lv, m in msgs if m == qc.MSG_TEY_NO_EDGE]
    assert tey and tey[0][0] == "ok" and "fluorescence spectrum is the one to use" in tey[0][1]
    assert any(m.startswith("the first energy (670 eV) was replaced") for _lv, m in msgs)
    t2 = SimpleNamespace(**base, notes=["fluorescence sum: edge step negative (-0.8): the signal falls"], spikes={})
    msgs2 = [(lv, str(m)) for lv, m in qc.check_zone(t2)]
    assert ("warn", qc.MSG_STEP_NEGATIVE) in msgs2 and qc.MSG_STEP_NEGATIVE.startswith("the fluorescence signal")
    assert qc.headline([("ok", qc.MSG_TEY_NO_EDGE)]) == qc.MSG_TEY_NO_EDGE


def test_check_overwrite_question_in_words():
    from aurora.app.check import CheckPerspective
    paths = [Path("r") / "1 alpha" / f"1 alpha{s}" for s in ("_full_reduced.csv", "_absorption.csv")]
    paths += [Path("r") / "2 beta" / f"2 beta{s}" for s in ("_full_reduced.csv", "_absorption.csv")]
    text = CheckPerspective.overwrite_text(paths)
    assert text.startswith("1 alpha and 2 beta were saved before (4 files). Replace them?") and "(s)" not in text


# ----------------------------------------------------------------------------- M20, S-18, M12, S-06, S-21, S-22
def test_compare_in_plain_words_with_every_label(qapp, demo_win):
    from aurora.app.compare import easy_norm_text
    text = easy_norm_text()
    assert not any(w in text for w in ("larch", "earlier", "flattened", "GUI"))
    assert "5–20 eV below and 15–35 eV above its edge position (E0)" in text
    win = demo_win
    lib = win.library
    lib.add_folder(win.session.project.root / "processed" / "reduced")
    for i, r in enumerate(lib.requests):
        r.show = r.signal != "TEY" and i < 4
    lib.changed.emit()
    win.show_perspective("compare")
    pump(qapp, 10)
    cp = win.compare
    cp.replot()
    pump(qapp, 10)
    cp._spread_labels()
    shown = [ti for ti, _x, _y in cp._trace_labels]
    assert len(shown) >= 2 and all(ti.isVisible() for ti in shown)                      # never dropped (S-06)
    assert cp._ylabel() == "absorption (step = 1)"
    assert "Curves offset by" in cp.status.text() and "for clarity" in cp.status.text()   # S-21
    cp.table.selectRow(0)
    pump(qapp)
    cp._draw_diag() if hasattr(cp, "_draw_diag") else None
    assert "grey lines" not in cp.diag_note.text()


# ----------------------------------------------------------------------------- M15: one notation for a window
def test_the_window_end_box_is_the_last_channel_counted(qapp, demo_win):
    from aurora.core import testdata
    win = demo_win
    ses = win.session
    sd = ses.stack(ses.stack_id(ses.project.find(testdata.STACK)))
    ses.select_stack(sd.id)
    win.show_perspective("reduce")
    pump(qapp, 10)
    wc = win.reduce.window_card
    if wc._sd is None:
        pytest.skip("no scan in step 1")
    lo, hi = wc.windows()["sdd1"]
    assert (wc.lo.value(), wc.hi.value()) == (lo, hi - 1)
    import re
    assert f"channels {lo}–{hi - 1}" in wc.ev_label.text() and not re.search(r"\d+:\d+", wc.ev_label.text())
    assert "move with it" not in wc.ev_label.text()


# ----------------------------------------------------------------------------- S-09: one sentence
def test_a_sample_on_a_stack_that_stopped_early_gets_one_sentence(qapp, demo_win):
    from aurora.core import testdata
    from aurora.core.zones import incomplete_text, incomplete_under
    ses = demo_win.session
    h = ses.holder()
    gone = next(s for s in h.dropped if s.status == "aborted")
    x0, x1 = sorted((float(gone.md["xstart"]), float(gone.md["xstop"])))
    y0, y1 = sorted((float(gone.md["ystart"]), float(gone.md["ystop"])))
    with ses.edit("a grain on the aborted stack"):
        z = ses.new_zone("3 gamma")
        z.rects.append([x0 + 0.01, x1 - 0.01, y0 + 0.01, y1 - 0.01])
    assert incomplete_under(z, h) is gone
    text = incomplete_text("3 gamma", gone)
    assert text == (f"3 gamma lies on {gone.time} {gone.label}, which stopped early (20 of 66 energies): no spectrum "
                    "can be made from it. Only complete stacks give spectra.")
    win = demo_win
    win.show_perspective("survey"); pump(qapp, 10)
    win.survey.zone_preview.refresh(); pump(qapp)
    assert win.survey.zone_preview.xas_note.text() == text
    from aurora.core.report import plan
    p = plan(ses.project, ses.zones, list(ses.sheet_rows))
    assert any(f.text == f"'3 gamma' (holderA) lies on {gone.time} {gone.label}, which stopped early (20 of 66 "
                         "energies): no spectrum can be made from it. Only complete stacks give spectra. It is left out."
               for f in p.findings)
    assert testdata.STACK


# ----------------------------------------------------------------------------- S-05, M06, M16, M21: the report
def test_the_report_holds_the_compare_plot_plain_words_and_the_erased_outline(qapp, tmp_path):
    from aurora.core import testdata
    from aurora.core.figures import FigureTrace
    from aurora.core.model import Project
    from aurora.core.report import ReportBuilder
    proj = Project.discover(_demo(tmp_path / "rep"))
    zf = testdata.make_fake_zones(proj)
    z = zf.holders["holderA"].zones[0]
    x0, x1, y0, y1 = z.rects[0]
    z.erects.append([x0, x1, y0, (y0 + y1) / 2])                                        # half of it erased
    E = np.linspace(670, 735, 66)
    snap = {"traces": [FigureTrace("1 alpha", E, np.tanh(E - 690), "#2A6CCA", "#2A6CCA", 0),
                       FigureTrace("2 beta", E, np.tanh(E - 695) + 0.6, "#DB4A12", "#D44303", 1)],
            "x_range": None, "ylabel": "absorption (step = 1), offset", "caption": "two spectra, offset by 0.6"}
    b = ReportBuilder(proj, zf, tmp_path / "reports", png_dpi=40, page_png_dpi=0, compare=snap)
    res = b.build()
    cover = " ".join(x.strip() for x in b.cover_lines)
    assert "Compare: 2 spectra as shown" in cover and any(f.name.startswith("compare") for f in res.figures)
    assert "ambxas" not in cover and "zone spectra" not in cover and "(see Appendix C)" in cover
    assert "QC pages" not in cover and "preset" not in cover and "outlines saved" in cover and "T0" not in cover
    assert "detector energy scale: standard, 10 eV per channel" in cover or "SDD calibration:" in cover
    c = b.appendix_c
    head = c[c.index("Spectra included (the flags with their source):") + 1]
    assert head.split()[:4] == ["holder", "sample", "scan", "edge"]
    assert not any(" / I0" in ln or "edge jump" in ln for ln in c)


def test_report_captions_wrap_above_the_footer(qapp, tmp_path):
    import matplotlib
    matplotlib.use("Agg")
    from aurora.core import report as core_report
    assert core_report.CAPTION_CHARS <= 160 and core_report.CAPTION_BOTTOM > 0.03
    assert core_report.spikes_words({"sdd4": 1, "sum": 2}) == "1 spike (SDD4); 2 spikes (sum)"


# ----------------------------------------------------------------------------- M25, S-23, S-30: help
def test_help_strings_read_once():
    from aurora.app.help_text import tooltip
    from aurora.glossary import GLOSSARY, where_sentence
    assert "What are Easy and Advanced mode?" not in tooltip("main.mode")
    for t in GLOSSARY:
        s = where_sentence(t)
        assert not s.endswith("….") and s.count("Advanced mode") <= 1 and s.count("(Advanced") <= 1, s


def test_help_menu_has_the_quick_start(qapp, demo_win):
    win = demo_win
    assert win.quick_action.text() == "Quick start"
    shown = []
    win.show_help = lambda doc="USER_GUIDE.md", anchor=None: shown.append((doc, anchor))
    win.quick_action.trigger()
    assert shown == [("USER_GUIDE.md", "quick-start-for-non-specialists")]


# ----------------------------------------------------------------------------- S-33, M27: names and titles
def test_offered_names_and_axis_titles(qapp, demo_win):
    ses = demo_win.session
    assert all("_f_k" not in n for n in ses.scan_labels())
    from aurora.app import check
    src = Path(check.__file__).read_text(encoding="utf-8")         # the view titles start with a word (M27)
    assert '"signal ÷ I0, scaled 0 to 1"' in src and '"÷ I0, scaled' not in src


# ----------------------------------------------------------------------------- M05: saved spectra are remembered
def test_saved_rows_say_so_and_the_guide_counts_them_from_disk(qapp, demo_win):
    from aurora.app.check import Q_OK
    from aurora.core import testdata
    win = demo_win
    ses = win.session
    sd = ses.stack(ses.stack_id(ses.project.find(testdata.STACK)))
    ses.select_stack(sd.id)
    x0, x1, y0, y1 = sd.bbox
    with ses.edit("one sample"):
        z = ses.new_zone("1 alpha")
        z.rects.append([x0, x1, y0, y1])
    ses.select_zone(z.id)
    win.show_perspective("reduce"); pump(qapp, 10)
    win.reduce.scan_list.setCurrentRow(0); pump(qapp)
    assert win.reduce.add_to_check() == 1
    win.show_perspective("check")
    assert win.jobs.wait_idle(60000)
    pump(qapp)
    ck = win.check
    assert ck.saved_count() == 0 and "saved" not in ck.queue.item(0, Q_OK).text()
    ck.ask_overwrite = lambda paths: "yes"
    ck.save_ticked()
    assert win.jobs.wait_idle(60000)
    pump(qapp)
    ck._fill_queue()
    assert ck.saved_count() == 1 and ck.queue.item(0, Q_OK).text().startswith("saved ")
    ck.n_saved = 0                                       # a new session: the files tell
    assert win.guide_state().n_saved == 1
    assert "each with its electron-yield version" in ck.queue_note.text() or "added to Compare" in ck.queue_note.text()


def test_kept_earlier_outlines_are_not_stamped_as_this_computer_s(qapp, tmp_path, monkeypatch):
    """M03: 'Keep the earlier outlines' copies them into the working folder without this computer's host, so the
    next opening still says they were saved earlier."""
    import json
    from aurora.app import mode
    from aurora.app.main import MainWindow
    from aurora.core import testdata
    from aurora.core.model import Project
    demo = _demo(tmp_path / "bt")
    d = testdata.make_fake_zones(Project.discover(demo)).to_json()
    d.pop("host", None)
    (demo / ".aurora").mkdir(exist_ok=True)
    (demo / ".aurora" / "session.json").write_text(json.dumps(d), encoding="utf-8")
    monkeypatch.setenv("AURORA_PROTECT_DIRS", str(demo))
    mode.set_advanced(False)
    win = MainWindow()
    win.ask_keep_outlines = lambda n, per, when: "keep"
    try:
        win.home.set_project(Project.discover(demo))
        pump(qapp)
        n = win.session.earlier_outlines()[0]
        work = tmp_path / "work" / demo.name
        assert n and win.set_work_folder(work)
        pump(qapp)
        saved = json.loads((work / ".aurora" / "session.json").read_text(encoding="utf-8"))
        assert not saved.get("host") and sum(len(h["zones"]) for h in saved["holders"].values()) >= n
        assert win.session.earlier_outlines()[0] == n                     # still said on Home
    finally:
        win.session._save_timer.stop()
        win.close()
