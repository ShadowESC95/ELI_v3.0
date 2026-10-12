"""ELI editions — the productization layer (shared core + composable edition packs).

ELI ships as one maintained core plus small, domain-specialised *edition packs*
(general, research, healthcare, legal, coding, content, vr, enterprise). This is the
"shared core + edition packs" model: core bugfixes happen once; editions diverge
only in the capabilities / personas / templates / policy they add on top.

Two orthogonal axes:
  • licence *tier*    — trial / personal / pro / enterprise  (seats + commercial
                        rights + support; see eli.licensing).
  • *edition*         — the domain pack(s) a licence entitles (this package).

A signed licence carries a tier AND entitles one or more editions. This module
only *discovers* packs from their manifest.json and resolves which are active;
gating against real licence entitlements is a documented seam (`active_editions`).

Nothing here is wired into startup yet — the registry is inert until the core
composes it. That keeps the frozen build and every existing import unaffected.
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
    policy_module: str
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
        policy_module=str(m.get("policy_module") or f"eli.editions.{pkg}.policy"),
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


def get_edition(name: str) -> Edition | None:
    return discover_editions().get(str(name).lower().strip())


def active_editions(entitled: set[str] | None = None) -> dict[str, Edition]:
    """Editions the current licence may use.

    `entitled` is the set of edition names a validated licence grants. When None
    (not yet wired to licence entitlements) we fall back to the always-available
    'general' pack only, so the default experience is unchanged.

    TODO(v3.1): source `entitled` from the signed licence payload — extend
    eli.licensing.types.LicensePayload with an `editions: list[str]` field and
    resolve it here. Enterprise tier ⇒ all editions.
    """
    found = discover_editions()
    if entitled is None:
        return {k: v for k, v in found.items() if v.name == "general"}
    return {k: v for k, v in found.items() if v.name in entitled}


__all__ = [
    "Edition",
    "discover_editions",
    "get_edition",
    "active_editions",
]
