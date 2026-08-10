#!/usr/bin/env bash
# Build installable Python package artifacts for ELI v3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Prefer the repo venv, as scripts/package_desktop_app.sh already does in v2.
# Defaulting straight to the system python3 meant that on any PEP 668 distro
# (Debian/Ubuntu mark theirs externally-managed) the very first pip install
# aborted, and the script produced NO artifacts while still looking like it ran.
if [ -x "$ROOT/.venv/bin/python3" ]; then
  PYTHON="${PYTHON:-$ROOT/.venv/bin/python3}"
else
  PYTHON="${PYTHON:-python3}"
fi

echo "[package] Building wheel/sdist"
"$PYTHON" -m pip install --quiet --upgrade build wheel setuptools
"$PYTHON" -m build "$ROOT"

echo "[package] Build artifacts:"
ls -lh "$ROOT/dist" | sed -n '1,80p'

echo ""
echo "Install from wheel:"
echo "  python -m pip install dist/eli_v3-*.whl[full]"
