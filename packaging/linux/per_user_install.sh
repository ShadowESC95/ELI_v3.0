#!/usr/bin/env bash
# Runs ELI v3 from a read-only payload (the source AppImage's mount, the .deb's /opt/eli-v3) through
# a writable per-user copy under ~/.local/share/ELI_v3. The first launch copies the payload and runs
# its setup; a launch from a payload of another version brings the copy's code up to date. Both
# launchers call this one copy: the .deb's own copied the payload on its first launch only, so an
# upgraded package changed nothing the user ran.
#
#   per_user_install.sh <payload folder> <marker file name> [ELI arguments...]
set -euo pipefail
HERE="$(cd "${1:?per_user_install.sh needs the payload folder}" && pwd)"
MARKER_NAME="${2:?per_user_install.sh needs the marker file name}"
shift 2
INSTALL_ROOT="${ELI_INSTALL_ROOT:-$HOME/.local/share/ELI_v3}"
MARKER="$INSTALL_ROOT/$MARKER_NAME"
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
  echo "First launch — preparing ELI v3 (a few minutes)…"
  _progress "First launch — preparing ELI v3 (a few minutes)…" & _ZPID=$!
  {
    echo "=== ELI v3 setup $(date -Iseconds) from $HERE ==="
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
  echo "Updating ELI v3 to ${_BUNDLE_VER:-the new version}…"
  _progress "Updating ELI v3 to ${_BUNDLE_VER:-the new version}…" & _ZPID=$!
  {
    echo "=== ELI v3 upgrade $(date -Iseconds) ${_INSTALLED_VER:-unknown} -> $_BUNDLE_VER from $HERE ==="
    _upgrade_app_tree
    printf '%s\n' "$_BUNDLE_VER" >"$MARKER"
  } >>"$LOG" 2>&1 || { kill "$_ZPID" 2>/dev/null || true; _notify "Update failed. See: $LOG"; exit 1; }
  kill "$_ZPID" 2>/dev/null || true; wait "$_ZPID" 2>/dev/null || true
fi
cd "$INSTALL_ROOT"
exec "$INSTALL_ROOT/RUN_ELI.sh" "$@"
