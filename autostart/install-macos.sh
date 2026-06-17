#!/bin/bash
# Ubiquity — macOS auto-start setup (launchd)
# Run once as the user who should run the sync.
#
# Usage:
#   ./install-macos.sh --dir /your/folder --port 5001

set -e

DIR=""
PORT="5001"

while [[ $# -gt 0 ]]; do
    case $1 in
        --dir)  DIR="$2";  shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$DIR" ]]; then
    echo "Usage: $0 --dir /your/folder [--port 5001]"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$(which python3)"
PLIST="$HOME/Library/LaunchAgents/com.ubiquity.sync.plist"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ubiquity.sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT_DIR/main.py</string>
        <string>--mode</string>
        <string>server</string>
        <string>--dir</string>
        <string>$DIR</string>
        <string>--port</string>
        <string>$PORT</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/ubiquity.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ubiquity.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Installed and started."
echo "Logs: tail -f /tmp/ubiquity.log"
echo "To remove: launchctl unload $PLIST && rm $PLIST"
