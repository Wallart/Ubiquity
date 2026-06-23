# PyInstaller spec — one-file executable for macOS, Windows, and Linux.
#
# Build:
#   macOS   →  ./build.sh          →  dist/Ubiquity.app  +  dist/Ubiquity.dmg
#   Windows →  build.bat           →  dist/ubiquity.exe
#              build.bat installer →  dist/UbiquitySetup.exe  (no-admin Inno Setup)
#   Linux   →  ./build.sh          →  dist/ubiquity

import sys

block_cipher = None

hidden_imports = [
    # watchdog platform backends (loaded at runtime via importlib)
    'watchdog.observers',
    'watchdog.observers.fsevents',
    'watchdog.observers.inotify',
    'watchdog.observers.winapi',
    'watchdog.observers.polling',
    # pystray platform backends
    'pystray._darwin',
    'pystray._win32',
    'pystray._xorg',
    # pyperclip platform helpers
    'pyperclip.handlers',
    # settings dialog
    'tkinter',
    'tkinter.filedialog',
]

a = Analysis(
    ['ubiquity.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=hidden_imports,
    hookspath=['hooks'],
    runtime_hooks=[],
    excludes=[
        # Google Cloud namespace — not used, crashes if google-cloud-core absent
        'google', 'google.cloud',
        # Data science stack — not used by Ubiquity
        'numpy', 'pandas', 'scipy', 'matplotlib',
        'sklearn', 'skimage', 'cv2',
        'IPython', 'jupyter', 'notebook',
        # Test frameworks
        'pytest', 'unittest',
        # Misc heavy packages unlikely to be needed
        'xmlrpc', 'email', 'html', 'http.server',
        'multiprocessing',
    ],
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
    # No console window — this is a tray app, not a CLI tool.
    console=False,
    onefile=True,
    icon='assets/ubiquity.ico',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='Ubiquity.app',
        icon='assets/ubiquity.icns',
        bundle_identifier='com.ubiquity.sync',
        info_plist={
            # Hide from Dock — the app lives entirely in the menu bar.
            'LSUIElement': True,
            'NSHighResolutionCapable': True,
            'CFBundleShortVersionString': '1.0',
        },
    )
