"""PL-03 to PL-10: Easy mode in plain words for colleagues who do not know XAS.

Guards on every Easy-visible text of the demo window (a sample queued and saved, so every page has its texts): no
bare jargon, no internals, no person or pipeline names. Then the Getting started guide (pure table, then following
the user through the window), the welcome panel, Reduce step 4, Check, Compare in Easy (no row silently dropped), the
signal words, a read-only beamtime and the quick start of the user guide."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from PySide6.QtCore import Qt  # noqa: E402

from aurora import glossary  # noqa: E402
from aurora.app import guide, mode  # noqa: E402

import plain_helpers as ph  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "aurora" / "resources" / "docs"


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def wait(qapp, s: float = 0.3) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < s:
        qapp.processEvents()


def dispose(w) -> None:
    """A parentless widget made by a test: deleted and its Python garbage collected here, on the GUI thread (left to
    the automatic collector, a worker thread of a later test could delete the widget there)."""
    import gc
    from PySide6.QtCore import QCoreApplication, QEvent
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


def close(win) -> None:
    import shiboken6
    if not shiboken6.isValid(win):                          # a module-scoped window the conftest already destroyed
        return
    win.session.confirm_leave = lambda *_a, **_k: True      # a read-only session would ask about its edits (modal)
    t = getattr(win.session, "_save_timer", None)
    if t is not None:
        t.stop()
    win.jobs.wait_idle(20000)
    win.close()


@pytest.fixture(scope="module")
def easy_texts(qapp, tmp_path_factory):
    """The Easy-visible texts of the demo window with one sample queued in Check and saved to Compare."""
    import os
    from demo_window import build_demo_window, pump
    from aurora.app import crash
    base = tmp_path_factory.mktemp("plain")
    old = os.environ.get("AURORA_SETTINGS_DIR")
    os.environ["AURORA_SETTINGS_DIR"] = str(base / "settings")
    real = crash.show_error
    crash.show_error = lambda *a, **k: None
    try:
        mode.set_advanced(False)
        win = build_demo_window(qapp, base)
        pump(qapp, 10)
        assert win.reduce.add_to_check() == 1
        win.show_perspective("check")
        wait(qapp, 2.0)
        win.check.save_ticked()
        win.jobs.wait_idle(30000)
        pump(qapp, 10)
        win.refresh_guide()
        texts = ph.plain_texts(win, qapp)
        yield texts
        close(win)
    finally:
        crash.show_error = real
        if old is None:
            os.environ.pop("AURORA_SETTINGS_DIR", None)
        else:
            os.environ["AURORA_SETTINGS_DIR"] = old


# ----------------------------------------------------------------------------- the guards
def test_the_collector_sees_every_page(easy_texts):
    pages = {t.where.split(":")[0] for t in easy_texts}
    assert set(ph.EASY_PAGES) <= pages
    every = " ".join(t.text for t in easy_texts)
    for words in ("Which X-rays to count", "Send to Check", "Bad energies", "How this spectrum was scaled",
                  "Mix of references (LCF)", "Getting started", "Samples outlined on this holder"):
        assert words in every, words


def test_no_bare_jargon_in_easy(easy_texts):
    bad = ph.violations(easy_texts, ph.jargon)
    assert not bad, "\n".join(bad)


def test_no_internals_in_easy(easy_texts):
    bad = ph.violations(easy_texts, ph.internals)
    assert not bad, "\n".join(bad)


def test_no_person_or_pipeline_names_in_easy(easy_texts):
    bad = ph.violations(easy_texts, ph.names)
    assert not bad, "\n".join(bad)


def test_the_guards_catch_a_bad_text():
    t = ph.Text("probe", "I0 mcc ch1 · sdd-scaled · John Bussey's GUI", "")
    assert ph.jargon(t) == ["I0", "sdd"] and ph.internals(t) == ["sdd-scaled"] and ph.names(t) == ["John Bussey"]
    assert ph.jargon(ph.Text("probe", "TEY", glossary.what_is("tey"))) is None


# ----------------------------------------------------------------------------- Getting started
def test_guide_next_step_table():
    S = guide.GuideState
    rows = [
        (S(opening="beam_2025"), "home", "Reading beam_2025"),
        (S(), "home", "Open the folder of your beamtime"),
        (S(has_project=True, read_only=True), "home", "This beamtime folder is read-only"),
        (S(has_project=True), "home", "This beamtime has no complete stack"),
        (S(has_project=True, n_complete_stacks=2), "survey", "Next: find your samples"),
        (S(has_project=True, n_complete_stacks=2, visited=frozenset({"survey"})), "reduce", "Next: outline your first"),
        (S(has_project=True, n_complete_stacks=2, n_zones=1), "reduce", "Next: in Reduce, select a sample"),
        (S(has_project=True, n_complete_stacks=2, n_zones=1, n_queued=1), "check", "Next: look at each spectrum"),
        (S(has_project=True, n_complete_stacks=2, n_zones=1, n_queued=1, n_saved=1), "compare", "Next: plot your"),
        (S(has_project=True, n_complete_stacks=2, n_zones=1, n_queued=1, n_saved=1, visited=frozenset({"compare"})),
         "report", "Next: build the report"),
        (S(has_project=True, n_complete_stacks=2, n_zones=1, n_queued=1, n_saved=1, visited=frozenset({"compare"}),
           report_folder="r"), "report", "The report is built"),
    ]
    for state, page, start in rows:
        n = guide.next_step(state)
        assert n.page == page and n.sentence.startswith(start), (state, n)
    assert guide.next_step(S()).actions == (("Open project…", "open"), ("Open the demo beamtime", "demo"))
    assert guide.next_step(rows[4][0]).actions == (("Go to Survey", "survey"),)
    done = guide.done_steps(S(has_project=True, n_zones=1, n_saved=1, visited=frozenset({"compare"}), n_library=2,
                              fit_ran=True, report_folder="r"))
    assert done == {"home", "survey", "reduce", "check", "compare", "fit", "report"}
    assert guide.done_steps(S()) == frozenset()


def test_guide_follows_the_user(qapp, tmp_path):
    """Fresh window: Open …; the demo: find your samples; Survey seen, then a zone: Add to Check; queued: in Check;
    saved: in Compare; Compare seen: build the report; built: the report is built. The strip's button goes to the
    page, and the top bar's Next link names it."""
    from demo_window import pump
    from aurora.app.main import MainWindow
    from aurora.cli import cmd_demo
    from aurora.core import testdata
    from aurora.core.model import Project
    mode.set_advanced(False)
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    try:
        win.refresh_guide()
        assert win.next_step.sentence.startswith("Open the folder of your beamtime")
        assert not win.home.guide_strip.isVisible() and win.next_text() == ""
        demo = tmp_path / "demo"
        cmd_demo(demo)
        win.home.set_project(Project.discover(demo))
        pump(qapp, 5)
        win.refresh_guide()
        strip = win.home.guide_strip
        assert strip.isVisible() and win.next_step.sentence.startswith("Next: find your samples")
        assert strip.action_btn.text() == "Go to Survey" and win.next_text().startswith("Next: Survey – ")
        strip.action_btn.click()
        assert win.current_key() == "survey"
        win.refresh_guide()
        assert win.next_text().startswith("Next: Reduce – outline your first sample")
        ses = win.session
        sd = ses.stack(ses.stack_id(Project.discover(demo).find(testdata.STACK)))
        ses.select_stack(sd.id)
        z = ses.new_zone("1 alpha")
        x0, x1, y0, y1 = sd.bbox
        z.rects.append([x0, x1, y0, y1])
        ses.touch()
        ses.select_zone(z.id)
        win.show_perspective("reduce")
        win.reduce.scan_list.setCurrentRow(0)
        pump(qapp, 5)
        win.refresh_guide()
        assert win.next_step.sentence == "Next: in Reduce, select a sample and press Add to Check."
        assert win.next_text().startswith("Here: in Reduce")              # Reduce is the current page: no link
        assert win.reduce.add_to_check() == 1
        assert win.reduce.next_link.isVisible() and "Go to Check" in win.reduce.next_link.text()
        win.refresh_guide()
        assert win.next_step.page == "check" and win.next_text().startswith("Next: Check – look at each spectrum")
        win.show_perspective("check")
        wait(qapp, 2.0)
        win.check.save_ticked()
        win.jobs.wait_idle(30000)
        pump(qapp, 10)
        win.refresh_guide()
        assert win.check.n_saved == 1 and win.next_step.page == "compare", win.next_step
        win.show_perspective("compare")
        win.refresh_guide()
        assert win.next_step.sentence.startswith("Next: build the report")
        win.report.built.emit(str(tmp_path / "build"))
        win.refresh_guide()
        assert win.next_step.sentence.startswith("The report is built")
        assert win.next_step.actions == (("Open folder", "folder"),)
        win.show_perspective("home")
        win.refresh_guide()
        assert strip.chips["report"].text() == "✓ Report" and strip.action_btn.objectName() == "primary"
        # Hide writes the setting; Advanced mode never shows the strip or the link
        strip.hide_btn.click()
        assert not strip.isVisible() and win.next_text() == ""
        from aurora.app.settings_dialog import get
        assert get("show_guide") is False
    finally:
        close(win)


