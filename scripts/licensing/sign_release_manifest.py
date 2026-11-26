#!/usr/bin/env python3
"""Sign eli/RELEASE.manifest into a copy of ELI v3's source about to be packaged.

    ELI_RELEASE_SIGNING_KEY=… python3 scripts/licensing/sign_release_manifest.py [--root DIR] [--require]

Every package that ships ELI v3 as source (the wheel, the sdist, the .deb, the portable folders
and tarballs) carries this manifest: the SHA-256 of each file that decides whether ELI may run
(eli/licensing/integrity.py, COVERED), signed with ELI's release key. An installed copy whose
files differ from it, or that has none, refuses to start (eli.licensing.integrity).

ELI_RELEASE_SIGNING_KEY is the release-signing key's PEM, or a path to it (an Actions secret in
the release workflow; never ELI's licence key). Without it nothing is signed: a warning, or with
--require a failure, since a package without its manifest will not start.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path, package_dir: Path | None = None):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[str(package_dir)] if package_dir else None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(REPO), help="the folder holding the eli package (a staging copy)")
    ap.add_argument("--require", action="store_true", help="fail when no release key is set")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    secret = os.environ.get("ELI_RELEASE_SIGNING_KEY", "").strip()
    if not secret:
        print("warning: ELI_RELEASE_SIGNING_KEY is not set; no release manifest signed: "
              "an installed copy of this package will refuse to start", file=sys.stderr)
        return 1 if args.require else 0
    key = secret.encode() if "-----BEGIN" in secret else Path(secret).expanduser().read_bytes()
    sdk_dir = REPO / "eli" / "licensing" / "_sdk"
    _load("eli_sdk", sdk_dir / "__init__.py", sdk_dir)
    from eli_sdk.integrity import build_manifest, sign_manifest

    covered = _load("eli_v3_integrity", REPO / "eli" / "licensing" / "integrity.py").COVERED
    version = re.search(r'^version = "([^"]+)"', (root / "pyproject.toml").read_text(), re.M).group(1)
    manifest = build_manifest(root, list(covered), product="eli", version=version)
    if not manifest["files"]:
        print(f"no files to sign under {root}", file=sys.stderr)
        return 1
    out = root / "eli" / "RELEASE.manifest"
    out.write_text(sign_manifest(manifest, key) + "\n", encoding="utf-8")
    print(f"signed {len(manifest['files'])} files for ELI v3 {version}: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
