#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

OS_VARIANT="$(uname -s)"

if [[ "${OS_VARIANT,,}" != "linux" ]]; then
    echo "This build script must be run on Linux."
    exit 1
fi

RUN_TESTS=false

if [ ! -z $1 ] && [ "$1" == "--test" ]; then
    RUN_TESTS="true"
elif [ "$#" != "0" ]; then
    echo "Usage: $0 [--test]"
    exit 2
fi

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt pyinstaller

if [ "$RUN_TESTS" == true ]; then
    .venv/bin/python -m pip install -r requirements-dev.txt
    PYTHONPATH=. .venv/bin/python -m pytest -q
fi

PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/curator/*.py dms/ui/*.py

rm -rf build dist

.venv/bin/python -m PyInstaller --noconfirm dms_fastgraph.spec

echo
echo "Built app bundle:"
echo "  $ROOT_DIR/dist/FastGraph Beta"
