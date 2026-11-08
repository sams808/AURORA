#!/usr/bin/env bash
# Build AURORA.app and its disk image from a CLEAN virtual environment. Run from the repo root on a Mac:
#     packaging/build_mac.sh
# Output: dist/AURORA.app, dist/AURORA-<version>-macos-<arm64|x86_64>.dmg. A Mac with Apple silicon builds the arm64
# image, an Intel Mac the x86_64 image (CI builds both).
# The build environment .venv-build is Python 3.12 (uv, else python3.12) with the pins of packaging/constraints.txt; it
# is recreated when its Python, constraints.txt or pyproject.toml changed. The bundle's real minimum macOS is measured
# (packaging/macos_minos.py) and the build stops when it exceeds packaging/min_macos.txt.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VENV="$ROOT/.venv-build"
PYV="$VENV/bin/python"
STAMPFILE="$VENV/aurora-build-stamp.txt"

# ------------------------------------------------------------------ build environment (recreated when stale)
NOW=""
HAVE=""
[ -x "$PYV" ] && NOW="$("$PYV" packaging/specutil.py stamp 2>/dev/null || true)"
[ -f "$STAMPFILE" ] && HAVE="$(head -n 1 "$STAMPFILE")"
if [ -z "$NOW" ] || [ "$NOW" != "$HAVE" ] || [ "${NOW#py3.12.}" = "$NOW" ]; then
    if [ -d "$VENV" ]; then echo "recreating the stale build environment $VENV"; rm -rf "$VENV"; fi
    if command -v uv >/dev/null 2>&1; then
        uv venv --seed --python 3.12 "$VENV"
    elif command -v python3.12 >/dev/null 2>&1; then
        python3.12 -m venv "$VENV"
    else
        echo "AURORA builds with Python 3.12: install it (brew install python@3.12) or uv (brew install uv), then run this script again."
        exit 1
    fi
    "$PYV" -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" || { echo "the build environment is not Python 3.12"; exit 1; }
    "$PYV" -m pip install --quiet --upgrade pip
    "$PYV" -m pip install --quiet "$ROOT[build]" -c "$ROOT/packaging/constraints.txt"
    "$PYV" packaging/specutil.py stamp > "$STAMPFILE"
fi
# AURORA itself, non-editable, every time (its metadata carries the version being built)
"$PYV" -m pip install --quiet --no-deps --force-reinstall "$ROOT" -c "$ROOT/packaging/constraints.txt"
VERSION="$("$PYV" packaging/specutil.py version)"
ARCH="$(uname -m)"
echo "building AURORA $VERSION for $ARCH with Python 3.12 from $VENV"

# ------------------------------------------------------------------ freeze, sign, check, self-test, disk image
"$PYV" -m PyInstaller --noconfirm --clean --log-level WARN packaging/aurora.spec --distpath "$ROOT/dist" --workpath "$ROOT/build"
APP="$ROOT/dist/AURORA.app"
# the licences are inside the bundle (Contents/Resources/aurora/resources/licenses); nothing is added afterwards, but
# re-sign ad hoc anyway (arm64 refuses to run an invalid signature) and verify
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
"$PYV" packaging/macos_minos.py "$APP" --check packaging/min_macos.txt --arch "$ARCH"
"$APP/Contents/MacOS/AURORA" --selftest --out "$ROOT/dist/selftest-macos-$ARCH.json"
packaging/make_dmg.sh "$APP" "$ROOT/dist/AURORA-$VERSION-macos-$ARCH.dmg"
echo "built $APP and dist/AURORA-$VERSION-macos-$ARCH.dmg"