def test_advanced_mode_shows_no_guide(qapp, tmp_path):
    from demo_window import build_demo_window
    mode.set_advanced(True)
    win = build_demo_window(qapp, tmp_path)
    try:
        win.refresh_guide()
        assert not win.home.guide_strip.isVisible() and not win.next_label.isVisible()
    finally:
        mode.set_advanced(False)
        close(win)


def test_welcome_is_blank_and_plain(qapp):
    from aurora.app.home import WELCOME_TEXT, HomePerspective
    banned = ("2026_CLS", "John Bussey", "CLS GUI", "WSU", "the lab")
    for adv in (False, True):
        mode.set_advanced(adv)
        texts = (WELCOME_TEXT, guide.getting_started_html(adv), HomePerspective._welcome_html())
        for t in texts:
            assert not any(b in t for b in banned), (adv, t[:120])
    mode.set_advanced(False)
    html = guide.getting_started_html(False)
    assert "my_beamtime/" in html and "open this folder" in html and "samples.csv" in html
    plain = guide.welcome_plain(False)
    for key, _title in guide.STEPS:
        line = guide._step_item(key)
        assert line in plain, line
    assert "glossary:" in html and "aurora:help:USER_GUIDE.md#quick-start-for-non-specialists" in html


def test_home_links_open_the_glossary_and_the_guide(qapp, monkeypatch):
    from PySide6.QtCore import QUrl
    from aurora.app import help as help_mod
    from aurora.app.home import HomePerspective
    seen = []
    monkeypatch.setattr(help_mod, "open_help", lambda w, doc="USER_GUIDE.md", anchor=None: seen.append((doc, anchor)))
    h = HomePerspective()
    h.on_link(QUrl("glossary:i0"))
    h.on_link(QUrl("glossary:"))
    h.on_link(QUrl("aurora:help:USER_GUIDE.md#quick-start-for-non-specialists"))
    assert seen == [("GLOSSARY.md", glossary.term("i0").anchor), ("GLOSSARY.md", None),
                    ("USER_GUIDE.md", "quick-start-for-non-specialists")]
    dispose(h)


