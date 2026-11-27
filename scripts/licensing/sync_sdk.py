#!/usr/bin/env python3
"""Copy eli-sdk into eli/licensing/_sdk and record what was copied.

    python3 scripts/licensing/sync_sdk.py ~/Desktop/eli-sdk

ELI v3 reads licence keys, issuer certificates, revocation lists and release manifests with the
same code as ELI Legal and the licence store. It keeps a copy rather than fetching the private
package; SOURCE.json lists the version and the SHA-256 of every file, and
tests/test_licensing_sdk.py fails if the copy is edited by hand.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

FILES = ("_encoding.py", "errors.py", "keys.py", "issuing.py", "revocations.py", "machine.py",
         "trial.py", "store.py", "activation.py", "integrity.py", "products.py", "versions.py",
         "py.typed", "eli.pub", "release.pub", "revoked.list")
HERE = Path(__file__).resolve().parents[2] / "eli" / "licensing" / "_sdk"


def record(folder: Path, version: str) -> dict:
    return {"version": version,
            "files": {f: hashlib.sha256((folder / f).read_bytes()).hexdigest() for f in FILES}}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    source = Path(argv[1]).expanduser() / "src" / "eli_sdk"
    version = re.search(r'__version__ = "([^"]+)"', (source / "__init__.py").read_text()).group(1)
    for name in FILES:
        shutil.copy2(source / name, HERE / name)
    (HERE / "SOURCE.json").write_text(json.dumps(record(HERE, version), indent=2) + "\n")
    print(f"eli-sdk {version} copied into {HERE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
