"""AURORA ships blank: nothing of one lab or beamtime in what is published, and every configuration file is optional.

1. No tracked text file and no shipped resource names the account of the machine that runs the tests (its user name or
   home folder name: a path copied from this machine), a CLS beamtime folder name (<year>_CLS_<a>-<b>, outside the
   dated CHANGELOG) or the author's first name outside the full credit name (read from pyproject.toml). The home-path
   patterns themselves are tests/test_publication.py's.
2. An optional local deny-list: AURORA_BLANK_DENYLIST (environment or tests/local_paths.json) names a text file kept
   outside the repository, one token per line ('#' starts a comment): no tracked file and no file under
   aurora/resources holds one (whole words, any case). Skipped when no list is configured.
3. The synthetic demo's names never again copy a real beamtime's (neutral patterns only).
4. A config-optional matrix on copies of the demo: each optional file or folder removed, empty or not JSON, a raw/-only
   beamtime and a loose folder of scans. Project.discover, `aurora project`, `aurora report` (with a session; without
   one a single line and exit 1) and a window visiting every perspective in Easy and Advanced mode run without an
   exception or an error dialog, and the project summary says what is missing.
"""
from __future__ import annotations

import getpass
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from local_paths import local_path  # noqa: E402

BEAMTIME_FOLDER = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d\d_CLS_[A-Za-z0-9]+-[A-Za-z0-9]+(?![A-Za-z0-9])")
GENERIC_ACCOUNTS = {"runner", "runneradmin", "root", "user", "admin", "administrator", "vagrant", "ci", "build"}


