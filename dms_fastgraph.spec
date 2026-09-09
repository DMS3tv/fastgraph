import os
from pathlib import Path

from dms.version import __version__


# The bundle name and identifier are build parameters so a development build
# can be installed beside the released one without replacing it:
#   FASTGRAPH_APP_NAME="FastGraph Dev" FASTGRAPH_BUNDLE_ID=com.dms.fastgraph.dev
# build_macos.sh sets both from its --name option.
APP_NAME = os.environ.get("FASTGRAPH_APP_NAME", "FastGraph Beta").strip() or "FastGraph Beta"
BUNDLE_ID = (
    os.environ.get("FASTGRAPH_BUNDLE_ID", "com.dms.fastgraph").strip()
    or "com.dms.fastgraph"
)

# A generated runtime hook makes the chosen name visible to the app itself
# (the window title reads it), without baking it into the source tree.
_hook_dir = Path("build") / "runtime_hooks"
_hook_dir.mkdir(parents=True, exist_ok=True)
_hook_path = _hook_dir / "fastgraph_app_name.py"
_hook_path.write_text(
    "import os\n"
    f"os.environ.setdefault('FASTGRAPH_APP_NAME', {APP_NAME!r})\n",
    encoding="utf-8",
)


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("HRTFs", "HRTFs"), ("Bounds", "Bounds"), ("assets", "assets")],
    hiddenimports=["PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(_hook_path)],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name=APP_NAME,
    icon="fastgraph.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX corrupts Qt DLLs on Windows; the app fails to start.
    console=False,
    disable_windowed_traceback=False,
    exclude_binaries=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,  # UPX corrupts Qt DLLs on Windows; the app fails to start.
    name=APP_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_NAME}.app",
    icon="build/FastGraph.icns",
    bundle_identifier=BUNDLE_ID,
    info_plist={
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "NSMicrophoneUsageDescription": (
            "DMS Fastgraph needs microphone access to record headphone measurements."
        ),
        "NSCameraUsageDescription": (
            "DMS Fastgraph needs camera access to attach R&D documentation photos."
        ),
    },
)
