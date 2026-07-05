#!/usr/bin/env bash
# Build a grandma-friendly Linux AppImage from the existing portable package.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-$(grep -E '^version' "$ROOT/pyproject.toml" | head -1 | awk -F'"' '{print $2}')}"
APP_NAME="ELI_v3.0"
OUT_DIR="$ROOT/dist/app_packages"
WORK="$ROOT/build/appimage"
APPDIR="$WORK/${APP_NAME}.AppDir"
APPIMAGE="$OUT_DIR/${APP_NAME}-${VERSION}-x86_64.AppImage"
PORTABLE="$OUT_DIR/${APP_NAME}-${VERSION}-linux-portable.tar.gz"
INSTALL_ROOT_NAME="ELI_v3"

mkdir -p "$OUT_DIR" "$WORK"

if [ ! -f "$PORTABLE" ]; then
  echo "[appimage] portable package missing — building it first"
  SKIP_TESTS="${SKIP_TESTS:-1}" bash "$ROOT/scripts/package_desktop_app.sh"
fi

echo "[appimage] staging AppDir from $(basename "$PORTABLE")"
rm -rf "$APPDIR"
mkdir -p "$APPDIR"
tar -xzf "$PORTABLE" -C "$WORK"
STAGING="$(find "$WORK" -maxdepth 1 -type d -name "${APP_NAME}-*-linux-portable" | head -1)"
[ -n "$STAGING" ] && [ -d "$STAGING" ] || { echo "[appimage] extract failed" >&2; exit 1; }
cp -a "$STAGING/." "$APPDIR/"
rm -rf "$STAGING"

ICON="$APPDIR/packaging/desktop/Eli_Icon.png"
[ -f "$ICON" ] || ICON="$ROOT/packaging/desktop/Eli_Icon.png"
[ -f "$ICON" ] || ICON="$ROOT/blueprints/eli_logo2.png"

cat > "$APPDIR/AppRun" <<APPRUN_EOF
#!/usr/bin/env bash
set -euo pipefail
HERE="\$(dirname "\$(readlink -f "\${0}")")"
INSTALL_ROOT="\${ELI_INSTALL_ROOT:-\$HOME/.local/share/${INSTALL_ROOT_NAME}}"
MARKER="\$INSTALL_ROOT/.eli_appimage_ready"
LOG="\$INSTALL_ROOT/setup.log"
mkdir -p "\$INSTALL_ROOT"
export ELI_PROJECT_ROOT="\$INSTALL_ROOT"
export ELI_DATA_DIR="\${ELI_DATA_DIR:-\$INSTALL_ROOT/artifacts}"
export ELI_CONFIG_DIR="\${ELI_CONFIG_DIR:-\$INSTALL_ROOT/config}"
export ELI_MODELS_DIR="\${ELI_MODELS_DIR:-\$INSTALL_ROOT/models}"
export ELI_CACHE_DIR="\${ELI_CACHE_DIR:-\$HOME/.cache/ELI_v3}"
export PYTHONPATH="\$INSTALL_ROOT\${PYTHONPATH:+:\$PYTHONPATH}"
_notify() { command -v zenity >/dev/null 2>&1 && zenity --info --title="ELI Setup" --width=420 --text="\$1" 2>/dev/null || true; }
_progress() { command -v zenity >/dev/null 2>&1 && zenity --progress --pulsate --auto-close --title="ELI Setup" --text="\$1" 2>/dev/null || true; }
if [ ! -f "\$MARKER" ]; then
  _progress "First launch — preparing ELI v3 (a few minutes)…" & _ZPID=\$!
  {
    echo "=== ELI v3 AppImage setup \$(date -Iseconds) ==="
    rsync -a --exclude=.venv "\$HERE/" "\$INSTALL_ROOT/" 2>&1 || cp -a "\$HERE/." "\$INSTALL_ROOT/"
    bash "\$INSTALL_ROOT/INSTALL_ELI.sh" --yes >>"\$LOG" 2>&1
    "\$INSTALL_ROOT/.venv/bin/python" -m eli.core.init_data >>"\$LOG" 2>&1 || true
    bash "\$INSTALL_ROOT/scripts/install_desktop_apps.sh" >>"\$LOG" 2>&1 || true
    touch "\$MARKER"
  } || { kill "\$_ZPID" 2>/dev/null || true; _notify "Setup failed. See: \$LOG"; exit 1; }
  kill "\$_ZPID" 2>/dev/null || true; wait "\$_ZPID" 2>/dev/null || true
  _notify "ELI v3 is ready."
fi
cd "\$INSTALL_ROOT"
exec "\$INSTALL_ROOT/RUN_ELI.sh" "\$@"
APPRUN_EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/eli-v3.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ELI v3
GenericName=Local AI Assistant
Exec=AppRun
Icon=eli
Categories=Utility;
Terminal=false
EOF

[ -f "$ICON" ] && cp "$ICON" "$APPDIR/eli.png"

APPIMAGETOOL=""
command -v appimagetool >/dev/null 2>&1 && APPIMAGETOOL="appimagetool"
[ -z "$APPIMAGETOOL" ] && [ -x "$ROOT/build/appimagetool-x86_64.AppImage" ] && APPIMAGETOOL="$ROOT/build/appimagetool-x86_64.AppImage"
if [ -z "$APPIMAGETOOL" ]; then
  curl -fsSL -o "$ROOT/build/appimagetool-x86_64.AppImage" \
    "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
  chmod +x "$ROOT/build/appimagetool-x86_64.AppImage"
  APPIMAGETOOL="$ROOT/build/appimagetool-x86_64.AppImage"
fi

export ARCH="${ARCH:-x86_64}" VERSION="$VERSION"
rm -f "$APPIMAGE"
"$APPIMAGETOOL" "$APPDIR" "$APPIMAGE"
( cd "$OUT_DIR" && sha256sum "$(basename "$APPIMAGE")" > "$(basename "$APPIMAGE").sha256" )
echo "[appimage] complete: $APPIMAGE"
