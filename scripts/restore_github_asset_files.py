#!/usr/bin/env python3
"""Restore model assets from a GitHub Release (the voice is fetched from its own pinned upstream).

Supports two release layouts:
  1. Manifest + chunked files (``direct_asset_manifest.json`` from upload_github_asset_files.py)
  2. Flat per-file assets (legacy ``local-assets-v2.1`` style — .gguf / .onnx at release root)
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import List

from asset_release_policy import (
    DEFAULT_ASSET_REPO,
    DEFAULT_ASSET_TAG,
    flat_restore_destination,
)

ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: List[str], cwd: Path = ROOT) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def _download(repo: str, tag: str, download_dir: Path) -> None:
    download_dir.mkdir(parents=True, exist_ok=True)
    _run(["gh", "release", "download", tag, "--repo", repo, "--dir", str(download_dir), "--clobber"])


def _cat_assets(asset_paths: List[Path], dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as out:
        for path in asset_paths:
            with path.open("rb") as src:
                shutil.copyfileobj(src, out, length=1024 * 1024)


def _restore_manifest(download_dir: Path, manifest_path: Path) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = manifest.get("files") or []
    for idx, row in enumerate(files, 1):
        rel = row.get("path")
        assets = [download_dir / name for name in (row.get("assets") or [])]
        missing = [str(p) for p in assets if not p.exists()]
        if missing:
            raise SystemExit(f"Missing assets for {rel}: {missing[:5]}")
        dst = ROOT / str(rel)
        print(f"[restore {idx}/{len(files)}] {rel}", flush=True)
        _cat_assets(assets, dst)
    return len(files)


def _restore_flat(download_dir: Path) -> int:
    """Place loose .gguf release files into models/."""
    restored = 0
    for path in sorted(download_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        if name in ("direct_asset_manifest.json",):
            continue
        if name.endswith(".part-0000") or ".tar.gz.part-" in name:
            continue
        if not name.endswith(".gguf"):
            print(f"[restore flat] skip unknown asset type: {name}", flush=True)
            continue
        dst = flat_restore_destination(ROOT, name)
        if dst.exists() and dst.stat().st_size == path.stat().st_size:
            print(f"[restore flat] already present: {dst.relative_to(ROOT)}", flush=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dst)
        print(f"[restore flat] {name} → {dst.relative_to(ROOT)}", flush=True)
        restored += 1
    if restored == 0:
        raise SystemExit(
            f"No restorable assets found in {download_dir}. "
            "Expected .gguf files or direct_asset_manifest.json."
        )
    return restored


def restore(args: argparse.Namespace) -> int:
    download_dir = Path(args.download_dir).expanduser().resolve()
    if not args.from_dir:
        print(f"[restore] Downloading release {args.repo}@{args.tag}", flush=True)
        _download(args.repo, args.tag, download_dir)
    else:
        download_dir = Path(args.from_dir).expanduser().resolve()

    manifest_path = download_dir / "direct_asset_manifest.json"
    if manifest_path.exists():
        print("[restore] Using direct_asset_manifest.json", flush=True)
        count = _restore_manifest(download_dir, manifest_path)
    else:
        print("[restore] No manifest — using flat-file layout", flush=True)
        count = _restore_flat(download_dir)

    print(f"[restore] Complete ({count} item(s) processed)", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore ELI model/voice assets from GitHub Release.")
    parser.add_argument("--repo", default=DEFAULT_ASSET_REPO)
    parser.add_argument("--tag", default=DEFAULT_ASSET_TAG)
    parser.add_argument("--download-dir", default=str(ROOT / "dist" / "github_assets" / "direct_download"))
    parser.add_argument("--from-dir", default="", help="restore from an existing local download directory")
    args = parser.parse_args()
    return restore(args)


if __name__ == "__main__":
    raise SystemExit(main())
