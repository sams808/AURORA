#!/usr/bin/env bash
# Build AURORA.app and its disk image from a CLEAN virtual environment. Run from the repo root on a Mac:
#     packaging/build_mac.sh
# Output: dist/AURORA.app, dist/AURORA-<version>-macos-<arm64|x86_64>.dmg. A Mac with Apple silicon builds the arm64
# image, an Intel Mac the x86_64 image (CI builds both).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
VENV="$ROOT/.venv-build"
[ -x "$VENV/bin/python" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet "$ROOT[build]"
VERSION="$("$VENV/bin/python" -c 'import aurora; print(aurora.__version__)')"
ARCH="$(uname -m)"
"$VENV/bin/python" -m PyInstaller --noconfirm --clean --log-level WARN packaging/aurora.spec --distpath "$ROOT/dist" --workpath "$ROOT/build"
APP="$ROOT/dist/AURORA.app"
# licences next to the executable (inside the bundle's Resources)
"$VENV/bin/python" packaging/third_party_licenses.py "$APP/Contents/Resources/THIRD_PARTY_LICENSES.txt"
mkdir -p "$APP/Contents/Resources/LICENSES" && cp packaging/LICENSES/*.txt "$APP/Contents/Resources/LICENSES/"
# adding files invalidates the seal: re-sign ad hoc (arm64 refuses to run an invalid signature)
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
"$APP/Contents/MacOS/AURORA" --selftest --out "$ROOT/dist/selftest-macos-$ARCH.json"
packaging/make_dmg.sh "$APP" "$ROOT/dist/AURORA-$VERSION-macos-$ARCH.dmg"
echo "built $APP and dist/AURORA-$VERSION-macos-$ARCH.dmg"
