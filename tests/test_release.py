"""One version source and build provenance (I46); the release workflow and its notes (I47); the installers (WP8)."""
import json
import re
import struct
import sys
import tomllib
from pathlib import Path

import pytest

import aurora

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging"
WORKFLOWS = ROOT / ".github" / "workflows"
sys.path.insert(0, str(PKG))
import macos_minos  # noqa: E402
import release_notes  # noqa: E402
import specutil  # noqa: E402

VERSION_LITERAL = re.compile(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])")
ALLOWED_LITERALS = {"10.0.17763"}       # aurora.iss MinVersion: the Windows 10 1809 build, not a version of AURORA


# ----------------------------------------------------------------------------- one version source
def test_changelog_heading_is_the_version():
    first = re.search(r"^## (\S+)", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    assert first and first.group(1) == aurora.__version__


def test_packaging_carries_no_version_literal():
    found = []
    for p in sorted(PKG.rglob("*")):
        if not p.is_file() or "LICENSES" in p.parts or "__pycache__" in p.parts or p.name == "constraints.txt":
            continue                                    # constraints.txt pins third-party versions by design
        text = p.read_text(encoding="utf-8", errors="replace")
        assert aurora.__version__ not in text, f"{p.name} names the version {aurora.__version__}"
        found += [f"{p.name}: {m}" for m in VERSION_LITERAL.findall(text) if m not in ALLOWED_LITERALS]
    assert not found, found


def test_specutil_reads_the_version_without_importing_aurora():
    assert specutil.version() == aurora.__version__
    assert specutil.version_tuple("1.22.3") == (1, 22, 3, 0) and specutil.version_tuple("2.0.1rc1") == (2, 0, 1, 0)
    assert specutil.dotted4() == aurora.__version__ + ".0"


def test_plist():
    pl = specutil.plist()
    assert pl["CFBundleShortVersionString"] == aurora.__version__ == pl["CFBundleVersion"]
    assert pl["LSMinimumSystemVersion"] == (PKG / "min_macos.txt").read_text(encoding="utf-8").strip() == "15.0"
    assert pl["NSHumanReadableCopyright"] == "© 2026 Sami Soudani, MIT licence"
    assert pl["LSApplicationCategoryType"] == "public.app-category.education" and pl["NSHighResolutionCapable"] is True
    doc = pl["CFBundleDocumentTypes"][0]
    assert doc["LSItemContentTypes"] == ["public.folder"] and doc["LSHandlerRank"] == "Alternate"
    import plistlib
    assert plistlib.loads(plistlib.dumps(pl)) == pl                    # serialisable as an Info.plist


def test_windows_version_strings():
    s = specutil.version_strings("aurora-cli.exe")
    assert s["FileVersion"] == s["ProductVersion"] == aurora.__version__ + ".0"
    assert s["CompanyName"] == "Washington State University" and s["ProductName"] == "AURORA"
    assert s["LegalCopyright"] == "© 2026 Sami Soudani, MIT licence"
    assert s["OriginalFilename"] == "aurora-cli.exe" and s["FileDescription"]


def test_version_info_carries_the_version():
    pytest.importorskip("PyInstaller")
    vi = specutil.version_info("AURORA.exe")
    nums = tuple(int(x) for x in aurora.__version__.split(".")) + (0,)
    assert vi.ffi.fileVersionMS == (nums[0] << 16) | nums[1] and vi.ffi.fileVersionLS == (nums[2] << 16) | nums[3]
    text = str(vi)
    assert f"'{aurora.__version__}.0'" in text and "Washington State University" in text and "AURORA.exe" in text


def test_installed_metadata_has_the_same_version():
    from importlib import metadata
    try:
        v = metadata.version("aurora-sgm")
    except metadata.PackageNotFoundError:
        pytest.skip("aurora-sgm is not installed")
    assert v == aurora.__version__


def test_pyproject_takes_the_version_from_the_package():
    d = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in d["project"] and "version" in d["project"]["dynamic"]
    assert d["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "aurora.__version__"}


def test_build_json(tmp_path):
    p = specutil.write_build_json(tmp_path / "_build.json")
    b = json.loads(p.read_text(encoding="utf-8"))
    for k in ("version", "describe", "commit", "date", "python", "pyside6", "numpy", "scipy", "pyinstaller"):
        assert k in b, k
    assert b["version"] == aurora.__version__ and b["numpy"]


def test_about_shows_the_build_commit(monkeypatch):
    pytest.importorskip("PySide6")
    from aurora.app import about
    from aurora.app.main import create_app
    create_app(["aurora-tests"])
    monkeypatch.setattr(about, "build_info", lambda: {"describe": "v9.9.9-3-gabc1234-dirty", "commit": "abc1234",
                                                      "date": "2026-11-08T10:00:00+00:00"})
    assert "build v9.9.9-3-gabc1234-dirty (2026-11-08T10:00:00+00:00)" in about.versions_text()
    d = about.AboutDialog()
    try:
        assert "gabc1234" in d.text()
    finally:
        d.deleteLater()
    monkeypatch.setattr(about, "build_info", lambda: {})
    assert "build" not in about.versions_text().split("\n")[0]


def test_stamp_names_python_and_the_pins():
    s = specutil.stamp()
    assert re.fullmatch(r"py\d+\.\d+\.\d+ [0-9a-f]{16}", s), s


# ----------------------------------------------------------------------------- icon and minimum macOS
def test_icns_holds_every_size():
    from PIL import Image
    im = Image.open(ROOT / "branding" / "aurora.icns")
    sizes = set(im.info["sizes"])
    px = {w * scale for (w, _h, scale) in sizes}
    assert {16, 32, 128, 256, 512, 1024} <= px
    assert {(16, 16, 1), (32, 32, 1), (128, 128, 1), (256, 256, 1), (512, 512, 1), (512, 512, 2)} <= sizes
    for (w, h, scale) in sorted(sizes):
        one = Image.open(ROOT / "branding" / "aurora.icns")
        one.size = (w, h)
        one.load(scale=scale)
        assert one.size == (w * scale, h * scale) and one.mode == "RGBA"


def _macho(minos: tuple[int, int, int] | None, cpu: int = 0x0100000C, old_style: bool = False) -> bytes:
    """A minimal 64-bit Mach-O header with one version load command (LC_BUILD_VERSION, or LC_VERSION_MIN_MACOSX)."""
    enc = lambda v: (v[0] << 16) | (v[1] << 8) | v[2]          # noqa: E731
    cmds = b""
    if minos is not None:
        if old_style:
            cmds = struct.pack("<IIII", 0x24, 16, enc(minos), enc((14, 0, 0)))
        else:
            cmds = struct.pack("<IIIIII", 0x32, 24, 1, enc(minos), enc((15, 2, 0)), 0)
    seg = struct.pack("<II", 0x19, 8)                                # an LC_SEGMENT_64 stub before it
    body = seg + cmds
    hdr = struct.pack("<IiiIIIII", 0xFEEDFACF, cpu, 0, 6, 2 if minos is not None else 1, len(body), 0, 0)
    return hdr + body + b"\x00" * 64


def test_macos_minos_parses_synthetic_headers():
    assert macos_minos.parse(_macho((15, 0, 0))) == [{"arch": "arm64", "minos": (15, 0, 0), "sdk": (15, 2, 0)}]
    assert macos_minos.parse(_macho((26, 0, 0), cpu=0x01000007))[0]["minos"] == (26, 0, 0)
    assert macos_minos.parse(_macho((10, 13, 0), old_style=True))[0]["minos"] == (10, 13, 0)
    assert macos_minos.parse(_macho(None))[0]["minos"] is None
    assert macos_minos.parse(b"not a binary at all") == []
    # universal binary: an x86_64 slice and an arm64 slice
    a, b = _macho((11, 0, 0), cpu=0x01000007), _macho((15, 0, 0))
    off_a, off_b = 4096, 8192
    fat = struct.pack(">II", 0xCAFEBABE, 2) + struct.pack(">iiIII", 0x01000007, 3, off_a, len(a), 12) \
        + struct.pack(">iiIII", 0x0100000C, 0, off_b, len(b), 14)
    fat = fat.ljust(off_a, b"\0") + a
    fat = fat.ljust(off_b, b"\0") + b
    got = macos_minos.parse(fat)
    assert [(g["arch"], g["minos"]) for g in got] == [("x86_64", (11, 0, 0)), ("arm64", (15, 0, 0))]


def test_macos_minos_scans_a_bundle_and_checks_the_floor(tmp_path, capsys):
    app = tmp_path / "AURORA.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Frameworks").mkdir()
    (app / "Contents" / "MacOS" / "AURORA").write_bytes(_macho((11, 0, 0)))
    (app / "Contents" / "Frameworks" / "QtCore").write_bytes(_macho((15, 0, 0)))
    (app / "Contents" / "Frameworks" / "readme.txt").write_text("text, not Mach-O", encoding="utf-8")
    res = macos_minos.scan(app)
    assert res["highest"] == (15, 0, 0) and res["by"] == [str(Path("Contents/Frameworks/QtCore"))]
    assert len(res["files"]) == 2
    assert macos_minos.main([str(app), "--check", str(PKG / "min_macos.txt"), "--arch", "arm64"]) == 0
    assert macos_minos.main([str(app), "--check", "14.0"]) == 1
    assert "needs macOS 15.0 but declares 14.0" in capsys.readouterr().out
    assert macos_minos.main([str(app), "--arch", "x86_64"]) == 1
    (app / "Contents" / "Frameworks" / "QtGui").write_bytes(_macho((26, 0, 0)))      # a PySide6 6.12 wheel would
    assert macos_minos.main([str(app), "--check", str(PKG / "min_macos.txt")]) == 1


# ----------------------------------------------------------------------------- build scripts
def test_build_win_bat():
    text = (PKG / "build_win.bat").read_text(encoding="utf-8")
    assert "uv venv --seed --python 3.12" in text and "py -3.12 -m venv" in text
    # a missing py -3.12 (and no uv) stops with one line
    block = text[text.index("\n:pylauncher"):text.index("\n:created")]
    assert 'py -3.12 -c "pass"' in block
    msg = [l for l in block.splitlines() if l.strip().startswith("echo AURORA builds with Python 3.12")]
    assert len(msg) == 1 and "goto :fail" in block
    assert '-c "%ROOT%\\packaging\\constraints.txt"' in text and "--force-reinstall" in text and "-e " not in text
    assert "specutil.py\" stamp" in text and "rmdir /s /q \"%VENV%\"" in text          # a stale .venv-build is recreated
    assert "specutil.py\" version" in text and "/DMyAppVersion=%VERSION%" in text
    assert "winget install --id JRSoftware.InnoSetup" in text and "/install" in text
    assert "AURORA-%VERSION%-windows-x64.zip" in text and "AURORA-%VERSION%-windows-x64-setup.exe" in text
    assert "\r\n" in (PKG / "build_win.bat").read_bytes().decode("utf-8")              # cmd.exe wants CRLF


def test_build_mac_sh():
    text = (PKG / "build_mac.sh").read_text(encoding="utf-8")
    assert "uv venv --seed --python 3.12" in text and "python3.12 -m venv" in text
    assert "AURORA builds with Python 3.12" in text and "exit 1" in text
    assert 'macos_minos.py "$APP" --check packaging/min_macos.txt --arch "$ARCH"' in text
    assert "codesign --force --deep --sign -" in text and "codesign --verify --deep --strict" in text
    assert "-c \"$ROOT/packaging/constraints.txt\"" in text and "--force-reinstall" in text
    assert b"\r\n" not in (PKG / "build_mac.sh").read_bytes()


def test_make_dmg():
    text = (PKG / "make_dmg.sh").read_text(encoding="utf-8")
    assert '"$STAGE/If macOS blocks AURORA.txt"' in text and "ln -s /Applications" in text
    assert "-fs HFS+ -format UDZO" in text and '-volname "AURORA $VERSION"' in text
    assert "THIRD_PARTY_LICENSES.txt" in text and "LICENSE" in text and "min_macos.txt" in text
    note = (PKG / "macos_first_launch.txt").read_text(encoding="utf-8")
    assert "Open Anyway" in note and "xattr -dr com.apple.quarantine" in note and "@MIN_MACOS@" in note
    assert "right-click" not in note.lower() and "macOS 14" not in note             # macOS 14 is unsupported


def test_constraints_pin_the_build():
    pins = {}
    for line in (PKG / "constraints.txt").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            name, ver = line.split(";")[0].split("==")
            pins[name.strip().lower()] = ver.strip()
    for name in ("pyside6", "pyside6-essentials", "pyside6-addons", "shiboken6", "numpy", "scipy", "h5py", "matplotlib",
                 "pyqtgraph", "xraydb", "platformdirs", "sqlalchemy", "pyinstaller", "pillow"):
        assert name in pins, name
    assert pins["pyside6"] == pins["shiboken6"] == pins["pyside6-essentials"] == pins["pyside6-addons"]
    assert pins["pyside6"].startswith("6.11.")          # 6.12's macOS binaries need macOS 26 (measured)


# ----------------------------------------------------------------------------- Windows installer
def test_inno_setup_script():
    iss = (PKG / "aurora.iss").read_text(encoding="utf-8")
    assert "AppId={{E4B9D82B-3D95-4DE5-805B-887FAFE5BFA1}" in iss
    assert "OutputBaseFilename={#MyAppName}-{#MyAppVersion}-windows-x64-setup" in iss
    assert "#error" in iss[iss.index("#ifndef MyAppVersion"):iss.index("#endif")]
    for line in ("PrivilegesRequired=lowest", "PrivilegesRequiredOverridesAllowed=commandline dialog",
                 "ArchitecturesAllowed=x64compatible", "UsePreviousAppDir=yes", "UsePreviousTasks=yes",
                 "CloseApplications=yes", "SetupIconFile=..\\branding\\aurora.ico", "LicenseFile=..\\LICENSE",
                 'AppPublisher={#MyAppPublisher}', '#define MyAppPublisher "Sami Soudani, Washington State University"'):
        assert line in iss, line
    assert 'Type: filesandordirs; Name: "{app}\\_internal"' in iss.split("\n[InstallDelete]")[1].split("[")[0]
    assert 'Type: filesandordirs; Name: "{app}\\_internal"' in iss.split("\n[UninstallDelete]")[1]
    tasks = iss.split("\n[Tasks]")[1].split("\n[")[0]
    assert 'Name: "desktopicon"' in tasks and 'Name: "folderverb"' in tasks and "Flags: unchecked" in tasks
    reg = iss.split("\n[Registry]")[1].split("\n[")[0]
    assert "Software\\Classes\\Directory\\shell\\AURORA" in reg and "uninsdeletekey" in reg and "Tasks: folderverb" in reg
    icons = iss.split("\n[Icons]")[1].split("\n[")[0]
    assert icons.count('WorkingDir: "{userdocs}"') == 2
    assert "\r\n" in (PKG / "aurora.iss").read_bytes().decode("utf-8")


# ----------------------------------------------------------------------------- release notes and workflow
CHANGELOG = """# Changelog

## 0.3.1-rc1 (2026-11-20)
Release candidate.
- one

## 0.3.1 (2026-11-21)
- two

## 0.3.10 (2027-01-01)
- ten
"""


def test_release_notes_extract_one_section():
    sec = release_notes.extract(CHANGELOG, "0.3.1-rc1")
    assert sec.startswith("## 0.3.1-rc1 (2026-11-20)") and "- one" in sec and "- two" not in sec and "ten" not in sec
    assert release_notes.extract(CHANGELOG, "0.3.1").startswith("## 0.3.1 (2026-11-21)")
    assert "- ten" not in release_notes.extract(CHANGELOG, "0.3.1")
    assert release_notes.extract(CHANGELOG, "0.3.10").strip().endswith("- ten")
    assert release_notes.extract(CHANGELOG, "0.3") == "" and release_notes.extract(CHANGELOG, "9.9.9") == ""


def test_release_notes_add_downloads_and_first_launch():
    text = release_notes.notes("0.3.1-rc1", CHANGELOG, "15.0")
    assert text.startswith("## 0.3.1-rc1") and "- two" not in text
    for a in release_notes.asset_names("0.3.1-rc1"):
        assert f"`{a}`" in text
    assert "macOS 15.0 or later" in text and "Open Anyway" in text and "More info > Run anyway" in text
    assert "SHA256SUMS.txt" in text
    with pytest.raises(SystemExit):
        release_notes.notes("9.9.9", CHANGELOG, "15.0")
    real = release_notes.notes(aurora.__version__, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), specutil.min_macos())
    assert real.startswith(f"## {aurora.__version__}")


