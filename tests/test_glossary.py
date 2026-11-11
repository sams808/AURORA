"""PL-01 / PL-02 / PL-03: one glossary (aurora.glossary) behind Help > Glossary, the generated GLOSSARY.md, the
'What is …?' tooltip lines, the report's 'How to read the figures' and the jargon guard."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from aurora import glossary as g

ROOT = Path(__file__).resolve().parent.parent
PERSONA_WORDS = ("XAS, edge, E0, K, L3, N4,5, edge step, edge jump, normalisation, pre-edge, post-edge, I0, Au mesh, TEY, "
                 "PFY, IPFY, SDD, SDD1, saturated, kcps, XRF, ROI, fixed bins, channel, Kα, holder, plate, survey, "
                 "stack, scan, footprint, zone, pixel, pitch, de-spike, spike, MAD, bad points, complete, aborted, test, "
                 "dropped, sample sheet, group, LCF, target, component, R-factor, χ², bootstrap, PCA, MCR-ALS, reduced "
                 "spectrum, sdd-scaled, shift, good-I0, calibration, Appendix A").split(", ")


def test_glossary_fields():
    keys = [t.key for t in g.GLOSSARY]
    assert len(keys) == len(set(keys)), "duplicate keys"
    anchors = [t.anchor for t in g.GLOSSARY]
    assert len(anchors) == len(set(anchors)), "duplicate anchors"
    sections = {k for k, _title in g.SECTIONS}
    for t in g.GLOSSARY:
        assert t.key and t.term and t.short and t.why and t.where, t.key
        assert t.section in sections, t.key
        assert t.plain.endswith(".") and len(t.plain) <= 320, (t.key, len(t.plain))
        assert 1 <= len(g._sentences(t.plain)) <= 4, t.key          # one or two sentences (a list counts its items)
        assert g.find(t.short) is t, t.short
        for a in t.aliases:
            assert g.find(a) is t, (a, g.find(a).key if g.find(a) else None, t.key)
    seen = {}
    for t in g.GLOSSARY:
        for w in (t.short, *t.aliases):
            assert seen.setdefault(w.casefold(), t.key) == t.key, f"'{w}' names {seen[w.casefold()]} and {t.key}"


def test_glossary_covers_the_personas():
    missing = [w for w in PERSONA_WORDS if g.find(w) is None]
    assert not missing, missing
    assert g.find("Au mesh").key == "i0" and g.find("sdd-scaled").key == "signal" and g.find("dropped").key == "status"


def test_glossary_voice_and_blank():
    banned = re.compile(r"\b(I|we|our|my)\b")
    names = ("John Bussey", "Dynes", "sgm_report", "2026_CLS", "WSU", "the lab", "jdoe")
    for t in g.GLOSSARY:
        for field in (t.term, t.short, t.plain, t.why, t.where, t.detail, t.report, *t.aliases):
            text = field.replace("I0", "").replace("(I)", "")
            assert not banned.search(text), (t.key, field)
            assert not any(n in field for n in names), (t.key, field)


def test_the_values_it_states_are_aurora_s_own():
    """F K edge near 697 eV and F Kα about 677 eV (xraydb 696.7 / 676.8 eV), 10 eV per channel without a
    calibration, the Easy scaling ranges, 200 bootstrap draws: the numbers the code uses."""
    from aurora.app.mode import EASY_DEFAULTS
    from aurora.core.constants import UNCALIBRATED_EV_PER_CH
    xraydb = pytest.importorskip("xraydb")
    assert round(xraydb.xray_edge("F", "K").energy) == 697 and "near 697 eV" in g.term("edge").plain
    assert round(xraydb.xray_lines("F")["Ka1"].energy) == 677 and "about 677 eV" in g.term("emission-line").plain
    assert round(xraydb.xray_lines("O")["Ka1"].energy) == 525 and "about 525 eV" in g.term("emission-line").plain
    assert UNCALIBRATED_EV_PER_CH == 10.0 and "10 eV" in g.term("channel").plain
    c = EASY_DEFAULTS["compare"]
    assert tuple(c["pre"]) == (-20.0, -5.0) and tuple(c["post"]) == (15.0, 35.0)
    assert "20 to 5 eV below E0 and 15 to 35 eV above" in g.term("pre-post").plain
    assert EASY_DEFAULTS["fit"]["bootstrap"] == 200 and "200 in Easy mode" in g.term("bootstrap").plain


def test_bundled_glossary_is_generated():
    md = (ROOT / "aurora" / "resources" / "docs" / "GLOSSARY.md").read_text(encoding="utf-8")
    assert md == g.render_markdown(), "GLOSSARY.md is stale: python -m aurora.glossary --write"
    r = subprocess.run([sys.executable, "-m", "aurora.glossary", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    outside = re.sub(r"`[^`]*`", "", md)
    assert "<" not in outside, "angle brackets only inside backticks"
    assert "**" not in md, "no bold names (the bold-name parity test does not read GLOSSARY.md)"


def test_a_stale_glossary_is_caught(tmp_path, monkeypatch):
    stale = tmp_path / "GLOSSARY.md"
    stale.write_text("# old\n", encoding="utf-8")
    monkeypatch.setattr(g, "markdown_path", lambda: stale)
    assert g.main(["--check"]) == 1
    assert g.main(["--write"]) == 0 and g.main(["--check"]) == 0


def test_glossary_anchors_match_help_slug():
    pytest.importorskip("PySide6")
    from aurora.app import help as help_mod
    heads = set(help_mod.headings("GLOSSARY.md"))
    for t in g.GLOSSARY:
        assert t.anchor == help_mod.slug(t.term), t.term
        assert t.anchor in heads, t.anchor
    assert ("GLOSSARY.md", "Glossary") == help_mod.DOCS[1][:2]


def test_report_paragraph_is_the_glossary():
    md = g.render_markdown()
    for key in g.REPORT_ORDER:
        t = g.term(key)
        assert t.report and t.report in g.report_paragraph()
        assert re.sub(r"\s+", " ", t.report) in re.sub(r"\s+", " ", md), key       # GLOSSARY.md says it too
        if key != "report":
            words = [t.short, *t.aliases]
            assert any(w.casefold() in t.report.casefold() for w in words), key
    from aurora.core.report import reading_lines
    lines = reading_lines()
    assert all(len(ln) <= 110 for ln in lines)                                   # the cover cuts at 110 characters
    assert " ".join(ln.strip() for ln in lines) == " ".join(g.report_paragraph())


def test_help_terms_resolve():
    pytest.importorskip("PySide6")
    from aurora.app.help_text import HELP, HELP_TERMS
    for key, terms in HELP_TERMS.items():
        assert key in HELP, key
        assert 1 <= len(terms) <= 2, key
        for k in terms:
            g.term(k)


def test_what_is_lines():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QLabel
    QApplication.instance() or QApplication([])
    from aurora.app.help_text import HELP, explain
    lab = explain(QLabel(), "home.col.flags")
    tip = lab.toolTip()
    assert tip.endswith(g.what_is("saturated") + "\n" + g.what_is("i0"))
    assert g.what_is("saturated").startswith("What is a saturated detector? A detector that receives more X-rays")
    assert lab.statusTip() == HELP["home.col.flags"][0].split("\n")[0]
    wt = lab.whatsThis()
    assert "Glossary: saturated detector, I0" in wt and wt.endswith("More: manual §2")
    import gc
    from PySide6.QtCore import QCoreApplication, QEvent
    lab.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


def test_jargon_guard():
    assert g.unexplained("divided by I0") == ["I0"]
    assert g.unexplained("divided by the beam intensity (I0)") == []
    assert g.unexplained("an electron-yield spectrum (TEY)") == []            # a hyphen counts as a space
    assert g.unexplained("TEY", tooltip=g.what_is("tey")) == []                # the tooltip explains it
    assert g.unexplained("a TEY trace", linked=("tey",)) == []                 # a linked term explains it
    assert g.unexplained("χ²ν 1.9") == ["χ²"]                                   # Advanced only: never explained
    assert g.unexplained("normalised, offset") == ["normalis"]
    assert g.unexplained("scaled so that it is normalised") == []
    assert g.unexplained("SDD1-4") == ["SDD1"] and g.unexplained("detectors SDD1-4") == []
