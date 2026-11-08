"""I50: README, the developer index, INSTALL and the manuals tell the truth about the repository and the installers.

- every relative link resolves (and its #anchor names a heading of the target);
- the minimum macOS any of them states as a floor ('macOS 15 or later', 'macOS 15+') equals packaging/min_macos.txt,
  so 'macOS 12+' or 'macOS 14 or later' fails;
- INSTALL tells how to pass SmartScreen and Gatekeeper ('Open Anyway', the quarantine command);
- every option of an AURORA command line quoted in them is accepted by aurora.cli's parser;
- the release file names INSTALL and README promise are the ones the workflows build.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "aurora" / "resources" / "docs"
FILES = {
    "README.md": ROOT / "README.md",
    "docs/README.md": ROOT / "docs" / "README.md",
    "docs/DEVELOPER.md": ROOT / "docs" / "DEVELOPER.md",
    "INSTALL.md": DOCS / "INSTALL.md",
    "MANUAL.md": DOCS / "MANUAL.md",
    "USER_GUIDE.md": DOCS / "USER_GUIDE.md",
    "TUTORIALS.md": DOCS / "TUTORIALS.md",
}
FENCE = re.compile(r"```.*?```", re.S)
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def text_of(name: str) -> str:
    return FILES[name].read_text(encoding="utf-8")


def slug(title: str) -> str:
    s = re.sub(r"[^\w\- ]", "", title.strip().lower(), flags=re.UNICODE)
    return s.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    text = FENCE.sub("", path.read_text(encoding="utf-8"))
    return {slug(m.group(2)) for m in re.finditer(r"^(#{1,6})\s+(.*?)\s*$", text, flags=re.M)}


# ----------------------------------------------------------------------------- links
@pytest.mark.parametrize("name", sorted(FILES))
def test_every_relative_link_resolves(name):
    path = FILES[name]
    checked = 0
    for target in LINK.findall(FENCE.sub("", text_of(name))):
        if re.match(r"^[a-z]+:", target) or target.startswith("mailto:"):
            continue
        file_part, _, frag = target.partition("#")
        dest = (path.parent / file_part).resolve() if file_part else path
        assert dest.exists(), f"{name}: link '{target}' points at a missing file {dest}"
        if frag and dest.suffix == ".md":
            assert frag in anchors(dest), f"{name}: link '{target}' has no heading '{frag}' in {dest.name}"
        checked += 1
    if name in ("README.md", "docs/README.md"):
        assert checked >= 4, f"{name} links to the manuals"


def test_readme_links_the_bundled_manuals():
    for name in ("README.md", "docs/README.md"):
        targets = {Path(t.partition("#")[0]).name: t for t in LINK.findall(text_of(name))}
        for doc in ("USER_GUIDE.md", "MANUAL.md", "TUTORIALS.md", "INSTALL.md"):
            assert doc in targets, f"{name} does not link {doc}"
            assert "aurora/resources/docs" in targets[doc], f"{name} links {doc} outside aurora/resources/docs"


@pytest.mark.parametrize("name", ["INSTALL.md", "MANUAL.md", "USER_GUIDE.md", "TUTORIALS.md"])
def test_no_raw_angle_brackets_outside_code(name):
    """The Help browser reads the manuals as Markdown with HTML: a bare <name> outside code is a tag, and the text
    after it disappears (the rest of a section, or of the page). Placeholders belong in backticks."""
    text = FENCE.sub("", text_of(name))
    text = re.sub(r"`[^`]*`", "", text, flags=re.S)
    tags = re.findall(r"<[A-Za-z/][^>\n]*>", text)
    assert not tags, f"{name}: raw HTML-like text outside code: {tags}"


# ----------------------------------------------------------------------------- minimum macOS
FLOOR = re.compile(r"macOS\s+(\d+(?:\.\d+)?)(?:\s+[A-Z][a-z]+)?\s*(?:\+|or later|and later|or newer|and newer)"
                   r"|(?:at least|requires|minimum(?: is)?|needs)\s+macOS\s+(\d+(?:\.\d+)?)")


def version(v: str) -> tuple[int, ...]:
    parts = [int(x) for x in v.split(".")]
    return tuple(parts + [0] * (3 - len(parts)))


def floors(text: str) -> list[str]:
    return [a or b for a, b in FLOOR.findall(text)]


def test_the_minimum_macos_is_the_measured_one():
    measured = (ROOT / "packaging" / "min_macos.txt").read_text(encoding="utf-8").strip()
    install = floors(text_of("INSTALL.md"))
    assert install, "INSTALL.md does not state the minimum macOS"
    for name in FILES:
        for f in floors(text_of(name)):
            assert version(f) == version(measured), \
                f"{name} states macOS {f} as the minimum; packaging/min_macos.txt says {measured}"


@pytest.mark.parametrize("wrong", ["Runs on macOS 12+ (Apple silicon or Intel).", "Requires macOS 14 or later.",
                                   "macOS 14 Sonoma or later", "needs macOS 13"])
def test_an_old_macos_floor_is_caught(wrong):
    measured = (ROOT / "packaging" / "min_macos.txt").read_text(encoding="utf-8").strip()
    assert any(version(f) != version(measured) for f in floors(wrong)), wrong


# ----------------------------------------------------------------------------- first launch
def test_install_explains_smartscreen_and_gatekeeper():
    text = re.sub(r"\s+", " ", text_of("INSTALL.md"))
    assert "SmartScreen" in text and "More info" in text and "Run anyway" in text
    assert "Open Anyway" in text and "Privacy & Security" in text
    assert "xattr -dr com.apple.quarantine /Applications/AURORA.app" in text
    assert "right-click" not in text.lower().replace("right-click menu", "")    # gone on macOS 15
    assert "aurora-cli.exe" in text and "no administrator rights" in text


def test_install_says_the_macos_builds_run_on_ci_runners_only():
    text = re.sub(r"\s+", " ", text_of("INSTALL.md"))
    assert "GitHub's hosted runners" in text and "not been run on a physical Mac" in text


# ----------------------------------------------------------------------------- command lines
AURORA_CMD = re.compile(r"(?:^|[\s`(])(?:\S*[/\\])?(?:aurora|aurora-cli\.exe|AURORA\.exe|AURORA)(?=\s|$)")


def option_strings() -> set[str]:
    import argparse
    from aurora.cli import build_parser
    p = build_parser()
    out = set()
    stack = [p]
    while stack:
        q = stack.pop()
        for a in q._actions:
            out.update(a.option_strings)
            if isinstance(a, argparse._SubParsersAction):
                stack.extend(a.choices.values())
    return out


def command_lines(text: str) -> list[str]:
    """Lines of code blocks and inline code spans that run AURORA's command line."""
    out = []
    for block in re.findall(r"```(?:\w+)?\n(.*?)```", text, flags=re.S):
        out += [ln for ln in block.splitlines() if AURORA_CMD.search(ln.split("#")[0])]
    for span in re.findall(r"`([^`\n]+)`", FENCE.sub("", text)):
        if AURORA_CMD.search(span):
            out.append(span)
    return out


