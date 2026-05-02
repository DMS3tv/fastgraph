#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This build script must be run on macOS."
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Missing .venv. Create it first with:"
  echo "  python3 -m venv .venv"
  echo "  source .venv/bin/activate"
  echo "  pip install -r requirements.txt pyinstaller"
  exit 1
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt pyinstaller

rm -rf build dist

.venv/bin/python -m PyInstaller --noconfirm dms_fastgraph.spec

echo
echo "Built app bundle:"
echo "  $ROOT_DIR/dist/DMS Fastgraph.app"