def test_term_label_links(qapp, monkeypatch):
    from aurora.app import help as help_mod
    from aurora.app.widgets import TermLabel
    seen = []
    monkeypatch.setattr(help_mod, "open_help", lambda w, doc="USER_GUIDE.md", anchor=None: seen.append((doc, anchor)))
    lab = TermLabel("divided by the {{i0|beam intensity (I0)}}, then {{tey}}")
    assert lab.plain_text() == "divided by the beam intensity (I0), then TEY"
    assert lab.linked_keys() == ["i0", "tey"] and "href=\"glossary:i0\"" in lab.text()
    assert lab.textInteractionFlags() & Qt.LinksAccessibleByKeyboard
    lab.linkActivated.emit("glossary:i0")
    assert seen == [("GLOSSARY.md", glossary.term("i0").anchor)]
    assert lab.accessibleDescription() == lab.plain_text()
    dispose(lab)


def test_term_buttons_and_the_help_menu(qapp, monkeypatch):
    from aurora.app.help_text import term_button
    from aurora.app.main import MainWindow
    b = term_button("xrf-window")
    assert b.text() == "?" and b.property("aurora_help_button") and b.accessibleName() == "What is the emission window?"
    assert b.toolTip().startswith(glossary.what_is("xrf-window"))
    dispose(b)
    for adv in (False, True):
        mode.set_advanced(adv)
        win = MainWindow()
        try:
            a = win.help_actions["GLOSSARY.md"]
            assert a.isVisible() and a.text() == "Glossary"
            a.trigger()
            assert win._help.current_doc() == "GLOSSARY.md"
            from aurora.app.help import open_glossary
            open_glossary(win, "i0")
            assert win._help.current_anchor() == glossary.term("i0").anchor
            win._help.close()
        finally:
            close(win)
    mode.set_advanced(False)


