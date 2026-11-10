"""The repository is public: no internal notes, no lab material and no home-folder paths in what git tracks.

- the private files (agent notes, planning, the beamtime scripts with the sample sheet, the local test paths) are not
  tracked, and .gitignore keeps them out of a later `git add`;
- no tracked text file names a home folder with an account in it (C:\\Users\\<name>\\, /Users/<name>/, /home/<name>/):
  the tests take their machine-specific folders from the environment or tests/local_paths.json (untracked);
- the README, the documentation index and the developer manual link only tracked files (a link to a file that stays
  on the owner's disk resolves there and is dead on GitHub).
The tracked list comes from git; outside a git checkout (an unpacked source archive) the tests are skipped.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PRIVATE = ("CLAUDE.md", "docs/BRAINSTORM.md", "docs/HANDOFF.md", "docs/sources/x.txt", "legacy/sgm_report_v2.5/x.json",
           "legacy/CLS_GUI_JohnBussey/x", "legacy/SGM_Beamline_Analysis/x", "tests/local_paths.json")
# an account's home folder; placeholders (<you>), the CI runner (RUNNER~1, runner) and the shared folders are not
HOME = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!<|RUNNER~1\b|Public\b|runneradmin\b)[A-Za-z0-9._-]+[\\/]"
                  r"|/Users/(?!<|Shared/|runner/)[A-Za-z0-9._-]+/|/home/(?!<|runner/)[A-Za-z0-9._-]+/")
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"```.*?```", re.S)


def git(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    except OSError:
        pytest.skip("git is not available")


@pytest.fixture(scope="module")
def tracked() -> list[str]:
    r = git("ls-files", "-z")
    if r.returncode != 0:
        pytest.skip("not a git checkout")
    return [p for p in r.stdout.decode("utf-8").split("\0") if p]


def test_private_files_are_not_tracked(tracked):
    for p in PRIVATE:
        prefix = p.rsplit("/", 1)[0] + "/" if p.endswith(("/x", "/x.txt", "/x.json")) else p
        hits = [t for t in tracked if t == p or t.startswith(prefix)]
        assert not hits, f"tracked although private: {hits}"


def test_private_files_are_ignored(tracked):
    for p in PRIVATE:
        r = git("check-ignore", "-q", "--no-index", p)
        assert r.returncode == 0, f"{p} is not in .gitignore: a 'git add' would publish it"


def test_no_home_folder_in_tracked_text(tracked):
    hits = []
    for rel in tracked:
        try:
            text = (ROOT / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue                                        # binary files (images, icons)
        hits += [f"{rel}:{i}" for i, line in enumerate(text.splitlines(), 1) if HOME.search(line)]
    assert not hits, f"home-folder paths in tracked files: {hits}"


def test_the_pattern_catches_an_account_path():
    for path in (r"C:\Users\jdoe\Desktop\beamtime", "C:/Users/jdoe/data", "/Users/jdoe/Data/x", "/home/jdoe/x"):
        assert HOME.search(path), path
    for path in (r"C:\Users\<you>\Apps\AURORA", r"C:\Users\RUNNER~1\AppData", "/Users/Shared/x", r"C:\Users\Public\x"):
        assert not HOME.search(path), path


@pytest.mark.parametrize("name", ["README.md", "docs/README.md", "docs/DEVELOPER.md", "CREDITS.md"])
def test_the_front_pages_link_only_tracked_files(tracked, name):
    files = set(tracked)
    path = ROOT / name
    for target in LINK.findall(FENCE.sub("", path.read_text(encoding="utf-8"))):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue
        dest = (path.parent / target.partition("#")[0]).resolve()
        try:
            rel = dest.relative_to(ROOT.resolve()).as_posix()
        except ValueError:
            pytest.fail(f"{name}: link '{target}' leaves the repository")
        assert rel in files or any(f.startswith(rel + "/") for f in files), \
            f"{name}: link '{target}' points at {rel}, which is not tracked (dead on GitHub)"
