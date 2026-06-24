"""Ubiquity — lance l'application avec icône dans la barre de menu."""
import os
import sys

# ── Single-instance guard ──────────────────────────────────────────────────
# LSUIElement apps have no Dock presence, so macOS does not prevent multiple
# instances from launching (e.g. the user clicking the bouncing Dock icon).
# Query NSRunningApplication for another process with our bundle identifier
# and exit silently if one is already running.  This avoids any lock file
# that could interfere with clean quit + relaunch cycles.
if sys.platform == 'darwin':
    try:
        import AppKit as _AppKit
        _others = [
            a for a in _AppKit.NSRunningApplication
                .runningApplicationsWithBundleIdentifier_('com.ubiquity.sync')
            if a.processIdentifier() != os.getpid()
        ]
        if _others:
            sys.exit(0)
    except Exception:
        pass

# ── Dock bounce ────────────────────────────────────────────────────────────
# Bounce the dock icon immediately, before heavy imports start.
# The switch back to Accessory happens after imports but BEFORE pystray
# initialises — this avoids the second-tray bug caused by changing the
# activation policy while pystray's status bar item is already running.
if sys.platform == 'darwin':
    try:
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
        _AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            _AppKit.NSApplicationActivationPolicyAccessory
        )
    except Exception:
        pass

if __name__ == '__main__':
    setup_logging()
    TrayApp().run()
