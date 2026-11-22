#!/usr/bin/env python3
"""Locate and print ELI's licence, wherever ELI happens to be running from.

ELI is proprietary software licensed under the ELI End User Licence Agreement,
so every artifact has to be able to put the terms in front of the user — not
just the Windows installer, which is the only wrapper with a licence pane of its
own. The terms are two files kept side by side: `LICENSE` (the short proprietary
notice) and `COMMERCIAL_EULA.md` (the agreement itself). This module is the one
place that knows where they live in each shape ELI ships in:

  * frozen bundle (exe / zip / dmg / AppImage) — `sys._MEIPASS/`, put there by
    ELI.spec's data manifest
  * source tree / portable tarball          — the repo root

Both entry points expose it as `--license`, so the command is identical on every
platform and in every download.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional

EULA_NAME = "COMMERCIAL_EULA.md"

# Companion files worth naming when someone asks about terms.
_COMPANIONS = ("NOTICE", "THIRD_PARTY_NOTICES.md", "models/MODEL_LICENSES.md")

SUMMARY = (
    "ELI v3 — proprietary commercial software, licensed under the\n"
    "ELI End User Licence Agreement (COMMERCIAL_EULA.md).\n"
    "Copyright (c) 2026 Jason Fitzgibbon Bridgeman (GitHub: ShadowESC95)\n"
    "<jaybridgeman0095@gmail.com>. All rights reserved.\n"
    "\n"
    "Use requires a purchased licence or a trial under that agreement.\n"
    "You may NOT redistribute, publish, host for others, sublicense or sell it.\n"
)

_MISSING = (
    "\nThe {name} file was not found next to this build.\n"
    "Ask for a copy at jaybridgeman0095@gmail.com.\n"
)


def _roots() -> tuple[Path, ...]:
    """Every directory that could hold the licence, most specific first."""
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:  # PyInstaller one-dir/one-file bundle
        roots.append(Path(meipass))
    exe_dir = Path(sys.executable).resolve().parent
    roots.append(exe_dir)
    # …/ELI.app/Contents/MacOS/ELI -> the bundle's Resources
    roots.append(exe_dir.parent / "Resources")
    # source checkout: eli/runtime/license_info.py -> repo root
    roots.append(Path(__file__).resolve().parent.parent.parent)
    roots.append(Path.cwd())
    seen: list[Path] = []
    for r in roots:
        if r not in seen:
            seen.append(r)
    return tuple(seen)


def license_path() -> Optional[Path]:
    """Full path to the LICENSE that ships with this build, or None."""
    for root in _roots():
        candidate = root / "LICENSE"
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def eula_path() -> Optional[Path]:
    """The EULA shipped beside LICENSE, or None."""
    path = license_path()
    if path is None:
        return None
    candidate = path.parent / EULA_NAME
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def license_text() -> str:
    """LICENSE followed by the EULA, or the summary for whichever didn't ship."""
    path = license_path()
    if path is None:
        return SUMMARY + _MISSING.format(name="LICENSE")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return SUMMARY
    eula = eula_path()
    if eula is None:
        return text.rstrip() + "\n" + _MISSING.format(name=EULA_NAME)
    try:
        return text.rstrip() + "\n\n" + "=" * 79 + "\n\n" + eula.read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return text.rstrip() + "\n" + _MISSING.format(name=EULA_NAME)


def made_by() -> str:
    """Who holds the copyright, as the licence this build ships with names them: who made ELI, the
    name alone (the handle and email after it are not part of it). "" when the licence names nobody."""
    m = re.search(r"Copyright\s*(?:©|\(c\))\s*(?:\d{4}(?:\s*[-–]\s*\d{4})?\s+)?([^.(<\n]+)", license_text(), re.I)
    return m.group(1).strip() if m else ""


def print_license() -> int:
    """Print the licence plus pointers to the third-party notices. Never raises."""
    try:
        sys.stdout.write(license_text().rstrip() + "\n")
        path = license_path()
        if path is not None:
            sys.stdout.write(f"\nThis copy: {path}\n")
            extra = [n for n in _COMPANIONS if (path.parent / n).is_file()]
            if extra:
                sys.stdout.write(
                    "Third-party components and model/voice terms: "
                    + ", ".join(extra) + "\n"
                )
    except Exception:  # a licence print must never take the process down
        try:
            sys.stdout.write(SUMMARY)
        except Exception:
            pass
    return 0
