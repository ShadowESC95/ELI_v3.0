#!/usr/bin/env python3
"""Make this install's own absolute paths in its runtime artifacts relative to it, so the install can
move. Every other path is the user's (or another ELI product's) and is kept exactly as written: it
never rewrites another version's or another checkout's paths into this one (eli.core.portable_paths).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eli.core.portable_paths import make_portable_path_value, project_root  # noqa: E402

PATH_KEYS = frozenset({
    "model_path", "user_db", "agent_db", "memory_db", "db_path",
    "last_write_db", "path", "file", "folder", "root", "mmproj_path",
    "vision_model_path", "vision_fast_model_path", "vision_fast_mmproj_path",
    "gguf_model_path", "custom_model_path", "bundled_model_path", "image_model_path",
})


def _scrub_obj(obj, root: Path):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and k in PATH_KEYS and isinstance(v, str):
                out[k] = make_portable_path_value(v, root)
            else:
                out[k] = _scrub_obj(v, root)
        return out
    if isinstance(obj, list):
        return [_scrub_obj(x, root) for x in obj]
    if isinstance(obj, str) and str(root).replace("\\", "/") in obj.replace("\\", "/"):
        return make_portable_path_value(obj, root)
    return obj


def scrub_file(path: Path, root: Path) -> bool:
    if not path.is_file():
        return False
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return False
    try:
        data = json.loads(raw)
    except Exception:
        # Not JSON: only this install's own prefix is taken off, as in a JSON value. Folder names of
        # other versions and checkouts were matched here and rewritten to this install's root.
        prefix = str(root).replace("\\", "/").rstrip("/") + "/"
        new_raw = raw.replace(prefix, "")
        if new_raw != raw:
            path.write_text(new_raw, encoding="utf-8")
            return True
        return False
    cleaned = _scrub_obj(data, root)
    if cleaned == data:
        return False
    path.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


def main() -> int:
    root = project_root(ROOT)
    targets = [
        root / "artifacts" / "runtime" / "world_model.json",
        root / "artifacts" / "runtime_snapshot.json",
        root / "artifacts" / "runtime" / "goals.json",
        root / "artifacts" / "runtime" / "autonomy_scheduler.json",
        root / "artifacts" / "vectors" / "meta.json",
        root / "config" / "settings.json",
    ]
    changed = 0
    for t in targets:
        if scrub_file(t, root):
            print(f"scrubbed: {t.relative_to(root)}")
            changed += 1
    # Reload + resave world model through canonical code path
    try:
        from eli.kernel.world_model import load_world_model, save_world_model
        load_world_model(force_reload=True)
        save_world_model()
        print("world_model: re-saved via kernel")
        changed += 1
    except Exception as exc:
        print(f"world_model refresh skipped: {exc}")
    print(f"done ({changed} updates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