def _workflow(name: str) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _on(wf: dict) -> dict:
    return wf.get("on", wf.get(True))                   # YAML 1.1 reads the key 'on' as True


def test_release_workflow():
    wf = _workflow("release.yml")
    assert _on(wf) == {"push": {"tags": ["v*"]}}
    jobs = wf["jobs"]
    check = "\n".join(s.get("run", "") for s in jobs["check-tag"]["steps"])
    assert '"$GITHUB_REF_NAME" != "v$VER"' in check and "specutil.py version" in check
    assert jobs["ci"]["uses"] == "./.github/workflows/ci.yml"
    pub = jobs["publish"]
    assert pub["permissions"] == {"contents": "write"} and set(pub["needs"]) == {"check-tag", "ci"}
    assert wf["permissions"] == {"contents": "read"}
    run = "\n".join(s.get("run", "") for s in pub["steps"])
    create = next(l for l in run.splitlines() if "gh release create" in l)
    assert "--draft" in create and "--verify-tag" in create and "--notes-file notes.md" in create
    assert "gh release upload" in run and "--clobber" in run and "isDraft" in run
    assert "gh release edit" not in run.replace('gh release edit "$TAG" --draft', "")          # never published here
    for pattern in ("AURORA-$VER-windows-x64-setup.exe", "AURORA-$VER-windows-x64.zip", "AURORA-$VER-macos-arm64.dmg",
                    "AURORA-$VER-macos-x86_64.dmg"):
        assert pattern in run, pattern
    assert "sha256sum" in run and "SHA256SUMS.txt" in run and "release_notes.py" in run


