from __future__ import annotations

import pickle
import sqlite3
from pathlib import Path
from typing import Any, Dict


def _store_paths() -> Dict[str, Path]:
    """Where the stores are, as the stores themselves resolve them (eli/core/paths with its ELI_USER_DB /
    ELI_MEMORY_DB / ELI_AGENT_DB overrides; the vector store's own path rule): the report read
    <data>/db/user.sqlite3 and <data>/vectors whatever the stores actually used."""
    from eli.core import paths as _paths
    from eli.memory.vector_store import _get_index_paths
    index, meta = _get_index_paths()
    return {"user_db": Path(_paths.user_db_path()), "agent_db": Path(_paths.agent_db_path()),
            "index": Path(index), "meta": Path(meta)}


def _count_table(cur: sqlite3.Cursor, table: str) -> int | None:
    try:
        safe = '"' + table.replace('"', '""') + '"'
        row = cur.execute(f"SELECT COUNT(*) FROM {safe}").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return None


def _distinct_conversations(cur: sqlite3.Cursor) -> int | None:
    """How many conversations the turns belong to: the distinct sessions of conversation_turns (the legacy
    `conversations` table nothing writes any more was counted, so this was always 0)."""
    try:
        row = cur.execute("SELECT COUNT(DISTINCT session_id) FROM conversation_turns "
                          "WHERE COALESCE(session_id, '') <> ''").fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return None


def inspect_sqlite(path: Path) -> Dict[str, Any]:
    """Every table of one database with its row count, and the conversations its turns belong to. A
    database that is not there, or a table that cannot be counted, is an error, never a 0."""
    out: Dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "tables": [],
        "counts": {},
        "conversations": None,
        "errors": [],
    }
    if not path.exists():
        out["errors"].append(f"missing: {path}")
        return out

    try:
        con = sqlite3.connect(str(path))
        cur = con.cursor()
        tables = [
            str(r[0])
            for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        out["tables"] = tables
        for table in tables:
            out["counts"][table] = _count_table(cur, table)
            if out["counts"][table] is None:
                out["errors"].append(f"{path.name}: {table} could not be counted")
        if "conversation_turns" in tables:
            out["conversations"] = _distinct_conversations(cur)
        con.close()
    except Exception as exc:
        out["errors"].append(type(exc).__name__ + ": " + str(exc))
    return out


def inspect_vector_store() -> Dict[str, Any]:
    where = _store_paths()
    index_path = where["index"]
    # Canonical metadata is JSON now; fall back to legacy pickle only if present.
    meta_path = where["meta"]
    if not meta_path.exists():
        _legacy = meta_path.with_name("meta.pkl")
        if _legacy.exists():
            meta_path = _legacy
    out: Dict[str, Any] = {
        "index_path": str(index_path),
        "index_exists": index_path.exists(),
        "index_size": index_path.stat().st_size if index_path.exists() else 0,
        "faiss_ntotal": None,
        "faiss_d": None,
        "meta_path": str(meta_path),
        "meta_exists": meta_path.exists(),
        "meta_size": meta_path.stat().st_size if meta_path.exists() else 0,
        "meta_type": None,
        "meta_len": None,
        "errors": [],
    }

    if index_path.exists():
        try:
            import faiss  # type: ignore
            index = faiss.read_index(str(index_path))
            out["faiss_ntotal"] = int(getattr(index, "ntotal", -1))
            out["faiss_d"] = int(getattr(index, "d", -1))
        except Exception as exc:
            out["errors"].append("faiss: " + type(exc).__name__ + ": " + str(exc))

    if meta_path.exists():
        try:
            if str(meta_path).endswith(".json"):
                import json as _json
                with meta_path.open("r", encoding="utf-8") as f:
                    meta = _json.load(f)
            else:
                with meta_path.open("rb") as f:
                    meta = pickle.load(f)
            out["meta_type"] = type(meta).__name__
            try:
                out["meta_len"] = len(meta)
            except Exception:
                out["meta_len"] = None
        except Exception as exc:
            out["errors"].append("meta: " + type(exc).__name__ + ": " + str(exc))

    return out


def memory_truth_report() -> Dict[str, Any]:
    """The one count of what memory holds (memories, turns, conversations, vectors), read from the
    stores where they are. Every status path formats these numbers rather than counting again."""
    where = _store_paths()
    user_db = where["user_db"]
    agent_db = where["agent_db"]
    user = inspect_sqlite(user_db)
    agent = inspect_sqlite(agent_db)
    vectors = inspect_vector_store()

    user_counts = user.get("counts", {}) if isinstance(user.get("counts"), dict) else {}
    agent_counts = agent.get("counts", {}) if isinstance(agent.get("counts"), dict) else {}

    summary = {
        "user_memories": int(user_counts.get("memories") or 0),
        "user_memory_fts": int(user_counts.get("memories_fts") or 0),
        "user_conversation_turns": int(user_counts.get("conversation_turns") or 0),
        "user_conversations": user.get("conversations"),
        "user_observations": int(user_counts.get("observations") or 0),
        "user_recall_log": int(user_counts.get("recall_log") or 0),
        "agent_memories": int(agent_counts.get("memories") or 0),
        "agent_observations": int(agent_counts.get("observations") or 0),
        "vector_ntotal": vectors.get("faiss_ntotal"),
        "vector_dim": vectors.get("faiss_d"),
        "vector_meta_len": vectors.get("meta_len"),
    }

    return {
        "summary": summary,
        "databases": {
            "user": user,
            "agent": agent,
        },
        "vectors": vectors,
    }


def format_memory_truth(report: Dict[str, Any] | None = None) -> str:
    """Human-readable memory summary for user-facing answers.

    Previously this returned a multi-hundred-line raw JSON `memory_truth_evidence`
    dump straight into chat. The full structured report is still available via
    memory_truth_report(); this returns prose. (Fix: issue #4 raw-JSON dump.)
    """
    report = report or memory_truth_report()
    s = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    dbs = report.get("databases", {})
    vectors = report.get("vectors", {})
    errors = []
    for block in (dbs.get("user", {}), dbs.get("agent", {}), vectors):
        errors.extend(block.get("errors", []) if isinstance(block, dict) else [])

    def _g(k):
        v = s.get(k)
        return v if v is not None else "?"

    lines = ["Memory system — grounded counts:"]
    lines.append(f"- long-term memories: {_g('user_memories')} (FTS index: {_g('user_memory_fts')})")
    lines.append(f"- conversation turns: {_g('user_conversation_turns')} across {_g('user_conversations')} conversations")
    lines.append(f"- observations: {_g('user_observations')}; recall-log rows: {_g('user_recall_log')}")
    lines.append(f"- FAISS vectors: {_g('vector_ntotal')} (dim {_g('vector_dim')}, meta {_g('vector_meta_len')})")
    lines.append(f"- agent-side: {_g('agent_memories')} memories, {_g('agent_observations')} observations")
    if errors:
        lines.append(f"- ⚠ {len(errors)} error(s): {errors[:3]}")
    else:
        lines.append("- status: all stores reachable, no errors.")
    return "\n".join(lines)
