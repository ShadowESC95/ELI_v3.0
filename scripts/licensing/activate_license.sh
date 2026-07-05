#!/usr/bin/env bash
# Activate an ELI v3 commercial license key on this machine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="${PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"

if [ "$#" -lt 1 ]; then
  echo "Usage: scripts/licensing/activate_license.sh ELIV3-..." >&2
  exit 2
fi

exec "$PY" -m eli.licensing.cli activate "$@"
