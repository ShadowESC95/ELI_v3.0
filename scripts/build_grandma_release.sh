#!/usr/bin/env bash
# Grandma-friendly release bundle: portable tar.gz + AppImage (+ checksums).
# Windows Setup.exe: run packaging/windows/build-windows.ps1 on a Windows PC.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export SKIP_TESTS="${SKIP_TESTS:-1}"

echo "=== ELI grandma-friendly release ==="
bash "$ROOT/scripts/package_desktop_app.sh"
bash "$ROOT/packaging/linux/build-appimage.sh"

VERSION="$(grep -E '^version' pyproject.toml | head -1 | awk -F'"' '{print $2}')"
OUT="$ROOT/dist/app_packages"

cat <<EOF

Done. Publish these from $OUT:

  Linux (easiest):
    • ELI_v3.0-${VERSION}-x86_64.AppImage     ← double-click (chmod +x first)
    • ELI_v3.0-${VERSION}-linux-portable.tar.gz

  Windows (on a Windows machine):
    bash build_packages.sh windows-lean
    powershell -File packaging/windows/build-windows.ps1 -Version ${VERSION}
    → ELI_v3.0-${VERSION}-Setup.exe  (or double-click ELI_Setup.bat inside the zip)

  Grandma Linux flow:
    chmod +x ELI_v3.0-${VERSION}-x86_64.AppImage
    ./ELI_v3.0-${VERSION}-x86_64.AppImage

EOF
