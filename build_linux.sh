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

# "$1" must be guarded: set -u makes a bare $1 fatal when the script is called
# with no arguments, which is how CI invokes it.
if [ "$#" -eq 0 ]; then
    :
elif [ "$#" -eq 1 ] && [ "${1}" == "--test" ]; then
    RUN_TESTS=true
else
    echo "Usage: $0 [--test]"
    exit 2
fi

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

# requirements.lock is the fully pinned build environment (app deps, test deps
# and PyInstaller) so release builds are reproducible. Regenerate it after an
# intentional upgrade with:
#   .venv/bin/python -m pip freeze > requirements.lock
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock

# CI runs the test suite in its own job before any build starts; --test stays
# here for local builds.
if [ "$RUN_TESTS" == true ]; then
    PYTHONPATH=. .venv/bin/python -m pytest -q -n auto
fi

PYTHONPATH=. .venv/bin/python -m py_compile main.py dms/*.py dms/curator/*.py dms/ui/*.py

rm -rf build dist

.venv/bin/python -m PyInstaller --noconfirm dms_fastgraph.spec

echo
echo "Built app bundle:"
echo "  $ROOT_DIR/dist/FastGraph Beta"
