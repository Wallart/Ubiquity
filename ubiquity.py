"""Ubiquity — lance l'application avec icône dans la barre de menu."""
import sys

# ── Single-instance guard ──────────────────────────────────────────────────
# LSUIElement apps have no Dock presence, so macOS does not prevent multiple
# instances from launching.  The Dock bounce (below) briefly shows an icon
# that a user may click, starting a second copy.  We use an exclusive flock
# on a per-user lock file to detect this and silently exit the duplicate.
_instance_lock_fh = None

def _acquire_instance_lock():
    global _instance_lock_fh
    try:
        import fcntl
        from pathlib import Path
        lock_path = Path.home() / '.ubiquity' / 'ubiquity.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        _instance_lock_fh = open(lock_path, 'w')
        fcntl.flock(_instance_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        import os
        _instance_lock_fh.write(str(os.getpid()))
        _instance_lock_fh.flush()
    except ImportError:
        pass  # Windows — flock not available, skip guard
    except OSError:
        sys.exit(0)  # Another instance holds the lock — exit silently

_acquire_instance_lock()

# ── Dock bounce ────────────────────────────────────────────────────────────
# Bounce the dock icon immediately, before heavy imports start.
# The switch back to Accessory happens after imports but BEFORE pystray
# initialises — this avoids the second-tray bug caused by changing the
# activation policy while pystray's status bar item is already running.
if sys.platform == 'darwin':
    try:
        import AppKit as _AppKit
        _ns = _AppKit.NSApplication.sharedApplication()
        _ns.setActivationPolicy_(_AppKit.NSApplicationActivationPolicyRegular)
        _ns.requestUserAttention_(_AppKit.NSInformationalRequest)
    except Exception:
        pass

# Heavy imports — PIL, pystray, tkinter… give the bounce time to show.
from ubiquity.tray import TrayApp, setup_logging  # noqa: E402

# Restore Accessory policy before pystray creates the status bar item.
if sys.platform == 'darwin':
    try:
        import AppKit as _AppKit
        _AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            _AppKit.NSApplicationActivationPolicyAccessory
        )
    except Exception:
        pass

if __name__ == '__main__':
    setup_logging()
    TrayApp().run()
