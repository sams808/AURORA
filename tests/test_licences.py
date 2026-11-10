"""LICENSE in the repository, third-party notices generated for the bundle, both shown by Help > Licences (I48)."""
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packaging"))
import third_party_licenses as tpl  # noqa: E402


def test_licence_file():
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License") and "Copyright (c) 2026 Sami Soudani" in text and "Sam " not in text


def test_pyproject_licence_is_spdx():
    d = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert d["project"]["license"] == "MIT" and d["project"]["license-files"] == ["LICENSE"]
    assert not any(c.startswith("License ::") for c in d["project"].get("classifiers", []))   # PEP 639: no both
    req = d["build-system"]["requires"][0]
    assert req.startswith("setuptools>=") and int(req.split(">=")[1].split(".")[0]) >= 77


@pytest.mark.parametrize("name", ["numpy", "scipy", "PySide6", "h5py", "matplotlib", "pyqtgraph", "xraydb", "platformdirs"])
def test_every_main_dependency_has_its_licence_text(name):
    entries = {e.name.lower(): e for e in tpl.generate()}
    e = entries[name.lower()]
    assert e.version and e.licence
    assert len(e.text) > 200 and not e.text.startswith("(no licence file"), e.text[:200]


def test_qt_is_listed_under_the_lgpl_not_the_commercial_licence():
    entries = {e.name.lower(): e for e in tpl.generate()}
    for name in ("pyside6", "shiboken6"):
        t = entries[name].text
        assert "GNU LESSER GENERAL PUBLIC LICENSE" in t and "Version 3" in t
        assert "Qt Commercial" not in t and "LicenseRef-Qt-Commercial" not in t


def test_build_tools_and_aurora_are_not_third_party_notices():
    names = {tpl._norm(e.name) for e in tpl.generate()}
    assert "aurora-sgm" not in names and "pyinstaller" not in names and "pip" not in names


def test_only_the_bundled_distributions(tmp_path):
    only = tpl.bundled_distributions(["numpy._core", "matplotlib.pyplot", "PySide6.QtCore", "shiboken6", "json", "aurora"])
    assert {"numpy", "matplotlib", "pyside6", "shiboken6"} <= only and "scipy" not in only
    out = tpl.write(tmp_path / "THIRD_PARTY_LICENSES.txt", only=only, version="x")
    text = out.read_text(encoding="utf-8")
    assert text.startswith("THIRD-PARTY LICENCES -- AURORA x") and "bundled into the application" in text
    assert "\nnumpy\n" in text and "\nscipy\n" not in text and "Sami Soudani" in text
    assert "John Bussey" in text and "James J. Dynes" in text


def test_licence_texts_ship_with_the_spec_and_the_installers():
    spec = (ROOT / "packaging" / "aurora.spec").read_text(encoding="utf-8")
    assert 'LIC = os.path.join("aurora", "resources", "licenses")' in spec
    for item in ('os.path.join(ROOT, "LICENSE"), LIC', 'os.path.join(ROOT, "CREDITS.md"), LIC',
                 'os.path.join(SPECPATH, "LICENSES", "*.txt")', 'THIRD_PARTY_LICENSES.txt'):
        assert item in spec, item
    assert "shutil.copy2(src, out)" in spec                     # next to the exe on Windows
    assert "LicenseFile=..\\LICENSE" in (ROOT / "packaging" / "aurora.iss").read_text(encoding="utf-8")
    dmg = (ROOT / "packaging" / "make_dmg.sh").read_text(encoding="utf-8")
    assert '"$ROOT/LICENSE" "$ROOT/CREDITS.md"' in dmg and "THIRD_PARTY_LICENSES.txt" in dmg
    lgpl = (ROOT / "packaging" / "LICENSES" / "LGPL-3.0.txt").read_text(encoding="utf-8")
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in lgpl and "Version 3, 29 June 2007" in lgpl


def test_help_licences_shows_the_licence(monkeypatch):
    pytest.importorskip("PySide6")
    from aurora.app.main import create_app
    create_app(["aurora-tests"])
    from aurora.app.diagnostics import LicencesDialog, THIRD_PARTY_NOTE
    d = LicencesDialog()
    try:
        text = d.text()
        assert (ROOT / "LICENSE").read_text(encoding="utf-8").strip() in text
        assert "James J. Dynes" in text                        # CREDITS.md
        # in a source checkout the third-party notices are generated at build time
        assert THIRD_PARTY_NOTE in text and "generated at build time" in text
    finally:
        d.deleteLater()


