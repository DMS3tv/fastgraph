import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

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

# An optional brand plugin (see dms/branding.py) is bundled only when it is
# named at build time: FASTGRAPH_BRAND_PLUGIN=<module> ./build_macos.sh ...
BRAND_PLUGIN = os.environ.get("FASTGRAPH_BRAND_PLUGIN", "").strip()
_brand_imports = collect_submodules(BRAND_PLUGIN) if BRAND_PLUGIN else []
_brand_datas = collect_data_files(BRAND_PLUGIN) if BRAND_PLUGIN else []

# A generated runtime hook makes the chosen name (and brand plugin) visible to
# the app itself (the window title reads it), without baking it into the
# source tree.
_hook_dir = Path("build") / "runtime_hooks"
_hook_dir.mkdir(parents=True, exist_ok=True)
_hook_path = _hook_dir / "fastgraph_app_name.py"
_hook_lines = ["import os", f"os.environ.setdefault('FASTGRAPH_APP_NAME', {APP_NAME!r})"]
if BRAND_PLUGIN:
    _hook_lines.append(f"os.environ.setdefault('FASTGRAPH_BRAND_PLUGIN', {BRAND_PLUGIN!r})")
_hook_path.write_text("\n".join(_hook_lines) + "\n", encoding="utf-8")


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("HRTFs", "HRTFs"), ("Bounds", "Bounds"), *_brand_datas],
    hiddenimports=["PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets", *_brand_imports],
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
