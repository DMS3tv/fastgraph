from dms.version import __version__


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("HRTFs", "HRTFs")],
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
    name="FastGraph Beta",
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
    name="FastGraph Beta",
)

app = BUNDLE(
    coll,
    name="FastGraph Beta.app",
    icon="fastgraph icon.png",
    bundle_identifier="com.dms.fastgraph",
    info_plist={
        "CFBundleName": "FastGraph Beta",
        "CFBundleDisplayName": "FastGraph Beta",
        "CFBundleShortVersionString": __version__,
        "CFBundleVersion": __version__,
        "NSMicrophoneUsageDescription": (
            "DMS Fastgraph needs microphone access to record headphone measurements."
        ),
    },
)
