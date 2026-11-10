"""Headless command line that always answers (I45): console twin, exit codes, logs, UTF-8, folder argument."""
import ast
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aurora import cli

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _log_dir(tmp_path, monkeypatch):
    """Every command here logs into the test's own folder; the handlers attached are removed afterwards."""
    monkeypatch.setenv("AURORA_LOG_DIR", str(tmp_path / "logs"))
    yield tmp_path / "logs"
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_aurora", False) or getattr(h, "_aurora_console", False):
            root.removeHandler(h)
            h.close()


@pytest.fixture
def demo(tmp_path) -> Path:
    root = tmp_path / "demo beamtime"
    assert cli.main(["demo", str(root)]) == 0
    return root


def test_a_folder_or_h5_as_first_argument_opens_the_gui(demo, monkeypatch):
    ran = []
    import aurora.app.main as app_main
    monkeypatch.setattr(app_main, "run", lambda project=None: ran.append(project) or 0)
    assert cli.main([str(demo)]) == 0 and ran == [demo]
    h5 = next(demo.rglob("*.h5"))
    assert cli.main([str(h5)]) == 0 and ran[-1] == h5


def test_a_failing_command_returns_1_and_logs_the_traceback(_log_dir, capsys):
    rc = cli.main(["report", "C:/does/not/exist"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "Traceback (most recent call last)" in err and "aurora report failed" in err
    log = (_log_dir / "aurora.log").read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in log and "aurora report failed" in log


def test_every_command_logs_to_the_file(_log_dir, demo):
    assert cli.main(["project", str(demo)]) == 0
    text = (_log_dir / "aurora.log").read_text(encoding="utf-8")
    assert "AURORA " in text and "command project" in text


def test_no_stdout_no_crash(demo, monkeypatch):
    """The windowed frozen app has sys.stdout = sys.stderr = None."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert cli.main(["project", str(demo)]) == 0
    assert cli.main(["report", "C:/does/not/exist"]) == 1
    assert not any(getattr(h, "_aurora_console", False) for h in logging.getLogger().handlers)


def test_logging_follows_a_new_log_folder(tmp_path, monkeypatch):
    from aurora.logsetup import setup_logging
    monkeypatch.setenv("AURORA_LOG_DIR", str(tmp_path / "a"))
    assert setup_logging() == tmp_path / "a" / "aurora.log"
    monkeypatch.setenv("AURORA_LOG_DIR", str(tmp_path / "b"))
    assert setup_logging() == tmp_path / "b" / "aurora.log"
    logging.getLogger("aurora.test").warning("line for b")
    ours = [h for h in logging.getLogger().handlers if getattr(h, "_aurora", False)]
    assert len(ours) == 1 and Path(ours[0].baseFilename) == tmp_path / "b" / "aurora.log"
    assert "line for b" in (tmp_path / "b" / "aurora.log").read_text(encoding="utf-8")
    assert "line for b" not in (tmp_path / "a" / "aurora.log").read_text(encoding="utf-8")


def test_a_test_run_never_writes_the_users_log(monkeypatch):
    from aurora.logsetup import log_dir
    monkeypatch.delenv("AURORA_LOG_DIR", raising=False)
    assert os.environ.get("PYTEST_CURRENT_TEST")
    d = log_dir()
    from platformdirs import user_log_dir
    assert d != Path(user_log_dir("AURORA", appauthor=False)) and "test" in d.name.lower()


def test_utf8_output_through_a_pipe(demo, tmp_path):
    """`python -m aurora project <demo>` into a pipe (cp1252 on Windows by default) prints 'F Kα' as UTF-8."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH", "")]))
    env["AURORA_LOG_DIR"] = str(tmp_path / "sub logs")
    p = subprocess.run([sys.executable, "-m", "aurora", "project", str(demo)], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=env, cwd=str(tmp_path), timeout=120)
    assert p.returncode == 0, p.stderr.decode("utf-8", "replace")
    out = p.stdout.decode("utf-8")
    assert "F Kα" in out


