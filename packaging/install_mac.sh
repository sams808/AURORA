#!/bin/bash
# Install or update AURORA on a Mac from the latest GitHub release, in one Terminal line:
#
#     curl -fsSL https://raw.githubusercontent.com/sams808/AURORA/main/packaging/install_mac.sh | bash
#
# Why it works the first time: files that curl downloads carry no quarantine attribute, so macOS does not stop the app
# at its first launch. A disk image downloaded with a browser, Mail, Messages or AirDrop is quarantined: AURORA is not
# notarised by Apple, so macOS then blocks its first launch until System Settings > Privacy & Security > Open Anyway.
# Before installing, this script checks the disk image against the release's SHA256SUMS.txt and verifies the app's
# signature, so nothing altered on the way is installed.
#
# What it does: checks macOS (15 Sequoia or later) and the processor (Apple silicon or Intel), downloads the matching
# disk image and SHA256SUMS.txt of the release, compares the checksum, mounts the image read-only, verifies the app's
# signature, closes a running AURORA, copies the app next to the old one and swaps it in (/Applications, or
# ~/Applications when /Applications cannot be written), verifies the installed copy, and opens AURORA.
#
# Options (environment variables):
#   AURORA_VERSION=<version>          a given release instead of the latest (the number without the v)
#   AURORA_INSTALL_DIR=/path          where AURORA.app goes (default /Applications, else ~/Applications)
#   AURORA_NO_OPEN=1                  do not open AURORA at the end
#   AURORA_SELFTEST=1                 run AURORA's self-test after installing (about a minute)
# Written for the bash 3.2 that macOS ships with.
set -eu

REPO="sams808/AURORA"
APP_NAME="AURORA.app"
MIN_MACOS=15

say() { printf '%s\n' "$*"; }
fail() {
    printf '\nAURORA was NOT installed: %s\n' "$*" >&2
    exit 1
}

# ---------------------------------------------------------------------------------------------- this Mac
[ "$(uname -s)" = "Darwin" ] || fail "this installer is for macOS."
MACOS="$(sw_vers -productVersion)"
MAJOR="${MACOS%%.*}"
case "$MAJOR" in ''|*[!0-9]*) fail "could not read the macOS version ($MACOS)." ;; esac
if [ "$MAJOR" -lt "$MIN_MACOS" ]; then
    fail "AURORA needs macOS $MIN_MACOS Sequoia or later, and this Mac has macOS $MACOS. Update macOS in System Settings > General > Software Update (if this Mac can run macOS $MIN_MACOS), then run this line again."
fi
# Apple silicon even when Terminal itself runs under Rosetta (uname -m would say x86_64 there)
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then ARCH="arm64"; CHIP="Apple silicon"
else ARCH="x86_64"; CHIP="Intel"; fi
say "This Mac: macOS $MACOS, $CHIP ($ARCH)."

# ---------------------------------------------------------------------------------------------- the release
VERSION="${AURORA_VERSION:-}"
if [ -z "$VERSION" ]; then
    # github.com/<repo>/releases/latest redirects to .../releases/tag/v<version> (no API rate limit)
    URL="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" || true)"
    VERSION="${URL##*/tag/}"
    VERSION="${VERSION#v}"
    case "$VERSION" in ''|*[!0-9.]*) fail "could not find the latest AURORA release (no internet connection?). Opened: '$URL'." ;; esac
fi
DMG_NAME="AURORA-$VERSION-macos-$ARCH.dmg"
BASE="https://github.com/$REPO/releases/download/v$VERSION"
say "Installing AURORA $VERSION ($DMG_NAME)."

TMP="$(mktemp -d "${TMPDIR:-/tmp}/aurora-install.XXXXXX")"
MNT="$TMP/mnt"
cleanup() {
    if [ -d "$MNT" ]; then        # detach whatever is mounted there (a folder that is no mount point: a harmless error)
        hdiutil detach -quiet "$MNT" 2>/dev/null || hdiutil detach -force -quiet "$MNT" 2>/dev/null || true
    fi
    rm -rf "$TMP" 2>/dev/null || true
}
trap cleanup EXIT

