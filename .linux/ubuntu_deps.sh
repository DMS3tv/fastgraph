#!/usr/bin/env bash
set -euo pipefail

if (( EUID != 0 )); then
    echo "Run this script as root: sudo ./.linux/ubuntu_deps.sh"
    exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
                binutils \
                build-essential \
                git \
                libatk1.0-0t64 \
                libcairo2 \
                libcairo-gobject2 \
                libdbus-1-3 \
                libegl1 \
                libfontconfig1 \
                libfreetype6 \
                libgdk-pixbuf-2.0-0 \
                libgl1 \
                libglib2.0-0t64 \
                libgssapi-krb5-2 \
                libgtk-3-0t64 \
                libpango-1.0-0 \
                libpangocairo-1.0-0 \
                libpython3.14 \
                libx11-6 \
                libx11-xcb1 \
                libxcb-glx0 \
                libxkbcommon0 \
                libportaudio2 \
                libxkbcommon-x11-0 \
                libxcb-cursor0 \
                libxcb-icccm4 \
                libxcb-keysyms1 \
                libxcb-randr0 \
                libxcb-render0 \
                libxcb-render-util0 \
                libxcb-shape0 \
                libxcb-shm0 \
                libxcb-sync1 \
                libxcb-util1 \
                libxcb-xfixes0 \
                libxcb-xkb1 \
                python3 \
                python3-venv