@pytest.mark.parametrize("name", ["INSTALL.md", "MANUAL.md", "USER_GUIDE.md", "TUTORIALS.md", "README.md"])
def test_every_quoted_option_is_accepted(name):
    known = option_strings()
    lines = command_lines(text_of(name))
    if name == "INSTALL.md":
        assert len(lines) >= 5
    for ln in lines:
        for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*", ln.split("  #")[0]):
            assert flag in known, f"{name}: '{flag}' in '{ln.strip()}' is not an option of aurora.cli"


def test_an_unknown_option_is_caught():
    known = option_strings()
    assert "--holders" in known and "--selftest" in known and "--platform" in known
    assert "--verify" not in known


# ----------------------------------------------------------------------------- release files
def test_the_promised_release_files_are_the_built_ones():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    iss = (ROOT / "packaging" / "aurora.iss").read_text(encoding="utf-8")
    assert "-windows-x64-setup" in iss and "windows-x64.zip" in ci and "-macos-${{ matrix.arch }}.dmg" in ci
    assert "arch: arm64" in ci and "arch: x86_64" in ci and "SHA256SUMS.txt" in release
    for name in ("INSTALL.md", "README.md"):
        text = text_of(name)
        for f in ("AURORA-<version>-windows-x64-setup.exe", "AURORA-<version>-windows-x64.zip",
                  "AURORA-<version>-macos-arm64.dmg", "AURORA-<version>-macos-x86_64.dmg", "SHA256SUMS.txt"):
            assert f in text, f"{name} does not name the release file {f}"


def test_readme_names_the_ci_runners_of_ci_yml():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    runners = set(re.findall(r"\b(windows-latest|windows-2022|windows-11-arm|macos-\d+(?:-intel)?|ubuntu-[\d.]+)\b", ci))
    readme = text_of("README.md")
    for r in runners:
        assert r in readme, f"README.md does not name the CI runner {r}"
    for stale in ("macos-14", "macos-latest", "macos-13"):
        assert stale not in readme or stale in ci, f"README.md names {stale}, which ci.yml does not use"


def test_the_credits_line_is_everywhere():
    line = "AURORA is version 2 of John Bussey's SGM XAS GUI"
    for name in ("README.md", "docs/README.md", "INSTALL.md", "MANUAL.md", "USER_GUIDE.md"):
        text = re.sub(r"\s+", " ", text_of(name))
        assert line in text and "James J. Dynes' SGM_Beamline_Analysis notebooks" in text, name
    for name, path in FILES.items():
        assert not re.search(r"\bSam\b", path.read_text(encoding="utf-8")), f"{name} names 'Sam' (the author's name is written in full)"


def test_easy_mode_is_the_documented_start_mode():
    for name in ("README.md", "USER_GUIDE.md", "MANUAL.md", "TUTORIALS.md"):
        text = re.sub(r"\s+", " ", text_of(name))
        assert "starts in Easy mode" in text.replace("**", ""), f"{name} does not say AURORA starts in Easy mode"
        assert "Ctrl+Shift+A" in text, name
