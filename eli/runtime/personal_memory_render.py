"""Unified personal-memory and belief-audit rendering — single voice for ELI.

Callers should use this module instead of reaching into personal_memory_surface,
personal_memory_clean_response, personal_memory_deep_response, or memory_audit
directly.
"""
from __future__ import annotations

from typing import Any, Dict


def render_profile_summary(question: str = "") -> str:
    """What ELI knows about the user — clean, poison-filtered facts."""
    from eli.runtime.personal_memory_clean_response import build_clean_personal_memory_response
    return str(build_clean_personal_memory_response(question) or "").strip()


def render_profile_deep(question: str = "") -> str:
    """Deep personal-memory explain (same canonical renderer)."""
    from eli.runtime.personal_memory_deep_response import build_personal_memory_deep_response
    return str(build_personal_memory_deep_response(question) or "").strip()


def render_belief_audit(query: str = "") -> str:
    """Why ELI believed something — memories + evidence ledger."""
    from eli.runtime.memory_audit import explain_belief
    return explain_belief(query)


def render_belief_audit_json(query: str = "", *, limit: int = 5) -> Dict[str, Any]:
    from eli.runtime.memory_audit import explain_belief_json
    return explain_belief_json(query, limit=limit)


def is_personal_memory_query(text: str) -> bool:
    from eli.runtime.personal_memory_surface import is_personal_memory_query as _is_pm
    return bool(_is_pm(text))
