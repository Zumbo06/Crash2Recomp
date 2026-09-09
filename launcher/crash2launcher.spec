# PyInstaller spec for the launcher.
#
# ONEDIR, deliberately, not onefile. PySide6 is LGPL v3: shipping Qt as
# separate files that a user can replace is what keeps a closed bundle
# compatible with that licence. Onefile would also unpack ~150 MB to a temp
# directory on every launch.
#
# Build:  pyinstaller launcher/crash2launcher.spec --noconfirm
# Usually invoked by tools/package.ps1 rather than by hand.

from pathlib import Path

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[(str(path), ".") for path in
           (Path(SPECPATH) / "crash2launcher" / "ui" / "assets").glob("*")
           if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".ico", ".svg"}],
    # Qt modules the launcher never touches. Excluding them takes the bundle
    # from ~400 MB to ~150 MB; each is a whole subsystem (a browser engine, a
    # 3D renderer, a charting library) that nothing here imports.
    excludes=[
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
        "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.Qt3DLogic",
        "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml",
        "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
        "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
        "PySide6.QtLocation", "PySide6.QtSerialPort", "PySide6.QtSql",
        "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio",
        "PySide6.QtTextToSpeech", "PySide6.QtSensors",
        "tkinter", "unittest", "pydoc_data",
    ],
    hookspath=[],
    runtime_hooks=[],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Crash2Launcher",
    debug=False,
    strip=False,
    upx=False,          # UPX trips antivirus heuristics on unsigned binaries
    console=False,      # a GUI app; startup failures go to a dialog (main.py)
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Crash2Launcher",
)