def test_rail_tooltips_say_what_each_page_is_for(qapp):
    from aurora.app.main import MainWindow
    win = MainWindow()
    try:
        tip = win.rail_buttons["survey"].toolTip()
        assert tip.startswith("Survey (Ctrl+2)") and "where the samples and the measured areas are" in tip
        assert "Advanced mode" in win.rail_buttons["maps"].toolTip()
    finally:
        close(win)


# ----------------------------------------------------------------------------- the pages
@pytest.fixture
def easy_demo(qapp, tmp_path):
    from demo_window import build_demo_window
    mode.set_advanced(False)
    win = build_demo_window(qapp, tmp_path)
    yield win
    close(win)


def test_step4_sentence(qapp, easy_demo):
    rd = easy_demo.reduce
    sid = rd.current_stack().id
    text = rd.step4_line.plain_text()
    assert "divided by the beam intensity (I0)" in text and "the four detectors" in text
    assert not any(w in text for w in ("mcc", "Au mesh", "k =", "SDD1-4"))
    assert rd.step4_line.toolTip().startswith("I0 mcc ch1 (Au mesh) · TEY mcc ch4 · zone sum")
    st = rd.settings_in_force(sid)
    st.sdd = [True, True, False, True]
    from aurora.core.flags import Flag
    sat = Flag("SDD saturated: SDD3", "sdd_saturated", "AURORA check", ("sdd3",))
    rd.session.flags_of = lambda scan, _f=sat: [_f]
    text = rd.step4_text(sid)
    assert "detectors 1, 2 and 4 (detector 3 saturated: left out)" in glossary_plain(text)
    assert "SDD3 saturated, left out" in rd.step4_detail(sid)
    st.signal, st.line = "ipfy", "O Kα"
    assert glossary_plain(rd.step4_text(sid)).startswith("The spectrum is an inverse yield (IPFY)")
    assert "belongs to oxygen, not to the edge element (fluorine)" in rd.ipfy_note()
    st.sdd = [False] * 4
    st.signal = "pfy"
    rd.session.flags_of = lambda scan: [Flag("SDD saturated: SDD1, SDD2, SDD3, SDD4", "sdd_saturated", "AURORA check",
                                             ("sdd1", "sdd2", "sdd3", "sdd4"))]
    assert glossary_plain(rd.step4_text(sid)).startswith("All four detectors saturated on this scan")


def glossary_plain(markup: str) -> str:
    from aurora.app.widgets import term_markup_plain
    return term_markup_plain(markup)


def test_reduce_titles_by_mode(qapp, easy_demo):
    rd = easy_demo.reduce
    assert rd.step_titles()[1:] == ["2 · Which X-rays to count", "3 · Outline the sample", "4 · Send to Check"]
    assert rd.del_zone_btn.text() == "Delete sample"
    assert rd.window_card.preset.itemText(0).endswith("(fixed window)")
    mode.set_advanced(True)
    try:
        assert rd.step_titles()[1:] == ["XRF window", "Stack map", "Zone"]
        assert "(10 eV/ch nominal)" in rd.window_card.ev_label.text() or "calibrated" in rd.window_card.ev_label.text()
    finally:
        mode.set_advanced(False)
    assert "per channel" in rd.window_card.ev_label.text()


def test_check_texts(qapp, easy_demo):
    from aurora.app.check import QCOLS, VIEWS
    win = easy_demo
    assert QCOLS == ("Sample", "Scan", "Pixels", "Bad energies", "Messages", "Looks OK")
    assert [t for _k, t in VIEWS] == ["the four detectors (spikes repaired)", "sum of the detectors, bad energies marked",
                                      "beam intensity (I0) and electron yield (TEY)",
                                      "fluorescence and TEY divided by the beam (I0)", "the same, scaled 0 to 1"]
    win.reduce.add_to_check()
    win.show_perspective("check")
    wait(qapp, 2.0)
    ck = win.check
    assert ck.guide_label.isVisible() and ck.look_for.isVisible()
    first = ck.msg_label.text().splitlines()[0]
    assert "pixels" in first and "cache" not in first and ":\\" not in first and ".npz" not in first
    assert ck.queue.item(0, 2).text().count(" of ") == 1
    mode.set_advanced(True)
    try:
        assert not ck.guide_label.isVisible()
        assert "points" in ck.msg_label.text().splitlines()[0]
    finally:
        mode.set_advanced(False)


