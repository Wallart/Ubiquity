"""Ubiquity — lance l'application avec icône dans la barre de menu."""
import sys

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
