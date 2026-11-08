"""Headless command line that always answers (I45): console twin, exit codes, logs, UTF-8, folder argument."""
import ast
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
