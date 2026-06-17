#!/usr/bin/env bash
# Build the macOS executable.
# Run once: pip install pyinstaller
set -e
pyinstaller ubiquity.spec --clean
echo "✓ Binary: dist/ubiquity"
