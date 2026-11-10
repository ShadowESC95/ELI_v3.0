from __future__ import annotations

import os

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .memory import (
    DBPaths,
    Memory,
    _clear_memory_singletons,
    get_agent_memory,
    get_memory,
    get_memory_authority,
    resolve_db_paths,
)

__all__ = [
    "DBPaths",
    "Memory",
    "resolve_db_paths",
    "get_memory",
    "get_agent_memory",
    "get_memory_authority",
    "_clear_memory_singletons",
    "add_memory",
    "search_memory",
    "recall_recent",
    "log_event",
    "get_memory_status",
]


_legacy_memory_singleton: Optional[Memory] = None


def _use_legacy_memory_db(explicit: Optional[bool] = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    return os.environ.get("ELI_USE_LEGACY_MEMORY_DB", "0").strip().lower() in {"1", "true", "yes", "on"}


def _active_memory() -> Memory:
    return get_memory()


def _legacy_memory() -> Memory:
    """
    Legacy compatibility store for older tests/modules that expect
    eli.memory helpers to write to user.sqlite3 instead of user.sqlite3.
    """
    global _legacy_memory_singleton
    db = resolve_db_paths().memory_db
    if db is None:
        db = resolve_db_paths().user_db
    db = Path(db).expanduser().resolve()
    if _legacy_memory_singleton is None or Path(_legacy_memory_singleton.db_path).resolve() != db:
        _legacy_memory_singleton = Memory(db_path=db)
    return _legacy_memory_singleton


def _compat_memory(use_legacy_db: Optional[bool] = None) -> Memory:
    return _legacy_memory() if _use_legacy_memory_db(use_legacy_db) else _active_memory()


def add_memory(
    text: str,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
):
    mem = _compat_memory(kwargs.get('use_legacy_db'))
    if hasattr(mem, "store_memory"):
        return mem.store_memory(text, tags=tags or [], metadata=metadata or {})
    raise AttributeError("Memory.store_memory not found")


def search_memory(query: str, limit: int = 5, **kwargs: Any):
    mem = _compat_memory(kwargs.get('use_legacy_db'))

    if hasattr(mem, "search_memory"):
        return mem.search_memory(query, limit=limit)

    if hasattr(mem, "recall_memory"):
        return mem.recall_memory(query, limit=limit)

    raise AttributeError("No search-compatible method found on Memory")


def recall_recent(limit: int = 5, k: Optional[int] = None, **kwargs: Any):
    mem = _compat_memory(kwargs.get('use_legacy_db'))
    actual_limit = k if k is not None else limit

    if hasattr(mem, "recall_recent"):
        return mem.recall_recent(limit=actual_limit)

    if hasattr(mem, "get_recent_conversation"):
        return mem.get_recent_conversation(limit=actual_limit)

    return []


def log_event(event_type: str, details: Any):
    mem = get_memory()
    if hasattr(mem, "log_habit_event"):
        return mem.log_habit_event(event_type, details)
    raise AttributeError("Memory.log_habit_event not found")


def get_memory_status(db_path: Optional[str | Path] = None) -> Dict[str, Any]:
    """
    Grounded telemetry helper used by UI/status answers: one database's counts as the one counter
    reads them (eli/memory/memory_truth.inspect_sqlite), instead of making the model guess.
    """
    from eli.memory.memory_truth import inspect_sqlite
    if db_path is None:
        resolved = resolve_db_paths().user_db or resolve_db_paths().memory_db
    else:
        resolved = Path(db_path).expanduser().resolve()

    db = Path(resolved).expanduser().resolve()
    seen = inspect_sqlite(db)
    counts = seen.get("counts") or {}
    out: Dict[str, Any] = {
        "ok": not seen.get("errors"),
        "db_path": str(db),
        "exists": bool(seen.get("exists")),
        "tables": list(seen.get("tables") or []),
        "memory_entries": int(counts.get("memories") or 0) + int(counts.get("semantic") or 0),
        "conversation_turns": int(counts.get("conversation_turns") or 0),
        "distinct_sessions": int(seen.get("conversations") or 0),
        "db_size_mb": db.stat().st_size / 1048576.0 if db.exists() else 0.0,
    }
    if "semantic" in counts:
        out["semantic_facts"] = int(counts.get("semantic") or 0)
    if not db.exists():
        out["error"] = f"database_not_found: {db}"
    elif seen.get("errors"):
        out["error"] = "; ".join(seen["errors"])
    return out

from .memory import get_search_memory, get_memory_authority, rebuild_vector_index_from_search_db

# === MKXI_LOCAL_DB_AUTHORITY_PATCH ===
from pathlib import Path as _MKXIPath
import os as _MKXIOS

def _mkxi_user_db_path():
    from eli.core.paths import get_paths as _mkxi_get_paths
    gp = _mkxi_get_paths()
    raw = (
        _MKXIOS.environ.get("ELI_MEMORY_DB")
        or _MKXIOS.environ.get("ELI_MEMORY_DB_PATH")
        or str(gp.user_db)
    )
    p = _MKXIPath(raw).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

def _mkxi_agent_db_path():
    from eli.core.paths import get_paths as _mkxi_get_paths
    gp = _mkxi_get_paths()
    raw = _MKXIOS.environ.get("ELI_AGENT_DB") or str(gp.agent_db)
    p = _MKXIPath(raw).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    return p

# Compatibility helpers for resolve_db_paths / get_memory / get_agent_memory
# REMOVED. The previous shadows returned plain dicts (causing
# `'dict' object has no attribute 'user_db'` on every callsite using
# attribute access) and bypassed the canonical Memory(db_path=...)
# arg contract. The canonical versions imported from eli.memory.memory
# at the top of this file (line ~16) return proper DBPaths dataclasses
# and accept an optional db_path. _mkxi_user_db_path / _mkxi_agent_db_path
# above remain in place — they are independent helpers any caller can
# use directly to resolve a single db path under env-var precedence.

# Compatibility public API: older runtime/executor code may import store_memory from eli.memory
# directly. Keep this wrapper generic and user-neutral.

def store_memory(*args, **kwargs):
    """
    Compatibility wrapper for storing a memory.

    Delegates to the active Memory authority without hard-coded user data.
    """
    from eli.memory.memory import get_memory

    mem = get_memory()

    for method_name in ("store_memory", "add_memory", "remember", "store"):
        method = getattr(mem, method_name, None)
        if callable(method):
            return method(*args, **kwargs)

    raise AttributeError(
        "No supported memory store method found on active Memory authority."
    )


def recall_memory(*args, **kwargs):
    """
    Compatibility wrapper for recalling memories.
    """
    from eli.memory.memory import get_memory

    mem = get_memory()

    for method_name in ("recall_memory", "recall", "search_memory", "search"):
        method = getattr(mem, method_name, None)
        if callable(method):
            return method(*args, **kwargs)

    raise AttributeError(
        "No supported memory recall/search method found on active Memory authority."
    )