say "Downloading (about 100 MB) ..."
curl -fL --retry 3 --progress-bar -o "$TMP/$DMG_NAME" "$BASE/$DMG_NAME" || fail "the download of $BASE/$DMG_NAME failed."
curl -fsSL --retry 3 -o "$TMP/SHA256SUMS.txt" "$BASE/SHA256SUMS.txt" || fail "the download of SHA256SUMS.txt failed."

# ---------------------------------------------------------------------------------------------- checks
EXPECTED="$(awk -v f="$DMG_NAME" '$2 == f || $2 == "*" f {print $1}' "$TMP/SHA256SUMS.txt")"
ACTUAL="$(shasum -a 256 "$TMP/$DMG_NAME" | awk '{print $1}')"
[ -n "$EXPECTED" ] || fail "SHA256SUMS.txt of the release does not list $DMG_NAME."
[ "$EXPECTED" = "$ACTUAL" ] || fail "the disk image does not match the release's checksum (expected $EXPECTED, got $ACTUAL). Run the line again."
say "Checksum OK."

mkdir -p "$MNT"
ATTACHED=""
for attempt in 1 2 3; do
    if hdiutil attach -readonly -nobrowse -noautoopen -quiet -mountpoint "$MNT" "$TMP/$DMG_NAME"; then ATTACHED=1; break; fi
    sleep $((attempt * 3))
done
[ -n "$ATTACHED" ] || fail "the disk image could not be opened."
[ -d "$MNT/$APP_NAME" ] || fail "the disk image holds no $APP_NAME."
codesign --verify --deep --strict "$MNT/$APP_NAME" 2>/dev/null || fail "the signature of the app in the disk image does not verify."
say "Signature OK."

# ---------------------------------------------------------------------------------------------- install
DEST="${AURORA_INSTALL_DIR:-}"
if [ -z "$DEST" ]; then
    if [ -w /Applications ]; then DEST="/Applications"; else DEST="$HOME/Applications"; fi
fi
mkdir -p "$DEST" || fail "could not create the folder $DEST."
[ -w "$DEST" ] || fail "this account cannot write in $DEST. Run the line from an administrator account, or set AURORA_INSTALL_DIR to a folder of your own."

if pgrep -x AURORA >/dev/null 2>&1; then         # asked, not forced: no Automation prompt, no edit cut short
    fail "AURORA is running: quit it first (menu AURORA > Quit AURORA, or Command-Q), then run the line again."
fi

# a complete new copy next to the old one, then swapped in (never a half-written app)
NEW="$DEST/.AURORA-$VERSION-installing.app"
rm -rf "$NEW"
ditto "$MNT/$APP_NAME" "$NEW" || fail "could not copy AURORA into $DEST (is the disk full?)."
hdiutil detach -quiet "$MNT" 2>/dev/null || true
if [ -e "$DEST/$APP_NAME" ]; then
    if ! rm -rf "$DEST/$APP_NAME" 2>/dev/null; then
        rm -rf "$NEW"
        fail "macOS did not let Terminal replace the AURORA already in $DEST. Allow it in System Settings > Privacy & Security > App Management (switch Terminal on), or drag the old AURORA to the Trash, then run the line again."
    fi
fi
mv "$NEW" "$DEST/$APP_NAME" || fail "could not move AURORA into place in $DEST."
APP="$DEST/$APP_NAME"
xattr -dr com.apple.quarantine "$APP" 2>/dev/null || true      # nothing to remove after curl; harmless
codesign --verify --deep --strict "$APP" 2>/dev/null || fail "the installed copy's signature does not verify."
GOT="$("$APP/Contents/MacOS/AURORA" --version 2>/dev/null || true)"
say "Installed: $APP ($GOT)."

if [ "${AURORA_SELFTEST:-0}" = "1" ]; then
    say "Running the self-test (about a minute) ..."
    "$APP/Contents/MacOS/AURORA" --selftest --out "$TMP/selftest.json" || fail "the self-test failed (see above)."
fi

if [ "${AURORA_NO_OPEN:-0}" != "1" ]; then
    say "Opening AURORA ..."
    open "$APP" || fail "AURORA is installed in $DEST but did not open: open it from there."
fi
say ""
say "Done. AURORA $VERSION is in $DEST: open it from there, or with Spotlight (Command-Space, AURORA)."
say "To update later, run the same line again."
