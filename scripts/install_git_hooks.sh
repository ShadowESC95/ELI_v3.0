#!/usr/bin/env bash
# Install repo git hooks.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS="$ROOT/.git/hooks"
mkdir -p "$HOOKS"
install -m 755 "$ROOT/scripts/git-hooks/commit-msg" "$HOOKS/commit-msg"
echo "[OK] Installed $HOOKS/commit-msg"
