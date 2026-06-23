#!/usr/bin/env bash
# Build Ubiquity for macOS (or Linux).
#
# Usage:
#   ./build.sh              — native macOS or Linux build
#   ./build.sh --linux      — cross-compile Linux binary from macOS via Docker
#
# Prerequisites (native):
#   pip install pyinstaller
#
# Prerequisites (--linux):
#   Docker Desktop running
#
# Output (macOS):
#   dist/Ubiquity.app   — drag-and-drop app bundle
#   dist/Ubiquity.dmg   — distributable disk image
#
# Output (Linux / --linux):
#   dist/ubiquity-linux — standalone binary (requires GTK + xclip on target)

set -e

# ── Cross-compile Linux binary from macOS via Docker ──────────────────
if [[ "$1" == "--linux" ]]; then
    echo "→ Building Linux binary via Docker…"
    docker build -t ubiquity-linux-builder -f docker/Dockerfile.linux .
    mkdir -p dist
    docker run --rm -v "$(pwd)/dist:/output" ubiquity-linux-builder
    exit 0
fi

python3.11 -m pip install -q pyinstaller -r requirements.txt
python3.11 -m PyInstaller ubiquity.spec --clean --noconfirm

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
