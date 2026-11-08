"""The frozen app builds reports (I43): matplotlib's svg and pdf backends in the spec and in the built archive, a
persistent font cache, and the report check the self-test and CI run on the frozen app's output."""
import ast
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packaging" / "aurora.spec"
PYZ_TOC = ROOT / "build" / "aurora" / "PYZ-00.toc"         # packaging\build_win.bat: --workpath build
DIST = ROOT / "dist" / "AURORA"
# This spec writes _build.json into every bundle; a build/ or dist/ left by an older spec (a checkout built before
# WP8a) says nothing about the current spec, so the checks on the built files wait for the next build. CI asserts
# _build.json in the bundle and test_spec_entry_script_and_bundled_extras that the spec writes it.
BUILD_JSON = DIST / "_internal" / "aurora" / "_build.json"
OLD_BUILD = not BUILD_JSON.is_file()
OLD_REASON = "no local build of this spec (dist/AURORA/_internal/aurora/_build.json absent): run packaging\\build_win.bat"


def _spec_tree() -> ast.Module:
    return ast.parse(SPEC.read_text(encoding="utf-8"))


def _call(name: str) -> ast.Call:
    calls = [n for n in ast.walk(_spec_tree()) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == name]
    assert len(calls) == 1, f"{len(calls)} {name}() calls in the spec"
    return calls[0]


def test_spec_bundles_the_matplotlib_backends():
    a = _call("Analysis")
    hooks = ast.literal_eval(next(k.value for k in a.keywords if k.arg == "hooksconfig"))
    backends = [b.lower() for b in hooks["matplotlib"]["backends"]]
    assert {"agg", "svg", "pdf"} <= set(backends)
    text = SPEC.read_text(encoding="utf-8")
    hidden = text[text.index("hiddenimports = ("):text.index("a = Analysis(")]
    for mod in ("matplotlib.backends.backend_svg", "matplotlib.backends.backend_pdf", "matplotlib.backends.backend_agg"):
        assert f'"{mod}"' in hidden, mod


def test_spec_entry_script_and_bundled_extras():
    text = SPEC.read_text(encoding="utf-8")
    a = _call("Analysis")
    assert "__main__.py" in ast.unparse(a.args[0])
    assert "specutil.version()" in text and "specutil.version_info(" in text and "specutil.plist(" in text
    assert "write_build_json" in text and '(BUILD_JSON, "aurora")' in text
    assert 'os.path.join("aurora", "resources", "licenses")' in text and "THIRD_PARTY_LICENSES.txt" in text


@pytest.mark.skipif(not PYZ_TOC.exists(), reason="no local PyInstaller build (build/aurora/PYZ-00.toc)")
@pytest.mark.skipif(OLD_BUILD, reason=OLD_REASON)
def test_the_built_archive_holds_the_backends():
    toc = PYZ_TOC.read_text(encoding="utf-8", errors="replace")
    for mod in ("matplotlib.backends.backend_svg", "matplotlib.backends.backend_pdf", "aurora.core.reportcheck"):
        assert f"'{mod}'" in toc, mod


def test_spec_keeps_build_tools_out_of_the_bundle():
    """numpy._pyinstaller (via collect_submodules('numpy')) once pulled PyInstaller (GPL), setuptools, altgraph and pefile
    into the application."""
    text = SPEC.read_text(encoding="utf-8")
    assert "*BUILD_TOOLS]" in text and 'collect_submodules("numpy") if not m.startswith(NUMPY_BUILD_ONLY' in text
    for name in ("PyInstaller", "setuptools", "pkg_resources", "altgraph", "pefile", "numpy._pyinstaller"):
        assert f'"{name}"' in text, name
    # numpy.f2py stays: scipy's `from numpy import *` imports it (a frozen build without it failed every check)
    assert 'NUMPY_BUILD_ONLY = ("numpy._pyinstaller",)' in text


@pytest.mark.skipif(not PYZ_TOC.exists(), reason="no local PyInstaller build (build/aurora/PYZ-00.toc)")
@pytest.mark.skipif(OLD_BUILD, reason=OLD_REASON)
def test_the_built_archive_has_no_build_tools():
    toc = PYZ_TOC.read_text(encoding="utf-8", errors="replace")
    for top in ("PyInstaller", "setuptools", "pkg_resources", "altgraph", "pefile", "numpy._pyinstaller"):
        assert f"('{top}'" not in toc and f"('{top}." not in toc, top
    assert "('numpy.f2py'" in toc


@pytest.mark.skipif(not (DIST / "AURORA.exe").exists(), reason="no local frozen Windows build (dist/AURORA)")
@pytest.mark.skipif(OLD_BUILD, reason=OLD_REASON)
def test_the_frozen_folder_carries_licences_provenance_and_tls():
    for rel in ("AURORA.exe", "aurora-cli.exe", "LICENSE", "CREDITS.md", "THIRD_PARTY_LICENSES.txt", "LICENSES/LGPL-3.0.txt",
                "_internal/aurora/_build.json", "_internal/aurora/resources/licenses/THIRD_PARTY_LICENSES.txt",
                "_internal/aurora/resources/licenses/LICENSE"):
        assert (DIST / rel).is_file(), rel
    tls = list((DIST / "_internal" / "PySide6" / "plugins" / "tls").glob("*.dll"))
    assert any("schannel" in p.name or "openssl" in p.name for p in tls), tls
    import json
    b = json.loads((DIST / "_internal" / "aurora" / "_build.json").read_text(encoding="utf-8"))
    import aurora
    assert b["version"] == aurora.__version__ and b["python"].startswith("3.12.") and b["pyside6"]
    third = (DIST / "THIRD_PARTY_LICENSES.txt").read_text(encoding="utf-8")
    for name in ("numpy", "scipy", "PySide6", "matplotlib", "pyqtgraph", "xraydb", "h5py", "platformdirs"):
        assert f"\n{name}\n" in third, name
    for tool in ("pyinstaller", "setuptools", "altgraph", "pefile", "aurora-sgm"):
        assert f"\n{tool}\n" not in third.lower(), tool


