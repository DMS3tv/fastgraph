from dms.version import __version__


a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("HRTFs", "HRTFs"), ("Bounds", "Bounds"), ("assets", "assets")],
    hiddenimports=["PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets"],
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
    name="FastGraph Beta",
)

app = BUNDLE(
    coll,
    name="FastGraph Beta.app",
    icon="build/FastGraph.icns",
    bundle_identifier="com.dms.fastgraph",
    info_plist={
        "CFBundleName": "FastGraph Beta",
        "CFBundleDisplayName": "FastGraph Beta",
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