def test_qc_messages_plain():
    from aurora.core import qc
    from aurora.core.flags import Flag
    from aurora.core.io.config import ScanSettings
    from test_wp4_queue import build_traces, sums
    banned = ("sgm_report", "MAD", "pile-up", "good-I0", "points")
    for tmpl in qc.TEMPLATES:
        assert not any(b in tmpl for b in banned), tmpl
    st = ScanSettings(line="F Kα", despike_mode="coherent")
    msgs = qc.check_zone(build_traces(sums(), st), n_total=12)
    assert all(not any(b in str(m) for b in banned) for _lv, m in msgs), msgs
    assert ("ok", "4 of 12 pixels inside the outline") in [(lv, str(m)) for lv, m in msgs]
    assert qc.check_zone(None)[0] == ("bad", qc.MSG_NO_PIXEL)
    inv = Flag("I0 invalid", "i0_invalid", "sgm_report")
    m = [m for lv, m in qc.check_zone(build_traces(sums(), st), flags=[inv]) if "beam reading" in m][0]
    assert m.source == "sgm_report" and qc.full_text(m).endswith("(sgm_report)") and "sgm_report" not in str(m)
    ip = ScanSettings(line="O Kα", signal="ipfy", despike_mode="coherent")
    msgs = qc.check_zone(build_traces(sums(), ip))
    assert qc.headline(msgs).startswith("inverse yield (IPFY)") or qc.worst(msgs) != "ok"
    none = ScanSettings(line="F Kα", sdd=[False] * 4, despike_mode="coherent")
    allsat = Flag("SDD saturated: SDD1, SDD2, SDD3, SDD4", "sdd_saturated", "AURORA check", ("sdd1", "sdd2", "sdd3", "sdd4"))
    msgs = qc.check_zone(build_traces(sums(), none), flags=[allsat])
    assert qc.worst(msgs) == "warn" and qc.headline(msgs) == qc.MSG_NO_DET


def test_compare_easy_unscaled_rows(qapp, tmp_path):
    """A row 680–692 eV of an F K spectrum cannot be scaled (nothing after the edge at 696.7 eV): Easy draws it 0 to 1
    dashed and says why; Advanced keeps today's message and leaves it out."""
    from demo_window import write_john_csv
    from aurora.app.compare import ComparePerspective
    from aurora.app.library import Library
    E = np.linspace(680, 692, 41)
    d = tmp_path / "short" / "S1_F_K"                  # the folder name gives the edge (F K, 696.7 eV)
    d.mkdir(parents=True)
    write_john_csv(d / "S1_F_K_full_reduced.csv", "S1", E, 0.2 + 0.01 * (E - 680))
    mode.set_advanced(False)
    lib = Library()
    cmp_ = ComparePerspective(lib)
    cmp_.resize(1200, 800)
    lib.add_folder(tmp_path / "short")
    for r in lib.requests:
        r.show = True
    lib.changed.emit()
    try:
        cmp_.replot()
        status = cmp_.status.text()
        assert "680–692 eV" in status and "after the edge" in status and "NaN" not in status, status
        assert "drawn scaled 0 to 1 instead (dashed)" in status
        dashed = [it for it in cmp_.plot.listDataItems() if it.opts["pen"].style() == Qt.DashLine]
        assert dashed, "the unscaled row is drawn"
        mode.set_advanced(True)
        cmp_.replot()
        assert "normalisation failed (all NaN)" in cmp_.status.text()
        assert not cmp_.plot.listDataItems()
    finally:
        mode.set_advanced(False)
        dispose(cmp_)


def test_signal_words(qapp, easy_demo):
    from aurora.app.compare import COL
    from aurora.app.vocab import SIGNAL_LABELS, signal_label
    t = easy_demo.compare.table
    cells = [t.item(r, COL["signal"]).text() for r in range(t.rowCount())]
    assert cells and set(cells) <= set(SIGNAL_LABELS.values()) and "sdd-scaled" not in cells
    mode.set_advanced(True)
    try:
        cells = [t.item(r, COL["signal"]).text() for r in range(t.rowCount())]
        assert "fluorescence (sdd-scaled)" in cells
    finally:
        mode.set_advanced(False)
    assert signal_label("sdd3_norm") == "detector 3 (SDD3)" and signal_label("odd") == "odd"
    fit = easy_demo.fit
    assert all("sdd-scaled" not in fit.target.itemText(i) for i in range(fit.target.count()))
    assert fit.select_target("NaF", "sdd-scaled") and fit.combo_key(fit.target) == "NaF [sdd-scaled]"


