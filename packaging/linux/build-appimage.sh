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

# Quoted heredoc: nothing below expands at build time. INSTALL_ROOT_NAME is ELI_v3.
cat > "$APPDIR/AppRun" <<'APPRUN_EOF'
#!/usr/bin/env bash
# ELI v3 AppImage — first double-click installs to ~/.local/share/ELI_v3, then launches.
# A newer AppImage over an existing install refreshes the code on its first launch.
set -euo pipefail
HERE="$(dirname "$(readlink -f "${0}")")"
INSTALL_ROOT="${ELI_INSTALL_ROOT:-$HOME/.local/share/ELI_v3}"
MARKER="$INSTALL_ROOT/.eli_appimage_ready"
LOG="$INSTALL_ROOT/setup.log"
mkdir -p "$INSTALL_ROOT"
export ELI_PROJECT_ROOT="$INSTALL_ROOT"
export ELI_DATA_DIR="${ELI_DATA_DIR:-$INSTALL_ROOT/artifacts}"
export ELI_CONFIG_DIR="${ELI_CONFIG_DIR:-$INSTALL_ROOT/config}"
export ELI_MODELS_DIR="${ELI_MODELS_DIR:-$INSTALL_ROOT/models}"
export ELI_CACHE_DIR="${ELI_CACHE_DIR:-$HOME/.cache/ELI_v3}"
export PYTHONPATH="$INSTALL_ROOT${PYTHONPATH:+:$PYTHONPATH}"
_notify() { command -v zenity >/dev/null 2>&1 && zenity --info --title="ELI Setup" --width=420 --text="$1" 2>/dev/null || true; }
_progress() { command -v zenity >/dev/null 2>&1 && zenity --progress --pulsate --auto-close --title="ELI Setup" --text="$1" 2>/dev/null || true; }

_bundle_version() {
  grep -E '^version' "$HERE/pyproject.toml" 2>/dev/null | head -1 | awk -F'"' '{print $2}'
}

_installed_version() {
  # Markers from before this check held no version; they read as "" and trigger one refresh.
  [ -f "$MARKER" ] && cat "$MARKER" || true
}

# Upgrade over an existing install. The install root also holds the user's own state, so this
# never deletes, never overwrites config (new config files are added, existing ones kept), and
# leaves artifacts/ (memory, notes, persona), models/, runtime/ (the GPU pack) and .venv alone.
_upgrade_app_tree() {
  local entry name
  for entry in "$HERE"/* "$HERE"/.[!.]*; do
    [ -e "$entry" ] || continue
    name="$(basename "$entry")"
    case "$name" in
      .venv|models|artifacts|runtime|config) continue ;;
    esac
    if command -v rsync >/dev/null 2>&1; then
      # --checksum: -a keeps build-time mtimes, so size+mtime alone can skip a changed file.
      rsync -a --checksum "$entry" "$INSTALL_ROOT/"
    else
      cp -a "$entry" "$INSTALL_ROOT/"
    fi
  done
  if [ -d "$HERE/config" ]; then
    mkdir -p "$INSTALL_ROOT/config"
    if command -v rsync >/dev/null 2>&1; then
      rsync -a --ignore-existing "$HERE/config/" "$INSTALL_ROOT/config/"
    else
      cp -an "$HERE/config/." "$INSTALL_ROOT/config/"
    fi
  fi
}

_BUNDLE_VER="$(_bundle_version)"
_INSTALLED_VER="$(_installed_version)"
_FORCE_SYNC="${ELI_APPIMAGE_FORCE_SYNC:-0}"

if [ ! -f "$MARKER" ]; then
  _progress "First launch — preparing ELI v3 (a few minutes)…" & _ZPID=$!
  {
    echo "=== ELI v3 AppImage setup $(date -Iseconds) ==="
    rsync -a --exclude=.venv "$HERE/" "$INSTALL_ROOT/" 2>&1 || cp -a "$HERE/." "$INSTALL_ROOT/"
    if [ -x "$INSTALL_ROOT/ELI_Setup.sh" ]; then
      bash "$INSTALL_ROOT/ELI_Setup.sh" >>"$LOG" 2>&1
    elif [ -x "$INSTALL_ROOT/INSTALL_ELI.sh" ]; then
      bash "$INSTALL_ROOT/INSTALL_ELI.sh" >>"$LOG" 2>&1
    else
      bash "$INSTALL_ROOT/install.sh" --yes >>"$LOG" 2>&1
    fi
    printf '%s\n' "$_BUNDLE_VER" >"$MARKER"
  } || { kill "$_ZPID" 2>/dev/null || true; _notify "Setup failed. See: $LOG"; exit 1; }
  kill "$_ZPID" 2>/dev/null || true; wait "$_ZPID" 2>/dev/null || true
  _notify "ELI v3 is ready."
elif [ "$_FORCE_SYNC" = "1" ] || { [ -n "$_BUNDLE_VER" ] && [ "$_INSTALLED_VER" != "$_BUNDLE_VER" ]; }; then
  _progress "Updating ELI v3 to ${_BUNDLE_VER:-the new version}…" & _ZPID=$!
  {
    echo "=== ELI v3 AppImage upgrade $(date -Iseconds) ${_INSTALLED_VER:-unknown} -> $_BUNDLE_VER ==="
    _upgrade_app_tree
    printf '%s\n' "$_BUNDLE_VER" >"$MARKER"
  } >>"$LOG" 2>&1 || { kill "$_ZPID" 2>/dev/null || true; _notify "Update failed. See: $LOG"; exit 1; }
  kill "$_ZPID" 2>/dev/null || true; wait "$_ZPID" 2>/dev/null || true
fi
cd "$INSTALL_ROOT"
exec "$INSTALL_ROOT/RUN_ELI.sh" "$@"
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
