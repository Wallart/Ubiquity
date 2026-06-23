# PyInstaller spec — one-file executable for macOS, Windows, and Linux.
#
# Build:
#   macOS   →  ./build.sh          →  dist/Ubiquity.app  +  dist/Ubiquity.dmg
#   Windows →  build.bat           →  dist/ubiquity.exe
#              build.bat installer →  dist/UbiquitySetup.exe  (no-admin Inno Setup)
#   Linux   →  ./build.sh          →  dist/ubiquity

import subprocess
import sys
from pathlib import Path

block_cipher = None


def _build_excludes():
    """Compute excludes dynamically: pip list minus requirements.txt deps."""
    req_file = Path(SPECPATH) / 'requirements.txt'
    needed = set()
    if req_file.exists():
        for line in req_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            pkg = line.split('>')[0].split('<')[0].split('=')[0].split('!')[0].strip()
            needed.add(pkg.lower().replace('-', '_'))
            try:
                out = subprocess.check_output(
                    ['pip', 'show', pkg], text=True, stderr=subprocess.DEVNULL
                )
                for dep_line in out.splitlines():
                    if dep_line.startswith('Requires:'):
                        for dep in dep_line.split(':', 1)[1].split(','):
                            dep = dep.strip().lower().replace('-', '_')
                            if dep:
                                needed.add(dep)
            except Exception:
                pass

    # Never exclude: PyInstaller internals + macOS system glue for pystray
    safe = {
        'setuptools', 'pip', 'wheel', 'packaging', 'importlib_metadata', 'zipp',
        'pyobjc_core', 'pyobjc_framework_cocoa', 'pyobjc_framework_corebluetooth',
        'pyobjc_framework_libdispatch', 'pyobjc_framework_quartz',
        'cffi', 'pycparser', 'six', 'typing_extensions', 'platformdirs', 'tk',
    }

    try:
        out = subprocess.check_output(
            ['pip', 'list', '--format=freeze'], text=True, stderr=subprocess.DEVNULL
        )
        installed = {
            line.split('=')[0].strip().lower().replace('-', '_')
            for line in out.splitlines() if line.strip()
        }
    except Exception:
        installed = set()

    excludes = sorted(installed - needed - safe)
    # Always exclude google.cloud — its hook crashes when google-cloud-core is absent
    for extra in ('google.cloud', 'google'):
        if extra not in excludes:
            excludes.insert(0, extra)
    return excludes


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
    # GCD main-thread dispatch (macOS only)
    'libdispatch',
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
    excludes=_build_excludes(),
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if sys.platform == 'darwin':
    # ── macOS: onedir inside the .app bundle (no temp extraction = fast startup) ──
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,   # binaries go into COLLECT, not the executable
        name='ubiquity',
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        console=False,
        icon='assets/ubiquity.ico',
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        name='ubiquity',
    )
    app = BUNDLE(
        coll,
        name='Ubiquity.app',
        icon='assets/ubiquity.icns',
        bundle_identifier='com.ubiquity.sync',
        info_plist={
            'LSUIElement': True,
            'NSHighResolutionCapable': True,
            'CFBundleShortVersionString': '1.0',
        },
    )
else:
    # ── Windows / Linux: single executable for easy distribution ──
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
        console=False,
        onefile=True,
        icon='assets/ubiquity.ico',
    )
