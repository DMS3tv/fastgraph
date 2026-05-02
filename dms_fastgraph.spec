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
    a.binaries,
    a.datas,
    [],
    name="DMS Fastgraph",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

app = BUNDLE(
    exe,
    name="DMS Fastgraph.app",
    icon=None,
    bundle_identifier="com.dms.fastgraph",
    info_plist={
        "CFBundleName": "DMS Fastgraph",
        "CFBundleDisplayName": "DMS Fastgraph",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "NSMicrophoneUsageDescription": (
            "DMS Fastgraph needs microphone access to record headphone measurements."
        ),
    },
)