def test_read_only_beamtime(qapp, tmp_path, monkeypatch, error_dialogs):
    """A beamtime AURORA may not write: the banner says so, the indicator reads read-only, Save ticked opens no dialog
    and offers Show in Compare without saving, Build the report is disabled with the reason."""
    import os
    from demo_window import pump
    from aurora.app.main import MainWindow
    from aurora.app.report import OUT_READ_ONLY
    from aurora.cli import cmd_demo
    from aurora.core import testdata
    from aurora.core.model import Project
    demo = tmp_path / "ro"
    cmd_demo(demo)
    monkeypatch.setenv("AURORA_PROTECT_DIRS", os.pathsep.join(filter(None, [os.environ.get("AURORA_PROTECT_DIRS", ""),
                                                                              str(demo)])))
    from PySide6.QtWidgets import QMessageBox
    boxes = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: boxes.append(a) or QMessageBox.Ok)
    mode.set_advanced(False)
    win = MainWindow()
    win.resize(1500, 940)
    win.show()
    try:
        proj = Project.discover(demo)
        win.home.set_project(proj)
        pump(qapp, 5)
        win.refresh_guide()
        assert win.banner.isVisible() and "read-only for AURORA" in win.banner_text.plain_text()
        assert win.session.read_only and win.session._idle_state() == "read-only"
        ses = win.session
        sd = ses.stack(ses.stack_id(proj.find(testdata.STACK)))
        ses.select_stack(sd.id)
        z = ses.new_zone("1 alpha")
        x0, x1, y0, y1 = sd.bbox
        z.rects.append([x0, x1, y0, y1])
        ses.touch()
        ses.select_zone(z.id)
        win.show_perspective("reduce")
        win.reduce.scan_list.setCurrentRow(0)
        pump(qapp, 5)
        assert win.reduce.add_to_check() == 1
        win.show_perspective("check")
        wait(qapp, 2.0)
        ck = win.check
        before = sorted((q, q.stat().st_mtime_ns) for q in demo.rglob("*") if q.is_file())
        ck.save_ticked()
        win.jobs.wait_idle(20000)
        pump(qapp, 5)
        assert not error_dialogs and not boxes, (error_dialogs, [b[1:] for b in boxes])
        assert ck.queue_note.text().startswith("Not saved: this beamtime folder is read-only")
        assert ck.unsaved_btn.isVisible()
        n0 = len(win.library.requests)
        assert ck.show_in_compare() == 1 and len(win.library.requests) > n0
        assert "not saved" in ck.queue_note.text()
        assert sorted((q, q.stat().st_mtime_ns) for q in demo.rglob("*") if q.is_file()) == before   # nothing written
        win.show_perspective("report")
        pump(qapp, 5)
        assert not win.report.build_btn.isEnabled() and OUT_READ_ONLY in win.report.build_btn.toolTip()
        assert win.report.out_note.isVisible()
        texts = ph.plain_texts(win, qapp)
        assert not ph.violations(texts, ph.internals), ph.violations(texts, ph.internals)
        win.banner_dismiss.click()
        assert not win.banner.isVisible()
    finally:
        close(win)


def test_saved_yesterday_reads_with_its_date(qapp, tmp_path):
    import datetime as dt
    import os
    from aurora.app.main import MainWindow
    from aurora.cli import cmd_demo
    from aurora.core.model import Project
    demo = tmp_path / "d"
    cmd_demo(demo)
    win = MainWindow()
    try:
        win.home.set_project(Project.discover(demo))
        ses = win.session
        ses.new_zone("1 alpha")                           # an edit, written by the autosave
        ses.flush()
        sf = ses.session_file
        assert sf.exists() and ses._idle_state() == f"saved {dt.datetime.now():%H:%M}"
        old = (dt.datetime.now() - dt.timedelta(days=1)).timestamp()
        os.utime(sf, (old, old))
        ses._file_stamp = None
        state = ses._idle_state()
        assert state.startswith("saved ") and len(state) == len("saved 2026-11-09 15:16"), state
    finally:
        close(win)


