from dms.version import __version__


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name="DMS Fastgraph Beta",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
    upx=True,
    name="DMS Fastgraph Beta",
)

app = BUNDLE(
    coll,
    name="DMS Fastgraph Beta.app",
    icon="fastgraph icon.png",
    bundle_identifier="com.dms.fastgraph",
    info_plist={
        "CFBundleName": "DMS Fastgraph Beta",
        "CFBundleDisplayName": "DMS Fastgraph Beta",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "NSMicrophoneUsageDescription": (
            "DMS Fastgraph needs microphone access to record headphone measurements."
        ),
    },
)
