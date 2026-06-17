# PyInstaller spec — produces a single-file executable on macOS and Windows.
# Build:
#   macOS   →  pyinstaller ubiquity.spec
#   Windows →  pyinstaller ubiquity.spec
#
# Output: dist/ubiquity  (macOS)  or  dist/ubiquity.exe  (Windows)

import sys

block_cipher = None

hidden_imports = [
    # watchdog uses platform-specific backends loaded at runtime
    'watchdog.observers',
    'watchdog.observers.fsevents',   # macOS
    'watchdog.observers.inotify',    # Linux
    'watchdog.observers.winapi',     # Windows
    'watchdog.observers.polling',    # fallback
    # pystray platform backends
    'pystray._darwin',
    'pystray._win32',
    # tkinter (settings dialog)
    'tkinter',
    'tkinter.filedialog',
]

a = Analysis(
    ['tray_app.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='ubiquity',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    onefile=True,
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='Ubiquity.app',
        bundle_identifier='com.ubiquity.sync',
    )