def test_main_is_guarded_in_a_subprocess(tmp_path):
    """A headless failure in a fresh interpreter: exit code 1, the traceback on stderr, no hang."""
    env = dict(os.environ, PYTHONPATH=str(ROOT), AURORA_LOG_DIR=str(tmp_path / "logs"))
    p = subprocess.run([sys.executable, "-m", "aurora", "report", str(tmp_path / "no such beamtime")],
                       capture_output=True, env=env, timeout=120)
    assert p.returncode == 1
    assert b"Traceback" in p.stderr
    assert b"Traceback" in (tmp_path / "logs" / "aurora.log").read_bytes()


# ----------------------------------------------------------------------------- the console twin in the spec
def _spec_calls(name: str) -> list[ast.Call]:
    tree = ast.parse((ROOT / "packaging" / "aurora.spec").read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == name]


def _kw(call: ast.Call, key: str):
    for k in call.keywords:
        if k.arg == key:
            return ast.literal_eval(k.value)
    raise KeyError(key)


def test_the_spec_has_a_console_twin_in_the_same_collect():
    exes = {_kw(c, "name"): _kw(c, "console") for c in _spec_calls("EXE")}
    assert exes == {"AURORA": False, "aurora-cli": True}
    collects = _spec_calls("COLLECT")
    assert len(collects) == 1
    names = {n.id for a in collects[0].args for n in ast.walk(a) if isinstance(n, ast.Name)}
    tree = ast.parse((ROOT / "packaging" / "aurora.spec").read_text(encoding="utf-8"))
    targets = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", "") == "EXE":
            targets[_kw(node.value, "name")] = node.targets[0].id
    assert targets["AURORA"] in names and targets["aurora-cli"] in names


# ----------------------------------------------------------------------------- text IO always names its encoding
TEXT_MODES = {"r", "w", "a", "x", "r+", "w+", "a+", "x+", "rt", "wt", "at", "xt", "rt+", "wt+", "at+"}


def _missing_encoding(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        kws = {k.arg for k in n.keywords}
        f = n.func
        where = f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path.name}:{n.lineno}"
        if isinstance(f, ast.Attribute) and f.attr in ("read_text", "write_text"):
            # Path.read_text(encoding=None, errors=None) / write_text(data, encoding=None, ...)
            need = 0 if f.attr == "read_text" else 1
            if "encoding" not in kws and len(n.args) <= need:
                bad.append(f"{where} {f.attr} without an encoding")
        elif (isinstance(f, ast.Name) and f.id == "open") or (isinstance(f, ast.Attribute) and f.attr == "open"
                                                                and getattr(f.value, "id", "") == "io"):
            mode = "r"
            if len(n.args) >= 2:
                mode = n.args[1].value if isinstance(n.args[1], ast.Constant) else None
            for k in n.keywords:
                if k.arg == "mode":
                    mode = k.value.value if isinstance(k.value, ast.Constant) else None
            if mode is None:
                bad.append(f"{where} open with a computed mode")
            elif "b" not in mode and "encoding" not in kws and len(n.args) < 4:
                bad.append(f"{where} text-mode open without an encoding")
        elif isinstance(f, ast.Attribute) and f.attr == "open" and n.args and isinstance(n.args[0], ast.Constant) \
                and n.args[0].value in TEXT_MODES and "encoding" not in kws:
            bad.append(f"{where} Path.open({n.args[0].value!r}) without an encoding")
    return bad


def test_no_text_io_without_an_encoding_under_aurora():
    bad = [b for p in sorted((ROOT / "aurora").rglob("*.py")) for b in _missing_encoding(p)]
    assert not bad, "\n".join(bad)