def _git(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    except OSError:
        pytest.skip("git is not available")


@pytest.fixture(scope="module")
def published() -> dict[str, bytes]:
    """{relative path: bytes} of every tracked file plus every file under aurora/resources (what the app ships)."""
    r = _git("ls-files", "-z")
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    rels = {p for p in r.stdout.decode("utf-8").split("\0") if p}
    rels |= {p.relative_to(ROOT).as_posix() for p in (ROOT / "aurora" / "resources").rglob("*") if p.is_file()}
    out = {}
    for rel in sorted(rels):
        try:
            out[rel] = (ROOT / rel).read_bytes()
        except OSError:
            continue                                         # deleted in the working tree, not yet committed
    return out


def _texts(published: dict[str, bytes]) -> dict[str, str]:
    out = {}
    for rel, data in published.items():
        try:
            out[rel] = data.decode("utf-8")
        except UnicodeDecodeError:
            continue                                         # images, icons, fonts
    return out


def _accounts() -> set[str]:
    names = set()
    for get in (getpass.getuser, lambda: Path.home().name):
        try:
            n = str(get() or "").strip()
        except Exception:
            n = ""
        if len(n) >= 3 and n.casefold() not in GENERIC_ACCOUNTS:
            names.add(n)
    return names


def _word(token: str) -> re.Pattern:
    return re.compile(rf"(?i)(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])")


# ----------------------------------------------------------------------------- 1. what is published
def test_no_account_name_of_this_machine(published):
    names = _accounts()
    if not names:
        pytest.skip("no account name to look for on this machine")
    hits = []
    for rel, text in _texts(published).items():
        for n in names:
            for i, line in enumerate(text.splitlines(), 1):
                if _word(n).search(line):
                    hits.append(f"{rel}:{i}")
    assert not hits, f"the account name of this machine is in published files: {hits[:20]}"


def test_no_beamtime_folder_name(published):
    hits = [f"{rel}:{i}" for rel, text in _texts(published).items() if rel != "CHANGELOG.md"
            for i, line in enumerate(text.splitlines(), 1) if BEAMTIME_FOLDER.search(line)]
    assert not hits, f"a CLS beamtime folder name in published files (use my_beamtime): {hits[:20]}"


def test_the_pattern_catches_a_beamtime_folder_name():
    year, a, b = "2031", "X", "Y"                     # built at run time, so this file does not trip its own scan
    assert BEAMTIME_FOLDER.search(f"D:\\beamtimes\\{year}_CLS_{a}-{b}\\raw")
    assert not BEAMTIME_FOLDER.search("my_beamtime") and not BEAMTIME_FOLDER.search("AURORA_CLS_tools")


def test_the_author_appears_only_by_full_name(published):
    """The credits keep the author's full name; no text calls the author by first name ('<first>'s rule', ...)."""
    author = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["authors"][0]["name"]
    first = author.split()[0]
    bare = re.compile(rf"(?<![A-Za-z0-9]){re.escape(first)}(?![A-Za-z0-9])(?!\s+{re.escape(author.split()[-1])})")
    hits = [f"{rel}:{i}" for rel, text in _texts(published).items()
            for i, line in enumerate(text.splitlines(), 1) if bare.search(line)]
    assert not hits, f"the author's first name outside the full credit name: {hits[:20]}"


# ----------------------------------------------------------------------------- 2. the local deny-list
def denylist() -> list[str] | None:
    p = local_path("AURORA_BLANK_DENYLIST")
    if p is None or not p.is_file():
        return None
    out = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        t = line.split("#", 1)[0].strip()
        if len(t) >= 2:
            out.append(t)
    return out


def scan_for_tokens(published: dict[str, bytes], tokens: list[str]) -> list[str]:
    """'path:line: token' for every whole-word hit in a text file; binary files are searched for tokens of 6 or more
    characters as UTF-8 bytes (a name in image metadata)."""
    hits = []
    pats = [(t, _word(t)) for t in tokens]
    for rel, data in published.items():
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            low = data.lower()
            hits += [f"{rel}: {t}" for t in tokens if len(t) >= 6 and t.lower().encode("utf-8") in low]
            continue
        for i, line in enumerate(text.splitlines(), 1):
            hits += [f"{rel}:{i}: {t}" for t, pat in pats if pat.search(line)]
    return hits


def test_no_token_of_the_local_denylist(published):
    tokens = denylist()
    if tokens is None:
        pytest.skip("no AURORA_BLANK_DENYLIST configured (a local list kept outside the repository)")
    hits = scan_for_tokens(published, tokens)
    assert not hits, f"deny-listed tokens in published files: {hits[:30]}"


def test_the_denylist_scan_finds_a_token():
    published = {"a.md": "the sample Zeta-9 of holder Q7\n".encode(), "b.png": b"\x89PNG..Author: Quentin Zed\x00",
                 "c.md": "Zeta-90 is another sample\n".encode()}
    assert scan_for_tokens(published, ["zeta-9", "Quentin Zed", "Q7"]) == ["a.md:1: zeta-9", "a.md:1: Q7",
                                                                           "b.png: Quentin Zed"]


# ----------------------------------------------------------------------------- 3. the demo's names
def test_the_demo_names_are_neutral():
    """The demo's names follow a made-up scheme (an allow-list, so no real name has to be written here to be kept
    out): holders holderA.., stacks of the samples alpha, beta, gamma or of a plain compound, groups 'Group <n>'."""
    from aurora.core import testdata
    compounds = "|".join(testdata.EXTRA_SAMPLES)
    holder = re.compile(r"^holder[A-J]$")
    survey = re.compile(r"^\d{6}_holder[A-J]_survey$")
    stack = re.compile(rf"^\d{{6}}_(?:alpha_beta|gamma|photodiode|{compounds})_f_k_stack$")
    assert holder.match(testdata.HOLDER) and holder.match(testdata.HOLDER2)
    assert survey.match(testdata.SURVEY) and survey.match(testdata.SURVEY2)
    assert all(stack.match(n) for n in (testdata.STACK, testdata.ABORTED, testdata.TEST, testdata.STACK2))
    assert set(testdata.DEMO_REFERENCES) <= set(testdata.EXTRA_SAMPLES)
    assert set(testdata.DEMO_SPECTRA) - set(testdata.DEMO_REFERENCES) == {"mixture 1", "mixture 2"}
    assert all(re.match(r"^Group \d$", g) for g in (testdata.GROUP1, testdata.GROUP2))
    assert [r[0] for r in testdata.DEMO_SHEET_ROWS] == ["1 alpha", "2 beta"]
    assert {r[1] for r in testdata.DEMO_SHEET_ROWS} == {testdata.HOLDER}
    assert set(testdata.PADS) == {"1 alpha", "2 beta", "3 gamma"}
    assert testdata.ctime() == "Fri Mar 14 09:28:00 2025" and testdata.DAY == "2025-03-14"


# ----------------------------------------------------------------------------- 4. every config file is optional
def _rm(*rels):
    def f(root: Path):
        for rel in rels:
            p = root / rel
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
    return f


def _write(files: dict[str, str]):
    def f(root: Path):
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
    return f


def _raw_only(root: Path):
    for c in list(root.iterdir()):
        if c.name not in ("raw", ".aurora"):
            shutil.rmtree(c) if c.is_dir() else c.unlink()


VARIANTS = {
    "full": lambda root: None,
    "no_roi_bins": _rm("config/roi_bins.json"),
    "no_config": _rm("config"),
    "no_sheet": _rm("samples.csv"),
    "no_processed": _rm("processed"),
    "no_references": _rm("references"),
    "empty_files": _write({"config/roi_bins.json": "", "config/sdd_calibration.json": "",
                           "config/plate_layout.json": "", "samples.csv": ""}),
    "not_json": _write({"config/roi_bins.json": "{not json", "config/sdd_calibration.json": "[1, 2",
                        "config/plate_layout.json": "{"}),
    # JSON of the wrong shape (the blank core, BL-09 / BL-10): a detector that is a number, a list for the lines, a
    # holder that is a number; then one line without sdd4 next to a good one
    "wrong_types": _write({"config/roi_bins.json": '["a"]', "config/sdd_calibration.json": '{"sdd1": 5}',
                           "config/plate_layout.json": '{"holderA": 5}'}),
    "line_missing_sdd": _write({"config/roi_bins.json": '{"F K\u03b1": {"sdd1": [60, 74], "sdd2": [61, 75], '
                                                        '"sdd3": [62, 76]}, "O K\u03b1": {"sdd1": [46, 58], '
                                                        '"sdd2": [46, 58], "sdd3": [46, 58], "sdd4": [46, 58]}}'}),
    "raw_only": _raw_only,
}


@pytest.fixture(scope="module")
def demo_base(tmp_path_factory):
    import contextlib
    import io
    from aurora.cli import cmd_demo
    base = tmp_path_factory.mktemp("blank") / "demo"
    with contextlib.redirect_stdout(io.StringIO()):
        assert cmd_demo(base) == 0
    return base


def _variant(demo_base: Path, tmp_path: Path, name: str, session: bool = True) -> Path:
    from aurora.core import testdata
    root = tmp_path / name
    shutil.copytree(demo_base, root)
    if session:
        zf = testdata.make_fake_zones(root)
        (root / ".aurora").mkdir(exist_ok=True)
        zf.save(root / ".aurora" / "session.json")
    VARIANTS[name](root)
    return root


@pytest.mark.parametrize("name", list(VARIANTS))
def test_every_config_file_is_optional_in_the_core_and_the_cli(demo_base, tmp_path, capsys, name):
    from aurora.cli import main
    from aurora.core import overview
    from aurora.core.model import Project
    from aurora.core.stack import StackData
    root = _variant(demo_base, tmp_path, name)
    proj = Project.discover(root)
    rows = dict(overview.project_rows(proj, overview.project_facts(proj)))
    for key in ("calibration", "ROI bins", "sample sheet"):
        assert rows.get(key), (key, rows)                 # the summary always says what it found, or that it found none
    if name in ("no_roi_bins", "no_config", "raw_only"):
        assert rows["ROI bins"].startswith("none found")
    if name in ("empty_files", "not_json", "wrong_types"):   # the file is there but holds no line (blank core, BL-10)
        assert rows["ROI bins"].startswith("config/roi_bins.json has no usable line: total XRF only")
    if name == "line_missing_sdd":                           # the good line is kept, the other one is named
        assert rows["ROI bins"].endswith("O Kα") and any("sdd4" in v for k, v in rows.items() if k == "config problem")
    if name in ("no_sheet", "empty_files", "raw_only"):
        assert rows["sample sheet"].startswith("none found")
    from aurora.core import testdata
    sd = StackData.load(proj, proj.find(testdata.STACK))
    assert sd.n_energies == len(testdata.EDGE_ENERGIES)
    assert main(["project", str(root)]) == 0
    capsys.readouterr()
    assert main(["report", str(root), "--preset", "draft", "--author", "", "--out", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert ".pdf" in out and "Traceback" not in out


def test_a_report_without_zones_is_one_line(demo_base, tmp_path, capsys):
    from aurora.cli import main
    root = _variant(demo_base, tmp_path, "raw_only", session=False)
    assert main(["report", str(root), "--out", str(tmp_path / "out")]) == 1
    cap = capsys.readouterr()
    lines = (cap.out + cap.err).strip().splitlines()
    assert not [ln for ln in lines if "Traceback" in ln] and lines[0].startswith("aurora report: no zones yet")
    assert len(lines) == 1 or (len(lines) == 2 and lines[1].strip().startswith("(details in"))     # the log, never a trace


def test_a_loose_folder_needs_no_config(tmp_path, capsys):
    from aurora.cli import main
    from aurora.core import testdata
    from aurora.core.model import Project
    root = testdata.make_fake_project(tmp_path / "CLS GUI", loose=True)
    proj = Project.discover(root)
    assert proj.loose and proj.find(testdata.STACK) is not None
    assert main(["project", str(root)]) == 0


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("pyqtgraph")
    from aurora.app.main import create_app
    return create_app(["aurora-tests"])


@pytest.mark.parametrize("name", ["full", "no_config", "no_sheet", "empty_files", "not_json", "wrong_types",
                                  "raw_only"])
def test_every_config_file_is_optional_in_the_window(qapp, demo_base, tmp_path, error_dialogs, name):
    """Every perspective in Easy and Advanced mode on the variant: no exception reaches the excepthook (conftest) and
    no error dialog opens."""
    from aurora.app import mode
    from aurora.app.main import PERSPECTIVES, MainWindow
    from aurora.core.model import Project
    root = _variant(demo_base, tmp_path, name, session=name != "raw_only")
    proj = Project.discover(root)
    win = MainWindow()
    win.resize(1200, 760)
    win.show()
    try:
        win.home.set_project(proj)
        qapp.processEvents()
        for advanced in (False, True):
            mode.set_advanced(advanced)
            for key, _title in PERSPECTIVES:
                if key == "maps" and not advanced:
                    continue
                win.show_perspective(key)
                for _ in range(3):
                    qapp.processEvents()
        win.jobs.wait_idle(60000)
        qapp.processEvents()
        assert not error_dialogs, error_dialogs
        assert not win.survey.sketch_card.isVisible()             # the demo has no sketch: no Sketch card at all
    finally:
        mode.set_advanced(False)
        win.session._save_timer.stop()
        win.session.project = None
        win.close()
