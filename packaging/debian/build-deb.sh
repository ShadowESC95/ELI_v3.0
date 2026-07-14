#!/usr/bin/env bash
# Build a Debian .deb from the portable Linux package. The read-only payload
# installs under /opt/eli-v3; a /usr/bin/eli-v3 launcher bootstraps a writable
# per-user install (venv + models) under ~/.local/share/ELI_v3 on first run —
# the same model the AppImage uses. No root-owned mutable state.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-$(grep -E '^version' "$ROOT/pyproject.toml" | head -1 | awk -F'"' '{print $2}')}"
APP_NAME="ELI_v3.0"
PKG="eli-mkxi"
OUT_DIR="$ROOT/dist"
APP_OUT="$ROOT/dist/app_packages"
WORK="$ROOT/build/deb"
PKGDIR="$WORK/${PKG}_${VERSION}_amd64"
PORTABLE="$APP_OUT/${APP_NAME}-${VERSION}-linux-portable.tar.gz"
OPT_DIR="opt/eli-v3"

command -v dpkg-deb >/dev/null || { echo "[deb] dpkg-deb not found — apt install dpkg" >&2; exit 1; }
mkdir -p "$OUT_DIR" "$APP_OUT" "$WORK"

if [ ! -f "$PORTABLE" ]; then
  echo "[deb] portable package missing — building it first"
  SKIP_TESTS="${SKIP_TESTS:-1}" bash "$ROOT/scripts/package_desktop_app.sh"
fi

echo "[deb] staging package tree"
rm -rf "$PKGDIR"
mkdir -p "$PKGDIR/DEBIAN" "$PKGDIR/$OPT_DIR" "$PKGDIR/usr/bin" \
         "$PKGDIR/usr/share/applications" \
         "$PKGDIR/usr/share/icons/hicolor/256x256/apps"

tar -xzf "$PORTABLE" -C "$WORK"
STAGING="$(find "$WORK" -maxdepth 1 -type d -name "${APP_NAME}-*-linux-portable" | head -1)"
[ -n "$STAGING" ] && [ -d "$STAGING" ] || { echo "[deb] extract failed" >&2; exit 1; }
cp -a "$STAGING/." "$PKGDIR/$OPT_DIR/"
rm -rf "$STAGING"

for ic in "$ROOT/packaging/desktop/eli-256.png" "$ROOT/packaging/desktop/Eli_Icon.png"; do
  [ -f "$ic" ] && { cp "$ic" "$PKGDIR/usr/share/icons/hicolor/256x256/apps/eli-v3.png"; break; }
done

cat > "$PKGDIR/usr/bin/eli-v3" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
PAYLOAD="/opt/eli-v3"
INSTALL_ROOT="${ELI_INSTALL_ROOT:-$HOME/.local/share/ELI_v3}"
MARKER="$INSTALL_ROOT/.eli_deb_ready"
LOG="$INSTALL_ROOT/setup.log"
mkdir -p "$INSTALL_ROOT"
export ELI_PROJECT_ROOT="$INSTALL_ROOT"
export ELI_DATA_DIR="${ELI_DATA_DIR:-$INSTALL_ROOT/artifacts}"
export ELI_CONFIG_DIR="${ELI_CONFIG_DIR:-$INSTALL_ROOT/config}"
export ELI_MODELS_DIR="${ELI_MODELS_DIR:-$INSTALL_ROOT/models}"
export ELI_CACHE_DIR="${ELI_CACHE_DIR:-$HOME/.cache/ELI_v3}"
export PYTHONPATH="$INSTALL_ROOT${PYTHONPATH:+:$PYTHONPATH}"
if [ ! -f "$MARKER" ]; then
  echo "First launch — preparing ELI v3 (per-user venv build, a few minutes)…"
  rsync -a --exclude=.venv "$PAYLOAD/" "$INSTALL_ROOT/" 2>/dev/null || cp -a "$PAYLOAD/." "$INSTALL_ROOT/"
  if   [ -x "$INSTALL_ROOT/ELI_Setup.sh" ];   then bash "$INSTALL_ROOT/ELI_Setup.sh"   >>"$LOG" 2>&1
  elif [ -x "$INSTALL_ROOT/INSTALL_ELI.sh" ]; then bash "$INSTALL_ROOT/INSTALL_ELI.sh" >>"$LOG" 2>&1
  else bash "$INSTALL_ROOT/install.sh" --yes  >>"$LOG" 2>&1; fi
  touch "$MARKER"
fi
cd "$INSTALL_ROOT"
exec "$INSTALL_ROOT/RUN_ELI.sh" "$@"
LAUNCH
chmod 0755 "$PKGDIR/usr/bin/eli-v3"

cat > "$PKGDIR/usr/share/applications/eli-v3.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ELI v3
GenericName=Local AI Assistant
Comment=Strictly local, private AI assistant and cognitive runtime
Exec=eli-v3
Icon=eli-v3
Categories=Utility;Development;
Terminal=false
EOF

INSTALLED_KB=$(du -sk "$PKGDIR/opt" "$PKGDIR/usr" | awk '{s+=$1} END{print s}')
cat > "$PKGDIR/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Depends: python3 (>= 3.10), python3-venv, python3-pip, rsync
Recommends: ffmpeg
Installed-Size: $INSTALLED_KB
Maintainer: ShadowESC95 <ShadowESC95@users.noreply.github.com>
Description: ELI — strictly local, private AI assistant and cognitive runtime
 Everything runs on your own machine: the model, your data, and all processing.
 Offline by default, enforced at the network socket. First launch builds a
 per-user Python environment under ~/.local/share/ELI_v3.
EOF

cat > "$PKGDIR/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -f /usr/share/icons/hicolor 2>/dev/null || true
exit 0
EOF
chmod 0755 "$PKGDIR/DEBIAN/postinst"

DEB="$OUT_DIR/${PKG}_${VERSION}_amd64.deb"
rm -f "$DEB"
if dpkg-deb --help 2>&1 | grep -q -- '--root-owner-group'; then
  dpkg-deb --build --root-owner-group "$PKGDIR" "$DEB"
elif command -v fakeroot >/dev/null 2>&1; then
  fakeroot dpkg-deb --build "$PKGDIR" "$DEB"
else
  dpkg-deb --build "$PKGDIR" "$DEB"
fi
( cd "$OUT_DIR" && sha256sum "$(basename "$DEB")" > "$(basename "$DEB").sha256" )
echo "[deb] complete: $DEB"