def test_the_encoding_check_finds_a_violation(tmp_path):
    p = tmp_path / "enc_probe.py"
    p.write_text("from pathlib import Path\nPath('x').read_text()\nopen('y', 'w')\nopen('z', 'rb')\n"
                 "Path('q').open('w')\nPath('k').write_text('t', encoding='utf-8')\n", encoding="utf-8")
    found = _missing_encoding(p)
    assert len(found) == 3, found


# ----------------------------------------------------------------------------- the report flags (I39, I42)
class _Builder:
    """Records the ReportBuilder arguments of a command-line report; builds nothing."""
    made: list = []

    def __init__(self, project, zones, out, **kw):
        from aurora.core.report import PRESETS
        self.kw = kw
        self.png_dpi = kw.get("png_dpi") or PRESETS[kw.get("preset", "final")]["png_dpi"]
        _Builder.made.append(self)

    def scope_keys(self):
        return ["N5"]

    def build(self, progress=None):
        from types import SimpleNamespace
        return SimpleNamespace(pdf=Path("r.pdf"), pages=5, entries=2, csvs=[], figures=[], skipped=[])


@pytest.fixture
def zones(demo) -> list[str]:
    """The demo with make_fake_zones saved beside it: the '--zones <file>' arguments."""
    from aurora.core import testdata
    p = demo / "zones_test.json"
    testdata.make_fake_zones(demo).save(p)
    return ["--zones", str(p)]


@pytest.fixture
def fake_builder(monkeypatch):
    from aurora.core import report
    _Builder.made = []
    monkeypatch.setattr(report, "ReportBuilder", _Builder)
    return _Builder.made


def test_report_flags_reach_the_builder(demo, zones, fake_builder, monkeypatch, capsys):
    monkeypatch.setattr(cli, "settings_author", lambda: "")
    from aurora.core import report
    monkeypatch.setattr(report, "os_full_name", lambda: "")
    assert cli.main(["report", str(demo), *zones, "--holders", "N5", "N6", "--days", "2026-11-05", "--preset", "draft",
                     "--author", "A B"]) == 0
    kw = fake_builder[-1].kw
    assert kw["holders"] == ["N5", "N6"] and kw["days"] == ["2026-11-05"] and kw["preset"] == "draft"
    assert kw["author"] == "A B" and kw["png_dpi"] is None and kw["appendix"] is None and kw["maps"] is None
    assert "draft report, figures at 110 dpi, author A B (from --author)" in capsys.readouterr().out
    # the defaults: Final, every holder, the preset's dpi and appendices; --no-appendix / --no-maps / --dpi win
    assert cli.main(["report", str(demo), *zones, "--no-appendix", "--no-maps", "--dpi", "150"]) == 0
    kw = fake_builder[-1].kw
    assert kw["preset"] == "final" and kw["holders"] is None and kw["days"] is None
    assert kw["appendix"] is False and kw["maps"] is False and kw["png_dpi"] == 150
    assert kw["author"] == "" and "no author line" in capsys.readouterr().out


def test_report_summary_counts_in_words(demo, zones, fake_builder, monkeypatch, capsys):
    """'5 pages, 1 zone spectrum, 1 CSV, 1 figure', never '1 figures'."""
    from types import SimpleNamespace
    monkeypatch.setattr(cli, "settings_author", lambda: "")
    monkeypatch.setattr(_Builder, "build", lambda self, progress=None: SimpleNamespace(
        pdf=Path("r.pdf"), pages=5, entries=1, csvs=[Path("a.csv")], figures=[Path("f.png")], skipped=[]))
    assert cli.main(["report", str(demo), *zones, "--author", "A B"]) == 0
    assert "r.pdf: 5 pages, 1 zone spectrum, 1 CSV, 1 figure\n" in capsys.readouterr().out


