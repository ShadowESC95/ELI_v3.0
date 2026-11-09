#!/usr/bin/env bash
# Build a grandma-friendly Linux AppImage from the existing portable package.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-$(grep -E '^version' "$ROOT/pyproject.toml" | head -1 | awk -F'"' '{print $2}')}"
APP_NAME="ELI_v3.0"
OUT_DIR="$ROOT/dist/app_packages"
WORK="$ROOT/build/appimage"
APPDIR="$WORK/${APP_NAME}.AppDir"
APPIMAGE="$OUT_DIR/${APP_NAME}-${VERSION}-x86_64.AppImage"
PORTABLE="$OUT_DIR/${APP_NAME}-${VERSION}-linux-portable.tar.gz"
INSTALL_ROOT_NAME="ELI_v3"

mkdir -p "$OUT_DIR" "$WORK"

if [ ! -f "$PORTABLE" ]; then
  echo "[appimage] portable package missing — building it first"
  SKIP_TESTS="${SKIP_TESTS:-1}" bash "$ROOT/scripts/package_desktop_app.sh"
fi

echo "[appimage] staging AppDir from $(basename "$PORTABLE")"
rm -rf "$APPDIR"
mkdir -p "$APPDIR"
tar -xzf "$PORTABLE" -C "$WORK"
STAGING="$(find "$WORK" -maxdepth 1 -type d -name "${APP_NAME}-*-linux-portable" | head -1)"
[ -n "$STAGING" ] && [ -d "$STAGING" ] || { echo "[appimage] extract failed" >&2; exit 1; }
cp -a "$STAGING/." "$APPDIR/"
rm -rf "$STAGING"

ICON="$APPDIR/packaging/desktop/Eli_Icon.png"
[ -f "$ICON" ] || ICON="$ROOT/packaging/desktop/Eli_Icon.png"
[ -f "$ICON" ] || ICON="$ROOT/blueprints/eli_logo2.png"

# Quoted heredoc: nothing below expands at build time. The install and upgrade of the per-user copy
# are packaging/linux/per_user_install.sh, which the .deb's launcher runs too.
cat > "$APPDIR/AppRun" <<'APPRUN_EOF'
#!/usr/bin/env bash
# ELI v3 AppImage — first double-click installs to ~/.local/share/ELI_v3, then launches.
# A newer AppImage over an existing install refreshes the code on its first launch.
set -euo pipefail
HERE="$(dirname "$(readlink -f "${0}")")"
exec bash "$HERE/packaging/linux/per_user_install.sh" "$HERE" .eli_appimage_ready "$@"
APPRUN_EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/eli-v3.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ELI v3
GenericName=Local AI Assistant
Exec=AppRun
Icon=eli
Categories=Utility;
Terminal=false
EOF

[ -f "$ICON" ] && cp "$ICON" "$APPDIR/eli.png"

APPIMAGETOOL=""
command -v appimagetool >/dev/null 2>&1 && APPIMAGETOOL="appimagetool"
[ -z "$APPIMAGETOOL" ] && [ -x "$ROOT/build/appimagetool-x86_64.AppImage" ] && APPIMAGETOOL="$ROOT/build/appimagetool-x86_64.AppImage"
if [ -z "$APPIMAGETOOL" ]; then
  curl -fsSL -o "$ROOT/build/appimagetool-x86_64.AppImage" \
    "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
  chmod +x "$ROOT/build/appimagetool-x86_64.AppImage"
  APPIMAGETOOL="$ROOT/build/appimagetool-x86_64.AppImage"
fi

export ARCH="${ARCH:-x86_64}" VERSION="$VERSION"
rm -f "$APPIMAGE"
"$APPIMAGETOOL" "$APPDIR" "$APPIMAGE"
( cd "$OUT_DIR" && sha256sum "$(basename "$APPIMAGE")" > "$(basename "$APPIMAGE").sha256" )
echo "[appimage] complete: $APPIMAGE"
