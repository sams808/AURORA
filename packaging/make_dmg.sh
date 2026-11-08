#!/usr/bin/env bash
# Disk image for one architecture:  packaging/make_dmg.sh dist/AURORA.app dist/AURORA-<version>-macos-<arch>.dmg
# The image holds AURORA.app, a shortcut to /Applications and the first-launch note (Gatekeeper on macOS 15+).
set -euo pipefail
APP="$1"
OUT="$2"
HERE="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/AURORA.app"                      # ditto keeps the code signature and extended attributes
ln -s /Applications "$STAGE/Applications"
cp "$HERE/macos_first_launch.txt" "$STAGE/If macOS blocks AURORA - read me.txt"
codesign --verify --deep --strict --verbose=2 "$STAGE/AURORA.app"
rm -f "$OUT"
hdiutil create -volname "AURORA $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "$OUT"
hdiutil verify "$OUT"
echo "wrote $OUT"
