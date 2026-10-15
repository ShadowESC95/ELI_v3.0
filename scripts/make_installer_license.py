#!/usr/bin/env python3
"""Render LICENSE and the EULA into the form Inno Setup can display correctly.

The installer's licence page is where a buyer accepts the terms, so it shows
both halves of them: LICENSE (the proprietary notice) followed by
COMMERCIAL_EULA.md (the agreement itself).

Inno Setup 6 reads a `LicenseFile` as the language's ANSI code page unless the
file starts with a UTF-8 BOM. Both files are UTF-8 without one and contain `©`,
`—` and `§`, so pointing Inno straight at them renders mojibake on the licence
page ("Â©"). This writes a BOM-prefixed, CRLF copy that Inno reads as UTF-8 —
the same failure mode the shipped .ps1 files guard against, fixed the same way.

The output is generated, never edited by hand, so LICENSE and the EULA stay the
single source of truth:

    python scripts/make_installer_license.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "LICENSE"
EULA = ROOT / "COMMERCIAL_EULA.md"
DEST = ROOT / "packaging" / "windows" / "LICENSE.txt"


def main() -> int:
    for required in (SRC, EULA):
        if not required.exists():
            print(f"error: {required} not found", file=sys.stderr)
            return 1
    text = (SRC.read_text(encoding="utf-8").rstrip() + "\n\n" + "=" * 79 + "\n\n"
            + EULA.read_text(encoding="utf-8"))
    # Inno's licence pane is a plain RichEdit: CRLF keeps the wrapping sane on
    # Windows, and the BOM is what makes it decode as UTF-8 at all.
    body = text.replace("\r\n", "\n").replace("\n", "\r\n")
    DEST.parent.mkdir(parents=True, exist_ok=True)
    DEST.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    print(f"[license] wrote {DEST.relative_to(ROOT)} "
          f"({DEST.stat().st_size} bytes, UTF-8 + BOM)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
