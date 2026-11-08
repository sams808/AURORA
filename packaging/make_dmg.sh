#!/usr/bin/env bash
# Disk image for one architecture:  packaging/make_dmg.sh dist/AURORA.app dist/AURORA-<version>-macos-<arch>.dmg
# The image holds AURORA.app, a shortcut to /Applications, the first-launch note 'If macOS blocks AURORA.txt'
# (Privacy & Security > Open Anyway, the xattr command, the minimum macOS from packaging/min_macos.txt) and the licences
# (LICENSE, CREDITS.md, THIRD_PARTY_LICENSES.txt, LICENSES/).
set -euo pipefail
APP="$1"
OUT="$2"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")"
MIN_MACOS="$(tr -d '[:space:]' < "$HERE/min_macos.txt")"
LICDIR="$APP/Contents/Resources/aurora/resources/licenses"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/AURORA.app"                      # ditto keeps the code signature and extended attributes
ln -s /Applications "$STAGE/Applications"
sed "s/@MIN_MACOS@/$MIN_MACOS/g" "$HERE/macos_first_launch.txt" > "$STAGE/If macOS blocks AURORA.txt"
cp "$ROOT/LICENSE" "$ROOT/CREDITS.md" "$STAGE/"
if [ -f "$LICDIR/THIRD_PARTY_LICENSES.txt" ]; then cp "$LICDIR/THIRD_PARTY_LICENSES.txt" "$STAGE/"; fi
mkdir -p "$STAGE/LICENSES" && cp "$HERE"/LICENSES/*.txt "$STAGE/LICENSES/"
codesign --verify --deep --strict --verbose=2 "$STAGE/AURORA.app"
rm -f "$OUT"
hdiutil create -volname "AURORA $VERSION" -srcfolder "$STAGE" -fs HFS+ -format UDZO -imagekey zlib-level=9 -ov "$OUT"
hdiutil verify "$OUT"
echo "wrote $OUT"