def test_report_author_order_settings_then_system_account(demo, zones, fake_builder, monkeypatch, tmp_path, capsys):
    from aurora.core import report
    monkeypatch.setattr(report, "os_full_name", lambda: "Test User")
    monkeypatch.setenv("AURORA_SETTINGS_DIR", str(tmp_path / "settings"))
    assert cli.settings_author() == ""
    assert cli.main(["report", str(demo), *zones]) == 0
    assert fake_builder[-1].kw["author"] == "Test User"
    assert "author Test User (from the system account)" in capsys.readouterr().out
    pytest.importorskip("PySide6")
    from aurora.app.settings import settings
    s = settings()
    s.setValue("report_author", "Settings Name")
    s.sync()
    assert cli.settings_author() == "Settings Name"
    assert cli.main(["report", str(demo), *zones]) == 0
    assert fake_builder[-1].kw["author"] == "Settings Name"
    assert cli.main(["report", str(demo), *zones, "--author", ""]) == 0          # explicitly none
    assert fake_builder[-1].kw["author"] == ""
    assert cli.report_author(None) == ("Settings Name", "Settings") and cli.report_author(" X ") == ("X", "--author")


def test_a_draft_of_one_holder_from_the_command_line(tmp_path):
    """A real build: --preset draft --holders N5 --author on a two-holder beamtime gives a Draft of N5 only, by A B."""
    from wp7_support import project, two_holder_zones
    proj = project(tmp_path, n_holders=2)
    zf = two_holder_zones(proj)
    zpath = tmp_path / "zones.json"
    zf.save(zpath)
    out = tmp_path / "reports"
    assert cli.main(["report", str(proj.root), "--zones", str(zpath), "--out", str(out), "--preset", "draft",
                     "--holders", "N5", "--author", "A B"]) == 0
    latest = json.loads((out / "latest.json").read_text(encoding="utf-8"))
    folders = [p for p in out.iterdir() if p.is_dir()]
    assert len(folders) == 1 and folders[0].name.endswith("_N5_draft"), [p.name for p in folders]
    man = json.loads((folders[0] / "report_manifest.json").read_text(encoding="utf-8"))
    assert man["options"]["preset"] == "draft" and man["options"]["holders"] == ["N5"] and man["options"]["png_dpi"] == 110
    assert man["author"] == "A B" and {e["holder"] for e in man["entries"]} == {"N5"}
    assert latest


def test_a_holder_that_does_not_exist_is_named(demo, zones, capsys):
    assert cli.main(["report", str(demo), *zones, "--holders", "N9"]) == 1
    err = capsys.readouterr().err
    assert "no holder of demo beamtime matches N9 (holders: N5)" in err and "Traceback" not in err


def test_the_cli_presets_are_the_reports():
    from aurora.core.report import PRESETS
    assert sorted(cli.REPORT_PRESETS) == sorted(PRESETS)


def test_every_option_and_argument_is_documented_by_help():
    import argparse
    parser = cli.build_parser()
    undocumented = []

    def walk(p, prefix):
        for a in p._actions:
            name = "/".join(a.option_strings) or a.dest
            if isinstance(a, argparse._SubParsersAction):
                assert a.help, f"{prefix} commands"
                helps = {c.dest: c.help for c in a._choices_actions}
                for cmd, sp in a.choices.items():
                    if not helps.get(cmd):
                        undocumented.append(f"{prefix} {cmd}")
                    walk(sp, f"{prefix} {cmd}")
            elif not a.help or a.help == argparse.SUPPRESS:
                undocumented.append(f"{prefix} {name}")
    walk(parser, "aurora")
    assert not undocumented, undocumented
    text = parser.format_help()
    for flag in ("--selftest", "--distcheck", "--platform", "--no-gui", "--keep", "--verbose", "--version"):
        assert flag in text, flag
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        cli.main(["report", "--help"])
    for flag in ("--author", "--holders", "--days", "--preset", "--zones", "--out", "--dpi", "--no-appendix", "--no-maps"):
        assert flag in buf.getvalue(), flag
