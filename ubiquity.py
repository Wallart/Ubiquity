"""Ubiquity — lance l'application avec icône dans la barre de menu."""
from ubiquity.tray import TrayApp, setup_logging

if __name__ == '__main__':
    setup_logging()
    TrayApp().run()
