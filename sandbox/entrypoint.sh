#!/usr/bin/env bash
# Start the sandbox's desktop (virtual screen, window manager, live view), then run tutorial-ai.
set -euo pipefail

W="${SCREEN_WIDTH:-1920}"
H="${SCREEN_HEIGHT:-1080}"
mkdir -p /tmp/sandbox-logs

Xvfb "${DISPLAY}" -screen 0 "${W}x${H}x24" -nolisten tcp -ac +extension RANDR \
    >/tmp/sandbox-logs/xvfb.log 2>&1 &
for _ in $(seq 1 100); do
    xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 && break
    sleep 0.1
done
xdpyinfo -display "${DISPLAY}" >/dev/null 2>&1 || { echo "ERROR the virtual screen did not start"; cat /tmp/sandbox-logs/xvfb.log; exit 1; }

xsetroot -solid '#1e1e1e'
eval "$(dbus-launch --sh-syntax)"
openbox >/tmp/sandbox-logs/openbox.log 2>&1 &

# Live view for the web UI: read-only VNC, served as noVNC on port 6080.
x11vnc -display "${DISPLAY}" -forever -shared -viewonly -nopw -quiet -rfbport 5900 -localhost \
    >/tmp/sandbox-logs/x11vnc.log 2>&1 &
websockify --web /usr/share/novnc 6080 localhost:5900 >/tmp/sandbox-logs/novnc.log 2>&1 &

cd /opt/tutorial-ai
if [ "${1:-}" = "shell" ]; then
    shift
    exec bash "$@"
fi
exec /opt/venv/bin/python -c 'import sys; sys.argv[0] = "tutorial-ai"; from tutorial_ai.cli import app; app()' "$@"
