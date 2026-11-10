"""Release notes of one version: its '## <version>' section of CHANGELOG.md, then the downloads, the minimum macOS and
the first-launch notes (Gatekeeper, SmartScreen). Used by .github/workflows/release.yml:

    python packaging/release_notes.py <version> notes.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

ASSETS = ("AURORA-{v}-windows-x64-setup.exe", "AURORA-{v}-windows-x64.zip", "AURORA-{v}-macos-arm64.dmg",
          "AURORA-{v}-macos-x86_64.dmg")


def extract(changelog: str, version: str) -> str:
    """The '## <version>' section (heading included) up to the next '## ' heading; '' when there is none. The version
    must be followed by a space, '(' or the end of the line, so it never matches a longer version that starts with it
    (a 10 after a 1, or an -rc suffix)."""
    pat = re.compile(rf"^## {re.escape(version)}(?=[ \t(]|$).*?(?=^## |\Z)", re.S | re.M)
    m = pat.search(changelog)
    return m.group(0).strip() + "\n" if m else ""


def asset_names(version: str) -> list[str]:
    return [a.format(v=version) for a in ASSETS]


def notes(version: str, changelog: str, min_macos: str) -> str:
    section = extract(changelog, version)
    if not section:
        raise SystemExit(f"CHANGELOG.md has no '## {version}' section: write it before tagging v{version}")
    win_setup, win_zip, mac_arm, mac_intel = asset_names(version)
    return f"""{section}
### Downloads
- Windows 10 and 11 (64-bit): `{win_setup}`, a per-user installer that needs no administrator rights; or `{win_zip}`,
  the same application to unpack and run anywhere.
- macOS {min_macos} or later on Apple silicon (M1 and later): `{mac_arm}`; on an Intel Mac: `{mac_intel}`.
- `SHA256SUMS.txt`: the SHA-256 checksum of every file above.

### First launch
- Windows: SmartScreen may say "Windows protected your PC" because the installer is not code-signed. Click
  More info > Run anyway.
- macOS: AURORA is not notarised by Apple. After the first attempt to open it, go to System Settings >
  Privacy & Security and click Open Anyway, or run `xattr -dr com.apple.quarantine /Applications/AURORA.app` in
  Terminal. The note "If macOS blocks AURORA.txt" inside the disk image explains both.
"""


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    sys.path.insert(0, str(HERE))
    import specutil
    version = argv[0].lstrip("v")
    text = notes(version, (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), specutil.min_macos())
    if len(argv) > 1:
        Path(argv[1]).write_text(text, encoding="utf-8")
        print(f"wrote {argv[1]}")
    else:
        try:                                  # a Windows console or pipe is cp1252, the notes hold '▸' and 'α'
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