def test_ci_is_callable_and_bounded():
    wf = _workflow("ci.yml")
    on = _on(wf)
    assert "workflow_call" in on and "tags" not in (on.get("push") or {})       # tags go through release.yml only
    for name, job in wf["jobs"].items():
        # the test suite takes 25 min on macos-15-intel: 45 there, 30 everywhere else
        assert job.get("timeout-minutes") == (45 if name == "tests" else 30), name
        for step in job.get("steps", []):
            run = step.get("run", "")
            if re.search(r"aurora-cli\.exe|\\AURORA\.exe|MacOS/AURORA|\$BIN|open -W", run) and "pyinstaller" not in run:
                assert step.get("timeout-minutes", 99) <= 5, (name, step.get("name"))
    names = {s.get("name", "") for j in wf["jobs"].values() for s in j.get("steps", [])}
    assert any(n.startswith("Frozen report") for n in names)
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "macos-15-intel" in text and "macos-26" in text and "macos-13" not in text and "macos-14" not in text
    assert "dist/AURORA/" not in text.replace("dist/AURORA.app", "")          # never dist/AURORA as a stand-in for the .app
    assert '-mountpoint "$MNT"' in text and "d['cwd'] == '/'" in text and "macos_minos.py" in text
    assert "/CURRENTUSER" in text and "stale.txt" in text and "unins000.exe" in text and "choco install innosetup" in text


def test_no_workflow_uses_node20_actions():
    for p in WORKFLOWS.glob("*.yml"):
        text = p.read_text(encoding="utf-8")
        for old in ("actions/checkout@v4", "actions/setup-python@v5", "actions/upload-artifact@v4",
                    "actions/download-artifact@v4"):
            assert old not in text, (p.name, old)
        for m in re.findall(r"actions/([a-z-]+)@v(\d+)", text):
            assert int(m[1]) >= {"checkout": 5, "setup-python": 6, "upload-artifact": 6, "download-artifact": 7}[m[0]], m
