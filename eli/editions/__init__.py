"""ELI editions — the productization layer (shared core + composable edition packs).

ELI ships as one maintained core plus small, domain-specialised *edition packs*
(general, research, healthcare, legal, coding, content, vr, enterprise). This is the
"shared core + edition packs" model: core bugfixes happen once; editions diverge
only in the personas / templates their manifest names.

Licences name products (eli.licensing.products), not editions. This module only
*discovers* packs from their manifest.json; the one consumer is
eli.bundles.bundle_apps, which reads the "app" block of an official bundle's manifest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_EDITIONS_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Edition:
    """A discovered edition pack (its manifest + on-disk location)."""

    pkg: str                       # package dir name, e.g. "research"
    name: str                      # manifest "edition", e.g. "research"
    label: str                     # human label, e.g. "ELI Research"
    status: str                    # "scaffold" | "beta" | "stable"
    summary: str
    default_persona: str
    capability_categories: tuple[str, ...]
    report_templates: tuple[str, ...]
    suggested_tiers: tuple[str, ...]
    manifest: dict[str, Any]

    @property
    def root(self) -> Path:
        return _EDITIONS_DIR / self.pkg


def _load(manifest_path: Path) -> Edition | None:
    try:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pkg = manifest_path.parent.name
    return Edition(
        pkg=pkg,
        name=str(m.get("edition") or pkg),
        label=str(m.get("label") or pkg.title()),
        status=str(m.get("status") or "scaffold"),
        summary=str(m.get("summary") or ""),
        default_persona=str(m.get("default_persona") or "eli_neutral"),
        capability_categories=tuple(m.get("adds_capability_categories") or ()),
        report_templates=tuple(m.get("report_templates") or ()),
        suggested_tiers=tuple(m.get("suggested_tiers") or ()),
        manifest=m,
    )


def discover_editions() -> dict[str, Edition]:
    """Every edition pack on disk, keyed by edition name."""
    out: dict[str, Edition] = {}
    for child in sorted(_EDITIONS_DIR.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue
        manifest = child / "manifest.json"
        if manifest.exists():
            ed = _load(manifest)
            if ed is not None:
                out[ed.name] = ed
    return out


__all__ = [
    "Edition",
    "discover_editions",
]