# ----------------------------------------------------------------------------- persistent font cache
def test_frozen_app_keeps_the_matplotlib_font_cache(monkeypatch, tmp_path):
    import platformdirs
    import aurora.__main__ as entry
    calls = []

    def fake_cache_dir(appname=None, appauthor=None, *a, **kw):
        calls.append((appname, appauthor))
        return str(tmp_path / "cache" / "AURORA")
    monkeypatch.setattr(platformdirs, "user_cache_dir", fake_cache_dir)
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "rthook temp"))      # what PyInstaller's runtime hook sets
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    entry.prepare_environment()
    assert calls == [("AURORA", False)]
    assert Path(os.environ["MPLCONFIGDIR"]) == tmp_path / "cache" / "AURORA" / "matplotlib"
    assert (tmp_path / "cache" / "AURORA" / "matplotlib").is_dir()


def test_from_source_matplotlib_keeps_its_own_default(monkeypatch, tmp_path):
    import aurora.__main__ as entry
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "mine"))
    entry.prepare_environment()
    assert os.environ["MPLCONFIGDIR"] == str(tmp_path / "mine")


def test_the_real_cache_folder_is_the_users():
    from platformdirs import user_cache_dir
    d = Path(user_cache_dir("AURORA", appauthor=False))
    assert "AURORA" in d.parts
    if sys.platform.startswith("win"):
        assert d.parts[-1] == "Cache" and Path(os.environ["LOCALAPPDATA"]) in d.parents
    elif sys.platform == "darwin":
        assert d == Path.home() / "Library" / "Caches" / "AURORA"


# ----------------------------------------------------------------------------- the report check
def _pdf(path: Path, n: int) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(path) as pdf:
        for i in range(n):
            fig = plt.figure(figsize=(3, 2))
            fig.text(0.5, 0.5, f"page {i + 1}")
            pdf.savefig(fig)
            plt.close(fig)
    return path


def test_report_check(tmp_path):
    from aurora.core.reportcheck import check, main, pdf_pages
    out = tmp_path / "report"
    (out / "figures").mkdir(parents=True)
    (out / "pages").mkdir()
    assert pdf_pages(_pdf(out / "r.pdf", 6)) == 6
    (out / "figures" / "a.png").write_bytes(b"png")
    (out / "figures" / "a.svg").write_text("<svg/>", encoding="utf-8")
    (out / "pages" / "page_001.png").write_bytes(b"png")          # page previews need no SVG
    assert check(out)["problems"] == [] and main([str(out)]) == 0
    (out / "figures" / "b.png").write_bytes(b"png")
    r = check(out)
    assert r["missing_svg"] == [out / "figures" / "b.png"] and main([str(out)]) == 1
    (out / "figures" / "b.png").unlink()
    _pdf(out / "r.pdf", 3)
    assert any("3 pages" in p for p in check(out)["problems"])


def test_selftest_report_check_requires_svg_and_five_pages():
    text = (ROOT / "aurora" / "selftest.py").read_text(encoding="utf-8")
    body = text[text.index("    def report():"):text.index('r.check("report (PDF, PNG, SVG, CSV)", report)')]
    assert "check(out, min_pages=5)" in body and "res.pages >= 5" in body and "pdf_pages(res.pdf) >= 5" in body


def test_selftest_report_check_fails_without_the_svg_backend(monkeypatch, tmp_path):
    """With matplotlib's svg backend unloadable (a frozen build without it), the report build fails or the check
    finds the PNGs without SVG: the self-test's report check cannot pass on such a build."""
    import importlib
    import matplotlib.backend_bases as bb
    from aurora.cli import cmd_demo
    from aurora.core import testdata
    from aurora.core.model import Project
    from aurora.core.report import ReportBuilder
    from aurora.core.reportcheck import check
    root = tmp_path / "demo"
    cmd_demo(root)
    proj = Project.discover(root)
    zf = testdata.make_fake_zones(proj)
    real = importlib.import_module

    def no_svg(name, *a, **kw):
        if name == "matplotlib.backends.backend_svg":
            raise ModuleNotFoundError("No module named 'matplotlib.backends.backend_svg' (simulated frozen build)")
        return real(name, *a, **kw)
    monkeypatch.setattr(importlib, "import_module", no_svg)
    monkeypatch.setitem(bb._default_backends, "svg", "matplotlib.backends.backend_svg")    # forget the cached class
    monkeypatch.delitem(sys.modules, "matplotlib.backends.backend_svg", raising=False)
    try:
        ReportBuilder(proj, zf, tmp_path / "rep", title="x", png_dpi=60).build()
    except ModuleNotFoundError as err:                  # the frozen failure: an exception at the first figure
        assert "backend_svg" in str(err)
        return
    assert check(tmp_path / "rep")["problems"]


def test_ci_builds_a_frozen_report():
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert len(re.findall(r"- name: Frozen report", text)) == 2                  # Windows and macOS
    assert text.count("aurora.core.reportcheck") == 2 and "--min-pages 5" in text
    assert "make_fake_zones" in text
