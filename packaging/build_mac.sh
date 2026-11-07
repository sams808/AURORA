#!/usr/bin/env bash
# Build AURORA.app from a clean virtual environment. Run from the repo root:  packaging/build_mac.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv-build"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -e "$ROOT[build]"
"$VENV/bin/python" -m PyInstaller --noconfirm --clean --log-level WARN "$ROOT/packaging/aurora.spec" --distpath "$ROOT/dist" --workpath "$ROOT/build"
"$ROOT/dist/AURORA/AURORA" --distcheck
echo "built $ROOT/dist/AURORA.app"
