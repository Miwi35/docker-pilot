#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PILOT_SCRIPT="$SCRIPT_DIR/docker_pilot.py"
LAUNCHER="$HOME/.local/bin/docker-pilot"
DESKTOP_FILE="$HOME/.config/autostart/docker-pilot.desktop"

echo "Docker Pilot — Install"
echo "======================"

# Verify Python GI dependencies
echo "Checking dependencies..."
python3 -c "
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('AyatanaAppIndicator3', '0.1')
gi.require_version('Notify', '0.7')
from gi.repository import Gtk, AyatanaAppIndicator3, Notify, GLib
print('  Python GI dependencies: OK')
" || {
    echo "ERROR: Missing Python GI dependencies."
    echo "Install with: sudo apt install gir1.2-ayatanaappindicator3-0.1 python3-gi gir1.2-notify-0.7"
    exit 1
}

if command -v docker &>/dev/null; then
    echo "  Docker: OK"
else
    echo "  WARNING: Docker not found in PATH. Docker Pilot needs Docker to work."
fi

# Make script executable
chmod +x "$PILOT_SCRIPT"

# Create launcher in ~/.local/bin
mkdir -p "$HOME/.local/bin"
cat > "$LAUNCHER" << EOF
#!/bin/bash
exec python3 "$PILOT_SCRIPT" "\$@"
EOF
chmod +x "$LAUNCHER"
echo "Launcher created: $LAUNCHER"

# Create autostart desktop entry
mkdir -p "$HOME/.config/autostart"
cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Type=Application
Name=Docker Pilot
Comment=Docker Compose project indicator
Exec=bash -c 'sleep 5 && exec python3 $PILOT_SCRIPT'
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
StartupNotify=false
Terminal=false
EOF
echo "Autostart entry created: $DESKTOP_FILE"

echo ""
echo "Installation complete!"
echo "  Run now:    docker-pilot"
echo "  Auto-start: enabled (with 5s delay after login)"
