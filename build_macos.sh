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
  echo "  pip install -r requirements.lock"
  exit 1
fi

# requirements.lock is the fully pinned build environment (app deps, test deps
# and PyInstaller) so release builds are reproducible. Regenerate it after an
# intentional upgrade with:
#   .venv/bin/python -m pip freeze > requirements.lock
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock

rm -rf build dist

ICON_SOURCE="$ROOT_DIR/fastgraph icon.png"
ICONSET="$ROOT_DIR/build/FastGraph.iconset"
MACOS_ICON="$ROOT_DIR/build/FastGraph.icns"

if [[ ! -f "$ICON_SOURCE" ]]; then
  echo "Missing icon source: $ICON_SOURCE"
  exit 1
fi

mkdir -p "$ICONSET"
sips -z 16 16 "$ICON_SOURCE" --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$ICON_SOURCE" --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 "$ICON_SOURCE" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$ICON_SOURCE" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$ICON_SOURCE" --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$ICON_SOURCE" --out "$ICONSET/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$ICON_SOURCE" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
iconutil -c icns "$ICONSET" -o "$MACOS_ICON"

.venv/bin/python -m PyInstaller --noconfirm dms_fastgraph.spec

echo
echo "Built app bundle:"
echo "  $ROOT_DIR/dist/FastGraph Beta.app"
