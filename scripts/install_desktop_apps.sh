#!/usr/bin/env bash
# Install desktop-app launchers (Linux) for BOTH ELI surfaces, so they appear in your
# application menu like any installed app:
#   • ELI v3.0          → the full desktop GUI
#   • ELI Server (Web)  → the self-hosted web app for phone/tablet/browser (LAN + token)
#
# One-click: ./scripts/install_desktop_apps.sh   (re-run to refresh).
# macOS/Windows: use the launchers directly (scripts/eli_launch.sh / install.bat) — .desktop
# files are a Linux convention; this script no-ops elsewhere with a hint.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ ! -x "$ROOT/.venv/bin/python" ]; then echo "[eli] note: .venv not found — installing launchers anyway (run install.sh to finish setup)."; fi

OS="$(uname -s)"
chmod +x "$ROOT/scripts/eli_launch.sh" "$ROOT/scripts/eli_serve.sh" 2>/dev/null || true

if [ "$OS" = "Darwin" ]; then
    # macOS: double-clickable .command launchers (open in Terminal from Finder).
    APPS="$HOME/Applications"
    mkdir -p "$APPS"
    cat > "$APPS/ELI v3.0.command" <<EOF
#!/bin/bash
cd "$ROOT" && exec "$ROOT/scripts/eli_launch.sh" gui
EOF
    cat > "$APPS/ELI Server (Web App).command" <<EOF
#!/bin/bash
cd "$ROOT" && exec "$ROOT/scripts/eli_serve.sh" --lan
EOF
    cat > "$APPS/ELI Uninstall.command" <<EOF
#!/bin/bash
exec "$ROOT/scripts/eli_uninstall.sh"
EOF
    chmod +x "$APPS/ELI v3.0.command" "$APPS/ELI Server (Web App).command" "$APPS/ELI Uninstall.command"
    echo "[OK] Installed launchers to $APPS :"
    echo "       • ELI v3.0.command            (desktop GUI)"
    echo "       • ELI Server (Web App).command (prints the phone URL + token)"
    echo "       • ELI Uninstall.command      (remove ELI; optionally delete the install)"
    echo "     Double-click from Finder. Inference stays 100% local."
    exit 0
elif [ "$OS" != "Linux" ]; then
    echo "[eli] Windows: run  powershell -ExecutionPolicy Bypass -File scripts\\install_desktop_apps.ps1"
    echo "      to add 'ELI v3.0' and 'ELI Server (Web App)' Start Menu shortcuts."
    exit 0
fi

APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
chmod +x "$ROOT/scripts/eli_launch.sh" "$ROOT/scripts/eli_serve.sh" \
         "$ROOT/scripts/eli_term.sh" "$ROOT/scripts/eli_uninstall.sh" 2>/dev/null || true
TERM_RUN="$ROOT/scripts/eli_term.sh"   # opens a real terminal (GNOME/Wayland ignore Terminal=true)

# Guard wrapper in ~/.local/bin (OUTSIDE the install) so the icons fail LOUDLY — with a
# visible dialog — if the install is later moved/deleted or its scripts go missing,
# instead of silently doing nothing (the failure mode that kept biting).
BIN="$HOME/.local/bin"; mkdir -p "$BIN"; GUARD="$BIN/eli-run"
cat > "$GUARD" <<'GUARD_EOF'
#!/usr/bin/env bash
# ELI launcher guard. Args: <install_root> <gui|serve|setup|uninstall>
R="$1"; A="$2"
_err(){ command -v zenity >/dev/null 2>&1 && zenity --error --title="ELI" --width=420 --text="$1" 2>/dev/null \
    || { command -v notify-send >/dev/null 2>&1 && notify-send "ELI" "$1" 2>/dev/null; } \
    || { printf '%s
' "$1"; sleep 6; }; }
[ -n "$R" ] && [ -d "$R" ] || { _err "ELI install not found at:
$R

Re-run ELI Setup, or reinstall the latest release."; exit 1; }
T="$R/scripts/eli_term.sh"
case "$A" in
  gui)       S="$R/scripts/eli_launch.sh";    RUN=(gui) ;;
  serve)     S="$R/scripts/eli_serve.sh";     RUN=(--lan --https) ;;
  setup)     S="$R/scripts/eli_setup.sh";     RUN=() ;;
  uninstall) S="$R/scripts/eli_uninstall.sh"; RUN=() ;;
  *) _err "Unknown ELI action: $A"; exit 2 ;;
esac
if [ "$A" = "gui" ]; then
  [ -x "$S" ] || { _err "ELI is damaged (missing launcher) at:
$R

Reinstall the latest release."; exit 1; }
  exec "$S" "${RUN[@]}"
fi
{ [ -x "$T" ] && [ -x "$S" ]; } || { _err "ELI is damaged (missing scripts) at:
$R

Re-run ELI Setup, or reinstall the latest release."; exit 1; }
exec "$T" "$S" "${RUN[@]}"
GUARD_EOF
chmod +x "$GUARD"

# Optional icon — use one if present, otherwise fall back to a stock icon name.
ICON="utilities-terminal"
for c in "$ROOT/packaging/desktop/Eli_Icon.png" "$ROOT/blueprints/Eli_Icon.png" "$ROOT/blueprints/eli_logo2.png"; do
    [ -f "$c" ] && { ICON="$c"; break; }
done

cat > "$APPS/eli-v3.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ELI v3.0
Comment=Local, private AI assistant (desktop GUI)
Exec=$GUARD "$ROOT" gui
Path=$ROOT
Icon=$ICON
Terminal=false
Categories=Utility;Development;Office;
StartupNotify=true
EOF

cat > "$APPS/eli-server.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ELI Server (Web App)
Comment=Self-hosted ELI web app — open from any device on your network (LAN + token)
Exec=$GUARD "$ROOT" serve
Path=$ROOT
Icon=$ICON
Terminal=false
Categories=Utility;Network;
StartupNotify=true
EOF

cat > "$APPS/eli-uninstall.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ELI Uninstall
GenericName=Remove ELI
Comment=Remove ELI's menu icons and the 'eli' command; optionally delete this install
Exec=$GUARD "$ROOT" uninstall
Path=$ROOT
Icon=$ICON
Terminal=false
Categories=Utility;Settings;
StartupNotify=true
Keywords=uninstall;remove;delete;eli;
EOF

chmod +x "$APPS/eli-v3.desktop" "$APPS/eli-server.desktop" "$APPS/eli-uninstall.desktop" 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true

echo "[OK] Installed desktop launchers to $APPS :"
echo "       • ELI v3.0            (desktop GUI)"
echo "       • ELI Server (Web App) (opens a terminal; prints the phone URL + token)"
echo "       • ELI Uninstall      (remove icons/command; optionally delete the install)"
echo "     They should now appear in your application menu. Inference stays 100% local."