def test_help_licences_reads_the_bundled_folder(monkeypatch, tmp_path):
    """In a frozen build the dialog reads aurora/resources/licenses (where the spec puts the three files)."""
    pytest.importorskip("PySide6")
    from aurora.app.main import create_app
    create_app(["aurora-tests"])
    from aurora.app import diagnostics
    lic = tmp_path / "licenses"
    lic.mkdir()
    for name, body in (("LICENSE", "MIT License\nCopyright (c) 2026 Sami Soudani\n"), ("CREDITS.md", "credits"),
                       ("THIRD_PARTY_LICENSES.txt", "THIRD-PARTY LICENCES -- AURORA test\nnumpy\n")):
        (lic / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(diagnostics, "_licence_dirs", lambda: [lic])
    d = diagnostics.LicencesDialog()
    try:
        assert [t for t, _x in d.texts] == ["AURORA licence", "Credits", "Third-party licences"]
        assert "THIRD-PARTY LICENCES -- AURORA test" in d.text()
    finally:
        d.deleteLater()


def test_a_licence_named_by_its_suffix_is_included():
    """packaging ships LICENSE plus LICENSE.APACHE and LICENSE.BSD (its LICENSE only points at them); fontTools adds
    LICENSE.external. Code and data files with 'license' in the name stay out."""
    for name in ("LICENSE", "LICENSE.txt", "COPYING.md", "LICENSE.APACHE", "LICENSE.BSD", "LICENSE.external"):
        assert tpl._is_licence_file(name), name
    for name in ("license.py", "licenses.json", "LICENSE.html", "license_expression.pyc", "README.md"):
        assert not tpl._is_licence_file(name), name
    dist = tpl.distributions().get("packaging")
    if dist is None or not any(f.name == "LICENSE.BSD" for f in dist.files or []):
        pytest.skip("this environment has no 'packaging' with LICENSE.BSD")
    text = tpl.entry(dist).text
    assert "Redistribution and use in source and binary forms" in text and "Apache License" in text


def test_the_xraydb_note_matches_its_licence():
    entries = {e.name.lower(): e for e in tpl.generate()}
    assert "MIT" in entries["xraydb"].licence
    head = tpl.header("x", [], "scope")
    assert "xraydb: MIT licence" in head and "public domain" not in head.lower()


def test_the_credits_carry_no_internal_notes():
    """CREDITS.md ships next to AURORA.exe, in the disk image and in Help > Licences: no to-do, no library list of
    its own (THIRD_PARTY_LICENSES.txt is generated from what is bundled)."""
    text = (ROOT / "CREDITS.md").read_text(encoding="utf-8")
    assert "to be confirmed" not in text.lower() and "todo" not in text.lower() and "licences to list" not in text
    assert "Third-party libraries (" not in text and "THIRD_PARTY_LICENSES.txt" in text
    assert "version 2 of John Bussey's SGM XAS GUI" in text
    assert "James J. Dynes' SGM_Beamline_Analysis Jupyter notebooks" in text


# what the reviewers found absent from AURORA's code: the pattern that would show it, and the credit word
ABSENT = {"lmfit": r"^\s*(?:import|from) lmfit", "RIXS": r"(?i)rixs",
          "triangulation": r"(?i)tripcolor|delaunay|triangulat", "email": r"(?i)\.eml\b|e-?mail draft",
          "hole detection": r"(?i)hole detect|detect_holes", "recalibration": r"(?i)recalibrat"}


def test_the_credits_table_claims_only_what_aurora_has():
    """The column 'What AURORA takes from it' names no feature that is not in the code; those belong to the column
    'Left in the original'."""
    import re
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "aurora").rglob("*.py"))
    rows = [ln.split("|") for ln in (ROOT / "CREDITS.md").read_text(encoding="utf-8").splitlines()
            if ln.startswith("| ") and not ln.startswith("| Who")]
    assert rows and all(len(r) >= 5 for r in rows)
    takes = " ".join(r[2] for r in rows)
    for word, pattern in ABSENT.items():
        if not re.search(pattern, code, flags=re.M):
            assert word.lower() not in takes.lower(), f"CREDITS.md credits AURORA with {word!r}, which it does not have"


def test_help_licences_shows_the_credits_as_a_table():
    pytest.importorskip("PySide6")
    from aurora.app.main import create_app
    create_app(["aurora-tests"])
    from aurora.app.diagnostics import LicencesDialog
    d = LicencesDialog()
    try:
        tab = next(d.tabs.widget(i) for i in range(d.tabs.count()) if d.tabs.tabText(i) == "Credits")
        shown = tab.toPlainText()
        assert "James J. Dynes" in shown and "|---|" not in shown and "# Credits" not in shown
    finally:
        d.deleteLater()
