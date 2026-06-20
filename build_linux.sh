#!/usr/bin/env bash
set -euo pipefail

#####
# Build on Linux.
# 
# Pretty similar to the MacOS version but left separate
# cause further work _may_ require bash >= 4 which OSX
# doesn't ship with out of the box and Linux (generally) does.
#
# Note: Tested with Ubuntu 26.04 (Resolute).
# Other note: I've tried Alpine Linux, sadly PyQt6 doesn't seem to have a musl variant.
# 
# For other distributions this will require some
# work to determine what dependencies need to be installed.
#
# For an example, see .linux/ubuntu_deps.sh.
#####

# Dave is still a legend for this one.
# https://stackoverflow.com/a/246128
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
cd "${SCRIPT_DIR}"

# Build .venv if it doesn't exist.
if [ ! -d ".venv/" ]; then
    python3 -m venv .venv
fi

# Check if requirements.txt has changed and install stuff if it has.
REQUIREMENTS_SHAFILE="requirements.txt.sha256"
set +e
sha256sum -c "${REQUIREMENTS_SHAFILE}"
set -e
SHARES="$?"

if [ ! -f "${REQUIREMENTS_SHAFILE}" ] || [ "${SHARES}" != "0" ]; then
    .venv/bin/python -m pip install --upgrade pip
    .venv/bin/pip install pyinstaller
    .venv/bin/pip install -r requirements.txt
    
    if [ "$1" == "--test" ]; then
        .venv/bin/pip install -r requirements-dev.txt
        PYTHONPATH=. .venv/bin/pytest -q
    fi
fi

rm -rf build dist

.venv/bin/python -m PyInstaller dms_fastgraph.spec

echo
echo "Built app bundle:"
echo "  ${PWD}/dist/FastGraph Beta"

sha256sum requirements.txt > "${REQUIREMENTS_SHAFILE}"
