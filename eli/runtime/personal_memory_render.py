"""Unified personal-memory and belief-audit rendering — single voice for ELI.

Callers should use this module instead of reaching into personal_memory_surface,
personal_memory_clean_response, personal_memory_deep_response, or memory_audit
directly.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Tuple


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


# ── whose rows a personal-memory answer may show ──────────────────────────────
#
# Every user's facts and messages are in the one user.sqlite3. An API caller with a user id of its
# own asking "what do you know about me?" was shown the owner's facts and past messages. The answers
# built from those tables read them through this, one rule for all of them: a row with a user id is
# that user's; a row with none (a table that records no user, or a row from before rows carried
# one) is the owner's, as eli/memory/retrieval.py treats unattributed rows.

def request_user() -> Tuple[str, bool]:
    """(the user this request is for, whether that user is the install's owner). Outside a request
    from someone else it is the active local user, who is the owner."""
    try:
        from eli.kernel.request_context import user_id_var
        uid = str(user_id_var.get() or "")
    except ImportError:
        uid = ""
    try:
        from eli.memory.policy import is_active_user
        owner = is_active_user(uid)
    except ImportError:
        owner = not uid
    if not uid:
        try:
            from eli.kernel.state import get_active_user_id
            uid = str(get_active_user_id() or "")
        except ImportError:
            uid = ""
    return uid, owner


def rows_for_this_user(columns: Iterable[str]) -> Optional[Tuple[str, tuple]]:
    """A WHERE condition and its parameters that keep a table's rows to the ones this request's user
    may see, given the table's columns; None when none of them may be shown."""
    uid, owner = request_user()
    if "user_id" in {str(c) for c in columns}:
        if owner:
            return "COALESCE(user_id, '') IN ('', ?)", (uid,)
        return "user_id = ?", (uid,)
    return ("1=1", ()) if owner else None
