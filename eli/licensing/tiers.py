"""Tier policy — seats, EULA refs, and checkout defaults for paid ELI v3."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from eli.licensing.types import VALID_TIERS


def _policy_path() -> Path:
    try:
        from eli.core.paths import project_root
        return project_root() / "config" / "licensing" / "tier_policy.json"
    except Exception:
        return Path(__file__).resolve().parents[2] / "config" / "licensing" / "tier_policy.json"


@lru_cache(maxsize=1)
def load_tier_policy() -> dict[str, Any]:
    path = _policy_path()
    if not path.is_file():
        return {"tiers": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def tier_config(tier: str) -> dict[str, Any]:
    t = str(tier or "").lower().strip()
    if t not in VALID_TIERS:
        raise ValueError(f"unknown tier: {tier!r} (expected one of {sorted(VALID_TIERS)})")
    cfg = (load_tier_policy().get("tiers") or {}).get(t)
    if not isinstance(cfg, dict):
        return {"label": t.title(), "seats_default": 1}
    return dict(cfg)


def default_seats(tier: str) -> int:
    return max(1, int(tier_config(tier).get("seats_default") or 1))


def default_expires_at(tier: str) -> str | None:
    days = tier_config(tier).get("duration_days_default")
    if days is None:
        return None
    try:
        n = int(days)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    dt = datetime.now(timezone.utc) + timedelta(days=n)
    return dt.replace(microsecond=0).isoformat()


def eula_path(tier: str) -> Path:
    rel = str(tier_config(tier).get("eula") or "COMMERCIAL_EULA.md")
    try:
        from eli.core.paths import project_root
        root = project_root()
    except Exception:
        root = Path(__file__).resolve().parents[2]
    return (root / rel).resolve()
