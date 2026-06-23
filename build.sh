#!/usr/bin/env bash
# Build Ubiquity for macOS (or Linux).
#
# Prerequisites:
#   pip install pyinstaller
#
# Output (macOS):
#   dist/Ubiquity.app   — drag-and-drop app bundle
#   dist/Ubiquity.dmg   — distributable disk image
#
# Output (Linux):
#   dist/ubiquity       — standalone binary (requires xclip or xsel for clipboard)

set -e

pyinstaller ubiquity.spec --clean

if [[ "$(uname)" == "Darwin" ]]; then
    echo "→ Creating Ubiquity.dmg…"
    rm -rf /tmp/ubiquity-dmg
    mkdir  /tmp/ubiquity-dmg
    cp -r  dist/Ubiquity.app /tmp/ubiquity-dmg/
    # Symlink to /Applications so users can drag the app in.
    ln -s /Applications /tmp/ubiquity-dmg/Applications
    hdiutil create \
        -volname "Ubiquity" \
        -srcfolder /tmp/ubiquity-dmg \
        -ov -format UDZO \
        dist/Ubiquity.dmg
    rm -rf /tmp/ubiquity-dmg
    echo "✓ dist/Ubiquity.app"
    echo "✓ dist/Ubiquity.dmg"
else
    echo "✓ dist/ubiquity"
    echo "Note: clipboard sync requires xclip or xsel (apt install xclip)"
fi
