#!/usr/bin/env python3
"""Bootstrap artifacts the claims suite expects on a fresh clone.

Safe to run before pytest tests/claims/ or in CI — idempotent, never raises.

    python tools/bootstrap_claims_artifacts.py             # at the checkout's root, what a build bundles
    python tools/bootstrap_claims_artifacts.py --out DIR   # the manifest and the inventory in DIR instead

The claims suite asks for its own folder (the run's runtime state, in its sandbox): written at the
root, a test run left both files in the checkout.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_CAPS = 200
_EXPECTED_DBS = (
    "user.sqlite3",
    "agent.sqlite3",
    "system_index.sqlite3",
    "coding_memory.sqlite3",
)


def _ensure_db_stores() -> bool:
    """Mirror install.sh: seed blank templates, then init full schema for claims. Where ELI keeps its
    databases (paths.db_dir()): the checkout's artifacts/db in a source tree, the sandbox under the test
    suite. It seeded the checkout's own folder whatever the run, so the suite wrote into it."""
    template_dir = ROOT / "config" / "templates" / "db"
    try:
        from eli.core.paths import db_dir
        artifact_dir = db_dir()
        if template_dir.is_dir():
            artifact_dir.mkdir(parents=True, exist_ok=True)
            if not any(artifact_dir.glob("*.sqlite3")):
                for src in template_dir.glob("*.sqlite3"):
                    shutil.copy2(src, artifact_dir / src.name)
        from eli.core.init_data import init_all_data

        init_all_data()
        missing = [n for n in _EXPECTED_DBS if not (artifact_dir / n).exists()]
        if missing:
            print(f"[WARN] DB stores missing after init: {', '.join(missing)}", file=sys.stderr)
            return False
        print(f"[OK] database stores ({len(_EXPECTED_DBS)} blank SQLite files)")
        return True
    except Exception as e:
        print(f"[WARN] DB bootstrap skipped: {e}", file=sys.stderr)
        return False


def refresh_manifest(path: Path) -> dict:
    """Bring the manifest at `path` up to what the code holds now; the updater writes it only when
    the capabilities changed. A manifest already there used to be kept whenever it held 200 or more
    capabilities, so one written before an action was added was what the whole claims suite read
    (it is generated, not tracked: nothing else replaces it)."""
    from eli.tools.registry.capability_updater import update_capability_manifest
    return update_capability_manifest(path=path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate the capability manifest and inventory.")
    parser.add_argument("--out", type=Path, default=ROOT,
                        help="folder for the manifest and the inventory (default: the checkout's root)")
    out = parser.parse_args(argv).out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "capability_manifest.json"
    ok = True

    if not _ensure_db_stores():
        ok = False

    try:
        r = refresh_manifest(manifest)
        if not r.get("ok"):
            print(f"[WARN] manifest refresh: {r}", file=sys.stderr)
            ok = False
        else:
            print(f"[OK] capability manifest ({r.get('total', '?')} capabilities)")
    except Exception as e:
        print(f"[WARN] manifest refresh failed: {e}", file=sys.stderr)
        ok = False

    if manifest.is_file():
        try:
            total = int(json.loads(manifest.read_text(encoding="utf-8")).get("total") or 0)
            if total < MIN_CAPS:
                print(f"[WARN] manifest total {total} < {MIN_CAPS}", file=sys.stderr)
                ok = False
        except Exception as e:
            print(f"[WARN] manifest unreadable: {e}", file=sys.stderr)
            ok = False
    else:
        print("[ERR] capability_manifest.json missing after bootstrap", file=sys.stderr)
        ok = False

    # capability_inventory.generated.json — bundled introspection data (the engine
    # fallback at engine.py + capability_sync's baseline). It is written by
    # CapabilitySync, NOT by update_capability_manifest (whose docstring wrongly
    # claims it), so without generating it here a fresh CI clone would ship the
    # AppImage WITHOUT it (ELI.spec only WARNS on its absence, it doesn't fail).
    # Produce it now so the same CI PyInstaller step bundles a complete set.
    inventory = out / "capability_inventory.generated.json"
    if not inventory.is_file():
        try:
            from eli.runtime.capability_sync import CapabilitySync
            sync = CapabilitySync(repo_root=ROOT)
            sync.run()
            # CapabilitySync writes into the runtime state dir, never the repo. This file is
            # not tracked here, so on a fresh clone the copy in `out` has to be made.
            if not inventory.is_file() and Path(sync.inventory_path).is_file():
                inventory.write_bytes(Path(sync.inventory_path).read_bytes())
        except Exception as e:
            print(f"[WARN] capability inventory generation skipped: {e}", file=sys.stderr)
    if inventory.is_file():
        print("[OK] capability inventory present")
    else:
        print("[ERR] capability_inventory.generated.json missing after bootstrap", file=sys.stderr)
        ok = False

    bp = ROOT / "blueprints"
    pdfs = list(bp.glob("*.pdf")) if bp.is_dir() else []
    if not pdfs:
        print("[WARN] no blueprints/*.pdf — ship PDF docs for reference", file=sys.stderr)
        ok = False
    else:
        print(f"[OK] blueprints ({len(pdfs)} PDFs in git; markdown stays local)")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
