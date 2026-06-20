#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This build script must be run on Linux."
    exit 1
fi

usage() {
    echo "Usage: $0 [--test]"
}

if (( $# > 1 )); then
    usage
    exit 2
fi

RUN_TESTS=false
case "${1:-}" in
    "") ;;
    --test) RUN_TESTS=true ;;
    *)
        usage
        exit 2
        ;;
esac

if [[ ! -x ".venv/bin/python" ]]; then
    python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt pyinstaller

if [[ "$RUN_TESTS" == true ]]; then
    .venv/bin/python -m pip install -r requirements-dev.txt
    PYTHONPATH=. .venv/bin/python -m pytest -q
fi

PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/curator/*.py dms/ui/*.py

rm -rf build dist

.venv/bin/python -m PyInstaller --noconfirm dms_fastgraph.spec

echo
echo "Built app bundle:"
echo "  $ROOT_DIR/dist/FastGraph Beta"
