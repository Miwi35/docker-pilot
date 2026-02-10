#!/bin/bash
set -euo pipefail

LAUNCHER="$HOME/.local/bin/docker-pilot"
DESKTOP_FILE="$HOME/.config/autostart/docker-pilot.desktop"

echo "Docker Pilot — Uninstall"
echo "========================"

# Kill running instance
if pkill -f "docker_pilot.py" 2>/dev/null; then
    echo "Stopped running instance"
fi

# Remove launcher
if [ -f "$LAUNCHER" ]; then
    rm "$LAUNCHER"
    echo "Removed: $LAUNCHER"
else
    echo "Launcher not found (already removed)"
fi

# Remove autostart entry
if [ -f "$DESKTOP_FILE" ]; then
    rm "$DESKTOP_FILE"
    echo "Removed: $DESKTOP_FILE"
else
    echo "Autostart entry not found (already removed)"
fi

echo ""
echo "Uninstall complete."
