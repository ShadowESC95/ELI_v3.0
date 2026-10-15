#!/usr/bin/env bash
# Point the shell `eli` command at THIS checkout and clear stale ELI_* exports.
#
# Fixes the common ghost-machine failure mode: a bash alias or exported
# ELI_PROJECT_ROOT from ELI v2 / another folder makes v3 write into the wrong
# artifacts/ tree even when launched from ELI_v3.0.
#
# Usage:  bash scripts/fix_eli_shell_env.sh [--force] [--yes]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORCE=0
ASSUME_YES=0
MARK_BEGIN="# >>> ELI v3 launcher (managed by scripts/fix_eli_shell_env.sh) >>>"
MARK_END="# <<< ELI v3 launcher <<<"

while [ "$#" -gt 0 ]; do
  case "$1" in
    --force) FORCE=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help)
      echo "Usage: bash scripts/fix_eli_shell_env.sh [--force] [--yes]"
      exit 0
      ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

echo "[fix-shell] checkout: $ROOT"

# 1) Install ~/.local/bin/eli → this checkout's eli.sh (clears stale env inside).
INSTALL_ARGS=(--force --name eli)
bash "$ROOT/scripts/install_eli_command.sh" ${INSTALL_ARGS[@]+"${INSTALL_ARGS[@]}"}

# 2) Optional second name so v2 and v3 can coexist.
bash "$ROOT/scripts/install_eli_command.sh" --force --name eli3

# 3) Patch shell rc files — aliases beat PATH, so an old alias must go.
_patch_rc() {
  local rc="$1"
  [ -f "$rc" ] || return 0
  local tmp
  tmp="$(mktemp)"
  # Drop previous managed block and bare alias eli / export ELI_PROJECT_ROOT lines.
  awk -v b="$MARK_BEGIN" -v e="$MARK_END" -v root="$ROOT" '
    $0 == b { skip=1; next }
    $0 == e { skip=0; next }
    skip { next }
    # a bare alias eli= line is removed only when it points at this checkout (another ELI version keeps its own)
    /^[[:space:]]*alias[[:space:]]+eli=/ && index($0, root) { next }
    /^[[:space:]]*export[[:space:]]+ELI_PROJECT_ROOT=/ { next }
    { print }
  ' "$rc" > "$tmp"
  mv "$tmp" "$rc"
  {
    echo ""
    echo "$MARK_BEGIN"
    echo "# This checkout when you type: eli-v3 or eli3 (other ELI versions keep their own names)"
    echo "alias eli-v3='$ROOT/eli.sh'"
    echo "alias eli3='$ROOT/eli.sh'"
    echo "# Do not export ELI_PROJECT_ROOT globally — each launcher sets it."
    echo "$MARK_END"
  } >> "$rc"
  echo "[fix-shell] updated: $rc"
}

for rc in "$HOME/.bashrc" "$HOME/.bash_aliases" "$HOME/.profile"; do
  _patch_rc "$rc"
done

echo "[fix-shell] done."
echo "[fix-shell] Open a new terminal, then:  type eli-v3"
echo "[fix-shell] Expected: alias eli-v3='$ROOT/eli.sh'  OR  $HOME/.local/bin/eli-v3"
