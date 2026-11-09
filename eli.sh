#!/usr/bin/env bash
# ELI v3 — Linux launcher
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
# This checkout's folders and environment, set where every launcher sets them.
# shellcheck disable=SC1091
source "$SCRIPT_DIR/scripts/eli_isolate_env.sh"
eli_isolate_env "$SCRIPT_DIR"

if [ ! -d "$VENV" ]; then
    echo "[ELI] Virtual environment not found. Run ./scripts/eli_setup.sh or bash install.sh first."
    exit 1
fi
eli_env_ready "$SCRIPT_DIR" || exit 1

exec "$VENV/bin/python" -m eli "$@"
