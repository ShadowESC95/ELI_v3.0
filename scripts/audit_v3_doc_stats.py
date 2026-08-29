#!/usr/bin/env python3
"""Print measured v3 stats for blueprint/doc authors. Run from repo root."""
from __future__ import annotations

import glob
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    eli_files = sorted(ROOT.glob("eli/**/*.py"))
    eli_lines = sum(sum(1 for _ in open(f, encoding="utf-8", errors="ignore")) for f in eli_files)
    m = json.loads((ROOT / "capability_manifest.json").read_text(encoding="utf-8"))
    caps = m["capabilities"]
    doc_routable = {
        c["action"] for c in caps if c.get("routable") or c.get("in_supported_list")
    }
    router_routable = sum(1 for c in caps if c.get("routable"))
    pkg: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for f in eli_files:
        name = f.relative_to(ROOT / "eli").parts[0]
        pkg[name][0] += 1
        pkg[name][1] += sum(1 for _ in open(f, encoding="utf-8", errors="ignore"))
    top_files = sorted(
        ((sum(1 for _ in open(f, encoding="utf-8", errors="ignore")), str(f.relative_to(ROOT))) for f in eli_files),
        reverse=True,
    )[:12]
    api = ROOT / "api" / "server.py"
    api_lines = sum(1 for _ in open(api, encoding="utf-8", errors="ignore")) if api.is_file() else 0
    print(f"eli/: {len(eli_files)} files, {eli_lines} lines")
    print(f"api/server.py: {api_lines} lines")
    print(f"capabilities: {m['total']} total; {len(doc_routable)} doc-routable; {router_routable} router-routable")
    print("packages:")
    for name, (files, lines) in sorted(pkg.items(), key=lambda x: -x[1][1]):
        print(f"  eli/{name}: {files} files, {lines} lines")
    print("largest modules:")
    for n, p in top_files:
        print(f"  {n:5d}  {p}")


if __name__ == "__main__":
    main()
