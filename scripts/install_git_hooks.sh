#!/usr/bin/env bash
# Install repo git hooks.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOKS="$ROOT/.git/hooks"
mkdir -p "$HOOKS"
for h in commit-msg pre-commit; do
  install -m 755 "$ROOT/scripts/git-hooks/$h" "$HOOKS/$h"
  echo "[OK] Installed $HOOKS/$h"
done
