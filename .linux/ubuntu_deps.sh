#!/usr/bin/env bash
set -euo pipefail

if (( EUID != 0 )); then
    echo "Run this script as root: sudo ./.linux/ubuntu_deps.sh"
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

# Ubuntu 24.04 renamed several libraries with a "t64" suffix for the 64-bit
# time_t transition, and every release ships a different libpythonX.Y. Read the
# release and pick package names that actually exist on this machine, so the
# same script works on the 22.04 CI runner and on a current desktop.
RELEASE_ID=""
RELEASE_VERSION=""
if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    RELEASE_ID="${ID:-}"
    RELEASE_VERSION="${VERSION_ID:-}"
fi

RELEASE_MAJOR="${RELEASE_VERSION%%.*}"
if [[ "$RELEASE_MAJOR" =~ ^[0-9]+$ ]] && (( RELEASE_MAJOR < 24 )); then
    T64=""      # 22.04 and older: libatk1.0-0, libglib2.0-0, libgtk-3-0
else
    T64="t64"   # 24.04 and newer: the same libraries with a t64 suffix
fi

echo "Detected ${RELEASE_ID:-unknown} ${RELEASE_VERSION:-unknown}; using '${T64:-non-t64}' library names."

PACKAGES=(
    binutils
    build-essential
    git
    "libatk1.0-0${T64}"
    libcairo2
    libcairo-gobject2
    libdbus-1-3
    libegl1
    libfontconfig1
    libfreetype6
    libgdk-pixbuf-2.0-0
    libgl1
    "libglib2.0-0${T64}"
    libgssapi-krb5-2
    "libgtk-3-0${T64}"
    libpango-1.0-0
    libpangocairo-1.0-0
    libportaudio2
    libx11-6
    libx11-xcb1
    libxcb-cursor0
    libxcb-glx0
    libxcb-icccm4
    libxcb-keysyms1
    libxcb-randr0
    libxcb-render0
    libxcb-render-util0
    libxcb-shape0
    libxcb-shm0
    libxcb-sync1
    libxcb-util1
    libxcb-xfixes0
    libxcb-xkb1
    libxkbcommon0
    libxkbcommon-x11-0
    python3
    python3-venv
)

apt-get update

# libpythonX.Y follows the system interpreter: 3.10 on 22.04, 3.12 on 24.04, and
# so on. Only add it if this release actually packages it.
PYTHON_MINOR="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
if [ -n "$PYTHON_MINOR" ] && apt-cache show "libpython${PYTHON_MINOR}" >/dev/null 2>&1; then
    PACKAGES+=("libpython${PYTHON_MINOR}")
else
    echo "No libpython${PYTHON_MINOR:-?} package on this release; skipping it."
fi

apt-get install -y --no-install-recommends "${PACKAGES[@]}"
