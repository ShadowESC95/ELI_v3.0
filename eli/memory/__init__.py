from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .memory import (
    DBPaths,
    Memory,
    _clear_memory_singletons,
    get_agent_memory,
    get_memory,
    get_memory_authority,
    get_search_memory,
    rebuild_vector_index_from_search_db,
    resolve_db_paths,
)

__all__ = [
    "DBPaths",
    "Memory",
    "resolve_db_paths",
    "get_memory",
    "get_agent_memory",
    "get_search_memory",
    "get_memory_authority",
    "rebuild_vector_index_from_search_db",
    "_clear_memory_singletons",
    "get_memory_status",
]


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
