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
