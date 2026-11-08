"""I44: the deep self-test. From source through Help > Run self-test (the subprocess `aurora --selftest --out`): at least
25 named and timed checks, all ok, exit code 0, the JSON keys; with matplotlib's SVG backend unimportable exactly the
report check fails and the exit code is 1 (the number of failed checks)."""
import importlib.abc
import json
import os
import sys

import pytest

pytest.importorskip("pyqtgraph")

from aurora import __version__  # noqa: E402

KEYS = {"version", "build", "platform", "machine", "python", "qt", "qt_platform", "native", "cwd", "credits", "checks"}
REPORT_CHECK = "report (PDF, PNG, SVG, CSV), provenance and verify"


@pytest.fixture(scope="module")
def qapp():
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


def test_help_run_selftest_runs_every_check_from_source(qapp, tmp_path, monkeypatch):
    """Help > Run self-test… for real: the background job starts `python -m aurora --selftest --out <json>` in a
    separate process, and the summary dialog reports every check ok."""
    from aurora.app import diagnostics
    from aurora.app.main import MainWindow
    from aurora.credits import CREDITS
    monkeypatch.setattr(diagnostics, "selftest_dir", lambda: tmp_path / "selftest")     # never the user's data folder
    win = MainWindow()
    try:
        win.selftest_action.trigger()
        assert win.jobs.wait_idle(600_000), "the self-test did not finish within 10 min"
        qapp.processEvents()
        summary = win.selftest_dialog.summary
        checks = summary["checks"]
        failed = [(c["name"], c["detail"]) for c in checks if not c["ok"]]
        assert not failed, failed
        assert summary["returncode"] == 0 and summary["ok"] is True
        assert len(checks) >= 25 and len(checks) == diagnostics.EXPECTED_CHECKS, len(checks)
        assert KEYS <= set(summary), sorted(KEYS - set(summary))
        assert summary["version"] == __version__ and summary["credits"] == CREDITS and summary["qt"]
        assert summary["qt_platform"] == "offscreen" and summary["native"] is False and summary["cwd"]
        assert isinstance(summary["build"], dict)
        names = [c["name"] for c in checks]
        assert len(set(names)) == len(names), "two checks share a name"
        for c in checks:
            assert {"name", "ok", "seconds", "detail"} <= set(c) and c["seconds"] >= 0, c
        assert REPORT_CHECK in names and "manuals open and their images resolve" in names
        n = len(checks)
        assert f"{n} of {n} checks ok" in win.selftest_dialog.text()
        written = json.loads((tmp_path / "selftest" / os.path.basename(summary["json"])).read_text(encoding="utf-8"))
        assert written["checks"] == checks
    finally:
        win.jobs.wait_idle(10_000)
        dlg = getattr(win, "selftest_dialog", None)
        if dlg is not None:
            dlg.close()
        win.close()


class _NoSvgBackend(importlib.abc.MetaPathFinder):
    """matplotlib.backends.backend_svg cannot be imported, as in a frozen build that left it out."""

    def find_spec(self, name, path=None, target=None):
        if name == "matplotlib.backends.backend_svg":
            raise ModuleNotFoundError("No module named 'matplotlib.backends.backend_svg' (simulated frozen build)")
        return None


def test_without_the_svg_backend_exactly_the_report_check_fails(tmp_path, monkeypatch):
    import matplotlib.backend_bases as bb
    from aurora import selftest
    from aurora.core import xrf_windows
    for var in ("AURORA_SETTINGS_DIR", "AURORA_LOG_DIR", "QT_QPA_PLATFORM", "QT_QPA_FONTDIR"):
        if var in os.environ:                  # the self-test sets them: put them back afterwards
            monkeypatch.setenv(var, os.environ[var])
        else:
            monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "meta_path", [_NoSvgBackend(), *sys.meta_path])
    monkeypatch.delitem(sys.modules, "matplotlib.backends.backend_svg", raising=False)
    monkeypatch.setitem(bb._default_backends, "svg", "matplotlib.backends.backend_svg")    # forget the cached class
    out = tmp_path / "selftest.json"
    try:
        rc = selftest.run(out=out, gui=False)
    finally:
        xrf_windows.clear_memory()
        xrf_windows.forget_projects()
    d = json.loads(out.read_text(encoding="utf-8"))
    failed = [c["name"] for c in d["checks"] if not c["ok"]]
    assert failed == [REPORT_CHECK], failed
    assert rc == 1 and d["ok"] is False
    report = next(c for c in d["checks"] if c["name"] == REPORT_CHECK)
    assert "backend_svg" in report["detail"] or "backend_svg" in report.get("traceback", "")
    assert len(d["checks"]) >= 25 and KEYS <= set(d)


def test_the_exit_code_counts_the_failed_checks(tmp_path, monkeypatch):
    from aurora import selftest
    for var in ("AURORA_SETTINGS_DIR", "AURORA_LOG_DIR"):
        if var in os.environ:
            monkeypatch.setenv(var, os.environ[var])
        else:
            monkeypatch.delenv(var, raising=False)

    def two_fail(r, ctx, base):
        r.check("passes", lambda: "fine")
        r.check("fails once", lambda: 1 / 0)
        r.check("fails twice", lambda: [][1])
    monkeypatch.setattr(selftest, "_core_checks", two_fail)
    rc = selftest.run(out=tmp_path / "s.json", gui=False)
    d = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert rc == 2 and [c["ok"] for c in d["checks"]] == [True, False, False]
    assert d["checks"][1]["detail"].startswith("ZeroDivisionError") and "traceback" in d["checks"][1]
    from aurora.cli import main
    monkeypatch.setattr(selftest, "run", lambda **kw: 3)
    assert main(["--selftest", "--no-gui"]) == 3
