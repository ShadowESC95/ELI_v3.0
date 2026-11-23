#!/usr/bin/env bash
# Build a Debian .deb from the portable Linux package. The read-only payload
# installs under /opt/eli-v3; a /usr/bin/eli-v3 launcher bootstraps a writable
# per-user install (venv + models) under ~/.local/share/ELI_v3 on first run —
# the same model the AppImage uses. No root-owned mutable state.
#
# The package is eli-v3, as v2's is eli-v2. Up to 3.0.171 it was eli-mkxi, with the same files,
# so this one takes its place: `apt install ./eli-v3_<version>_amd64.deb` removes eli-mkxi and
# installs eli-v3. Neither package has a script that removes anything: the per-user folder is the
# user's, never the package's.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-$(grep -E '^version' "$ROOT/pyproject.toml" | head -1 | awk -F'"' '{print $2}')}"
APP_NAME="ELI_v3.0"
PKG="eli-v3"
FORMER_PKG="eli-mkxi"
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

# The install and upgrade of the per-user copy are packaging/linux/per_user_install.sh, the same
# script the source AppImage runs: a newer package brings the copy's code up to date on its first
# launch, and leaves the user's config, memory, models and environment as they are.
cat > "$PKGDIR/usr/bin/eli-v3" <<'LAUNCH'
#!/usr/bin/env bash
set -euo pipefail
PAYLOAD="/opt/eli-v3"
exec bash "$PAYLOAD/packaging/linux/per_user_install.sh" "$PAYLOAD" .eli_deb_ready "$@"
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
# Breaks and Replaces name every eli-mkxi up to this version, not only older ones: the name changed
# at 3.0.171 itself, so a build of that version exists under both names, and dpkg only lets one
# package's files take over another's when Replaces names that package's version. No eli-mkxi is
# built from here on, so every one there is falls under it. Provides keeps the old name answered;
# dpkg and apt never count a package's own Provides against its own Breaks.
cat > "$PKGDIR/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Depends: python3 (>= 3.10), python3-venv, python3-pip, rsync, python3-dev, build-essential, portaudio19-dev, libglib2.0-0, libgl1, libegl1, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-render-util0, libxcb-shape0
Recommends: ffmpeg
Provides: $FORMER_PKG (= $VERSION)
Breaks: $FORMER_PKG (<= $VERSION)
Replaces: $FORMER_PKG (<= $VERSION)
Installed-Size: $INSTALLED_KB
Maintainer: Jason Fitzgibbon Bridgeman <jaybridgeman0095@gmail.com>
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