def test_plain_error_words():
    from aurora.app.widgets import plain_error
    from aurora.core.io.safety import ProtectedPathError
    assert plain_error(ProtectedPathError(13, "x", "p")).startswith("this folder is protected")
    assert plain_error(PermissionError(13, "Permission denied", "C:\\x")).startswith("AURORA may not write here")
    assert plain_error("OSError: [Errno 22] Invalid argument: 'C:\\\\data\\\\f.csv'") == "Invalid argument: C:\\data\\f.csv"
    assert plain_error("aurora.core.stack.StackError: the stack has no energies") == "the stack has no energies"
    assert plain_error(OSError(28, "No space left on device")) == "the disk is full"


# ----------------------------------------------------------------------------- the user guide
def test_quick_start_in_user_guide():
    text = (DOCS / "USER_GUIDE.md").read_text(encoding="utf-8")
    head = text.index("## Quick start for non-specialists")
    assert head < text.index("## 1.")
    sec = text[head:text.index("## 1.")]
    block = sec.split("**What the beamline measured, in five lines.**", 1)[1].split("**Your first spectrum", 1)[0]
    numbered = [ln for ln in block.splitlines() if ln[:2] in ("1.", "2.", "3.", "4.", "5.")]
    assert len(numbered) == 5
    assert "Help > Glossary" in sec
    first = " ".join(text.split("\n\n")[1].split())
    assert "John Bussey" in first and "Dynes" in first and "Help > Glossary" in first


def test_findings_grouped_by_kind(qapp):
    """Several findings of one kind are one parent row in plain words (collapsed beyond three); one finding stays a
    row of its own; the parent row has no Ignore tick, its children keep theirs."""
    from aurora.app.report import FINDING_PLAIN, ReportPerspective
    from aurora.app.session import Session
    from aurora.core import report as core_report
    from aurora.core.report import Finding

    class FakePlan:
        def __init__(self, findings):
            self.findings, self.n_zones = findings, 0

        def by_group(self):
            return {}

        def totals(self, ign):
            return f"{len(self.open_findings(ign))} warnings"

        def open_findings(self, ign):
            return [f for f in self.findings if f.id not in ign]

    assert set(FINDING_PLAIN) == set(core_report.FINDING_KINDS)
    ses = Session()
    rp = ReportPerspective(ses)
    try:
        fs = [Finding(f"u{i}", "uncovered_stack", f"stack {i} is covered by no zone") for i in range(5)]
        fs.append(Finding("g1", "ungrouped", "zone 2 has no group"))
        rp._plan = FakePlan(fs)
        rp._fill_plan()
        lst = rp.preflight_list
        tops = [lst.topLevelItem(i) for i in range(lst.topLevelItemCount())]
        assert len(tops) == 2
        parent = next(t for t in tops if t.childCount())
        assert parent.text(0).startswith("5 measurements (complete stacks) have no sample outlined")
        assert parent.childCount() == 5 and not parent.isExpanded() and parent.data(0, Qt.UserRole) is None
        assert core_report.FINDING_KINDS["uncovered_stack"] in parent.toolTip(0)
        single = next(t for t in tops if not t.childCount())
        assert single.data(0, Qt.UserRole) == "g1" and lst.itemWidget(single, 2) is not None
        child = parent.child(0)
        assert child.data(0, Qt.UserRole) == "u0" and lst.itemWidget(child, 2) is not None
    finally:
        dispose(rp)


def test_term_buttons_are_placed(qapp, easy_demo, monkeypatch):
    """The '?' next to the step-2 preset, the Check view, the Compare signals and the Fit target (Easy mode only)
    open the glossary at their word."""
    from aurora.app import help as help_mod
    win = easy_demo
    buttons = {"xrf-window": (win.reduce.window_card.preset_help, "reduce"), "views": (win.check.view_help, "check"),
               "signal": (win.compare.signal_help, "compare"), "lcf": (win.fit.lcf_help, "fit")}
    seen = []
    monkeypatch.setattr(help_mod, "open_help", lambda w, doc="USER_GUIDE.md", anchor=None: seen.append((doc, anchor)))
    for key, (b, page) in buttons.items():
        win.show_perspective(page)
        wait(qapp, 0.1)
        assert b.isVisible() and b.property("aurora_term") == key, key
        b.click()
        assert seen[-1] == ("GLOSSARY.md", glossary.term(key).anchor)
    mode.set_advanced(True)
    try:
        for key, (b, page) in buttons.items():
            win.show_perspective(page)
            wait(qapp, 0.1)
            assert not b.isVisible(), key
    finally:
        mode.set_advanced(False)
