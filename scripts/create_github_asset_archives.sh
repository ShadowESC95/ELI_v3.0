#!/usr/bin/env bash
# Build GitHub Release asset archives for large local payloads.
#
# This does not push anything. It creates split tar.gz chunks under
# dist/github_assets/upload/. Each chunk is kept below the GitHub release
# per-asset practical limit so `gh release upload` can send them safely.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT/dist/github_assets/upload"
CHUNK_SIZE="${ELI_ASSET_CHUNK_SIZE:-1900m}"
INCLUDE_RUNTIME=0
INCLUDE_VENV=0

usage() {
  cat <<'EOF'
Usage: scripts/create_github_asset_archives.sh [options]

Options:
  --include-runtime   Include artifacts/ runtime state. Private/local data risk.
  --include-venv      Include .venv/. Machine-specific and not recommended.
  --chunk-size SIZE   split size, default 1900m.
  -h, --help          Show help.

Default archive set (what dist/github_assets/asset_manifest.json marks for upload):
  models/     the models ELI's catalog names
  tts_piper/  voices, without the restricted ones
  dist/github_assets/asset_manifest.json
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --include-runtime) INCLUDE_RUNTIME=1 ;;
    --include-venv) INCLUDE_VENV=1 ;;
    --chunk-size)
      shift
      CHUNK_SIZE="${1:?--chunk-size requires a value}"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

mkdir -p "$OUT_DIR"

echo "[asset] Writing ignored-file manifest"
python3 "$ROOT/scripts/github_asset_manifest.py" --output "$ROOT/dist/github_assets/asset_manifest.json"

# Packs the files given NUL-separated on stdin into $OUT_DIR/<name>.tar.gz.part-*.
make_split_tar_from_list() {
  local name="$1"
  local prefix="$OUT_DIR/${name}.tar.gz.part-"
  local list="$OUT_DIR/.${name}.files"
  cat >"$list"
  rm -f "$prefix"*
  if [ ! -s "$list" ]; then
    echo "[asset] Skip $name: nothing to publish"
    rm -f "$list"
    return 0
  fi
  echo "[asset] Creating split archive: $name"
  (
    cd "$ROOT"
    tar -czf - --null -T "$list" 2>"$OUT_DIR/${name}.tar.log" | split -b "$CHUNK_SIZE" - "$prefix"
  )
  rm -f "$list"
  ls -lh "$prefix"*
}

make_split_tar() {
  local name="$1"
  shift
  local p
  for p in "$@"; do
    if [ -e "$ROOT/$p" ]; then printf '%s\0' "$p"; fi
  done | make_split_tar_from_list "$name"
}

# The model and voice archives hold what the manifest marks for upload, and nothing else: the same
# rule as the direct-file upload (scripts/asset_release_policy.py), so restricted voices and the
# person's own recordings, wake words and adapters under models/ are never packed.
manifest_files() {
  python3 - "$ROOT/dist/github_assets/asset_manifest.json" "$1" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
for row in manifest["files"]:
    if row["category"] == sys.argv[2] and row["upload_recommended"]:
        sys.stdout.write(row["path"] + "\0")
PY
}

manifest_files model_asset | make_split_tar_from_list "eli-model-assets"
manifest_files voice_asset | make_split_tar_from_list "eli-voice-assets"

if [ "$INCLUDE_RUNTIME" -eq 1 ]; then
  make_split_tar "eli-runtime-private-state" "artifacts"
fi

if [ "$INCLUDE_VENV" -eq 1 ]; then
  make_split_tar "eli-local-venv-linux" ".venv"
fi

cp "$ROOT/dist/github_assets/asset_manifest.json" "$OUT_DIR/asset_manifest.json"
echo "[asset] Archive directory: $OUT_DIR"
